"""Run a candidate version through every trial check, in order.

The first failure stops the trial. Each check stores its evidence in the
update directory. Nothing here touches the live instance: the candidate runs on
a sanitized copy, on its own port, with no credentials and only loopback, and
the one model it can reach is a fake.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence
import uuid

from ..store import SelfUpdateStore
from ..types import UpdateRecord
from ..verification.system_probe import SystemProbeError, doctor_baseline, probe_browser, probe_instance
from ..versions import layout as paths
from ..versions import pointer, store as version_store
from ..versions.layout import Layout
from . import data_copy, rehearsal, sandbox, schema_guard
from .fake_model import FINAL_ANSWER, TURN_MARKER, FakeModel
from .instance import TrialError, TrialInstance, WINDOWS_NOTE, Workspace, workspace_for

TURN_SECONDS = 180
SHELL_STARTED = "OPENPROGRAM_SHELL_STARTED"
WINDOW_TIMEOUT = "the first window did not load"
DESKTOP_SECONDS = 60
MIGRATION_SCALED_LIMIT_SECONDS = 30 * 60
_SELF_TEST_MODULE = "openprogram.operations.self_update.trial.self_test"
_REFUSED_TEST_ARGUMENTS = ("--timeout", "-n", "--numprocesses", "--dist", "--tx")
_OUTPUT_LIMIT = 64 * 1024
Verifier = Callable[[TrialInstance, Mapping[str, bool]], Mapping[str, Any]]


@dataclass(frozen=True)
class TrialRequest:
    layout: Layout
    record: UpdateRecord
    store: SelfUpdateStore
    candidate: str
    previous: str
    live_state: Path
    worktree: Path | None = None
    tests: Sequence[Mapping[str, Any]] = ()
    verifier: Verifier | None = None
    launcher: Path | None = None
    shell_executable: Path | None = None
    browser_python: Path | None = None


@dataclass
class TrialResult:
    passed: bool
    isolation: str
    failed_check: str | None = None
    reason: str | None = None
    notes: list[str] = field(default_factory=list)
    evidence: Path | None = None
    checks: list[str] = field(default_factory=list)


def plan_tests(store: SelfUpdateStore, record: UpdateRecord) -> list[dict]:
    """The tests the update's verification plan names."""
    from ..verification.verifier_config import load_verifier_config

    config = load_verifier_config(store, record)
    if config.get("schema") != 2:
        return []
    return [check for check in config["verification_plan"]["checks"] if check.get("entry") == "test:python"]


class _Run:
    def __init__(self, request: TrialRequest) -> None:
        self.request = request
        self.layout = request.layout
        self.record = request.record
        self.workspace: Workspace = workspace_for(self.layout, self.record.request.update_id).create()
        self.manifest = version_store.read_manifest(self.layout, request.candidate)
        self.old_manifest = version_store.read_manifest(self.layout, request.previous)
        self.destructive = self.manifest.get("destructive_migration")
        self.evidence = request.store.root / self.record.request.update_id / "trial"
        self.evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.port = sandbox.free_port()
        self.model = FakeModel()
        self.result = TrialResult(False, sandbox.isolation_level(), evidence=self.evidence)
        sandbox.record_isolation(self.evidence)
        self.report: data_copy.CopyReport | None = None
        self.baseline: dict[str, bool] = {}
        self.old_started = False
        self.turn_execution: str | None = None
        self.logs_before_old: dict[str, schema_guard.LogSnapshot] = {}
        self.logs_after_old: dict[str, schema_guard.LogSnapshot] = {}
        self.snapshot_before_old: dict[str, schema_guard.DatabaseSnapshot] = {}
        self.snapshot_after_old: dict[str, schema_guard.DatabaseSnapshot] = {}
        self.candidate: TrialInstance | None = None
        self.turn_session = f"trial_{uuid.uuid4().hex[:16]}"

    def _instance(self, label: str, version: str, manifest: Mapping[str, Any]) -> TrialInstance:
        readable = () if self.request.worktree is None else (self.request.worktree,)
        return TrialInstance(self.workspace, self.layout, version, manifest["base"], port=self.port,
                             model_port=self.model.port, live_state=self.request.live_state,
                             launcher=self.request.launcher, label=label, extra_readable=readable)

    def _record(self, name: str, status: str = "passed", **data: Any) -> None:
        number = len(self.result.checks) + 1
        self.result.checks.append(name)
        path = self.evidence / f"{number:02d}-{name}.json"
        path.write_text(json.dumps({"check": name, "status": status, "at": time.time(), **data},
                                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ---- the checks, in the order they run ---------------------------------

    def prepare_data(self) -> None:
        try:
            self.report = data_copy.copy_state(self.request.live_state, self.workspace.state_dir,
                                               model_base_url=self.model.base_url)
        except (data_copy.DataCopyError, OSError) as exc:
            raise TrialError(str(exc)) from exc
        if self.report.sampled:
            self.result.notes.append(
                "These databases were too large to copy whole and were tested on a sample: "
                + ", ".join(self.report.sampled) + "."
            )
        self.snapshot_before_old = schema_guard.snapshot_all(self.workspace.state_dir)
        self.logs_before_old = schema_guard.snapshot_logs(self.workspace.state_dir)
        self._record("data_copy", databases=[item.name for item in self.report.databases],
                     sampled=self.report.sampled, conversations=len(self.report.conversations),
                     sanitized=self.report.sanitized)

    def baseline_check(self) -> None:
        old = self._instance("baseline", self.request.previous, self.old_manifest)
        try:
            old.start()
            try:
                seconds = old.wait_ready()
                self.old_started = True
                self.baseline = doctor_baseline(self.record, old.target())
            except TrialError as exc:
                # The old version is what the owner runs today. If it cannot
                # come up on the copy, no row can regress from it.
                self._record("baseline", "unavailable", reason=str(exc))
                return
            self._record("baseline", seconds=seconds, doctor=self.baseline)
        finally:
            old.stop()
            self.snapshot_after_old = schema_guard.snapshot_all(self.workspace.state_dir)
            self.logs_after_old = schema_guard.snapshot_logs(self.workspace.state_dir)

    def cold_start(self) -> None:
        self.candidate = self._instance("candidate", self.request.candidate, self.manifest)
        self.candidate.start()
        seconds = self.candidate.wait_ready()
        scale = max((item.scale for item in (self.report.databases if self.report else ())), default=1.0)
        scaled = self.candidate.migration_seconds * scale
        if scaled > MIGRATION_SCALED_LIMIT_SECONDS:
            raise TrialError(
                f"migrating the full data would take about {int(scaled // 60)} minutes, past the 30 minute limit"
            )
        self._record("cold_start", seconds=seconds, migration_seconds=self.candidate.migration_seconds,
                     scaled_migration_seconds=scaled)

    def system_probe_check(self) -> None:
        gate = probe_instance(self.record, self.candidate.target(self.baseline),
                              revision=self.manifest["commit"], timeout=60, require_loaded=True)
        self._record("system_probe", gate=gate)

    def browser_check(self) -> None:
        base = self.layout.base(self.manifest["base"])
        python = self.request.browser_python or paths.venv_python(self.layout.version(self.request.candidate) / "venv")
        status = probe_browser(self.candidate.target(), python=str(python), browsers=base / "assets" / "playwright")
        self._record("browser_probe", status)

    def full_turn(self) -> None:
        endpoint = rehearsal.instance_endpoint(self.workspace.state_dir)
        try:
            self.turn_execution = rehearsal.run_turn(endpoint, self.turn_session, f"{TURN_MARKER} check the agent loop",
                                                     seconds=TURN_SECONDS)
        except TrialError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported as the check's reason
            raise TrialError(f"the agent turn failed: {type(exc).__name__}: {exc}"[:500]) from exc
        requests = self.model.turn_requests()
        kinds = [item["kind"] for item in requests]
        if kinds[:2] != ["tool_call", "final"]:
            raise TrialError(f"the agent loop did not make a tool call and finish: {kinds}")
        if requests[1].get("tool_result") in (None, ""):
            raise TrialError("the agent loop did not send the tool's result back to the model")
        messages = rehearsal.load_conversation(endpoint, self.turn_session)
        if not any(FINAL_ANSWER in json.dumps(item, default=str) for item in messages):
            raise TrialError("the agent turn was not saved to the conversation")
        self._record("full_turn", tool=requests[0]["tool"], messages=len(messages))

    def _test_command(self, check: Mapping[str, Any]) -> list[str]:
        argv = [str(part) for part in check["argv"]]
        refused = [part for part in argv[1:] if part.split("=", 1)[0] in _REFUSED_TEST_ARGUMENTS]
        if refused:
            raise TrialError(f"the verification plan uses arguments a version's Python cannot run: {refused}")
        python = paths.venv_python(self.layout.version(self.request.candidate) / "venv")
        return [str(python), "-I", "-B", str(self.request.worktree / argv[0]), *argv[1:]]

    def tests_check(self) -> None:
        if self.request.worktree is None or not self.request.tests:
            self._record("tests", "skipped")
            return
        results = []
        for check in self.request.tests:
            command = sandbox.wrap_command(self.candidate.policy, self._test_command(check),
                                           profile_path=self.workspace.profile,
                                           python=str(paths.base_python(self.layout.base(self.manifest["base"]))))
            try:
                done = subprocess.run(
                    command, cwd=self.request.worktree, env=self.candidate.environment(), stdin=subprocess.DEVNULL,
                    capture_output=True, timeout=float(check.get("timeout_seconds", 300)), text=True, errors="replace",
                )
            except subprocess.TimeoutExpired as exc:
                raise TrialError(f"test {check.get('id')} did not finish in time") from exc
            output = (done.stdout + done.stderr)[-_OUTPUT_LIMIT:]
            results.append({"id": check.get("id"), "code": done.returncode, "output": output})
            if done.returncode != 0:
                self._record("tests", "failed", results=results)
                raise TrialError(f"test {check.get('id')} failed with code {done.returncode}")
        self._record("tests", results=results)

    def desktop_check(self) -> None:
        shell = self.request.shell_executable
        main = self.layout.version(self.request.candidate) / "desktop" / "main.js"
        if shell is None or not shell.exists() or not main.is_file():
            self._record("desktop_smoke", "skipped")
            return
        environment = {**self.candidate.environment(), "OPENPROGRAM_TRIAL_DESKTOP_MAIN": str(main)}
        # The candidate's main process is confined like everything else it runs,
        # so Chromium's own sandbox, which cannot start inside this one, is off.
        command = sandbox.wrap_command(self.candidate.policy, [str(shell), "--disable-gpu", "--no-sandbox"],
                                       profile_path=self.workspace.profile,
                                       python=str(paths.base_python(self.layout.base(self.manifest["base"]))))
        try:
            done = subprocess.run(
                command, cwd=self.workspace.temporary, env=environment,
                stdin=subprocess.DEVNULL, capture_output=True, timeout=DESKTOP_SECONDS, text=True, errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
            if SHELL_STARTED not in output:
                self._desktop_unavailable("start")
                return
            raise TrialError("the desktop main process did not reach its ready handshake") from exc
        if SHELL_STARTED not in done.stdout:
            self._desktop_unavailable("start")
            return
        if WINDOW_TIMEOUT in done.stderr:
            # Chromium's helpers may be what the sandbox stopped, so a missing
            # window says nothing about the candidate; code that threw does.
            self._desktop_unavailable("open a window")
            return
        if done.returncode != 0 or "OPENPROGRAM_MAIN_READY" not in done.stdout:
            raise TrialError("the desktop main process did not reach its ready handshake")
        self._record("desktop_smoke")

    def _desktop_unavailable(self, step: str) -> None:
        """Nothing of the candidate was judged; the switch's relaunch handshake still is."""
        self.result.notes.append(f"The App could not {step} inside the trial's sandbox, so its desktop code was not tried there.")
        self._record("desktop_smoke", "unavailable")

    def schema_check(self) -> None:
        before = self.snapshot_after_old or self.snapshot_before_old
        baseline = self.snapshot_before_old if self.snapshot_after_old else None
        problems = schema_guard.compare_all(self.workspace.state_dir, before, baseline)
        problems += schema_guard.compare_logs(self.workspace.state_dir, self.logs_after_old or self.logs_before_old,
                                              self.logs_before_old if self.logs_after_old else None)
        if problems and self.destructive is None:
            self._record("schema_guard", "failed", problems=problems)
            raise TrialError("the candidate changed stored data in a way a rollback cannot undo: " + "; ".join(problems[:5]))
        self._record("schema_guard", "passed" if not problems else "allowed by trailer", problems=problems)

    def _run_self_test(self, argv: list[str], *, check_output: bool = True) -> dict:
        done = subprocess.run(argv, cwd=self.workspace.temporary, env=self.candidate.environment(),
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=180, text=True, errors="replace")
        if done.returncode != 0:
            raise TrialError(f"the candidate's updater failed its self-test: {done.stderr[-500:]}")
        if not check_output:
            return {}
        try:
            return json.loads(done.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError) as exc:
            raise TrialError("the candidate's updater gave no self-test result") from exc

    def _dummy_root(self) -> tuple[Layout, str]:
        """Two dummy versions in a root of their own."""
        root = Layout(self.workspace.directory / "updater-self-test")
        base_id = self.manifest["base"]
        python = paths.base_python(root.base(base_id))
        if sys.platform == "darwin":
            # The macOS launcher runs Python inside itself from the base's library.
            root.base(base_id).parent.mkdir(parents=True, exist_ok=True)
            root.base(base_id).symlink_to(self.layout.base(base_id), target_is_directory=True)
        else:
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"")
        for version in ("dummy-a", "dummy-b"):
            staging = root.versions / f".staging-{version}"
            binary = staging / "venv" / ("Scripts" if os.name == "nt" else "bin")
            site = paths.site_packages(staging, base_id) / "openprogram"
            site.mkdir(parents=True)
            (site / "__init__.py").write_text(f'VERSION = "{version}"\n', encoding="utf-8")
            (staging / "desktop").mkdir()
            (staging / "desktop" / "main.js").write_text("exports.start = () => {};\n", encoding="utf-8")
            binary.mkdir(parents=True, exist_ok=True)
            program = binary / "python"
            program.write_text(f'#!/bin/sh\necho "ran {version}"\n', encoding="utf-8")
            program.chmod(0o755)
            (staging / "venv" / "pyvenv.cfg").write_text(f"home = {python.parent}\n", encoding="utf-8")
            version_store.seal(root, staging, {
                "id": version, "commit": "0" * 40, "base": base_id, "shell_compat": 1, "built_by": "trial",
                "built_at": "1970-01-01T00:00:00Z", "components": [], "parent": None, "frontend_sha": None,
            })
        pointer.create(root, pointer.Pointer(generation=1, version="dummy-a", base=base_id, last_good="dummy-a",
                                             last_good_base=base_id, writer="trial"))
        return root, base_id

    def _launcher_reads(self, root: Layout, expected: str) -> None:
        if os.name == "nt":
            return  # Dummy versions are POSIX scripts; Windows stays unverified here.
        report = "import openprogram; print('ran', openprogram.VERSION)"
        done = subprocess.run([str(self.candidate.launcher), "--root", str(root.root), "-c", report],
                              capture_output=True, text=True, timeout=60, env=self.candidate.environment())
        if f"ran {expected}" not in done.stdout:
            raise TrialError(f"the launcher could not follow the pointer the candidate wrote to {expected}")

    def _plant_update(self, store: SelfUpdateStore) -> str:
        """An update in flight, in the trial's own ``self-updates/``, written by this old code."""
        from dataclasses import replace as _replace
        from ..types import UpdatePhase, VALID_TRANSITIONS

        request = _replace(self.record.request, update_id="su_trial_inflight", created_at=time.time(), timeout_seconds=10)
        store.create(request)
        path = [UpdatePhase.PREPARING]
        frontier = [[UpdatePhase.PREPARING]]
        while frontier and path[-1] is not UpdatePhase.ACTIVATING:
            path = frontier.pop(0)
            frontier += [path + [target] for source, target in VALID_TRANSITIONS
                         if source is path[-1] and target not in path]
        for phase in path[1:]:
            store.transition(request.update_id, phase, detail={})
        return request.update_id

    def updater_self_test(self) -> None:
        root, base_id = self._dummy_root()
        python = paths.venv_python(self.layout.version(self.request.candidate) / "venv")
        wrap = lambda argv: sandbox.wrap_command(  # noqa: E731
            self.candidate.policy, argv, profile_path=self.workspace.profile,
            python=str(paths.base_python(self.layout.base(self.manifest["base"]))))
        base = wrap([str(python), "-I", "-B", "-m", _SELF_TEST_MODULE])
        moved = self._run_self_test([*base, "switch", str(root.root), "dummy-b", base_id])
        if moved.get("version") != "dummy-b":
            raise TrialError("the candidate's updater did not move the pointer")
        self._launcher_reads(root, "dummy-b")
        returned = self._run_self_test([*base, "rollback", str(root.root), "dummy-b"])
        if returned.get("version") != "dummy-a":
            raise TrialError("the candidate's updater did not return the pointer")
        self._launcher_reads(root, "dummy-a")
        store = SelfUpdateStore(self.workspace.state_dir / "self-updates")
        update_id = self._plant_update(store)
        self._run_self_test(self.candidate.command("-m", _SELF_TEST_MODULE, "sweep"))
        record = store.load(update_id)  # The old code must still read what the candidate wrote.
        self._record("updater_self_test", swept_phase=record.state.phase.value)
        shutil.rmtree(root.root, ignore_errors=True)

    def rollback_rehearsal(self) -> None:
        self.candidate.stop()
        if self.destructive is not None:
            self.result.notes.append(
                f"This update carries a destructive migration ({self.destructive}), so rolling it back "
                "will need the backup taken at the switch."
            )
            self._record("rollback_rehearsal", "skipped", destructive=self.destructive)
            return
        if not self.old_started:
            self.result.notes.append("The old version could not start on the copied data, so rollback was not rehearsed.")
            self._record("rollback_rehearsal", "unavailable")
            return
        old = self._instance("rehearsal", self.request.previous, self.old_manifest)
        try:
            old.start()
            old.wait_ready()
            read = rehearsal.rehearse(old, self.record, baseline=self.baseline, session_id=self.turn_session,
                                      execution_id=self.turn_execution, reply=FINAL_ANSWER)
        finally:
            old.stop()
        self._record("rollback_rehearsal", **read)

    def verifier_check(self) -> None:
        if self.request.verifier is None:
            self._record("verifier", "skipped")
            return
        self.candidate = self._instance("verified", self.request.candidate, self.manifest)
        self.candidate.start()
        self.candidate.wait_ready()
        receipt = self.request.verifier(self.candidate, self.baseline)
        self._record("verifier", verdict=receipt.get("verdict"))

    def run(self) -> TrialResult:
        if self.result.isolation != "sandbox":
            self.result.notes.append(WINDOWS_NOTE if os.name == "nt" else
                                     "No OS sandbox was available for this trial: the copied data and separate port were the only isolation.")
        steps = (
            ("data_copy", self.prepare_data), ("baseline", self.baseline_check), ("cold_start", self.cold_start),
            ("system_probe", self.system_probe_check), ("browser_probe", self.browser_check),
            ("full_turn", self.full_turn), ("tests", self.tests_check), ("desktop_smoke", self.desktop_check),
            ("schema_guard", self.schema_check), ("updater_self_test", self.updater_self_test),
            ("rollback_rehearsal", self.rollback_rehearsal), ("verifier", self.verifier_check),
        )
        self.model.start()
        try:
            for name, step in steps:
                try:
                    step()
                except (TrialError, SystemProbeError) as exc:
                    self.result.failed_check, self.result.reason = name, str(exc)[:2000]
                    return self.result
                except Exception as exc:  # noqa: BLE001 - a failed check, never a crashed controller
                    self.result.failed_check = name
                    self.result.reason = f"{type(exc).__name__}: {exc}"[:2000]
                    return self.result
            self.result.passed = True
            return self.result
        finally:
            if self.candidate is not None:
                self.candidate.stop()
            self.model.stop()
            self._keep_logs()
            self.workspace.finish(passed=self.result.passed)

    def _keep_logs(self) -> None:
        for log in self.workspace.directory.glob("*.log"):
            shutil.copy2(log, self.evidence / log.name)
        worker = self.workspace.state_dir / "logs" / "worker.log"
        if worker.is_file():
            shutil.copy2(worker, self.evidence / "worker.log")


def run_trial(request: TrialRequest) -> TrialResult:
    """Every check of section 3.8, in order; the live instance is never touched."""
    return _Run(request).run()


__all__ = ["TrialRequest", "TrialResult", "plan_tests", "run_trial"]
