"""iThenticate 2.0 / Turnitin Similarity through the Turnitin Core API (TCA), the REST API
their integrations use, for authors whose institution issues TCA credentials. Plain code,
no model calls.

Docs: the API reference https://developers.turnitin.com/docs/tca (anchors below; it needs
a Turnitin customer login, the schemas are those of the TCA OpenAPI document 1.0.226), the
public guides https://developers.turnitin.com/turnitin-core-api/faq,
https://developers.turnitin.com/turnitin-core-api/best-practice/eula-workflow and
https://developers.turnitin.com/turnitin-core-api/information-for-ithenticate-integrators.

Every request carries `Authorization: Bearer <TCA_API_KEY>`, `X-Turnitin-Integration-Name`
and `X-Turnitin-Integration-Version` (#required-headers; the FAQ asks for the software's
name and version). Each tenant has its own base URL, https://<tenant>.turnitin.com/api/v1.
The flow (the documented one, with polling where an integration would host a webhook):

  GET  /features-enabled                 require_eula, licensed search_repositories
  GET  /eula/latest, /eula/{v}/accept/{user}   only when the tenant requires the EULA
  POST /submissions                      201 {id, status: CREATED}; 451 = EULA not accepted
  PUT  /submissions/{id}/original        the file, binary/octet-stream; 202
  GET  /submissions/{id}                 until status COMPLETE (ERROR carries error_code)
  PUT  /submissions/{id}/similarity      202; 409 CANNOT_EXTRACT_TEXT / TOO_LITTLE_TEXT
  GET  /submissions/{id}/similarity      until status COMPLETE: overall_match_percentage,
                                         top_matches
  POST /submissions/{id}/viewer-url      {viewer_url}, a short-lived launch link

The EULA is never accepted here (no POST /eula/{v}/accept): when the tenant requires it and
the owner has not accepted it at Turnitin, the author reads it and writes the line
`Turnitin EULA accepted: <version>` in workflow/text_overlap.md, which goes into the
submission as its `eula` record; until then nothing is sent. The one submission is never
deleted or changed afterwards, and it is not added to the index (add_to_index false), so
the venue's own check later does not match the paper against this self-check.
"""

import getpass
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import providers as base

DOC = "https://developers.turnitin.com/docs/tca"
INTEGRATION = {"X-Turnitin-Integration-Name": "OpenProgram auto_research",
               "X-Turnitin-Integration-Version": "0.1"}
REPOSITORIES = ["INTERNET", "PUBLICATION", "CROSSREF", "CROSSREF_POSTED_CONTENT"]
WAIT = 600              # seconds for upload processing and report generation together
FIRST_POLL, MAX_POLL = 5.0, 60.0
MAX_FILE = 100 * 1024 * 1024        # the FAQ: 100 MB a file, 800 pages, 2 MB of text
LANGUAGE = "en-US"
PERMISSIONS = "USER"    # owner, submitter and viewer: one author checking their own paper
EULA_LINE = re.compile(r"^[ \t]*[-*]?[ \t]*turnitin eula accepted[ \t]*:[ \t]*(\S+)", re.I | re.M)
DONE = ("COMPLETE", "COMPLETED")


def clock() -> float:
    return time.monotonic()


class EulaRequired(base.ProviderError):
    """The tenant requires the Turnitin EULA and the author has not accepted it."""

    def __init__(self, message: str, url: str, version: str, extra: dict | None = None):
        super().__init__(message, 451, extra)
        self.url, self.version = url, version


def api_base(url: str) -> str:
    """TCA_BASE_URL as https://<tenant>.turnitin.com/api/v1, whether it was given with or
    without /api or /api/v1."""
    url = url.strip().rstrip("/")
    if not url.startswith("https://"):
        raise base.ProviderError(f"TCA_BASE_URL {url!r} is not an https:// URL")
    if url.endswith("/api/v1"):
        return url
    return url + ("/v1" if url.endswith("/api") else "/api/v1")


def default_owner() -> str:
    """A stable user id for this computer's user, so an EULA acceptance stays found."""
    return "auto-research-" + hashlib.sha256(getpass.getuser().encode()).hexdigest()[:16]


def stamp(seconds: float | None = None) -> str:
    moment = datetime.fromtimestamp(seconds, timezone.utc) if seconds else datetime.now(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class Turnitin(base.Provider):
    name = "turnitin"
    label = "Turnitin / iThenticate (TCA)"
    env = ("TCA_API_KEY", "TCA_BASE_URL", "TCA_OWNER")
    doc = DOC
    sends = ("发送编译好的 PDF（整篇论文，含参考文献，报告里排除参考文献和引号内文字；没有 PDF 时只发正文），"
             "只建一份提交，不加入 Turnitin 比对库")

    def key(self) -> str:
        return os.environ.get("TCA_API_KEY", "") if os.environ.get("TCA_BASE_URL") else ""

    def setup(self) -> str:
        return f"{self.label} (TCA_BASE_URL + TCA_API_KEY)"

    # one request ---------------------------------------------------------------------

    def request(self, method: str, path: str, label: str, body=None, data: bytes | None = None,
                headers: dict | None = None):
        url = self.root + path
        head = {"Authorization": f"Bearer {self.key()}", **INTEGRATION, **(headers or {})}
        if body is not None:
            data, head["Content-Type"] = json.dumps(body).encode("utf-8"), "application/json"
        try:
            return base.retry(lambda: base.http(method, url, data, head), f"{self.label} {label}")
        except base.ProviderError as e:
            e.extra = {**self.state, **e.extra}
            raise

    # the flow ------------------------------------------------------------------------

    def scan(self, text: str, title: str, context: dict | None = None) -> dict:
        context = context or {}
        if not self.key():
            raise base.ProviderError(f"{self.label}: TCA_BASE_URL and TCA_API_KEY are not both set")
        self.root = api_base(os.environ["TCA_BASE_URL"])
        self.state = {}
        owner = os.environ.get("TCA_OWNER") or default_owner()
        notes = []

        # https://developers.turnitin.com/docs/tca#get-features-enabled
        features = self.request("GET", "/features-enabled", "features-enabled")
        licensed = ((features.get("similarity") or {}).get("generation_settings") or {}).get("search_repositories")
        repositories = [r for r in REPOSITORIES if r in licensed] if licensed else list(REPOSITORIES)
        if licensed and not repositories:
            repositories = list(licensed)
        eula = self.eula(owner, (features.get("tenant") or {}).get("require_eula", True), context)

        file_name, data, kind = self.document(text, title, context)
        self.state["sent_file"] = file_name
        if kind == "text":
            notes.append("没有找到编译好的 PDF，只发送了正文（.txt）")

        # https://developers.turnitin.com/docs/tca#create-a-submission
        body = {"owner": owner, "submitter": owner, "title": title or file_name,
                "owner_default_permission_set": PERMISSIONS, "submitter_default_permission_set": PERMISSIONS,
                "metadata": {"owners": [{"id": owner}], "submitter": {"id": owner},
                             "original_submitted_time": stamp()}}
        if eula:
            body["eula"] = eula
        try:
            created = self.request("POST", "/submissions", "create submission", body=body)
        except base.ProviderError as e:
            if e.code == 451:
                raise EulaRequired(f"{self.label}: Turnitin refused the submission until the EULA is accepted "
                                   f"(HTTP 451)", self.eula_url, self.eula_version) from None
            raise
        sid = created.get("id") or ""
        if not sid:
            raise base.ProviderError(f"{self.label}: the new submission has no id")
        self.state["submission_id"] = sid

        # https://developers.turnitin.com/docs/tca#upload-submission-file-contents
        self.request("PUT", f"/submissions/{sid}/original", "upload", data=data,
                     headers={"Content-Type": "binary/octet-stream",
                              "Content-Disposition": f'inline; filename="{file_name}"'})

        deadline = clock() + WAIT
        # https://developers.turnitin.com/docs/tca#get-submission-info
        info = self.wait(f"/submissions/{sid}", "submission", deadline)

        # https://developers.turnitin.com/docs/tca#generate-similarity-report
        self.request("PUT", f"/submissions/{sid}/similarity", "generate report", body={
            "indexing_settings": {"add_to_index": False},
            "generation_settings": {"search_repositories": repositories, "auto_exclude_self_matching_scope": "ALL"},
            "view_settings": {"exclude_quotes": True, "exclude_bibliography": True}})

        # https://developers.turnitin.com/docs/tca#get-similarity-report-info
        report = self.wait(f"/submissions/{sid}/similarity", "similarity report", deadline)

        # https://developers.turnitin.com/docs/tca#create-viewer-launch-url
        viewer = ""
        try:
            viewer = self.request("POST", f"/submissions/{sid}/viewer-url", "viewer-url", body={
                "viewer_user_id": owner, "locale": LANGUAGE, "viewer_default_permission_set": PERMISSIONS,
                "similarity": {"view_settings": {"save_changes": False}}}).get("viewer_url") or ""
        except base.ProviderError as e:
            notes.append(f"没有拿到报告查看链接：{e}")

        total = int(info.get("word_count") or 0) or base.words(text)
        matches = [self.match(m, total) for m in report.get("top_matches") or []]
        matches.sort(key=lambda m: -m["percent"])
        return {"score": float(report.get("overall_match_percentage") or 0), "words": total, "chunks": 1,
                "matches": matches, "submission_id": sid, "viewer_url": viewer, "sent_file": file_name,
                "repositories": repositories, "notes": notes,
                "by_repository": {k: report.get(f"{k}_match_percentage") for k in
                                  ("internet", "publication", "submitted_works")
                                  if report.get(f"{k}_match_percentage") is not None}}

    def eula(self, owner: str, required: bool, context: dict) -> dict:
        """The eula record for the submission, {} when none is needed; raises EulaRequired.
        https://developers.turnitin.com/docs/tca#get-eula-version-info and
        #get-eula-acceptance-info; never #accept-eula-version."""
        self.eula_url, self.eula_version = "", ""
        if not required:
            return {}
        latest = self.request("GET", f"/eula/latest?lang={LANGUAGE}", "EULA")
        self.eula_version, self.eula_url = latest.get("version") or "", latest.get("url") or ""
        try:
            accepted = self.request("GET", f"/eula/{self.eula_version}/accept/{owner}", "EULA acceptance")
        except base.ProviderError as e:
            if e.code != 404:
                raise
            accepted = []
        if isinstance(accepted, list) and any(a.get("version") in (self.eula_version, None) for a in accepted
                                              if isinstance(a, dict)):
            return {}
        m = EULA_LINE.search(context.get("notes") or "")
        if m and m.group(1).strip().rstrip(".") == self.eula_version:
            return {"accepted_timestamp": stamp(context.get("notes_time")), "language": LANGUAGE,
                    "version": self.eula_version}
        raise EulaRequired(f"{self.label}: the tenant requires the Turnitin EULA {self.eula_version}, "
                           f"not yet accepted for {owner}", self.eula_url, self.eula_version)

    def document(self, text: str, title: str, context: dict) -> tuple[str, bytes, str]:
        """(file name, bytes, pdf or text): the compiled PDF of the main .tex, else the prose."""
        pdf = context.get("pdf")
        if pdf and Path(pdf).is_file() and 0 < Path(pdf).stat().st_size <= MAX_FILE:
            return self.filename(Path(pdf).name), Path(pdf).read_bytes(), "pdf"
        stem = Path(pdf).stem if pdf else re.sub(r"\W+", "_", title or "paper")[:60]
        return self.filename(stem + ".txt"), text.encode("utf-8"), "text"

    @staticmethod
    def filename(name: str) -> str:
        """ASCII only, so Content-Disposition needs no encoding."""
        return re.sub(r"[^A-Za-z0-9._-]+", "_", name) or "paper.txt"

    def wait(self, path: str, what: str, deadline: float) -> dict:
        """GET path until status is COMPLETE, with growing pauses, until the deadline."""
        pause = FIRST_POLL
        while True:
            answer = self.request("GET", path, what)
            status = str(answer.get("status") or "").upper()
            if status in DONE:
                return answer
            if status in ("ERROR", "FAILED") or answer.get("error_code"):
                raise base.ProviderError(f"{self.label}: the {what} failed: "
                                         f"{answer.get('error_code') or answer.get('message') or status}",
                                         extra=self.state)
            if clock() + pause > deadline:
                raise base.ProviderError(f"{self.label}: the {what} was not ready after {WAIT // 60} minutes "
                                         f"(status {status or 'unknown'}); the submission stays at Turnitin",
                                         extra=self.state)
            base.sleep(pause)
            pause = min(pause * 1.5, MAX_POLL)

    @staticmethod
    def match(m: dict, total: int) -> dict:
        """A top match: name, percentage (of the whole paper), source_type,
        matched_word_count_total. TCA gives no URL or excerpt; a name that is a web address
        is used as the url."""
        name = m.get("name") or ""
        url = name if re.match(r"^(https?://|www\.)\S+$", name) else ""
        words = int(m.get("matched_word_count_total") or 0)
        percent = m.get("percentage")
        percent = float(percent) if isinstance(percent, (int, float)) else (100 * words / total if total else 0.0)
        return {"url": url, "title": "" if url else name, "matched_words": words, "percent": round(percent, 1),
                "excerpt": "", "cited": False, "doi": "", "source_type": m.get("source_type") or "",
                "institution": m.get("institution_name") or ""}


__all__ = ["EulaRequired", "Turnitin"]
