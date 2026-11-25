"""Turnitin / iThenticate through the Turnitin Core API, with HTTP replaced by a fake tenant
that answers in the documented TCA shapes (OpenAPI 1.0.226): features-enabled, EULA,
submissions, upload, similarity, viewer-url."""

import json

import pytest

from auto_research.tasks.overlap import providers, turnitin

from .test_commercial import http_error, opt_in
from .test_workflow import make, no_service_keys, offline, run  # noqa: F401  fixtures

ROOT = "https://uni.turnitin.com/api/v1"
SID = "0ec9141f-3390-460e-8d2f-a4080080e749"
PDF = b"%PDF-1.4\n% a compiled paper\n"
EULA_URL = "https://uni.turnitin.com/api/v1/eula/v1beta/view"


def features(require_eula=False, repositories=("INTERNET", "PUBLICATION", "SUBMITTED_WORK")):
    return {"similarity": {"viewer_modes": {"match_overview": True, "all_sources": False},
                           "generation_settings": {"search_repositories": list(repositories),
                                                   "submission_auto_excludes": True},
                           "view_settings": {"exclude_bibliography": True, "exclude_quotes": True}},
            "tenant": {"require_eula": require_eula}, "product_name": "iThenticate", "access_options": ["CORE_API"]}


def similarity(status="COMPLETE", score=9, matches=()):
    return {"submission_id": SID, "overall_match_percentage": score, "internet_match_percentage": 4,
            "publication_match_percentage": score, "submitted_works_match_percentage": None, "status": status,
            "time_requested": "2026-11-24T10:00:00Z", "time_generated": "2026-11-24T10:01:00Z",
            "top_source_largest_matched_word_count": 40, "top_matches": [
                {"name": n, "percentage": p, "submission_id": "x", "source_type": t, "matched_word_count_total": w,
                 "submitted_date": "2024-01-01", "institution_name": ""} for n, p, t, w in matches]}


class Tenant:
    """A fake TCA tenant: routes[(method, path)] is an answer, a list of answers in turn, an
    exception, or a function of the request."""

    def __init__(self):
        self.calls = []
        self.now = 0.0
        self.routes = {
            ("GET", "/features-enabled"): features(),
            ("POST", "/submissions"): {"owner": "me", "title": "t", "status": "CREATED", "id": SID,
                                       "created_time": "2026-11-24T10:00:00Z"},
            ("PUT", f"/submissions/{SID}/original"): {"message": f"Successfully uploaded file for submission {SID}"},
            ("GET", f"/submissions/{SID}"): [{"id": SID, "status": "PROCESSING"},
                                             {"id": SID, "status": "COMPLETE", "word_count": 400,
                                              "page_count": 2, "content_type": "application/pdf"}],
            ("PUT", f"/submissions/{SID}/similarity"): {"message": "Successfully scheduled similarity report "
                                                                   "generation"},
            ("GET", f"/submissions/{SID}/similarity"): [similarity("PENDING"), similarity(matches=[
                ("A memory blog", 7, "INTERNET", 28),
                ("Hierarchical Episodic Memory for Agents", 12, "PUBLICATION", 48)])],
            ("POST", f"/submissions/{SID}/viewer-url"): {"viewer_url": "https://viewer.turnitin.com/launch/abc"},
        }

    def http(self, method, url, data, headers, timeout=providers.TIMEOUT):
        assert url.startswith(ROOT), url
        path = url[len(ROOT):]
        body = json.loads(data) if data and headers.get("Content-Type") == "application/json" else data
        self.calls.append({"method": method, "path": path, "body": body, "headers": headers})
        answer = self.routes.get((method, path.split("?")[0]))
        if answer is None:
            raise AssertionError(f"unexpected {method} {path}")
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if callable(answer):
            answer = answer(body)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def sleep(self, seconds):
        self.now += seconds

    def paths(self, method=None):
        return [c["path"] for c in self.calls if method in (None, c["method"])]


@pytest.fixture
def tenant(monkeypatch):
    t = Tenant()
    monkeypatch.setattr(providers, "http", t.http)
    monkeypatch.setattr(providers, "sleep", t.sleep)
    monkeypatch.setattr(turnitin, "clock", lambda: t.now)
    monkeypatch.setenv("TCA_BASE_URL", "https://uni.turnitin.com")
    monkeypatch.setenv("TCA_API_KEY", "tca-key")
    monkeypatch.setenv("TCA_OWNER", "author-7")
    return t


def paper(tmp_path, pdf=True):
    repo = make(tmp_path)
    opt_in(repo)
    if pdf:
        (repo / "0main.pdf").write_bytes(PDF)
    return repo


def test_full_flow_sends_the_pdf_and_fails_on_an_uncited_source(tmp_path, offline, tenant):
    out, data = run(paper(tmp_path))
    assert [(c["method"], c["path"]) for c in tenant.calls] == [
        ("GET", "/features-enabled"), ("POST", "/submissions"), ("PUT", f"/submissions/{SID}/original"),
        ("GET", f"/submissions/{SID}"), ("GET", f"/submissions/{SID}"), ("PUT", f"/submissions/{SID}/similarity"),
        ("GET", f"/submissions/{SID}/similarity"), ("GET", f"/submissions/{SID}/similarity"),
        ("POST", f"/submissions/{SID}/viewer-url")]
    for c in tenant.calls:
        assert c["headers"]["Authorization"] == "Bearer tca-key"
        assert c["headers"]["X-Turnitin-Integration-Name"] and c["headers"]["X-Turnitin-Integration-Version"]
    create = tenant.calls[1]["body"]
    assert create["owner"] == create["submitter"] == "author-7" and "eula" not in create
    assert create["title"] == "Adaptive Memory for Long Horizon Agents"
    assert create["owner_default_permission_set"] == create["submitter_default_permission_set"] == "USER"
    upload = tenant.calls[2]
    assert upload["body"] == PDF and upload["headers"]["Content-Type"] == "binary/octet-stream"
    assert upload["headers"]["Content-Disposition"] == 'inline; filename="0main.pdf"'
    report_request = tenant.calls[5]["body"]
    assert report_request["indexing_settings"] == {"add_to_index": False}       # institution index untouched
    # wanted repositories the licence allows; CROSSREF is not licensed on this tenant
    assert report_request["generation_settings"]["search_repositories"] == ["INTERNET", "PUBLICATION"]
    assert report_request["view_settings"] == {"exclude_quotes": True, "exclude_bibliography": True}
    assert not any(m == "DELETE" or "/eula" in p for m, p in ((c["method"], c["path"]) for c in tenant.calls))

    c = data["commercial"]
    assert c["status"] == "done" and c["provider"] == "turnitin" and c["score"] == 9.0 and c["words"] == 400
    assert c["submission_id"] == SID and c["viewer_url"].startswith("https://viewer.turnitin.com")
    assert c["sent_file"] == "0main.pdf" and c["by_repository"] == {"internet": 4, "publication": 9}
    cited, blog = c["matches"]                                   # largest first
    assert cited["title"] == "Hierarchical Episodic Memory for Agents" and cited["cited"]   # in the bibliography
    assert blog["percent"] == 7.0 and blog["matched_words"] == 28 and not blog["cited"]
    assert out["status"] == "FAIL" and c["problems"] == [
        "Turnitin / iThenticate (TCA): 7.0% of the paper matches A memory blog, which the paper does not cite"]
    assert out["commercial"]["submission_id"] == SID
    report = open(out["report"]).read()
    assert SID in report and "https://viewer.turnitin.com/launch/abc" in report and "INTERNET、PUBLICATION" in report


def test_without_pdf_the_prose_goes_as_text(tmp_path, offline, tenant):
    out, data = run(paper(tmp_path, pdf=False))
    upload = tenant.calls[2]
    assert upload["headers"]["Content-Disposition"] == 'inline; filename="0main.txt"'
    sent = upload["body"].decode()
    assert "Hierarchical summarization compresses" in sent and "Not On Arxiv" not in sent
    assert data["commercial"]["sent_file"] == "0main.txt"
    assert "只发送了正文" in open(out["report"]).read()


def test_eula_required_is_never_accepted_for_the_author(tmp_path, offline, tenant):
    tenant.routes[("GET", "/features-enabled")] = features(require_eula=True)
    tenant.routes[("GET", "/eula/latest")] = {"version": "v1beta", "valid_from": "2018-05-01T00:00:00Z",
                                              "valid_until": None, "url": EULA_URL,
                                              "available_languages": ["en-US", "de-DE"]}
    tenant.routes[("GET", "/eula/v1beta/accept/author-7")] = http_error(404, {"status": "404",
                                                                             "message": "Not Found"})
    repo = paper(tmp_path)
    out, data = run(repo)
    c = data["commercial"]
    assert c["status"] == "eula_required" and c["eula_url"] == EULA_URL and c["eula_version"] == "v1beta"
    assert tenant.paths("POST") == [] and tenant.paths("PUT") == []          # nothing sent, nothing accepted
    assert out["status"] == "PASS_WITH_NOTES"
    report = open(out["report"]).read()
    assert EULA_URL in report and "Turnitin EULA accepted: v1beta" in report and "不会代为同意" in report

    # the author read it and wrote the line: the acceptance goes with the submission
    (repo / "workflow" / "text_overlap.md").write_text("Send to plagiarism service: yes\n"
                                                       "Turnitin EULA accepted: v1beta\n")
    tenant.calls.clear()
    _, data = run(repo)
    assert data["commercial"]["status"] == "done"
    eula = next(c for c in tenant.calls if c["path"] == "/submissions")["body"]["eula"]
    assert eula["version"] == "v1beta" and eula["language"] == "en-US" and eula["accepted_timestamp"].endswith("Z")
    assert not any(p.endswith("/accept") for p in tenant.paths("POST"))

    # a line naming an older version does not count
    (repo / "workflow" / "text_overlap.md").write_text("Send to plagiarism service: yes\n"
                                                       "Turnitin EULA accepted: v0\n")
    _, data = run(repo)
    assert data["commercial"]["status"] == "eula_required"


def test_eula_accepted_at_turnitin_needs_no_line(tmp_path, offline, tenant):
    tenant.routes[("GET", "/features-enabled")] = features(require_eula=True)
    tenant.routes[("GET", "/eula/latest")] = {"version": "v2", "url": EULA_URL}
    tenant.routes[("GET", "/eula/v2/accept/author-7")] = [[{"user_id": "author-7", "version": "v2",
                                                            "accepted_timestamp": "2026-01-01T00:00:00Z",
                                                            "language": "en-US"}]]
    _, data = run(paper(tmp_path))
    assert data["commercial"]["status"] == "done"
    assert "eula" not in next(c for c in tenant.calls if c["path"] == "/submissions")["body"]


def test_451_on_create_is_eula_required(tmp_path, offline, tenant):
    tenant.routes[("POST", "/submissions")] = http_error(451, {"status": "451", "message": "EULA not accepted"})
    out, data = run(paper(tmp_path))
    assert data["commercial"]["status"] == "eula_required" and out["status"] == "PASS_WITH_NOTES"
    assert len(tenant.paths("POST")) == 1                                     # 451 is not retried


def test_report_not_ready_stops_at_the_timeout(tmp_path, offline, tenant):
    tenant.routes[("GET", f"/submissions/{SID}/similarity")] = similarity("PENDING")
    out, data = run(paper(tmp_path))
    c = data["commercial"]
    assert c["status"] == "error" and "not ready after 10 minutes" in c["note"]
    assert c["submission_id"] == SID and SID in open(out["report"]).read()
    assert tenant.now <= turnitin.WAIT
    polls = tenant.paths("GET").count(f"/submissions/{SID}/similarity")
    assert 5 < polls < 30                                                    # backoff, not a busy loop
    assert out["status"] == "PASS_WITH_NOTES" and "viewer-url" not in " ".join(tenant.paths())


@pytest.mark.parametrize("route, answer, expect, sid", [
    (("GET", "/features-enabled"), http_error(401, {"status": "401", "message": "Unauthorized"}),
     "HTTP 401 Unauthorized", ""),
    (("POST", "/submissions"), http_error(400, {"status": "400", "message": "Bad Request"}), "HTTP 400", ""),
    (("PUT", f"/submissions/{SID}/original"), http_error(413, {"status": "413", "message": "too large"}),
     "HTTP 413", SID),
    (("GET", f"/submissions/{SID}"), {"id": SID, "status": "ERROR", "error_code": "TOO_LITTLE_TEXT"},
     "TOO_LITTLE_TEXT", SID),
    (("PUT", f"/submissions/{SID}/similarity"),
     http_error(409, {"status": "409", "message": "CANNOT_EXTRACT_TEXT"}), "CANNOT_EXTRACT_TEXT", SID),
])
def test_refusals_are_named_with_the_submission_id(tmp_path, offline, tenant, route, answer, expect, sid):
    tenant.routes[route] = answer
    out, data = run(paper(tmp_path))
    c = data["commercial"]
    assert c["status"] == "error" and expect in c["note"] and c["submission_id"] == sid
    assert sum(1 for x in tenant.calls if (x["method"], x["path"]) == route) == 1   # 4xx is not retried
    assert out["status"] == "PASS_WITH_NOTES"


def test_server_errors_are_retried_and_a_missing_viewer_is_only_a_note(tmp_path, offline, tenant):
    tenant.routes[("GET", "/features-enabled")] = [http_error(503, {"message": "busy"}), features()]
    tenant.routes[("POST", f"/submissions/{SID}/viewer-url")] = http_error(403, {"message": "Forbidden"})
    _, data = run(paper(tmp_path))
    c = data["commercial"]
    assert tenant.paths().count("/features-enabled") == 2
    assert c["status"] == "done" and c["viewer_url"] == "" and any("查看链接" in n for n in c["notes"])

    tenant.calls.clear()
    tenant.routes[("GET", "/features-enabled")] = http_error(502, {"message": "bad gateway"})
    _, data = run(paper(tmp_path / "again"))
    assert tenant.paths().count("/features-enabled") == providers.TRIES
    assert data["commercial"]["status"] == "error" and "HTTP 502" in data["commercial"]["note"]


def test_configuration(monkeypatch):
    monkeypatch.setenv("TCA_API_KEY", "k")
    provider, missing = providers.select("turnitin")
    assert provider is None and "Turnitin / iThenticate (TCA) (TCA_BASE_URL + TCA_API_KEY)" in missing
    monkeypatch.setenv("TCA_BASE_URL", "https://uni.turnitin.com/api/")
    provider, missing = providers.select("")
    assert provider.name == "turnitin" and len(missing) == 2
    for given in ("https://uni.turnitin.com", "https://uni.turnitin.com/api", "https://uni.turnitin.com/api/v1/"):
        assert turnitin.api_base(given) == ROOT
    with pytest.raises(providers.ProviderError, match="https"):
        turnitin.api_base("http://uni.turnitin.com")
    monkeypatch.delenv("TCA_OWNER", raising=False)
    assert turnitin.default_owner() == turnitin.default_owner() and turnitin.default_owner().startswith("auto-")
