"""Run the app's UI outside OpenProgram, for development: the same backend operations
behind the same window.openprogramApp calls, on one project folder.

    python dev_server.py <project_dir> [port]

Operations run in a thread of this process with the caller's default model; progress
events are kept in memory. Storage is ~/.openprogram/cache/auto_research_dev_storage.json. Not
for normal use: the installed app runs each operation in its own process under the
host's permissions.
"""

import json
import sys
import threading
import traceback
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import backend  # noqa: E402

SHIM = """<script>
// The host bridge, served by dev_server.py.
const post = (path, body) => fetch(path, {method: "POST", body: JSON.stringify(body || {})}).then(async r => {
  const v = await r.json(); if (!r.ok) throw new Error(v.error || r.statusText); return v; });
window.openprogramApp = {
  run: (operation, input, requestKey) => post("/api/run", {operation, input, requestKey}),
  status: (id, after) => post("/api/status", {id, after}),
  runs: () => post("/api/runs"),
  cancel: (id) => post("/api/cancel", {id}),
  load: () => post("/api/load"),
  save: (value, version) => post("/api/save", {value, version}),
  // No conversation outside OpenProgram: the page draws no messages and no composer.
  on: () => () => {},
  chat: { open: () => new Promise(() => {}), note: async () => {}, place: async () => {}, reveal: async () => false, close: async () => {} },
};
</script>"""

RUNS: dict[str, dict] = {}
LOCK = threading.Lock()


class Context:
    def __init__(self, project: Path, run: dict):
        self.project_path = str(project)
        self.run = run

    def progress(self, value):
        with LOCK:
            self.run["events"].append({"sequence": len(self.run["events"]) + 1, "type": "progress", "value": value})

    def ask(self, question):
        raise RuntimeError("dev_server cannot answer questions")


def start(project: Path, operation: str, value: dict) -> dict:
    run = {"id": uuid.uuid4().hex, "operation": operation, "status": "running", "result": None,
           "error": None, "events": []}
    RUNS[run["id"]] = run

    def work():
        try:
            from openprogram.runtime.agent.runtime.factory import create_runtime
            from openprogram.runtime.agent.call_state import _current_runtime
            _current_runtime.set(create_runtime(model=None))
            result = backend.operations[operation](value, Context(project, run))
            run.update(status="completed", result=result)
        except Exception as e:
            traceback.print_exc()
            run.update(status="failed", error=f"{type(e).__name__}: {e}")
    threading.Thread(target=work, daemon=True).start()
    return {"id": run["id"], "status": "running"}


def handler(project: Path):
    storage = Path.home() / ".openprogram" / "cache" / "auto_research_dev_storage.json"

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(HERE / "ui"), **k)

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                html = (HERE / "ui" / "index.html").read_text(encoding="utf-8").replace("<head>", "<head>" + SHIM, 1)
                body = html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            try:
                if self.path == "/api/run":
                    out = start(project, body["operation"], body.get("input") or {})
                elif self.path == "/api/status":
                    run = RUNS[body["id"]]
                    with LOCK:
                        out = {**run, "events": [e for e in run["events"] if e["sequence"] > (body.get("after") or 0)]}
                elif self.path == "/api/runs":
                    out = [{k: r[k] for k in ("id", "operation", "status")} for r in RUNS.values()]
                elif self.path == "/api/cancel":
                    out = {"ok": False, "note": "dev_server cannot cancel a running thread"}
                elif self.path == "/api/load":
                    out = json.loads(storage.read_text()) if storage.exists() else {"value": {}, "version": 0}
                elif self.path == "/api/save":
                    storage.parent.mkdir(parents=True, exist_ok=True)
                    out = {"value": body["value"], "version": int(body.get("version") or 0) + 1}
                    storage.write_text(json.dumps(out))
                else:
                    self.send_error(404)
                    return
                data, code = json.dumps(out, ensure_ascii=False).encode(), 200
            except Exception as e:
                data, code = json.dumps({"error": f"{type(e).__name__}: {e}"}).encode(), 400
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return Handler


def main():
    project = Path(sys.argv[1]).expanduser().resolve()
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 18190
    print(f"Auto Research dev UI for {project} at http://localhost:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), handler(project)).serve_forever()


if __name__ == "__main__":
    main()
