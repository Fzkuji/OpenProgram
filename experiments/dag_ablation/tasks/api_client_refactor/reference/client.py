import json
import urllib.request


class ApiError(Exception):
    pass


def urllib_transport(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req) as r:
        return r.status, r.read().decode()


class ApiClient:
    def __init__(self, base, transport=urllib_transport, retries=2):
        self.base = base.rstrip("/")
        self.transport = transport
        self.retries = retries

    def _call(self, method, path, body=None):
        url = self.base + path
        status, text = self.transport(method, url, body)
        attempts = 0
        while status >= 500 and attempts < self.retries:
            attempts += 1
            status, text = self.transport(method, url, body)
        if not 200 <= status < 300:
            raise ApiError(status)
        return json.loads(text)

    def get(self, path):
        return self._call("GET", path)

    def post(self, path, body):
        return self._call("POST", path, body)
