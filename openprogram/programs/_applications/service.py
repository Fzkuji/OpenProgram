"""Application operations owned by the server, not by an open browser view."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from pathlib import Path
import uuid

from openprogram.execution.model import ExecutionStatus as Status, TERMINAL_EXECUTION_STATUSES
from openprogram.execution.store import default_store
from . import catalog, state


class ApplicationService:
    def __init__(self):
        self.tasks: dict[str, asyncio.Task] = {}
        self.processes: dict[str, subprocess.Popen] = {}
        self.lock = asyncio.Lock()
        self.attempts = {}
        self.cancel_commands = {}
        from openprogram.execution.control import default_control_service
        from .driver import ApplicationDriver
        self.control = default_control_service()
        self.driver = ApplicationDriver(self)

    def describe(self, run_id: str) -> dict:
        with state.connect() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise FileNotFoundError("application operation not found")
            value = dict(row)
        instance = state.get_instance(value["instance_id"])
        catalog.get(instance["app_id"])
        record = default_store().get_execution(run_id)
        return {"id": run_id, "instance_id": instance["id"], "operation": value["operation"],
                "status": record.status.value if record else "interrupted",
                "result": json.loads(value["result"]) if value["result"] else None,
                "error": value["error"], "question": json.loads(value["question"]) if value["question"] else None}

    async def submit(self, instance_id: str, operation: str, value: dict, request_key: str, *, agent: bool = False) -> dict:
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 128:
            raise ValueError("request_key is required (1–128 characters)")
        instance = state.get_instance(instance_id)
        app = catalog.get(instance["app_id"])
        spec = app["operations"].get(operation)
        if spec is None or (agent and not spec.get("agent", False)):
            raise ValueError("operation is not available")
        from jsonschema import validate
        validate(value, spec.get("input", {}))
        fingerprint = hashlib.sha256(json.dumps([operation, value], sort_keys=True, allow_nan=False).encode()).hexdigest()
        async with self.lock:
            current = catalog.get(instance["app_id"])
            if current["digest"] != app["digest"]:
                raise ValueError("application changed during submission; reopen it")
            with state.connect() as db:
                old = db.execute("SELECT id,fingerprint FROM operations WHERE instance_id=? AND request_key=?", (instance_id, request_key)).fetchone()
                if old:
                    if old["fingerprint"] != fingerprint:
                        raise ValueError("request_key already used with different input")
                    return self.describe(old["id"])
            root = catalog.home() / "versions" / app["digest"]
            if await asyncio.to_thread(catalog.package_digest, root) != app["digest"]:
                raise ValueError("installed application content changed; reinstall from its source")
            store = default_store()
            revision = store.create_revision(manifest={"application": app["id"], "digest": app["digest"], "operation": operation})
            run_id = "app_" + uuid.uuid4().hex
            store.create_execution(session_id="app:" + instance_id, revision_id=revision.revision_id, execution_id=run_id)
            with state.connect() as db:
                db.execute("INSERT INTO operations(id,instance_id,request_key,fingerprint,definition,operation,input) VALUES(?,?,?,?,?,?,?)",
                           (run_id, instance_id, request_key, fingerprint, json.dumps(app), operation, json.dumps(value)))
            from openprogram.execution.driver import DriverBinding
            record = store.get_execution(run_id)
            attempt, leased = self.control.attempts.lease(run_id, expected_version=record.status_version,
                                                        owner_id="application-worker", ttl_seconds=60)
            attempt, _ = self.control.attempts.activate(attempt.attempt_id, generation=attempt.generation,
                                                       expected_execution_version=leased.status_version)
            self.control.attempts.set_process_owner(attempt.attempt_id, generation=attempt.generation, active=True)
            self.attempts[run_id] = attempt
            self.control.registry.bind(DriverBinding(run_id, attempt.attempt_id, attempt.generation, self.driver, attempt))
            state.emit(run_id, {"type": "accepted"})
            task = asyncio.create_task(self._execute(run_id, instance, app, operation, value))
            self.tasks[run_id] = task
            task.add_done_callback(lambda _: self.tasks.pop(run_id, None))
            return self.describe(run_id)

    def transition(self, run_id: str, target: Status):
        store = default_store()
        record = store.get_execution(run_id)
        if record and record.status not in TERMINAL_EXECUTION_STATUSES and record.status != target:
            attempt = self.attempts.get(run_id)
            if attempt and target in TERMINAL_EXECUTION_STATUSES:
                self.control.finish_attempt(attempt_id=attempt.attempt_id, generation=attempt.generation,
                    expected_execution_version=record.status_version, target=target, outcome=target.value,
                    command_id=self.cancel_commands.pop(run_id, None))
                self.attempts.pop(run_id, None)
            else:
                store.transition_execution(run_id, expected_version=record.status_version, target=target)
            state.emit(run_id, {"type": "status", "status": target.value})

    async def _execute(self, run_id, instance, app, operation, value):
        from openprogram._compat import ProcessTreeOwner
        owner = ProcessTreeOwner()
        process = None
        waiter = None
        async def heartbeat():
            while True:
                await asyncio.sleep(10)
                attempt = self.attempts.get(run_id)
                if attempt is None:
                    return
                self.control.attempts.heartbeat(attempt.attempt_id, generation=attempt.generation, ttl_seconds=60)
        lease = asyncio.create_task(heartbeat())
        try:
            executable = catalog.python_path(app["digest"])
            root = catalog.home() / "versions" / app["digest"]
            env = dict(os.environ)
            # Load only the installed framework package, not arbitrary project
            # directories inherited through PYTHONPATH.
            import openprogram
            env["PYTHONPATH"] = str(Path(openprogram.__file__).resolve().parent.parent)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            # Retain the OS tree handle independently of the leader's lifetime.
            # Spawn without an await so cancellation cannot lose the new owner.
            process = owner.popen(
                [str(executable), "-m", "openprogram.programs._applications.runner"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=str(root), env=env,
            )
            waiter = asyncio.create_task(asyncio.to_thread(process.wait))
            self.processes[run_id] = process
            process.stdin.write((json.dumps({"run_id": run_id, "instance": instance, "definition": app,
                "root": str(root), "operation": operation, "input": value, "model": app.get("model", "")}) + "\n").encode())
            await asyncio.to_thread(process.stdin.flush)
            outcome = None
            while line := await asyncio.to_thread(process.stdout.readline, 1024 * 1024 + 1):
                if len(line) > 1024 * 1024:
                    raise ValueError("application event exceeds 1 MiB")
                event = json.loads(line)
                if event.get("type") not in {"progress", "question", "result", "error"}:
                    raise ValueError("invalid application event")
                state.emit(run_id, event)
                if event["type"] == "question":
                    with state.connect() as db:
                        db.execute("UPDATE operations SET question=? WHERE id=?", (json.dumps(event), run_id))
                elif event["type"] in {"result", "error"}:
                    outcome = event
            await asyncio.shield(waiter)
            await self._stop(process, owner)
            if process.returncode or not outcome:
                raise RuntimeError(f"application process exited without a result (exit {process.returncode})")
            with state.connect() as db:
                db.execute("UPDATE operations SET result=?,error=?,question=NULL WHERE id=?",
                           (json.dumps(outcome.get("value")) if outcome["type"] == "result" else None,
                            outcome.get("message"), run_id))
            self.transition(run_id, Status.COMPLETED if outcome["type"] == "result" else Status.FAILED)
        except asyncio.CancelledError:
            if process:
                await self._stop(process, owner)
            record = default_store().get_execution(run_id)
            if record.status != Status.CANCELLING:
                with state.connect() as db:
                    db.execute("UPDATE operations SET error=? WHERE id=?", ("The worker stopped. Review saved progress before starting a new operation.", run_id))
            self.transition(run_id, Status.CANCELLED if record.status == Status.CANCELLING else Status.INTERRUPTED)
            raise
        except Exception as exc:
            if process:
                await self._stop(process, owner)
            with state.connect() as db:
                db.execute("UPDATE operations SET error=?,question=NULL WHERE id=?", (str(exc), run_id))
            self.transition(run_id, Status.FAILED)
        finally:
            if waiter:
                await asyncio.shield(waiter)
            if process:
                process.stdin.close()
                process.stdout.close()
            self.processes.pop(run_id, None)
            lease.cancel()
            await asyncio.gather(lease, return_exceptions=True)
            with state.connect() as db:
                db.execute("UPDATE operations SET question=NULL WHERE id=?", (run_id,))

    async def _stop(self, process, owner):
        # The leader may have exited while descendants still own stdout.
        await asyncio.to_thread(owner.terminate)
        await asyncio.to_thread(process.wait)

    async def _interrupt(self, task):
        # Every caller shares one cancellation. Shield the wait from a client
        # disconnect; neither repeated requests nor shutdown may cancel cleanup.
        if not task.done() and not task.cancelling():
            task.cancel()
        await asyncio.shield(asyncio.gather(task, return_exceptions=True))

    async def cancel(self, run_id: str):
        self.describe(run_id)
        if run_id in self.tasks:
            self.transition(run_id, Status.CANCELLING)
            task = self.tasks[run_id]
            await self._interrupt(task)
            self.transition(run_id, Status.CANCELLED)
            with state.connect() as db:
                db.execute("UPDATE operations SET question=NULL WHERE id=?", (run_id,))
        return self.describe(run_id)

    async def answer(self, run_id: str, request_id: str, answer):
        async with self.lock:
            run = self.describe(run_id)
            question = run["question"]
            process = self.processes.get(run_id)
            if run["status"] != Status.RUNNING.value or not question or question["request_id"] != request_id or process is None:
                raise ValueError("question is no longer waiting")
            process.stdin.write((json.dumps({"request_id": request_id, "answer": answer}) + "\n").encode())
            await asyncio.to_thread(process.stdin.flush)
            with state.connect() as db:
                db.execute("UPDATE operations SET question=NULL WHERE id=?", (run_id,))
            return self.describe(run_id)

    async def revoke(self, app_id: str):
        for run_id in list(self.tasks):
            run = self.describe(run_id)
            if state.get_instance(run["instance_id"])["app_id"] == app_id:
                await self.cancel(run_id)

    async def close(self):
        run_ids = list(self.tasks)
        tasks = list(self.tasks.values())
        await asyncio.gather(*(self._interrupt(task) for task in tasks))
        for run_id in run_ids:
            self.transition(run_id, Status.INTERRUPTED)

    def recover(self):
        with state.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM operations")]
        for run_id in ids:
            record = default_store().get_execution(run_id)
            if record and record.status not in TERMINAL_EXECUTION_STATUSES:
                if record.current_attempt_id:
                    self.control.recover_owner_loss(run_id)
                else:
                    self.transition(run_id, Status.CANCELLED if record.status == Status.CANCELLING else Status.INTERRUPTED)
                with state.connect() as db:
                    db.execute("UPDATE operations SET question=NULL,error=? WHERE id=?", ("The worker stopped. Review saved progress before starting a new operation.", run_id))
