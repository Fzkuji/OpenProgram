"""Real function steps survive version selection without replaying completed writes."""

from pathlib import Path
import threading
import time

import pytest

from openprogram.agentic_programming.function import agentic_function
from openprogram.execution import AttemptStore, ExecutionStore, RuntimeControlService
from openprogram.execution.driver import DriverRegistry
from openprogram.execution.model import CapabilitySet


def _first(folder):
    root = Path(folder)
    with (root / "effects").open("a") as stream:
        stream.write("first\n")
    (root / "entered").touch()
    limit = time.monotonic() + 5
    while not (root / "release").exists():
        if time.monotonic() > limit:
            raise TimeoutError("test release was not supplied")
        time.sleep(0.01)
    return "saved"


def _finish(folder, value):
    with (Path(folder) / "effects").open("a") as stream:
        stream.write(value + "\n")
    return value


def _version_a(folder):
    from openprogram.agentic_programming.continuation import step

    previous = step("first", _first, folder)
    return step("finish", _finish, folder, previous + "-A")


def _version_b(folder):
    from openprogram.agentic_programming.continuation import step

    previous = step("first", _first, folder)
    return step("finish", _finish, folder, previous + "-B")


def _version_removed(folder):
    from openprogram.agentic_programming.continuation import step

    return step("finish", _finish, folder, "unsafe")


def _version_changed_input(folder):
    from openprogram.agentic_programming.continuation import step

    previous = step("first", _first, folder + "-different")
    return step("finish", _finish, folder, previous + "-B")


def _nested_a(folder):
    from openprogram.agentic_programming.continuation import workflow

    return workflow("child", _version_a, folder)


def _nested_b(folder):
    from openprogram.agentic_programming.continuation import workflow

    return workflow("child", _version_b, folder)


def _loop_a(folder):
    from openprogram.agentic_programming.continuation import step

    values = []
    for index in range(2):
        values = values + [step("first", _first, folder)]
    return step("finish", _finish, folder, values[0] + "-" + values[1] + "-A")


def _loop_b(folder):
    from openprogram.agentic_programming.continuation import step

    values = []
    for index in range(2):
        values = values + [step("first", _first, folder)]
    return step("finish", _finish, folder, values[0] + "-" + values[1] + "-B")


def _changed_finish(folder, value):
    value = value + "-changed"
    with (Path(folder) / "effects").open("a") as stream:
        stream.write(value + "\n")
    return value


# Same function code, a replaced dependency, as after reloading its module.
import types

_changed_version = types.FunctionType(
    _version_a.__code__,
    {**_version_a.__globals__, "_finish": _changed_finish},
    "_version_a",
)


def _make_closure(value):
    def action():
        return value

    return action


_closure_one = _make_closure("one")
_closure_two = _make_closure("two")


def _with_closures(folder):
    from openprogram.agentic_programming.continuation import step

    previous = step("first", _first, folder)
    one = step("one", _closure_one)
    two = step("two", _closure_two)
    return step("finish", _finish, folder, previous + "-" + one + "-" + two)


@pytest.mark.parametrize(
    "policy,suffix,original_fn,replacement_fn,fresh_process",
    [
        ("keep_original", "A", _version_a, _version_b, False),
        ("keep_original", "A", _version_a, _version_b, True),
        ("use_latest", "B", _version_a, _version_b, True),
        ("use_latest", "B", _version_a, _version_b, False),
        ("keep_original", "one-two", _with_closures, _with_closures, False),
        ("use_latest", "A", _version_a, _version_a, False),
        ("keep_original", "A", _version_a, _changed_version, False),
        ("use_latest", "A-changed", _version_a, _changed_version, False),
        ("use_latest", None, _version_a, _version_removed, False),
        ("use_latest", None, _version_a, _version_changed_input, False),
        ("keep_original", "A", _nested_a, _nested_b, False),
        ("use_latest", "B", _nested_a, _nested_b, False),
        ("keep_original", "saved-A", _loop_a, _loop_b, False),
        ("use_latest", "saved-B", _loop_a, _loop_b, False),
    ],
)
def test_registered_function_resumes_selected_code_without_repeating_effect(
    tmp_path, monkeypatch, policy, suffix, original_fn, replacement_fn, fresh_process
):
    # Exercise the public decorator before any internal continuation imports.
    from openprogram.agentic_programming.function import _registry
    import importlib

    monkeypatch.setattr(
        importlib.import_module("openprogram.agentic_programming.function"),
        "_registry",
        dict(_registry),
    )
    original = agentic_function(
        original_fn, name="durable-demo", resumable=True, as_tool=False
    )
    replacement = agentic_function(
        replacement_fn, name="durable-demo", resumable=True, as_tool=False
    )
    from openprogram.agentic_programming.continuation import (
        FunctionSuspended,
        function_execution,
    )

    store = ExecutionStore(tmp_path / "executions.db")
    revision = store.create_revision(manifest={"entrypoint": "durable-demo"})
    execution = store.create_execution(
        execution_id="execution",
        run_id="run",
        session_id="session",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(
            pause=True,
            safe_point_kinds=("function.step.after",),
            state_schema_version=1,
        ),
    )
    attempts = AttemptStore(store)
    leased, execution = attempts.lease(
        "execution",
        expected_version=execution.status_version,
        owner_id="worker",
        ttl_seconds=30,
    )
    active, execution = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=execution.status_version,
    )
    service = RuntimeControlService(store, attempts, DriverRegistry())
    outcomes = []

    def run():
        try:
            with function_execution(
                store,
                attempt_id=active.attempt_id,
                generation=active.generation,
                call_key="original-call",
                policy=policy,
            ):
                original(str(tmp_path))
        except BaseException as exc:
            outcomes.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "entered").exists():
            assert time.monotonic() < deadline, outcomes
            time.sleep(0.01)
        import asyncio

        current = store.get_execution("execution")
        asyncio.run(
            service.request_pause(
                command_id="pause",
                execution_id="execution",
                expected_version=current.status_version,
                actor={"surface": "test"},
            )
        )
    finally:
        (tmp_path / "release").touch()
        thread.join(5)
    assert not thread.is_alive()
    assert len(outcomes) == 1 and isinstance(outcomes[0], FunctionSuspended), outcomes
    assert (tmp_path / "effects").read_text().splitlines() == ["first"]
    paused = store.get_execution("execution")
    assert paused.status.value == "paused"
    import asyncio

    asyncio.run(
        service.request_continue(
            command_id="continue",
            execution_id="execution",
            expected_version=paused.status_version,
            actor={"surface": "test"},
        )
    )
    resumed = store.get_execution("execution")
    fresh_store = ExecutionStore(tmp_path / "executions.db")
    if fresh_process:
        import json
        import subprocess
        import sys

        script = """
import json, sys
from openprogram.execution import ExecutionStore
from openprogram.agentic_programming.continuation import function_execution
from openprogram.agentic_programming.function import agentic_function
import tests.integration.execution.test_function_version_resume as source
folder, policy = sys.argv[1:]
del source._version_a
store = ExecutionStore(folder + '/executions.db')
execution = store.get_execution('execution')
replacement = agentic_function(source._version_b, name='durable-demo', as_tool=False, resumable=True)
with function_execution(store, attempt_id=execution.current_attempt_id, generation=execution.owner_lease['generation'], call_key='original-call', policy=policy):
    result = replacement(folder)
print(json.dumps(result))
"""
        completed = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), policy],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert completed.returncode == 0, completed.stderr
        assert (
            json.loads(completed.stdout.strip().splitlines()[-1]) == "saved-" + suffix
        )
        assert (tmp_path / "effects").read_text().splitlines() == [
            "first",
            "saved-" + suffix,
        ]
        return
    if suffix is None:
        from openprogram.agentic_programming.continuation import (
            FunctionCompatibilityError,
        )

        with pytest.raises(FunctionCompatibilityError):
            with function_execution(
                fresh_store,
                attempt_id=resumed.current_attempt_id,
                generation=resumed.owner_lease["generation"],
                call_key="original-call",
                policy=policy,
            ):
                replacement(str(tmp_path))
        assert fresh_store.get_execution("execution").status.value == "paused"
        assert (tmp_path / "effects").read_text().splitlines() == ["first"]
        blocked = fresh_store.get_execution("execution")
        asyncio.run(
            service.request_continue(
                command_id="choose-original",
                execution_id="execution",
                expected_version=blocked.status_version,
                actor={"surface": "test"},
                code_change_policy="keep_original",
            )
        )
        resumed = fresh_store.get_execution("execution")
        from openprogram.agentic_programming.continuation import default_policy

        with function_execution(
            fresh_store,
            attempt_id=resumed.current_attempt_id,
            generation=resumed.owner_lease["generation"],
            call_key="original-call",
            policy=default_policy(fresh_store, "execution"),
        ):
            assert replacement(str(tmp_path)) == "saved-A"
        assert (tmp_path / "effects").read_text().splitlines() == ["first", "saved-A"]
        return
    with function_execution(
        fresh_store,
        attempt_id=resumed.current_attempt_id,
        generation=resumed.owner_lease["generation"],
        call_key="original-call",
        policy=policy,
    ):
        result = replacement(str(tmp_path))
        assert replacement(str(tmp_path)) == result
    assert result == "saved-" + suffix
    first_count = 2 if original_fn is _loop_a else 1
    assert (tmp_path / "effects").read_text().splitlines() == [
        "first"
    ] * first_count + ["saved-" + suffix]


def _parallel_version_a(left, right):
    from openprogram.agentic_programming.continuation import parallel

    return parallel(
        {"left": (_version_a, (left,), {}), "right": (_version_a, (right,), {})}
    )


def _parallel_version_b(left, right):
    from openprogram.agentic_programming.continuation import parallel

    return parallel(
        {"left": (_version_b, (left,), {}), "right": (_version_b, (right,), {})}
    )


def _active_execution(tmp_path):
    store = ExecutionStore(tmp_path / "state.db")
    revision = store.create_revision(manifest={"entrypoint": "parallel"})
    execution = store.create_execution(
        session_id="session",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(
            pause=True,
            safe_point_kinds=("function.step.after",),
            state_schema_version=1,
        ),
    )
    attempts = AttemptStore(store)
    leased, execution = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="owner",
        ttl_seconds=30,
    )
    active, execution = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=execution.status_version,
    )
    return store, active, RuntimeControlService(store, attempts, DriverRegistry())


@pytest.mark.parametrize("policy,suffix", [("keep_original", "A"), ("use_latest", "B")])
def test_parallel_branches_commit_before_pause_and_resume_independently(
    tmp_path, policy, suffix
):
    import asyncio
    from openprogram.agentic_programming.continuation import (
        FunctionSuspended,
        function_execution,
        invoke,
    )

    store, active, service = _active_execution(tmp_path)
    folders = [tmp_path / name for name in ("left", "right")]
    for folder in folders:
        folder.mkdir()
    args = tuple(str(folder) for folder in folders)
    outcomes = []

    def run():
        try:
            with function_execution(
                store,
                attempt_id=active.attempt_id,
                generation=active.generation,
                call_key="parallel",
                policy=policy,
            ):
                invoke(_parallel_version_a, "parallel", args, {})
        except BaseException as exc:
            outcomes.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not all((folder / "entered").exists() for folder in folders):
            assert time.monotonic() < deadline, outcomes
            time.sleep(0.01)
        execution = store.get_execution(active.execution_id)
        asyncio.run(
            service.request_pause(
                command_id="pause",
                execution_id=execution.execution_id,
                expected_version=execution.status_version,
                actor={"surface": "test"},
            )
        )
    finally:
        for folder in folders:
            (folder / "release").touch()
        thread.join(5)
    assert not thread.is_alive()
    assert len(outcomes) == 1 and isinstance(outcomes[0], FunctionSuspended), outcomes
    execution = store.get_execution(active.execution_id)
    assert execution.status.value == "paused"
    asyncio.run(
        service.request_continue(
            command_id="continue",
            execution_id=execution.execution_id,
            expected_version=execution.status_version,
            actor={"surface": "test"},
        )
    )
    execution = store.get_execution(active.execution_id)
    with function_execution(
        store,
        attempt_id=execution.current_attempt_id,
        generation=execution.owner_lease["generation"],
        call_key="parallel",
        policy=policy,
    ):
        result = invoke(_parallel_version_b, "parallel", args, {})
    assert result == {"left": "saved-" + suffix, "right": "saved-" + suffix}
    for folder in folders:
        assert (folder / "effects").read_text().splitlines() == [
            "first",
            "saved-" + suffix,
        ]


def _uncertain_action(folder):
    _finish(folder, "external-write")
    raise RuntimeError("connection lost before receipt")


def _uncertain_function(folder):
    from openprogram.agentic_programming.continuation import step

    return step("write", _uncertain_action, folder)


def test_owner_loss_never_reissues_unconfirmed_function_write(tmp_path):
    from openprogram.agentic_programming.continuation import function_execution, invoke

    store, active, service = _active_execution(tmp_path)
    with pytest.raises(RuntimeError, match="connection lost"):
        with function_execution(
            store,
            attempt_id=active.attempt_id,
            generation=active.generation,
            call_key="unknown",
        ):
            invoke(_uncertain_function, "unknown", (str(tmp_path),), {})
    recovered = service.recover_owner_loss(
        active.execution_id, attempt_id=active.attempt_id, generation=active.generation
    )
    assert recovered.execution.status.value == "reconciliation_required"
    assert (tmp_path / "effects").read_text().splitlines() == ["external-write"]


def _stage(folder, name):
    path = Path(folder)
    with (path / "effects").open("a") as stream:
        stream.write(name + "\n")
    (path / (name + "-entered")).touch()
    deadline = time.monotonic() + 5
    while not (path / (name + "-release")).exists():
        if time.monotonic() > deadline:
            raise TimeoutError(name)
        time.sleep(0.01)
    return name


def _three_a(folder):
    from openprogram.agentic_programming.continuation import step

    first = step("first", _stage, folder, "first")
    second = step("second", _stage, folder, "second")
    return step("finish", _finish, folder, first + second + "-A")


def _three_b(folder):
    from openprogram.agentic_programming.continuation import step

    first = step("first", _stage, folder, "first")
    second = step("second", _stage, folder, "second")
    return step("finish", _finish, folder, first + second + "-B")


def _three_c(folder):
    from openprogram.agentic_programming.continuation import step

    first = step("first", _stage, folder, "first")
    second = step("second", _stage, folder, "second")
    return step("finish", _finish, folder, first + second + "-C")


def _nested_three_a(folder):
    from openprogram.agentic_programming.continuation import workflow

    return workflow("child", _three_a, folder) + "-outer-A"


def _nested_three_b(folder):
    from openprogram.agentic_programming.continuation import workflow

    return workflow("child", _three_b, folder) + "-outer-B"


def _nested_three_c(folder):
    from openprogram.agentic_programming.continuation import workflow

    return workflow("child", _three_c, folder) + "-outer-C"


@pytest.mark.parametrize(
    "versions,expected",
    [
        ((_three_a, _three_b, _three_c), "firstsecond-B"),
        ((_nested_three_a, _nested_three_b, _nested_three_c), "firstsecond-B-outer-B"),
    ],
)
def test_second_restart_retains_last_adopted_version(tmp_path, versions, expected):
    import asyncio
    from openprogram.agentic_programming.continuation import (
        FunctionSuspended,
        function_execution,
        invoke,
    )

    store, active, service = _active_execution(tmp_path)

    def phase(fn, policy, stage):
        execution = store.get_execution(active.execution_id)
        outcomes = []

        def run():
            try:
                with function_execution(
                    store,
                    attempt_id=execution.current_attempt_id,
                    generation=execution.owner_lease["generation"],
                    call_key="multi",
                    policy=policy,
                ):
                    invoke(fn, "multi", (str(tmp_path),), {})
            except BaseException as exc:
                outcomes.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not (tmp_path / (stage + "-entered")).exists():
                assert time.monotonic() < deadline, outcomes
                time.sleep(0.01)
            current = store.get_execution(active.execution_id)
            asyncio.run(
                service.request_pause(
                    command_id="pause-" + stage,
                    execution_id=current.execution_id,
                    expected_version=current.status_version,
                    actor={"surface": "test"},
                )
            )
        finally:
            (tmp_path / (stage + "-release")).touch()
            thread.join(5)
        assert not thread.is_alive()
        assert len(outcomes) == 1 and isinstance(outcomes[0], FunctionSuspended), (
            outcomes
        )
        paused = store.get_execution(active.execution_id)
        assert paused.status.value == "paused"
        asyncio.run(
            service.request_continue(
                command_id="continue-" + stage,
                execution_id=paused.execution_id,
                expected_version=paused.status_version,
                actor={"surface": "test"},
            )
        )

    phase(versions[0], "keep_original", "first")
    phase(versions[1], "use_latest", "second")
    execution = store.get_execution(active.execution_id)
    with function_execution(
        store,
        attempt_id=execution.current_attempt_id,
        generation=execution.owner_lease["generation"],
        call_key="multi",
        policy="keep_original",
    ):
        assert invoke(versions[2], "multi", (str(tmp_path),), {}) == expected
    assert (tmp_path / "effects").read_text().splitlines() == [
        "first",
        "second",
        "firstsecond-B",
    ]


_hidden_counter = 0


def _hidden_state_action(folder):
    global _hidden_counter
    _hidden_counter += 1
    return _finish(folder, str(_hidden_counter))


def _hidden_state_function(folder):
    from openprogram.agentic_programming.continuation import step

    return step("write", _hidden_state_action, folder)


def test_hidden_mutable_state_is_rejected_before_external_work(tmp_path, monkeypatch):
    import importlib

    function_module = importlib.import_module(
        "openprogram.agentic_programming.function"
    )
    monkeypatch.setattr(function_module, "_registry", dict(function_module._registry))
    from openprogram.agentic_programming.continuation import (
        FunctionCompatibilityError,
        function_execution,
    )

    store, active, _service = _active_execution(tmp_path)
    function = agentic_function(_hidden_state_function, resumable=True, as_tool=False)
    with pytest.raises(FunctionCompatibilityError, match="Mutable helper state"):
        with function_execution(
            store,
            attempt_id=active.attempt_id,
            generation=active.generation,
            call_key="stateful",
        ):
            function(str(tmp_path))
    assert not (tmp_path / "effects").exists()
    assert store.get_execution(active.execution_id).status.value == "paused"
    assert _hidden_counter == 0


def _local_import_action(folder):
    from pathlib import Path

    Path(folder, "effects").write_text("unexpected")
    return "written"


def _local_import_function(folder):
    from openprogram.agentic_programming.continuation import step

    return step("write", _local_import_action, folder)


def test_untracked_step_import_is_rejected_before_external_work(tmp_path, monkeypatch):
    import importlib

    function_module = importlib.import_module(
        "openprogram.agentic_programming.function"
    )
    monkeypatch.setattr(function_module, "_registry", dict(function_module._registry))
    from openprogram.agentic_programming.continuation import (
        FunctionCompatibilityError,
        function_execution,
    )

    store, active, _service = _active_execution(tmp_path)
    function = agentic_function(_local_import_function, resumable=True, as_tool=False)
    with pytest.raises(FunctionCompatibilityError, match="module scope"):
        with function_execution(
            store,
            attempt_id=active.attempt_id,
            generation=active.generation,
            call_key="local-import",
        ):
            function(str(tmp_path))
    assert not (tmp_path / "effects").exists()
    assert store.get_execution(active.execution_id).status.value == "paused"


def _package_action(folder):
    return _finish(folder, _package_dependency.suffix)


def _package_function(folder):
    from openprogram.agentic_programming.continuation import step

    return step("write", _package_action, folder)


def test_opaque_package_is_rejected_before_external_work(tmp_path, monkeypatch):
    import importlib
    import sys

    function_module = importlib.import_module(
        "openprogram.agentic_programming.function"
    )
    monkeypatch.setattr(function_module, "_registry", dict(function_module._registry))
    from openprogram.agentic_programming.continuation import (
        FunctionCompatibilityError,
        function_execution,
    )

    package = tmp_path / "durable_dependency"
    package.mkdir()
    (package / "__init__.py").write_text("from .impl import suffix\n")
    (package / "impl.py").write_text("suffix = 'A'\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        monkeypatch.setitem(
            globals(),
            "_package_dependency",
            importlib.import_module("durable_dependency"),
        )
        store, active, _service = _active_execution(tmp_path)
        function = agentic_function(_package_function, resumable=True, as_tool=False)
        with pytest.raises(FunctionCompatibilityError, match="Opaque"):
            with function_execution(
                store,
                attempt_id=active.attempt_id,
                generation=active.generation,
                call_key="package",
            ):
                function(str(tmp_path))
        assert not (tmp_path / "effects").exists()
        assert store.get_execution(active.execution_id).status.value == "paused"
    finally:
        sys.modules.pop("durable_dependency.impl", None)
        sys.modules.pop("durable_dependency", None)
