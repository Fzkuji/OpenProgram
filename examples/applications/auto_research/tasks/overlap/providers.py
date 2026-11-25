"""Commercial plagiarism databases, asked only when the author set a key. Plain code, no
model calls.

Learned from iThenticate, Turnitin, Copyleaks and Originality.ai: those compare a text
against web pages and publisher content this package cannot reach. Two services are
supported because an individual can get a key and read the result in the same HTTP
response, with no webhook to host:

  winston      Winston AI, POST https://api.gowinston.ai/v2/plagiarism, Bearer key from
               https://dev.gowinston.ai (free credits on sign-up). Key: WINSTONAI_API_KEY
               (or WINSTON_API_KEY).
  originality  Originality.ai, POST https://api.originality.ai/api/v3/scan with
               check_plagiarism, header X-OAI-API-KEY (Enterprise plan). Key:
               ORIGINALITY_API_KEY.

  turnitin     iThenticate 2.0 / Turnitin Similarity through the Turnitin Core API (TCA),
               for authors whose institution issues TCA credentials: TCA_BASE_URL (the
               tenant, https://<tenant>.turnitin.com/api/v1), TCA_API_KEY, optional TCA_OWNER.
               Submission, upload and report generation are asynchronous; turnitin.py polls.

Copyleaks delivers results only through webhooks, so it is not here.

Every provider has scan(text, title) -> {score, words, chunks, matches: [{url, title,
matched_words, percent, excerpt, cited, doi}]}: score and percent are 0..100 over the words
sent; the text goes in chunks within the service's size limit, one request at a time.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

TIMEOUT = 120          # a plagiarism scan takes up to 60 s on the service's side
TRIES = 3              # 429, 5xx and network errors are retried
UA = {"User-Agent": "openprogram-text-overlap/0.1 (pre-submission self-check)"}
EXCERPT = 300


class ProviderError(Exception):
    """A request the service refused or did not answer; the message is for the report.
    code is the HTTP status of a refusal (0 otherwise); extra goes into the report as is
    (the Turnitin submission id of a run that failed after creating one)."""

    def __init__(self, message: str, code: int = 0, extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.extra = dict(extra or {})


def post(url: str, body: dict, headers: dict, timeout: int = TIMEOUT) -> dict:
    """POST JSON, return the JSON answer. An HTTP error raises HTTPError, a network
    error URLError or TimeoutError."""
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={**UA, "Content-Type": "application/json",
                                              "Accept": "application/json", **headers})
    with urllib.request.urlopen(request, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def http(method: str, url: str, data: bytes | None, headers: dict, timeout: int = TIMEOUT):
    """Any method with a raw body; the JSON answer, {} for an empty one. Errors as post."""
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={**UA, "Accept": "application/json", **headers})
    with urllib.request.urlopen(request, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace").strip()
    return json.loads(raw) if raw else {}


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def _detail(e: urllib.error.HTTPError) -> str:
    try:
        d = json.loads(e.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001  a body that is not JSON says nothing more
        return ""
    if isinstance(d, dict):
        return " ".join(str(d[k]) for k in ("error", "description", "message") if d.get(k))
    return ""


def call(url: str, body: dict, headers: dict, label: str) -> dict:
    """One JSON POST with retries on 429 (after Retry-After), 5xx and network errors."""
    answer = retry(lambda: post(url, body, headers), label)
    if not isinstance(answer, dict):
        raise ProviderError(f"{label}: the answer is not a JSON object")
    return answer


def retry(send, label: str):
    """send() up to TRIES times: 429 (after Retry-After), 5xx and network errors are
    retried, any other HTTP error raises ProviderError with its code at once."""
    last, code = "", 0
    for attempt in range(TRIES):
        try:
            return send()
        except urllib.error.HTTPError as e:
            last, code = f"{label}: HTTP {e.code} {_detail(e)}".rstrip(), e.code
            if e.code != 429 and e.code < 500:
                raise ProviderError(last, e.code) from None
            wait = (e.headers.get("Retry-After") if e.headers else None) or ""
            sleep(float(wait) if wait.replace(".", "", 1).isdigit() else 5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            last, code = f"{label}: {type(e).__name__} {getattr(e, 'reason', '') or ''}".rstrip(), 0
            sleep(5 * (attempt + 1))
    raise ProviderError(last or f"{label}: no answer", code)


def chunks(text: str, limit: int) -> list[str]:
    """The text in pieces of at most limit characters, cut between sentences (between
    words when one sentence is longer than limit)."""
    pieces = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]
    out, current = [], ""
    for p in pieces:
        while len(p) > limit:
            cut = p.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if current:
                out.append(current)
                current = ""
            out.append(p[:cut])
            p = p[cut:].strip()
        if current and len(current) + 1 + len(p) > limit:
            out.append(current)
            current = p
        else:
            current = f"{current} {p}" if current else p
    if current:
        out.append(current)
    return out


def words(text: str) -> int:
    return len(text.split())


def _cut(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= EXCERPT else text[:EXCERPT].rsplit(" ", 1)[0] + " …"


class Provider:
    name = ""
    label = ""
    env: tuple = ()
    doc = ""
    chunk_chars = 10_000
    min_chars = 0
    gap = 0.0           # seconds between two requests
    sends = "只发送正文，不发送参考文献"

    def key(self) -> str:
        return next((os.environ[v] for v in self.env if os.environ.get(v)), "")

    def setup(self) -> str:
        """What to set, for the "not configured" list."""
        return f"{self.label} ({' / '.join(self.env)})"

    def scan(self, text: str, title: str, context: dict | None = None) -> dict:
        """context (the compiled PDF, the notes) is for providers that send a file."""
        key = self.key()
        if not key:
            raise ProviderError(f"{self.label}: no key in {' or '.join(self.env)}")
        parts = chunks(text, self.chunk_chars)
        if len(parts) > 1 and len(parts[-1]) < self.min_chars:      # a tail too short to send alone;
            parts[-2:] = [parts[-2] + " " + parts[-1]]                 # chunk_chars is below the API's limit
        if not parts or len(" ".join(parts)) < self.min_chars:
            raise ProviderError(f"{self.label}: the text is shorter than {self.min_chars} characters")
        total, weighted, sources = 0, 0.0, {}
        for i, part in enumerate(parts):
            if i and self.gap:
                sleep(self.gap)
            n = words(part)
            score, found = self.parse(call(*self.request(part, f"{title} ({i + 1}/{len(parts)})", key),
                                           label=self.label), part)
            total += n
            weighted += score * n
            for s in found:
                merged = sources.setdefault(s["url"] or s["title"], {**s, "matched_words": 0, "excerpts": []})
                merged["matched_words"] += s["matched_words"]
                merged["cited"] = merged["cited"] or s["cited"]
                merged["excerpts"] += s["excerpts"]
        matches = [{"url": s["url"], "title": s["title"], "matched_words": s["matched_words"],
                    "percent": round(100 * s["matched_words"] / total, 1) if total else 0.0,
                    "excerpt": _cut(" … ".join(dict.fromkeys(e for e in s["excerpts"] if e))),
                    "cited": s["cited"], "doi": s.get("doi", "")} for s in sources.values()]
        matches.sort(key=lambda m: -m["percent"])
        return {"score": round(weighted / total, 1) if total else 0.0, "words": total,
                "chunks": len(parts), "matches": matches}

    def request(self, text: str, title: str, key: str) -> tuple[str, dict, dict]:
        raise NotImplementedError

    def parse(self, answer: dict, text: str) -> tuple[float, list[dict]]:
        """(score of this chunk 0..100, sources: {url, title, matched_words, excerpts, cited})."""
        raise NotImplementedError


class Winston(Provider):
    """https://docs.gowinston.ai/api-reference/v2/plagiarism/post
    Request {text (100..120000 characters), language, country, excluded_sources};
    answer {result: {score, textWordCounts, totalPlagiarismWords, ...}, sources: [{score,
    url, title, plagiarismWords, citation, is_excluded, plagiarismFound: [{startIndex,
    endIndex, sequence}]}], ...}. Errors {error, description}: 400, 401, 402 (no credits),
    403, 415, 429, 500, 503. 2 credits per word
    (https://docs.gowinston.ai/api-reference/introduction)."""

    name = "winston"
    label = "Winston AI"
    env = ("WINSTONAI_API_KEY", "WINSTON_API_KEY")
    doc = "https://docs.gowinston.ai/api-reference/v2/plagiarism/post"
    url = "https://api.gowinston.ai/v2/plagiarism"
    chunk_chars = 100_000
    min_chars = 100
    gap = 1.0

    def request(self, text, title, key):
        return self.url, {"text": text, "language": "en", "country": "us"}, {"Authorization": f"Bearer {key}"}

    def parse(self, answer, text):
        if "result" not in answer:
            raise ProviderError(f"{self.label}: the answer has no result")
        n = words(text)
        found = []
        for s in answer.get("sources") or []:
            if s.get("is_excluded"):
                continue
            matched = s.get("plagiarismWords")
            if not isinstance(matched, (int, float)):
                matched = round((s.get("score") or 0) * n / 100)
            found.append({"url": s.get("url") or "", "title": s.get("title") or "", "matched_words": int(matched),
                          "excerpts": [p.get("sequence") or "" for p in s.get("plagiarismFound") or []],
                          "cited": bool(s.get("citation"))})
        return float((answer.get("result") or {}).get("score") or 0), found


class Originality(Provider):
    """https://docs.originality.ai/scan (base https://api.originality.ai/api/v3)
    Request {title, content, check_ai, check_plagiarism, check_facts, check_readability,
    check_grammar, storeScan, aiModelVersion}, all required; answer {results: {plagiarism:
    {score, results: [{phrase, results: [{link, title, scores: [{score, sentence}],
    cite_info: {identifiers: {doi}}}]}]}}}. 500 requests a minute, 429 above it; long
    texts should be split because a plagiarism check can take up to 60 s
    (https://docs.originality.ai/originality-ai-api-v1)."""

    name = "originality"
    label = "Originality.ai"
    env = ("ORIGINALITY_API_KEY",)
    doc = "https://docs.originality.ai/scan"
    url = "https://api.originality.ai/api/v3/scan"
    chunk_chars = 10_000
    min_chars = 50
    gap = 0.2

    def request(self, text, title, key):
        return self.url, {"title": title, "content": text, "check_ai": False, "check_plagiarism": True,
                          "check_facts": False, "check_readability": False, "check_grammar": False,
                          "storeScan": False, "aiModelVersion": "model-5.0"}, {"X-OAI-API-KEY": key}

    def parse(self, answer, text):
        plag = (answer.get("results") or {}).get("plagiarism")
        if not isinstance(plag, dict):
            raise ProviderError(f"{self.label}: the answer has no plagiarism result")
        found = {}
        for phrase in plag.get("results") or []:
            for r in phrase.get("results") or []:
                link = r.get("link") or ""
                s = found.setdefault(link or r.get("title") or "", {
                    "url": link, "title": r.get("title") or "", "matched_words": 0, "excerpts": [],
                    "cited": False, "doi": ((r.get("cite_info") or {}).get("identifiers") or {}).get("doi") or ""})
                s["matched_words"] += words(phrase.get("phrase") or "")
                s["excerpts"].append(phrase.get("phrase") or "")
        return float(plag.get("score") or 0), list(found.values())


from .turnitin import Turnitin  # noqa: E402  it builds on Provider above

PROVIDERS = {p.name: p for p in (Winston(), Originality(), Turnitin())}


def select(choice: str = "") -> tuple[Provider | None, list[str]]:
    """(the provider to use, the labels of providers without a key). choice is the
    paper's notes.plagiarism_provider: a provider name, "none" to switch the check off,
    or empty for the first provider with a key."""
    choice = (choice or "").strip().lower()
    missing = [p.setup() for p in PROVIDERS.values() if not p.key()]
    if choice in ("none", "off", "no"):
        return None, []
    if choice:
        if choice not in PROVIDERS:
            raise ValueError(f"notes.plagiarism_provider {choice!r} is not one of {sorted(PROVIDERS)}")
        p = PROVIDERS[choice]
        return (p if p.key() else None), missing
    return next((p for p in PROVIDERS.values() if p.key()), None), missing


__all__ = ["PROVIDERS", "Provider", "ProviderError", "chunks", "http", "retry", "select"]
