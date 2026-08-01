import json
import urllib.request


class ApiError(Exception):
    pass


class ApiClient:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def _call(self, method, path, body=None):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()

    def get(self, path):
        status, text = self._call("GET", path)
        if not 200 <= status < 300:
            raise ApiError(status)
        return json.loads(text)

    def post(self, path, body):
        status, text = self._call("POST", path, body)
        if not 200 <= status < 300:
            raise ApiError(status)
        return json.loads(text)
