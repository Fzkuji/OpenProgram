import multiprocessing
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.runtime.agent_registry import manager
from openprogram.interfaces.server.routes.catalog import agents


def _client(tmp_path, monkeypatch, *, raise_server_exceptions: bool = True) -> TestClient:
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    manager.create("main", name="Default Agent", make_default=True)
    app = FastAPI()
    agents.register(app)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _create_agent_process(root: str, results) -> None:
    manager._state_root = lambda: Path(root)
    try:
        results.put(("ok", manager.create_from_name("Concurrent Agent").id))
    except Exception as exc:  # pragma: no cover - assertion reports the child error
        results.put(("error", repr(exc)))


def test_agent_tools_can_be_read_and_updated(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    detail = client.get("/api/agents/main")
    assert detail.status_code == 200
    assert detail.json()["agent"]["id"] == "main"

    response = client.patch(
        "/api/agents/main",
        json={"updated_at": detail.json()["agent"]["updated_at"],
              "tools": {"mode": "selected", "allowed": ["read", "paper_agent"]}},
    )
    assert response.status_code == 200
    assert response.json()["agent"]["tools"] == {
        "mode": "selected",
        "allowed": ["read", "paper_agent"],
    }
    assert manager.get("main").tools == response.json()["agent"]["tools"]

    response = client.patch(
        "/api/agents/main",
        json={"updated_at": response.json()["agent"]["updated_at"],
              "tools": {"mode": "automatic"}},
    )
    assert response.json()["agent"]["tools"] == {"mode": "automatic"}


def test_agent_tools_route_rejects_invalid_policy(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.patch(
        "/api/agents/main",
        json={"tools": {"mode": "selected", "allowed": "read"}},
    )
    assert response.status_code == 400
    assert manager.get("main").tools == {"mode": "automatic"}


def test_agent_tools_route_returns_404_for_unknown_agent(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/agents/missing").status_code == 404
    assert client.patch(
        "/api/agents/missing",
        json={"tools": {"mode": "none"}},
    ).status_code == 404


def test_agent_lifecycle_and_complete_configuration(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    created = client.post(
        "/api/agents",
        json={"id": "research", "name": "Research Agent"},
    )
    assert created.status_code == 201
    assert created.json()["agent"]["id"] == "research"
    assert not (tmp_path / "agents" / "research" / "workspace").exists()

    version = created.json()["agent"]["updated_at"]
    updated = client.patch(
        "/api/agents/research",
        json={
            "updated_at": version,
            "name": "Research",
            "model": {"provider": "openai", "id": "gpt-5.4"},
            "thinking_effort": "high",
            "system_prompt": "Research carefully.",
            "identity": {
                "name": "Research",
                "mention_patterns": ["@research", "@papers"],
            },
            "skills": {
                "allowed": ["research-*"],
                "disabled": ["research-live"],
                "categories": ["research"],
            },
            "tools": {"mode": "selected", "allowed": ["read", "web_search"]},
            "mcp": {
                "allowed": ["filesystem", "browser"],
                "disabled": ["linear"],
                "required": ["filesystem"],
            },
            "session_scope": "per-peer",
            "session_idle_minutes": 120,
            "session_daily_reset": "04:00",
        },
    )
    assert updated.status_code == 200
    agent = updated.json()["agent"]
    assert agent["model"] == {"provider": "openai", "id": "gpt-5.4"}
    assert agent["skills"]["categories"] == ["research"]
    assert agent["mcp"]["required"] == ["filesystem"]
    assert agent["session_scope"] == "per-peer"

    made_default = client.post("/api/agents/research/default")
    assert made_default.status_code == 200
    assert made_default.json()["agent"]["default"] is True
    assert manager.get("main").default is False

    blocked = client.delete("/api/agents/research")
    assert blocked.status_code == 409

    deleted = client.delete("/api/agents/main")
    assert deleted.status_code == 204
    assert manager.get("main") is None


def test_agent_creation_generates_unique_ids_from_the_name(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    first = client.post("/api/agents", json={"name": "Research Agent"})
    second = client.post("/api/agents", json={"name": "Research Agent"})
    non_latin = client.post("/api/agents", json={"name": "研究助手"})

    assert first.status_code == 201
    assert first.json()["agent"]["id"] == "research-agent"
    assert second.status_code == 201
    assert second.json()["agent"]["id"] == "research-agent-2"
    assert non_latin.status_code == 201
    assert non_latin.json()["agent"]["id"] == "agent"


def test_agent_creation_requires_only_a_non_empty_name(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    missing = client.post("/api/agents", json={})

    assert missing.status_code == 400
    assert missing.json()["error"] == "name must be a string"


def test_explicit_agent_ids_remain_backward_compatible(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    created = client.post("/api/agents", json={"id": "legacy-agent"})
    duplicated = client.post(
        "/api/agents/main/duplicate", json={"id": "legacy-copy"},
    )

    assert created.status_code == 201
    assert created.json()["agent"]["name"] == "Legacy Agent"
    assert duplicated.status_code == 201
    assert duplicated.json()["agent"]["name"] == "Legacy Copy"


def test_name_only_creation_is_unique_across_processes(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    processes = [
        context.Process(target=_create_agent_process, args=(str(tmp_path), results))
        for _ in range(8)
    ]
    for process in processes:
        process.start()
    rows = [results.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)

    assert all(process.exitcode == 0 for process in processes)
    assert all(status == "ok" for status, _ in rows), rows
    assert {value for _, value in rows} == {
        "concurrent-agent",
        "concurrent-agent-2",
        "concurrent-agent-3",
        "concurrent-agent-4",
        "concurrent-agent-5",
        "concurrent-agent-6",
        "concurrent-agent-7",
        "concurrent-agent-8",
    }


def test_agent_update_rejects_conflicts_and_invalid_values(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    current = client.get("/api/agents/main").json()["agent"]

    stale = client.patch(
        "/api/agents/main",
        json={"updated_at": current["updated_at"] - 1, "name": "Stale"},
    )
    assert stale.status_code == 409
    assert manager.get("main").name == "Default Agent"

    invalid = client.patch(
        "/api/agents/main",
        json={
            "session_scope": "global",
            "session_idle_minutes": -1,
            "session_daily_reset": "25:00",
        },
    )
    assert invalid.status_code == 400

    contradictory = client.patch(
        "/api/agents/main",
        json={
            "mcp": {
                "allowed": ["filesystem"],
                "disabled": ["filesystem"],
                "required": ["filesystem"],
            },
        },
    )
    assert contradictory.status_code == 400


def test_agent_duplicate_copies_configuration_without_sessions(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.patch(
        "/api/agents/main",
        json={
            "updated_at": client.get("/api/agents/main").json()["agent"]["updated_at"],
            "model": {"provider": "openai", "id": "gpt-5.4"},
            "tools": {"mode": "none"},
            "system_prompt": "Copied instructions.",
        },
    )
    source_session = tmp_path / "agents" / "main" / "sessions" / "history.json"
    source_session.write_text("{}", encoding="utf-8")

    response = client.post(
        "/api/agents/main/duplicate",
        json={"id": "main_copy", "name": "Main Copy"},
    )
    assert response.status_code == 201
    duplicate = response.json()["agent"]
    assert duplicate["model"] == {"provider": "openai", "id": "gpt-5.4"}
    assert duplicate["tools"] == {"mode": "none"}
    assert duplicate["system_prompt"] == "Copied instructions."
    assert not (
        tmp_path / "agents" / "main_copy" / "sessions" / "history.json"
    ).exists()

    project = tmp_path / "repo"
    (project / ".git").mkdir(parents=True)
    (project / "pkg").mkdir()
    (project / "AGENTS.md").write_text("root rules", encoding="utf-8")
    (project / "pkg" / "AGENTS.md").write_text("pkg rules", encoding="utf-8")
    monkeypatch.setenv("OPENPROGRAM_WORKDIR", str(project / "pkg"))
    from openprogram.runtime.context import agents_md as agents_md_module
    monkeypatch.setattr(agents_md_module, "global_path", lambda: tmp_path / "AGENTS.md")
    agents_md = client.get("/api/agents-md")
    assert agents_md.status_code == 200
    payload = agents_md.json()
    assert payload["global"]["exists"] is False
    assert Path(payload["global"]["path"]).name == "AGENTS.md"
    assert payload["project_root"] == str(project.resolve())
    assert payload["loaded"] == [
        str(project.resolve() / "AGENTS.md"),
        str(project.resolve() / "pkg" / "AGENTS.md"),
    ]

    generated = client.post(
        "/api/agents/main/duplicate",
        json={"name": "Main Copy"},
    )
    generated_again = client.post(
        "/api/agents/main/duplicate",
        json={"name": "Main Copy"},
    )
    assert generated.status_code == 201
    assert generated.json()["agent"]["id"] == "main-copy"
    assert generated_again.status_code == 201
    assert generated_again.json()["agent"]["id"] == "main-copy-2"
    assert generated.json()["agent"]["system_prompt"] == "Copied instructions."


def test_agent_duplicate_rolls_back_when_registry_write_fails(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, raise_server_exceptions=False)
    original_write_index = manager._write_index

    def fail_write_index(data) -> None:
        raise OSError("simulated registry failure")

    monkeypatch.setattr(manager, "_write_index", fail_write_index)
    response = client.post(
        "/api/agents/main/duplicate", json={"name": "Broken Copy"},
    )
    monkeypatch.setattr(manager, "_write_index", original_write_index)

    assert response.status_code == 500
    assert manager.get("broken-copy") is None
    assert not (tmp_path / "agents" / "broken-copy").exists()
    assert [agent.id for agent in manager.list_all()] == ["main"]


def test_agent_duplicate_preserves_preexisting_incomplete_directory(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    existing = tmp_path / "agents" / "reserved-copy"
    existing.mkdir(parents=True)
    marker = existing / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    explicit = client.post(
        "/api/agents/main/duplicate", json={"id": "reserved-copy"},
    )
    generated = client.post(
        "/api/agents/main/duplicate", json={"name": "Reserved Copy"},
    )

    assert explicit.status_code == 400
    assert marker.read_text(encoding="utf-8") == "keep"
    assert generated.status_code == 201
    assert generated.json()["agent"]["id"] == "reserved-copy-2"


def test_agent_same_timestamp_updates_cannot_both_commit(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    original = client.get("/api/agents/main").json()["agent"]
    barrier = threading.Barrier(2)
    validate = agents._validated_patch

    def synchronized_validation(raw):
        result = validate(raw)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(agents, "_validated_patch", synchronized_validation)
    changes = [
        {"model": {"provider": "openai", "id": "model-a"},
         "tools": {"mode": "selected", "allowed": ["read"]}},
        {"model": {"provider": "openai", "id": "model-b"},
         "tools": {"mode": "none"}},
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            lambda patch: client.patch("/api/agents/main", json={
                "updated_at": original["updated_at"], **patch,
            }),
            changes,
        ))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json()["agent"] for response in responses if response.status_code == 200)
    conflict = next(response.json() for response in responses if response.status_code == 409)
    assert conflict["agent"] == winner
    assert client.get("/api/agents/main").json()["agent"] == winner


def test_agent_revision_description_and_required_precondition(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    original = client.get("/api/agents/main").json()["agent"]
    assert original["revision"] == 1
    missing = client.patch("/api/agents/main", json={"name": "Not saved"})
    assert missing.status_code == 428
    saved = client.patch("/api/agents/main", json={
        "expected_revision": original["revision"],
        "description": "General assistant", "thinking_effort": "",
    })
    assert saved.status_code == 200
    result = saved.json()["agent"]
    assert result["revision"] == original["revision"] + 1
    assert result["description"] == "General assistant"
    assert result["thinking_effort"] == ""
    stale = client.patch("/api/agents/main", json={
        "expected_revision": original["revision"], "name": "Stale",
    })
    assert stale.status_code == 409
    assert stale.json()["agent"] == result


def test_agent_model_and_tools_save_is_one_atomic_replace(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, raise_server_exceptions=False)
    original = client.get("/api/agents/main").json()["agent"]
    replace = manager.os.replace
    writes = []

    def fail_replacement(source, destination):
        if Path(destination).name == "agent.json":
            writes.append(destination)
            raise OSError("simulated configuration write failure")
        return replace(source, destination)

    monkeypatch.setattr(manager.os, "replace", fail_replacement)
    failed = client.patch("/api/agents/main", json={
        "updated_at": original["updated_at"],
        "model": {"provider": "openai", "id": "model-a"},
        "tools": {"mode": "selected", "allowed": ["read"]},
    })
    assert failed.status_code == 500
    assert client.get("/api/agents/main").json()["agent"] == original
    assert len(writes) == 1
    assert not list((tmp_path / "agents" / "main").glob("*.tmp"))

    def record_replacement(source, destination):
        if Path(destination).name == "agent.json":
            writes.append(destination)
        return replace(source, destination)

    writes.clear()
    monkeypatch.setattr(manager.os, "replace", record_replacement)
    saved = client.patch("/api/agents/main", json={
        "updated_at": original["updated_at"],
        "model": {"provider": "openai", "id": "model-a"},
        "tools": {"mode": "none"},
    })
    assert saved.status_code == 200
    assert len(writes) == 1


def test_agent_list_failure_is_not_an_empty_library(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, raise_server_exceptions=False)

    def unavailable():
        raise OSError("registry read failed")

    monkeypatch.setattr(manager, "list_all", unavailable)
    assert client.get("/api/agents").status_code == 500


def _conditional_agent_patch_process(root, revision, label, barrier, results):
    manager._state_root = lambda: Path(root)
    app = FastAPI()
    agents.register(app)
    validate = agents._validated_patch

    def synchronized_validation(raw):
        result = validate(raw)
        barrier.wait(timeout=15)
        return result

    agents._validated_patch = synchronized_validation
    with TestClient(app) as client:
        response = client.patch("/api/agents/main", json={
            "expected_revision": revision,
            "model": {"provider": "openai", "id": label},
            "tools": {"mode": "selected", "allowed": [label]},
        })
        results.put((response.status_code, response.json()))


def test_agent_same_revision_is_atomic_across_processes(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    original = client.get("/api/agents/main").json()["agent"]
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    barrier = context.Barrier(2)
    processes = [context.Process(
        target=_conditional_agent_patch_process,
        args=(str(tmp_path), original["revision"], label, barrier, results),
    ) for label in ("first", "second")]
    try:
        for process in processes:
            process.start()
        rows = [results.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=20)
        assert all(process.exitcode == 0 for process in processes)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        results.close()
        results.join_thread()
    assert sorted(status for status, _ in rows) == [200, 409]
    saved = client.get("/api/agents/main").json()["agent"]
    assert saved["revision"] == original["revision"] + 1
    assert saved["tools"]["allowed"] == [saved["model"]["id"]]
    assert all(body["agent"] == saved for _, body in rows)


def test_legacy_record_is_not_rewritten_on_read_and_preserves_internal_merge(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    path = tmp_path / "agents" / "main" / "agent.json"
    raw = json.loads(path.read_text())
    raw.pop("revision")
    raw.pop("description")
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    loaded = client.get("/api/agents/main").json()["agent"]
    assert loaded["revision"] == 1
    assert loaded["description"] == ""
    assert loaded["thinking_effort"] == "medium"
    assert path.read_bytes() == before
    with manager.configuration_lock():
        manager.update("main", {"tools": {"disabled": ["write"]}})
    assert manager.get("main").tools == {"mode": "automatic", "disabled": ["write"]}
    assert manager.get("main").revision == 2


@pytest.mark.parametrize("field,value", [
    ("expected_revision", True), ("expected_revision", -1),
    ("expected_revision", "1"), ("updated_at", True), ("updated_at", "1"),
])
def test_agent_invalid_precondition_is_rejected(tmp_path, monkeypatch, field, value) -> None:
    client = _client(tmp_path, monkeypatch)
    original = client.get("/api/agents/main").json()["agent"]
    response = client.patch("/api/agents/main", json={field: value, "name": "Changed"})
    assert response.status_code == 400
    assert client.get("/api/agents/main").json()["agent"] == original


@pytest.mark.parametrize("target", ["record", "index"])
def test_agent_list_does_not_hide_storage_read_failures(tmp_path, monkeypatch, target):
    client = _client(tmp_path, monkeypatch, raise_server_exceptions=False)
    path = manager._agent_file("main") if target == "record" else manager._index_file()
    read_text = Path.read_text
    def failing_read(self, *args, **kwargs):
        if self == path:
            raise OSError("storage unavailable")
        return read_text(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", failing_read)
    assert client.get("/api/agents").status_code == 500


@pytest.mark.parametrize("operation", ["patch", "create"])
def test_agent_serialization_failure_cleans_partial_temp_files(tmp_path, monkeypatch, operation):
    client = _client(tmp_path, monkeypatch, raise_server_exceptions=False)
    original = manager.get("main")
    path = manager._agent_file("main")
    before = path.read_bytes()
    dump = json.dump
    def failing_dump(data, stream, *args, **kwargs):
        if operation == "patch" or Path(stream.name).parent == tmp_path:
            stream.write('{"partial":')
            raise OSError("storage full")
        return dump(data, stream, *args, **kwargs)
    monkeypatch.setattr(manager.json, "dump", failing_dump)
    response = (client.patch("/api/agents/main", json={"expected_revision": original.revision, "name": "Changed"})
                if operation == "patch" else client.post("/api/agents", json={"name": "New agent"}))
    assert response.status_code == 500
    assert path.read_bytes() == before
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize('clock_offset', [0, -100])
def test_legacy_timestamp_cannot_commit_twice_when_clock_stalls(
    tmp_path, monkeypatch, clock_offset,
) -> None:
    client = _client(tmp_path, monkeypatch)
    saved = client.get('/api/agents/main').json()['agent']
    monkeypatch.setattr(manager.time, 'time', lambda: saved['updated_at'] + clock_offset)
    first = client.patch('/api/agents/main', json={
        'updated_at': saved['updated_at'], 'name': 'First writer',
    })
    second = client.patch('/api/agents/main', json={
        'updated_at': saved['updated_at'], 'name': 'Stale writer',
    })
    assert [first.status_code, second.status_code] == [200, 409]
    assert client.get('/api/agents/main').json()['agent']['name'] == 'First writer'


def test_agent_memory_new_defaults_and_legacy_read_is_non_mutating(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    saved = client.get('/api/agents/main').json()['agent']
    assert saved['memory'] == {
        'mode': 'off', 'read_spaces': ['self'], 'write_space': 'self', 'required': False,
    }
    assert saved['memory_policy_epoch'] == 1
    path = manager._agent_file('main')
    raw = json.loads(path.read_text())
    raw.pop('memory')
    raw.pop('memory_policy_epoch')
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    legacy = client.get('/api/agents/main').json()['agent']
    assert legacy['memory'] == {
        'mode': 'read_write', 'read_spaces': ['legacy_global'],
        'write_space': 'legacy_global', 'required': False,
    }
    assert legacy['memory_policy_epoch'] == 1
    assert path.read_bytes() == before
    renamed = client.patch('/api/agents/main', json={
        'expected_revision': legacy['revision'], 'name': 'Legacy renamed',
    }).json()['agent']
    assert renamed['memory'] == legacy['memory']
    assert renamed['memory_policy_epoch'] == 1


def test_agent_memory_roundtrip_epoch_and_partial_updates(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    current = client.get('/api/agents/main').json()['agent']
    policy = {'mode': 'read_write', 'read_spaces': ['self', 'legacy_global'],
              'write_space': 'legacy_global', 'required': True}
    def save(patch):
        nonlocal current
        response = client.patch('/api/agents/main', json={
            'expected_revision': current['revision'], **patch,
        })
        assert response.status_code == 200, response.text
        current = response.json()['agent']
        assert client.get('/api/agents/main').json()['agent'] == current
        return current
    assert save({'memory': policy})['memory_policy_epoch'] == 2
    assert current['memory'] == policy
    assert save({'description': 'Only description'})['memory_policy_epoch'] == 2
    assert save({'memory': dict(policy)})['memory_policy_epoch'] == 2
    assert save({'memory': {'mode': 'off'}})['memory_policy_epoch'] == 3
    assert current['memory']['write_space'] == 'legacy_global'
    assert save({'memory': {'mode': 'read_write'}})['memory_policy_epoch'] == 4
    assert current['memory'] == policy
    direct = manager.update('main', {'memory': {'required': False}})
    assert direct.memory_policy_epoch == 5
    assert direct.memory['read_spaces'] == policy['read_spaces']
    with pytest.raises(ValueError, match='memory_policy_epoch'):
        manager.update('main', {'memory_policy_epoch': 1})


@pytest.mark.parametrize('invalid', [
    None, [], {'mode': 'automatic'}, {'mode': []},
    {'read_spaces': 'self'}, {'read_spaces': ['/tmp/private']},
    {'read_spaces': [[]]}, {'write_space': '../other'}, {'write_space': []},
    {'required': 'false'}, {'required': 1}, {'path': '/tmp/private'},
    {'mode': 'read_only', 'read_spaces': []},
    {'mode': 'read_write', 'read_spaces': ['self'], 'write_space': 'legacy_global'},
])
def test_agent_memory_invalid_policy_leaves_saved_record_unchanged(tmp_path, monkeypatch, invalid):
    client = _client(tmp_path, monkeypatch)
    current = client.get('/api/agents/main').json()['agent']
    response = client.patch('/api/agents/main', json={
        'expected_revision': current['revision'], 'memory': invalid,
    })
    assert response.status_code == 400, response.text
    assert client.get('/api/agents/main').json()['agent'] == current
    epoch = client.patch('/api/agents/main', json={
        'expected_revision': current['revision'], 'memory_policy_epoch': 0,
    })
    assert epoch.status_code == 400


@pytest.mark.parametrize('space', ['self', 'legacy_global'])
def test_agent_memory_duplicate_keeps_configuration_without_copying_space(tmp_path, monkeypatch, space):
    from openprogram.state.memory.policy import resolve, space_path
    from openprogram.state import paths
    monkeypatch.setattr(paths, 'get_state_dir', lambda: tmp_path)
    client = _client(tmp_path, monkeypatch)
    original = manager.get('main')
    policy = {'mode': 'read_write', 'read_spaces': [space], 'write_space': space, 'required': False}
    saved = client.patch('/api/agents/main', json={
        'expected_revision': original.revision, 'memory': policy,
    }).json()['agent']
    source_root = space_path(resolve('main', saved), space)
    source_root.mkdir(parents=True, exist_ok=True)
    marker = source_root / 'source-only.txt'
    marker.write_text('Source memory')
    response = client.post('/api/agents/main/duplicate', json={'name': 'Copy'})
    assert response.status_code == 201
    duplicate = response.json()['agent']
    assert duplicate['id'] != 'main'
    assert duplicate['created_at'] != saved['created_at']
    assert duplicate['memory'] == policy
    assert duplicate['memory_policy_epoch'] == 1
    target_root = space_path(resolve(duplicate['id'], duplicate), space)
    if space == 'self':
        assert target_root != source_root
        assert not target_root.exists()
    else:
        assert target_root == source_root
    assert marker.read_text() == 'Source memory'
