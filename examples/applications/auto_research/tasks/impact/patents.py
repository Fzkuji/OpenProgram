"""Patent sources of tech_transfer, tried in order: the USPTO Open Data Portal (ODP)
patent application search, then the PatentsView PatentSearch API (suspended since
PatentsView moved to ODP on 20 March 2026; kept for when it returns). Plain code, no
model calls.

Every source returns records of one shape: number, kind (grant or application), title,
abstract, claims, date, assignees, cpc, url, source, phrases. ODP's search has no
abstract; for the patents kept, the per-application grant or pre-grant XML named by
the associated-documents endpoint gives the abstract and claims when it is a single
XML file (a weekly bulk zip is not downloaded).
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from auto_research.tasks.citations import sources

from ..scouting.common import cell
from .common import words

ODP = "https://api.uspto.gov/api/v1/patent/applications/search"
ODP_DOCS = "https://api.uspto.gov/api/v1/patent/applications/{}/associated-documents"
ODP_FIELDS = ["applicationNumberText", "applicationMetaData.inventionTitle", "applicationMetaData.patentNumber",
              "applicationMetaData.grantDate", "applicationMetaData.filingDate",
              "applicationMetaData.earliestPublicationNumber", "applicationMetaData.earliestPublicationDate",
              "applicationMetaData.cpcClassificationBag", "applicationMetaData.applicantBag",
              "applicationMetaData.firstApplicantName", "assignmentBag"]
PATENTSVIEW = "https://search.patentsview.org/api/v1/patent/"
PV_FIELDS = ["patent_id", "patent_title", "patent_abstract", "patent_date", "assignees.assignee_organization",
             "cpc_current.cpc_group_id"]
PER_PHRASE = 25             # records read per phrase
CLAIMS_CHARS = 3000         # claims text kept per patent
RETRY_WAIT = 3.0


def request(url: str, body: dict | None, headers: dict, as_json: bool = True) -> tuple[int, object]:
    """POST (with body) or GET, one retry on 429 and 5xx; (HTTP status, parsed body).
    Status 0 means no answer at all."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={**sources.UA, "Accept": "application/json" if as_json else "*/*",
                                          **({"Content-Type": "application/json"} if body is not None else {}),
                                          **headers})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=sources.TIMEOUT) as r:
                text = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(text) if as_json else text)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(RETRY_WAIT)
                continue
            return e.code, None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            if attempt == 0:
                time.sleep(RETRY_WAIT)
                continue
            return 0, None
    return 0, None


def search(phrases: list[str], notes: list[str]) -> tuple[list[dict], dict]:
    """The patents of the first configured source that answers, with what each source did."""
    tried = []
    for name, env, fn in (("USPTO ODP", "USPTO_API_KEY", odp_search),
                          ("PatentsView", "PATENTSVIEW_API_KEY", patentsview_search)):
        key = os.environ.get(env, "").strip()
        entry = {"name": name, "env": env, "configured": bool(key), "answered": False, "found": 0}
        tried.append(entry)
        if not key:
            continue
        found = fn(phrases, key, notes)
        if found is None:
            sources.UNAVAILABLE.add(name)
            continue
        entry.update(answered=True, found=len(found))
        return found, {"tried": tried, "used": name}
    if not any(t["configured"] for t in tried):
        notes.append("专利来源未配置（没有 USPTO_API_KEY，也没有 PATENTSVIEW_API_KEY），没有做专利检索；"
                     "OpenAlex 不收录专利，也没有专利引用论文的数据，所以没有替代来源")
    return [], {"tried": tried, "used": ""}


def refused(name: str, status: int, url: str) -> str:
    if status in (401, 403):
        return f"{name} 拒绝了 API key（HTTP {status}）"
    if status == 0:
        return f"{name} 没有响应（{url}）"
    return f"{name} 出错（HTTP {status}）"


# USPTO Open Data Portal

def odp_query(phrase: str) -> str:
    """OpenSearch query on the invention title: every content word of the phrase."""
    ws = [re.sub(r"[^\w-]", "", w) for w in words(phrase)]
    ws = [w for w in ws if w]
    return f"applicationMetaData.inventionTitle:({' AND '.join(ws)})" if ws else ""


def odp_search(phrases: list[str], key: str, notes: list[str]) -> list[dict] | None:
    """Applications and grants whose title has every word of a phrase, newest filing
    first, merged by number; None when ODP refuses or does not answer."""
    url = os.environ.get("USPTO_ODP_URL", "").strip() or ODP
    found: dict[str, dict] = {}
    for phrase in phrases:
        q = odp_query(phrase)
        if not q:
            continue
        body = {"q": q, "fields": ODP_FIELDS, "sort": [{"field": "applicationMetaData.filingDate", "order": "desc"}],
                "pagination": {"offset": 0, "limit": PER_PHRASE}}
        status, data = request(url, body, {"X-API-KEY": key})
        if status == 404:                       # ODP's answer for "no matching records"
            continue
        if status != 200 or not isinstance(data, dict):
            notes.append(refused("USPTO ODP", status, url) + "，改试下一个来源")
            return None
        for item in data.get("patentFileWrapperDataBag") or []:
            rec = odp_record(item)
            if rec["number"]:
                found.setdefault(rec["number"], dict(rec, phrases=[]))["phrases"].append(phrase)
    return list(found.values())


def publication_id(text: str) -> str:
    """"US 2014-0167116 A1" -> "US20140167116A1"."""
    return re.sub(r"[\s-]", "", text or "")


def odp_record(item: dict) -> dict:
    meta = item.get("applicationMetaData") or {}
    names = []
    for a in item.get("assignmentBag") or []:
        for e in (a or {}).get("assigneeBag") or []:
            n = " ".join(str((e or {}).get("assigneeNameText") or "").split())
            if n and n not in names:
                names.append(n)
    applicants = []
    for a in meta.get("applicantBag") or []:
        n = " ".join(str((a or {}).get("applicantNameText") or "").split())
        if n and n not in applicants:
            applicants.append(n)
    if not applicants and meta.get("firstApplicantName"):
        applicants = [" ".join(str(meta["firstApplicantName"]).split())]
    granted = str(meta.get("patentNumber") or "").strip()
    pub = publication_id(str(meta.get("earliestPublicationNumber") or ""))
    application = str(item.get("applicationNumberText") or "").strip()
    if granted:
        number, kind, date, url = granted, "grant", meta.get("grantDate") or "", f"https://patents.google.com/patent/US{granted}"
    elif pub:
        number, kind, date, url = pub, "application", meta.get("earliestPublicationDate") or meta.get("filingDate") or "", \
            f"https://patents.google.com/patent/{pub}"
    else:
        number, kind, date, url = application, "application", meta.get("filingDate") or "", ""
    return {"number": number, "kind": kind, "application": application,
            "title": " ".join(str(meta.get("inventionTitle") or "").split()), "abstract": "", "claims": "",
            "date": str(date), "assignees": names or applicants, "assignee_role": "assignee" if names else "applicant",
            "cpc": [c.strip() for c in meta.get("cpcClassificationBag") or [] if str(c).strip()],
            "url": url, "source": "USPTO ODP"}


def odp_texts(patents: list[dict], key: str) -> int:
    """Abstract and claims of each ODP record from its own grant or pre-grant XML; how many got text."""
    got = 0
    for p in patents:
        if p.get("source") != "USPTO ODP" or not p.get("application"):
            continue
        status, data = request(ODP_DOCS.format(p["application"]), None, {"X-API-KEY": key})
        if status != 200 or not isinstance(data, dict):
            continue
        bag = (data.get("patentFileWrapperDataBag") or [{}])[0] or {}
        for meta_key in ("grantDocumentMetaData", "pgpubDocumentMetaData"):
            uri = str((bag.get(meta_key) or {}).get("fileLocationURI") or "")
            if not uri.lower().split("?")[0].endswith(".xml"):
                continue                       # a weekly bulk zip: not downloaded
            status, xml = request(uri, None, {"X-API-KEY": key}, as_json=False)
            if status == 200 and xml:
                abstract, claims = xml_texts(xml)
                if abstract or claims:
                    p["abstract"], p["claims"] = abstract, claims
                    got += 1
                    break
    return got


def xml_texts(xml: str) -> tuple[str, str]:
    """The abstract and the claims of a USPTO grant or application XML document."""
    try:
        root = ET.fromstring(re.sub(r"<!DOCTYPE[^>]*>", "", xml, count=1).encode("utf-8"))
    except ET.ParseError:
        return "", ""
    text = lambda el: " ".join(" ".join(el.itertext()).split()) if el is not None else ""
    return text(root.find(".//abstract")), text(root.find(".//claims"))[:CLAIMS_CHARS]


# PatentsView PatentSearch

def patentsview_search(phrases: list[str], key: str, notes: list[str]) -> list[dict] | None:
    """Patents whose abstract or title has every word of a phrase, newest first; None
    when PatentsView refuses or does not answer."""
    url = os.environ.get("PATENTSVIEW_URL", "").strip() or PATENTSVIEW
    found: dict[str, dict] = {}
    for phrase in phrases:
        body = {"q": {"_or": [{"patent_abstract": {"_text_all": phrase}}, {"patent_title": {"_text_all": phrase}}]},
                "f": PV_FIELDS, "s": [{"patent_date": "desc"}], "o": {"size": PER_PHRASE}}
        status, data = request(url, body, {"X-Api-Key": key})
        if status != 200 or not isinstance(data, dict):
            note = refused("PatentsView", status, url)
            if status == 0:
                note += "；USPTO 在 2026 年 3 月把 PatentsView 迁到 Open Data Portal，PatentSearch API 暂停，恢复后用 PATENTSVIEW_URL 指向新地址"
            notes.append(note)
            return None
        if data.get("error"):
            notes.append(f"PatentsView 报错（检索 “{phrase}”）：{cell(data.get('message') or data.get('error'), 120)}")
            continue
        for p in data.get("patents") or []:
            rec = patentsview_record(p)
            if rec["number"]:
                found.setdefault(rec["number"], dict(rec, phrases=[]))["phrases"].append(phrase)
    return list(found.values())


def patentsview_record(p: dict) -> dict:
    orgs, cpc = [], []
    for a in p.get("assignees") or []:
        name = " ".join(str((a or {}).get("assignee_organization") or "").split())
        if name and name not in orgs:
            orgs.append(name)
    for c in p.get("cpc_current") or []:
        g = str((c or {}).get("cpc_group_id") or "").strip()
        if g and g not in cpc:
            cpc.append(g)
    number = str(p.get("patent_id") or "").strip()
    return {"number": number, "kind": "grant", "application": "",
            "title": " ".join(str(p.get("patent_title") or "").split()),
            "abstract": " ".join(str(p.get("patent_abstract") or "").split()), "claims": "",
            "date": str(p.get("patent_date") or ""), "assignees": orgs, "assignee_role": "assignee", "cpc": cpc,
            "url": f"https://patents.google.com/patent/US{number}" if number else "", "source": "PatentsView"}


__all__ = ["odp_texts", "search"]
