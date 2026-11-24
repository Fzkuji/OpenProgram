"""Runners started ahead: an operation starts on one that already loaded its backend."""
import json
import sys
import time

from openprogram.programs._host.ui.service import ApplicationService


def _app(tmp_path, digest="d1"):
    root = tmp_path / "app"
    root.mkdir(exist_ok=True)
    (root / "backend.py").write_text(
        "import time\nLOADED = time.time()\n"
        "operations = {'when': lambda value, context: {'loaded': LOADED, 'echo': value['x']}}\n")
    return {"id": "test.ready", "path": str(root), "digest": digest, "capabilities": [],
            "backend": {"kind": "python", "entry": "backend:operations"},
            "operations": {"when": {"output": {"type": "object"}}}}


def _service():
    service = ApplicationService.__new__(ApplicationService)
    service.ready = {}
    return service


def test_a_ready_runner_has_loaded_the_backend_before_its_operation(tmp_path):
    service, app = _service(), _app(tmp_path)
    service._refill(app, sys.executable)
    assert len(service.ready[app["id"]]) == ApplicationService.READY
    time.sleep(3)                       # the runner loads while nobody waits
    process, owner = service._take_ready(app, sys.executable)
    sent = time.time()
    service._send(process, {"run_id": "r1", "instance": {"id": "i1", "project_id": ""}, "definition": app,
                            "root": app["path"], "operation": "when", "input": {"x": 7}})
    event = json.loads(process.stdout.readline())
    process.wait(timeout=10)
    assert event["type"] == "result" and event["value"]["echo"] == 7
    assert event["value"]["loaded"] < sent       # imported before the request came
    service.drop_ready()


def test_runners_for_an_older_version_are_never_used(tmp_path):
    service = _service()
    old = _app(tmp_path, "d1")
    service._refill(old, sys.executable)
    stale = [process for _, process, _ in service.ready[old["id"]]]
    assert service._take_ready(_app(tmp_path, "d2"), sys.executable) is None
    for process in stale:
        process.wait(timeout=10)
    assert service.ready[old["id"]] == []


def test_a_ready_runner_whose_service_goes_away_exits(tmp_path):
    service, app = _service(), _app(tmp_path)
    service._refill(app, sys.executable)
    processes = [process for _, process, _ in service.ready[app["id"]]]
    for process in processes:
        process.stdin.close()
    for process in processes:
        assert process.wait(timeout=10) == 0
