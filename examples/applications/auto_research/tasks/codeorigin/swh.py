"""Software Heritage's public API, keyless: which contents the archive knows (/known/),
an origin's current revision, a path inside it and a content's bytes. Anonymous clients
get 120 requests an hour; the client reads X-RateLimit-Remaining and X-RateLimit-Reset,
waits for a reset that is near, and otherwise stops and names the limit. SWH_TOKEN (a
free account's bearer token) raises the limit. Content-to-origin lookups (the graph and
provenance endpoints) need a token, so origins come from candidates that are verified
here by path and hash."""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from ..citations import sources

API = "https://archive.softwareheritage.org/api/1"
LABEL = "Software Heritage"
BATCH = 1000            # SWHIDs per /known/ request
BUDGET = 90             # requests one run may make
RESERVE = 3             # leave this many of the hour's requests
MAX_WAIT = 30.0         # wait for a rate-limit reset at most this long
TRIES = 3


class Archive:
    def __init__(self, budget: int = BUDGET):
        self.budget = budget
        self.calls = 0
        self.remaining: int | None = None
        self.reset: float | None = None
        self.problem = ""          # why the archive stopped answering, for the report
        token = os.environ.get("SWH_TOKEN", "").strip()
        self.headers = {**sources.UA, **({"Authorization": f"Bearer {token}"} if token else {})}

    # Transport ------------------------------------------------------------------------------

    def request(self, path: str, body=None, raw: bool = False):
        """JSON (or bytes when raw) of one API request; None for 404 or when the archive
        cannot be used any more (self.problem says why)."""
        if self.problem:
            return None
        if self.calls >= self.budget:
            self.problem = f"本次运行的 {self.budget} 次请求额度已用完"
            return None
        if not self.wait_for_limit():
            return None
        url = API + path
        data = json.dumps(body).encode() if body is not None else None
        headers = {**self.headers, **({"Content-Type": "application/json"} if data else {})}
        for attempt in range(TRIES):
            self.calls += 1
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
                with urllib.request.urlopen(req, timeout=sources.TIMEOUT) as r:
                    self.read_limit(r.headers)
                    payload = r.read()
                return payload if raw else json.loads(payload.decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                self.read_limit(e.headers)
                if e.code in (400, 404):
                    return None
                if e.code == 429:
                    wait = self.seconds_to_reset(e.headers)
                    if wait is None or wait > MAX_WAIT:
                        self.problem = "匿名请求已达每小时上限" + (f"，约 {int(wait // 60) + 1} 分钟后恢复" if wait else "")
                        return None
                    time.sleep(wait)
                    continue
                if e.code in (401, 403):
                    self.problem = f"HTTP {e.code}"
                    return None
            except Exception:  # noqa: BLE001  timeouts and resets are retried
                pass
            time.sleep(1.5 * (attempt + 1))
        self.problem = "请求多次失败"
        return None

    def read_limit(self, headers) -> None:
        if not headers:
            return
        try:
            if headers.get("X-RateLimit-Remaining") is not None:
                self.remaining = int(headers.get("X-RateLimit-Remaining"))
            if headers.get("X-RateLimit-Reset") is not None:
                self.reset = float(headers.get("X-RateLimit-Reset"))
        except (TypeError, ValueError):
            pass

    def seconds_to_reset(self, headers=None) -> float | None:
        if headers is not None and headers.get("Retry-After"):
            try:
                return float(headers.get("Retry-After"))
            except ValueError:
                pass
        self.read_limit(headers)
        return max(0.0, self.reset - time.time()) if self.reset else None

    def wait_for_limit(self) -> bool:
        if self.remaining is None or self.remaining > RESERVE:
            return True
        wait = self.seconds_to_reset()
        if wait is not None and wait <= MAX_WAIT:
            time.sleep(wait)
            self.remaining = None
            return True
        self.problem = "匿名请求快到每小时上限" + (f"，约 {int(wait // 60) + 1} 分钟后恢复" if wait else "")
        return False

    # Queries --------------------------------------------------------------------------------

    def known(self, swhids: list[str]) -> dict[str, bool] | None:
        """{swhid: known} for every SWHID; None when the archive could not answer them all."""
        out = {}
        for i in range(0, len(swhids), BATCH):
            reply = self.request("/known/", body=swhids[i:i + BATCH])
            if not isinstance(reply, dict):
                if not self.problem:
                    self.problem = "/known/ 没有返回结果"
                return None
            out.update({k: bool((v or {}).get("known")) for k, v in reply.items()})
        return out

    def revision(self, origin: str) -> str:
        """The revision the origin's HEAD pointed to at the latest archived visit; ""."""
        visit = self.request(f"/origin/{quote(origin)}/visit/latest/?require_snapshot=true")
        snap = (visit or {}).get("snapshot")
        if not snap:
            return ""
        branches = (self.request(f"/snapshot/{snap}/?branches_from=HEAD&branches_count=1") or {}).get("branches", {})
        head = branches.get("HEAD") or {}
        url = head.get("target_url") or ""
        if head.get("target_type") == "revision":
            return head.get("target", "")
        return url.rstrip("/").rsplit("/", 1)[-1] if "/revision/" in url else ""

    def lookup(self, revision: str, path: str) -> dict | None:
        """{sha1_git, length} of the file at path in the revision; None when it is not a file there."""
        reply = self.request(f"/revision/{revision}/directory/{quote(path)}/")
        if not isinstance(reply, dict) or reply.get("type") != "file":
            return None
        sums = (reply.get("content") or {}).get("checksums") or {}
        return {"sha1_git": sums.get("sha1_git", ""), "length": (reply.get("content") or {}).get("length")}

    def root(self, revision: str) -> list[dict]:
        """The entries (name, type, target) of the revision's top folder."""
        reply = self.request(f"/revision/{revision}/directory/")
        entries = reply.get("content") if isinstance(reply, dict) else reply
        return [e for e in entries or [] if isinstance(e, dict)]

    def raw(self, sha1_git: str) -> bytes | None:
        return self.request(f"/content/sha1_git:{sha1_git}/raw/", raw=True)


def quote(text: str) -> str:
    return urllib.parse.quote(text, safe=":/@")


def browse(sha1_git: str) -> str:
    """The archive page of a content."""
    return f"https://archive.softwareheritage.org/swh:1:cnt:{sha1_git}"


__all__ = ["Archive", "browse"]
