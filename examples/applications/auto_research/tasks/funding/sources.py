"""Free, keyless funding sources, read into one record shape. Plain code, no model calls.

Open calls: Grants.gov (search2 and fetchOpportunity, JSON POST) and the EU Funding &
Tenders search API (SEDIA key, multipart POST) with its topic details (JSON GET).
Funded projects, as evidence of fit: the NSF Awards API (GET) and NIH RePORTER (JSON POST).
JSON GETs go through citations.sources.fetch, JSON POSTs through scouting.common.post,
and only two requests are written here, with the same retries and UNAVAILABLE record:
the multipart POST the EU search needs, and a GET (JSON or bytes) whose TLS context adds
certifi's roots to the system's, because the EU hosts chain to a root (GlobalSign R46)
that macOS's /etc/ssl/cert.pem lacks. Tests replace the functions of this module.

A call record: {source, id, number, title, funder, agency, status (open, forecast),
deadline (YYYY-MM-DD or ""), deadlines (every date given), url, text, budget}.
A project record: {source, id, title, program, opportunity, year, amount, url, text}.
"""

import html
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

from auto_research.tasks.citations import sources
from auto_research.tasks.scouting import common

GRANTS_SEARCH = "https://api.grants.gov/v1/api/search2"
GRANTS_DETAIL = "https://api.grants.gov/v1/api/fetchOpportunity"
GRANTS_PAGE = "https://www.grants.gov/search-results-detail/{id}"
EU_SEARCH = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
EU_TOPIC = "https://ec.europa.eu/info/funding-tenders/opportunities/data/topicDetails/{id}.json"
EU_PAGE = "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/topic-details/{id}"
NSF_AWARDS = "https://api.nsf.gov/services/v1/awards.json"
NSF_PAGE = "https://www.nsf.gov/awardsearch/showAward?AWD_ID={id}"
NIH_SEARCH = "https://api.reporter.nih.gov/v2/projects/search"
EU_OPEN, EU_FORTHCOMING, EU_CLOSED = "31094502", "31094501", "31094503"
EU_TYPES = ["1", "2"]          # 1 call topic, 2 topic of a call for proposals; 0 tenders, 8 cascade funding
LABEL = {"grants": "Grants.gov", "eu": "EU Funding & Tenders", "nsf": "NSF Awards", "nih": "NIH RePORTER"}
TEXT_CHARS = 6000              # description kept per call


def plain_html(text: str) -> str:
    """HTML as reading text: scripts and styles go, block ends become line breaks."""
    text = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", str(text or ""))
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6]|table|ul|ol|section)>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def day(value) -> str:
    """A date as YYYY-MM-DD from the forms these APIs use; "" when none."""
    s = str(value or "").strip()
    if not s:
        return ""
    if s.isdigit() and len(s) >= 12:                                   # epoch milliseconds
        return datetime.fromtimestamp(int(s) / 1000, timezone.utc).strftime("%Y-%m-%d")
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return "-".join(m.groups())
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    for fmt in ("%b %d, %Y",):
        try:
            return datetime.strptime(s[:12].strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


# Grants.gov

def grants_search(keyword: str = "", number: str = "", rows: int = 25) -> list[dict]:
    """Posted and forecasted opportunities for a keyword or an opportunity number."""
    body = {"oppStatuses": "forecasted|posted", "rows": rows}
    if keyword:
        body["keyword"] = keyword
    if number:
        body["oppNum"] = number
    d = common.post(GRANTS_SEARCH, {}, body, label=LABEL["grants"])
    hits = ((d or {}).get("data") or {}).get("oppHits") or []
    return [grants_hit(h) for h in hits if isinstance(h, dict) and h.get("id")]


def grants_hit(h: dict) -> dict:
    return {"source": "grants", "id": f"grants:{h['id']}", "number": str(h.get("number") or ""),
            "title": html.unescape(str(h.get("title") or "")).strip(), "funder": str(h.get("agency") or ""),
            "agency": str(h.get("agencyCode") or ""),
            "status": "forecast" if h.get("oppStatus") == "forecasted" else "open",
            "deadline": day(h.get("closeDate")), "deadlines": [d for d in [day(h.get("closeDate"))] if d],
            "url": GRANTS_PAGE.format(id=h["id"]), "text": "", "budget": ""}


def grants_detail(opp_id: str) -> dict | None:
    """The full opportunity: description, response date, award ceiling, eligibility, links."""
    raw = str(opp_id).split(":")[-1]
    d = common.post(GRANTS_DETAIL, {}, {"opportunityId": int(raw) if raw.isdigit() else raw},
                    label=LABEL["grants"])
    data = (d or {}).get("data")
    if not isinstance(data, dict) or not data.get("id"):
        return None
    s = data.get("synopsis") or data.get("forecast") or {}
    desc = s.get("synopsisDesc") or s.get("forecastDesc") or ""
    date = day(s.get("responseDateStr") or s.get("responseDate") or s.get("estApplicationResponseDateStr")
               or s.get("estApplicationResponseDate"))
    eligible = "; ".join(str(a.get("description", "")) for a in s.get("applicantTypes") or [] if isinstance(a, dict))
    ceiling = str(s.get("awardCeiling") or "").strip()
    return {"text": plain_html(desc)[:TEXT_CHARS], "deadline": date,
            "budget": "" if ceiling in ("", "0", "none", "None") else ceiling,
            "eligibility": eligible, "link": str(s.get("fundingDescLinkUrl") or ""),
            "title": html.unescape(str(data.get("opportunityTitle") or "")),
            "number": str(data.get("opportunityNumber") or ""),
            "funder": str((data.get("agencyDetails") or {}).get("agencyName")
                      or (data.get("topAgencyDetails") or {}).get("agencyName") or ""),
            "additional": plain_html(s.get("applicantEligibilityDesc") or ""),
            "posted": day(s.get("postingDateStr") or s.get("postingDate")),
            "archive": day(s.get("archiveDateStr") or s.get("archiveDate"))}


# EU Funding & Tenders

MAX_BYTES = 30_000_000


def tls() -> ssl.SSLContext:
    """The system's roots plus certifi's when it is installed."""
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(certifi.where())
    except (ImportError, OSError):
        pass
    return context


def request(req: urllib.request.Request, label: str = "", as_json: bool = True):
    """Send a request with retries on 429, 5xx and timeouts; None (and the label in UNAVAILABLE) on failure."""
    for attempt in range(common.RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=sources.TIMEOUT * 2, context=tls()) as r:
                data = r.read(MAX_BYTES)
            return json.loads(data.decode("utf-8", "replace")) if as_json else data
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == common.RETRIES:
                break
        except (urllib.error.URLError, TimeoutError):
            if attempt == common.RETRIES:
                break
        except Exception:  # noqa: BLE001  any other failure: the source is unavailable
            break
        time.sleep(common.RETRY_WAIT * (attempt + 1))
    if label:
        sources.UNAVAILABLE.add(label)
    return None


def post_form(url: str, params: dict, parts: dict[str, str], label: str = ""):
    """POST multipart/form-data (each part JSON) and read JSON back."""
    boundary = uuid.uuid4().hex
    body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n'
                    f"Content-Type: application/json\r\n\r\n{value}\r\n".encode() for name, value in parts.items())
    body += f"--{boundary}--\r\n".encode()
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    return request(urllib.request.Request(full, data=body, method="POST", headers={
        **sources.UA, "Content-Type": f"multipart/form-data; boundary={boundary}"}), label)


def get(url: str, label: str = "", as_json: bool = True):
    return request(urllib.request.Request(url, headers=sources.UA), label, as_json)


def first(m: dict, key: str) -> str:
    v = m.get(key)
    if isinstance(v, list):
        v = v[0] if v else ""
    return str(v or "")


def eu_search(text: str, size: int = 50) -> list[dict]:
    """Open and forthcoming grant topics of the EU portal for a text query, latest deadline
    first (the portal keeps long-past topics marked forthcoming; relevance order puts them on top)."""
    query = {"bool": {"must": [{"terms": {"type": EU_TYPES}},
                               {"terms": {"status": [EU_OPEN, EU_FORTHCOMING]}}]}}
    d = post_form(EU_SEARCH, {"apiKey": "SEDIA", "text": text, "pageSize": size, "pageNumber": 1},
                  {"query": json.dumps(query), "languages": json.dumps(["en"]),
                   "sort": json.dumps({"field": "deadlineDate", "order": "DESC"})}, label=LABEL["eu"])
    out = []
    for r in (d or {}).get("results") or []:
        m = r.get("metadata") or {}
        ident = first(m, "identifier")
        if not ident:
            continue
        dates = sorted({day(x) for x in m.get("deadlineDate") or [] if day(x)})
        status = first(m, "status")
        out.append({"source": "eu", "id": f"eu:{ident}", "number": ident,
                    "title": html.unescape(first(m, "title") or str(r.get("summary") or "")).strip(),
                    "funder": "European Commission (" + (first(m, "callIdentifier").split("-")[0] or "EU") + ")",
                    "agency": first(m, "callIdentifier"),
                    "status": "closed" if status == EU_CLOSED else "forecast" if status == EU_FORTHCOMING else "open",
                    "deadline": "", "deadlines": dates, "url": EU_PAGE.format(id=ident),
                    "text": plain_html(first(m, "description") or first(m, "descriptionByte"))[:TEXT_CHARS],
                    "budget": ""})
    return out


def eu_topic(identifier: str) -> dict | None:
    """The topic's details: description, conditions (page limits, eligibility), deadlines."""
    d = get(EU_TOPIC.format(id=identifier.lower()), label=LABEL["eu"])
    t = (d or {}).get("TopicDetails") if isinstance(d, dict) else None
    if not isinstance(t, dict):
        return None
    dates = set()
    for actions in ((t.get("budgetOverviewJSONItem") or {}).get("budgetTopicActionMap") or {}).values():
        for a in actions or []:
            dates |= {day(x) for x in a.get("deadlineDates") or [] if day(x)}
    return {"title": str(t.get("title") or ""), "identifier": str(t.get("identifier") or identifier),
            "call": str(t.get("callTitle") or ""), "deadlines": sorted(dates),
            "description": plain_html(t.get("description") or ""), "conditions": plain_html(t.get("conditions") or ""),
            "support": plain_html(t.get("supportInfo") or "")}


# Funded projects

def nsf_awards(keyword: str, rows: int = 25) -> list[dict]:
    fields = "id,title,fundProgramName,startDate,estimatedTotalAmt,abstractText,agency"
    d = sources.fetch(NSF_AWARDS, {"keyword": keyword, "printFields": fields, "rpp": rows}, label=LABEL["nsf"])
    awards = (((d or {}).get("response") or {}).get("award") or []) if isinstance(d, dict) else []
    return [{"source": "nsf", "id": f"nsf:{a['id']}", "title": str(a.get("title") or ""),
             "program": str(a.get("fundProgramName") or ""), "opportunity": "",
             "year": day(a.get("startDate"))[:4], "amount": str(a.get("estimatedTotalAmt") or ""),
             "url": NSF_PAGE.format(id=a["id"]), "text": str(a.get("abstractText") or "")[:3000]}
            for a in awards if isinstance(a, dict) and a.get("id")]


def nih_projects(text: str, years: list[int], rows: int = 25) -> list[dict]:
    body = {"criteria": {"advanced_text_search": {"operator": "and", "search_field": "projecttitle,terms,abstracttext",
                                                  "search_text": text}, "fiscal_years": years},
            "include_fields": ["ProjectTitle", "ProjectNum", "FiscalYear", "AbstractText", "AgencyIcAdmin",
                               "AwardAmount", "OpportunityNumber", "ApplId"], "limit": rows}
    d = common.post(NIH_SEARCH, {}, body, label=LABEL["nih"])
    out = []
    for p in (d or {}).get("results") or []:
        if not isinstance(p, dict) or not p.get("project_num"):
            continue
        out.append({"source": "nih", "id": f"nih:{p['project_num']}", "title": str(p.get("project_title") or ""),
                    "program": str((p.get("agency_ic_admin") or {}).get("abbreviation") or ""),
                    "opportunity": str(p.get("opportunity_number") or ""), "year": str(p.get("fiscal_year") or ""),
                    "amount": str(p.get("award_amount") or ""),
                    "url": f"https://reporter.nih.gov/project-details/{p.get('appl_id') or p['project_num']}",
                    "text": str(p.get("abstract_text") or "")[:3000]})
    return out


def fetch_page(url: str) -> bytes | None:
    """A call's page or PDF as bytes."""
    return get(url, label=urllib.parse.urlparse(url).netloc or url, as_json=False)


__all__ = ["day", "eu_search", "eu_topic", "fetch_page", "grants_detail", "grants_search", "nih_projects",
           "nsf_awards", "plain_html"]
