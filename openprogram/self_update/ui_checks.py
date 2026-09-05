"""One-shot main-window capture requests owned by the active verifier Job."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import struct
import threading
import time
import zlib

UI_PROTOCOL = 1
UI_INTERACTION_PROTOCOL = 1
UI_VIEW_PROTOCOL = 1
UI_TEST_OBJECT_PROTOCOL = 1
MAX_CAPTURE_BYTES = 1572864
_pending = {}
_lock = threading.RLock()


def _validate_png(raw, width, height):
    """Validate bounded, non-interlaced 8-bit RGB/RGBA native capture bytes."""
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("invalid PNG signature")
    position, channels, ended = 8, None, False
    compressed = bytearray()
    while position + 12 <= len(raw):
        size = int.from_bytes(raw[position:position + 4], "big")
        kind = raw[position + 4:position + 8]
        payload = raw[position + 8:position + 8 + size]
        end = position + size + 12
        if end > len(raw) or zlib.crc32(kind + payload) != int.from_bytes(raw[end - 4:end], "big"):
            raise ValueError("invalid PNG chunk")
        if position == 8:
            if kind != b"IHDR" or size != 13:
                raise ValueError("invalid PNG header")
            w, h, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", payload)
            if (w, h) != (width, height) or depth != 8 or color not in (2, 6) or (compression, filtering, interlace) != (0, 0, 0):
                raise ValueError("unsupported native PNG dimensions or format")
            channels = 3 if color == 2 else 4
        elif kind == b"IHDR":
            raise ValueError("duplicate PNG header")
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            ended = size == 0 and end == len(raw)
            break
        elif not kind[0] & 32 and kind != b"PLTE":
            raise ValueError("unsupported critical PNG chunk")
        position = end
    if not ended or channels is None:
        raise ValueError("incomplete PNG capture")
    stride = width * channels + 1
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(compressed, stride * height + 1)
    except zlib.error:
        raise ValueError("invalid PNG compression") from None
    if (len(pixels) != stride * height or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or any(pixels[row] > 4 for row in range(0, len(pixels), stride))):
        raise ValueError("invalid PNG pixel data")


def admit_plan(plan, request):
    if any(check["entry"] == "ui:main" for check in plan["checks"]):
        from .package_protocol import validate_ui_package
        from .native_checks import runtime_identity
        from .verification_plan import required_ui_protocol
        package = validate_ui_package(Path(request.app_path))
        if package["protocol"] < required_ui_protocol(plan["checks"]):
            raise ValueError("App does not support guarded UI interactions")
        runtime_identity(Path(request.app_path))
        _main_connection()


def _main_connection():
    from openprogram.webui.ws_actions.webtab import registered_desktop_windows
    matches = [(ws, revision) for ws, name, revision in registered_desktop_windows() if name == "main"]
    if len(matches) != 1:
        raise ValueError("one registered main Desktop connection is required")
    return matches[0]


def _live(store, update_id, grant):
    from .verification_channel import load_grant, _check_job
    from openprogram.agent.job.store import load_job
    from openprogram.agent.job.types import JobStatus
    with store._locked():
        record = store._load_active_unlocked()
        if record is None or record.request.update_id != update_id or load_grant(store, record) != grant:
            raise ValueError("UI verifier authorization changed")
        job = load_job(record.request.session_id, grant["job_id"])
        _check_job(store, record, grant, job)
        if grant["worker_pid"] != os.getpid() or job.status is not JobStatus.RUNNING or job.cancel_requested_at is not None:
            raise ValueError("UI verifier is no longer running")
        return record


def _process_identity(identity):
    app_pid, renderer_pid = identity["app_pid"], identity["renderer_pid"]
    if any(type(pid) is not int or pid <= 0 for pid in (app_pid, renderer_pid)):
        raise ValueError("invalid Desktop process identity")
    def field(pid, name):
        try:
            return subprocess.check_output(["/bin/ps", "-p", str(pid), "-o", name + "="],
                                           text=True, timeout=2).strip()
        except subprocess.SubprocessError:
            raise ValueError("Desktop process is unavailable") from None
    app = "/Applications/OpenProgram.app"
    executable, renderer = field(app_pid, "comm"), field(renderer_pid, "comm")
    if (executable != app + "/Contents/MacOS/OpenProgram" or not renderer.startswith(app + "/Contents/Frameworks/")
            or not renderer.endswith("/OpenProgram Helper (Renderer)") or field(renderer_pid, "ppid") != str(app_pid)):
        raise ValueError("capture does not belong to the default App processes")
    return dict(app_pid=app_pid, renderer_pid=renderer_pid, executable=executable,
                app_started=field(app_pid, "lstart"), renderer_started=field(renderer_pid, "lstart"))


def validate_capture(body, contract):
    keys = {"schema", "nonce", "update_id", "attempt", "check_id", "worker_pid", "identity",
            "observed_at", "screenshot", "accessibility", "cleanup_complete"}
    if "interaction" in contract:
        keys.add("interaction")
    if (not isinstance(body, dict) or set(body) != keys
            or type(body["schema"]) is not int or body["schema"] != UI_PROTOCOL
            or any(body[k] != contract[k] for k in ("nonce", "update_id", "attempt", "check_id", "worker_pid"))
            or type(body["attempt"]) is not int or type(body["worker_pid"]) is not int
            or body["cleanup_complete"] is not True
            or type(body["observed_at"]) not in (int, float) or not math.isfinite(body["observed_at"])
            or not contract["issued_at"] <= body["observed_at"] <= min(time.time(), contract["deadline"])
            or len(json.dumps(body, allow_nan=False).encode()) > contract["max_output_bytes"]):
        raise ValueError("invalid or expired main-window capture")
    identity = body["identity"]
    if (not isinstance(identity, dict) or set(identity) != {"app_path", "app_pid", "candidate_sha", "window_id",
            "web_contents_id", "renderer_pid", "route", "bounds", "target_id"}
            or identity["app_path"] != "/Applications/OpenProgram.app"
            or identity["candidate_sha"] != contract["candidate_sha"] or identity["route"] != "/s/" + contract["session_id"]
            or any(type(identity[k]) is not int or identity[k] <= 0 for k in ("app_pid", "renderer_pid", "window_id", "web_contents_id"))
            or not isinstance(identity["target_id"], str) or not 1 <= len(identity["target_id"]) <= 128):
        raise ValueError("main-window identity differs from approved scope")
    bounds = identity["bounds"]
    if (not isinstance(bounds, dict) or set(bounds) != {"x", "y", "width", "height"}
            or any(type(v) is not int for v in bounds.values()) or not 1 <= bounds["width"] <= 16384
            or not 1 <= bounds["height"] <= 16384):
        raise ValueError("invalid main-window bounds")
    screenshot = body["screenshot"]
    if (not isinstance(screenshot, dict) or set(screenshot) != {"mime_type", "width", "height", "sha256", "data"}
            or screenshot["mime_type"] != "image/png" or not isinstance(screenshot["data"], str)
            or any(type(screenshot[k]) is not int or not 1 <= screenshot[k] <= 16384 for k in ("width", "height"))
            or screenshot["width"] * screenshot["height"] > 32_000_000):
        raise ValueError("invalid screenshot metadata")
    raw = base64.b64decode(screenshot["data"], validate=True)
    if hashlib.sha256(raw).hexdigest() != screenshot["sha256"]:
        raise ValueError("screenshot digest changed")
    _validate_png(raw, screenshot["width"], screenshot["height"])
    ax = body["accessibility"]
    if not isinstance(ax, dict) or not isinstance(ax.get("nodes"), list) or not 1 <= len(ax["nodes"]) <= 10000:
        raise ValueError("accessibility evidence is unavailable")
    if "interaction" in contract:
        step = body["interaction"]
        action = contract["interaction"]
        if action["kind"] == "test_object":
            if (not isinstance(step, dict) or set(step) != set(action) | {"before", "after", "restored"}
                    or any(step[key] != value for key, value in action.items())
                    or step["before"] != action["initial_title"] or step["after"] != action["title"]
                    or step["restored"] != action["initial_title"]):
                raise ValueError("isolated test object did not perform the approved change and restoration")
            return
        if action["kind"] == "view":
            if (not isinstance(step, dict) or set(step) != {"kind", "target", "before", "after", "restored"}
                    or {key: step[key] for key in ("kind", "target")} != action
                    or step["before"] not in ("session", "dag") or step["after"] != action["target"]
                    or step["restored"] != step["before"]):
                raise ValueError("UI perspective or restoration differs from the approved operation")
            return
        if (not isinstance(step, dict) or set(step) != {"kind", "delta_y", "before", "after", "restored"}
                or type(step["delta_y"]) is not int
                or {key: step[key] for key in ("kind", "delta_y")} != contract["interaction"]):
            raise ValueError("UI interaction differs from approved action")
        for name in ("before", "after", "restored"):
            metrics = step[name]
            if (not isinstance(metrics, dict) or set(metrics) != {"top", "left", "height", "viewport"}
                    or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1e9 for v in metrics.values())
                    or not 0 < metrics["viewport"] <= metrics["height"]
                    or metrics["top"] > metrics["height"] - metrics["viewport"]):
                raise ValueError("invalid UI scroll metrics")
        before, after = step["before"], step["after"]
        expected = {**before, "top": max(0, min(before["height"] - before["viewport"], before["top"] + step["delta_y"]))}
        if after != expected or step["restored"] != before:
            raise ValueError("UI scroll or restoration did not match the approved operation")


def exchange(store, *, update_id, nonce, principal_id, body=None):
    from openprogram.agent.authority import owner_principal_id
    if principal_id != owner_principal_id() or not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{64}", nonce):
        raise ValueError("UI request is unavailable")
    with _lock:
        entry = _pending.get(nonce)
        if entry is None or entry["update_id"] != update_id or entry["root"] != store.root:
            raise ValueError("UI request is unavailable")
        _live(store, update_id, entry["grant"])
        if time.monotonic() >= entry["end"] or _main_connection() != entry["connection"]:
            raise ValueError("UI request expired or connection changed")
        if body is None:
            if entry["claimed"]:
                raise ValueError("UI request was already claimed")
            entry["claimed"] = True
            return {k: v for k, v in entry["contract"].items() if k != "issued_at"}
        if not entry["claimed"] or entry["capture"] is not None:
            raise ValueError("UI request cannot accept another result")
        validate_capture(body, entry["contract"])
        if "fixture" in entry and entry["fixture"] != {"phase": "restored", "title": entry["contract"]["interaction"]["initial_title"]}:
            raise ValueError("isolated test object has no backend restoration evidence")
        encoded = json.dumps(body, allow_nan=False)
        if entry["grant"]["token"] in encoded:
            raise ValueError("capture contains a private credential")
        entry["process"] = _process_identity(body["identity"])
        entry["capture"] = deepcopy(body)
        return {"ok": True, "nonce": nonce}


def _fixture_command(entry, command):
    action = entry["contract"].get("interaction", {})
    return ("fixture" in entry and isinstance(command, dict)
            and set(command) == {"action", "nonce", "object_id", "op", "title"}
            and command["action"] == "self_update_test_object" and command["nonce"] == entry["contract"]["nonce"]
            and command["object_id"] == action["object_id"]
            and (command["op"], command["title"]) in (("rename", action["title"]), ("restore", action["initial_title"])))


async def handle_test_object(ws, command):
    """Only mutate the active check's ephemeral object, never a session record."""
    from .store import SelfUpdateStore
    reply = {"ok": False}
    try:
        with _lock:
            nonce = command.get("nonce") if isinstance(command, dict) else None
            entry = _pending.get(nonce) if isinstance(nonce, str) else None
            if (entry is None or entry["connection"] != _main_connection() or entry["connection"][0] is not ws
                    or not entry["claimed"] or entry["capture"] is not None or time.monotonic() >= entry["end"]
                    or not _fixture_command(entry, command)):
                raise ValueError("test object unavailable")
            _live(SelfUpdateStore(entry["root"]), entry["update_id"], entry["grant"])
            fixture = entry["fixture"]
            expected, phase = (("initial", "renamed") if command["op"] == "rename" else ("renamed", "restored"))
            if fixture["phase"] != expected:
                raise ValueError("test object operation already applied or out of order")
            fixture.update(phase=phase, title=command["title"])
            reply = {"ok": True, "nonce": nonce, "object_id": command["object_id"], **fixture}
    except (ValueError, RuntimeError, OSError, KeyError, TypeError):
        pass  # Never reflect arbitrary client text or private grant material.
    await ws.send_text(json.dumps({"type": "self_update_test_object", "data": reply}))


def permits_ws_command(ws, command):
    with _lock:
        for entry in _pending.values():
            if entry["connection"][0] is ws:
                return isinstance(command, dict) and (
                    command.get("action") == "webtab_result" or _fixture_command(entry, command)
                    or command.get("action") == "cancel_job" and command.get("job_id") == entry["grant"]["job_id"])
    return True


def observe_ui(store, record, check, grant):
    from .native_checks import runtime_identity
    from .package_protocol import validate_ui_package
    from .verification_plan import required_ui_protocol
    from .system_probe import _probe_system
    from openprogram.webui.ws_actions.webtab import request_on_ws
    connection = _main_connection()
    now = time.time()
    seconds = min(check["timeout_seconds"], grant["deadline"] - now)
    if seconds <= 0:
        raise TimeoutError("UI verification deadline expired")
    end = time.monotonic() + seconds
    def remaining():
        _live(store, record.request.update_id, grant)
        value = end - time.monotonic()
        if value <= 0 or _main_connection() != connection:
            raise ValueError("UI verification expired or disconnected")
        return value
    app = Path(record.request.app_path)
    package = validate_ui_package(app)
    if package["protocol"] < required_ui_protocol([check]):
        raise ValueError("App does not support guarded UI interactions")
    runtime = runtime_identity(app, expected_revision=record.request.candidate_sha)
    before = _probe_system(record, record.request.candidate_sha, remaining())
    nonce = secrets.token_hex(32)
    contract = dict(schema=UI_PROTOCOL, nonce=nonce, update_id=record.request.update_id, attempt=record.state.attempt,
                    session_id=record.request.session_id, candidate_sha=record.request.candidate_sha,
                    worker_pid=grant["worker_pid"], check_id=check["id"], issued_at=now,
                    deadline=now + seconds, max_output_bytes=check["max_output_bytes"], action="capture")
    if "interaction" in check:
        contract["interaction"] = deepcopy(check["interaction"])
    entry = dict(update_id=record.request.update_id, root=store.root, grant=grant, end=end,
                 connection=connection, contract=contract, claimed=False, capture=None)
    if check.get("interaction", {}).get("kind") == "test_object":
        entry["fixture"] = {"phase": "initial", "title": check["interaction"]["initial_title"]}
    with _lock:
        if _pending:
            raise ValueError("another main-window capture is active")
        _pending[nonce] = entry
    try:
        reply = request_on_ws(connection[0], {"op": "self_update_capture", "window_id": "main", "nonce": nonce}, remaining())
        remaining()
        with _lock:
            capture = deepcopy(entry["capture"])
        if not reply.get("ok") or capture is None:
            raise ValueError("Desktop did not return an authorized main-window capture")
        if (_process_identity(capture["identity"]) != entry["process"] or validate_ui_package(app) != package
                or runtime_identity(app, expected_revision=record.request.candidate_sha) != runtime):
            raise ValueError("Desktop identity changed during capture")
        after = _probe_system(record, record.request.candidate_sha, remaining())
        if before["worker_pid"] != after["worker_pid"]:
            raise ValueError("worker changed during capture")
        remaining()
        return {"system_gate": after, "observation": {"entry": "ui:main", "status": 200,
            "content_type": "application/json", "body": json.dumps(capture, allow_nan=False),
            "observed_at": capture["observed_at"]}}
    finally:
        with _lock:
            _pending.pop(nonce, None)


def validate_observation(observation, record, check, grant):
    body = json.loads(observation["body"])
    contract = dict(nonce=body.get("nonce"), update_id=record.request.update_id, attempt=record.state.attempt,
                    check_id=check["id"], worker_pid=grant["worker_pid"], candidate_sha=record.request.candidate_sha,
                    session_id=record.request.session_id, issued_at=grant["issued_at"], deadline=grant["deadline"],
                    max_output_bytes=check["max_output_bytes"])
    if "interaction" in check:
        contract["interaction"] = check["interaction"]
    validate_capture(body, contract)
    if observation["status"] != 200 or observation["observed_at"] != body["observed_at"]:
        raise ValueError("capture observation changed")
