"""The built-in Agents are saved Agents: installed once, then ordinary records."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram import Agent, Runtime
from openprogram.runtime.agent_registry import manager
from openprogram.interfaces.server.routes.catalog import agents


def test_builtin_agents_are_saved_independent_and_preserve_owner_edits(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    manager.create("main", name="Existing", make_default=True)
    before = manager.get("main").to_dict()
    from openprogram.runtime.agent_registry.builtin_agents import BUILTIN_IDS, create_builtin_agents
    rows = create_builtin_agents(model_refs={"utility": manager.AgentModelRef("fixture", "small")})
    assert [row.id for row in rows] == list(BUILTIN_IDS)
    assert {"decision", "utility", "planner", "butler", "chief-of-staff", "research-desk", "writer"} <= set(BUILTIN_IDS)
    assert manager.get("main").to_dict() == before
    assert manager.get_default().id == "main"
    assert manager.get("image") is None
    assert manager.get("decision").name == "Decision"
    assert manager.get("utility").model.id == "small"
    specialists = [manager.get(agent_id) for agent_id in ("decision", "utility", "planner")]
    assert all(row.memory["mode"] == "off" for row in specialists)
    assert all(row.system_prompt == "" for row in specialists)
    from openprogram.runtime.agent.runtime.shared import _current_instructions
    seen_instructions = []
    def call(content, **kwargs):
        seen_instructions.append(_current_instructions.get())
        return '{"call":"B"}' if any("Pick." in block.get("text", "") for block in content) else "extracted"
    runtime = Runtime(call=call)
    token = _current_instructions.set("Instructions supplied by the caller")
    try:
        instance = Agent.from_spec(manager.get("utility"), runtime=runtime)
        assert instance.instructions is None
        assert instance("Extract one field") == "extracted"
        picker = Agent.from_spec(manager.get("decision"), runtime=runtime)
        assert picker.choose("Pick.", {"A": "First", "B": "Second"}) == "B"
        assert seen_instructions == ["Instructions supplied by the caller"] * 2
    finally:
        _current_instructions.reset(token)
        runtime.close()
    manager.update("utility", {"name": "My helper", "system_prompt": "Owner instructions"})
    saved = manager.get("utility").to_dict()
    create_builtin_agents()
    assert manager.get("utility").to_dict() == saved
    app = FastAPI()
    agents.register(app)
    with TestClient(app) as client:
        assert len(client.get("/api/agents").json()["agents"]) == len(BUILTIN_IDS)
        manager.delete("decision")
        assert len(client.get("/api/agents").json()["agents"]) == len(BUILTIN_IDS) - 1
        assert manager.get("decision") is None
    # A deleted built-in stays deleted: the index remembers it was installed.
    assert [row.id for row in create_builtin_agents()] == [agent_id for agent_id in BUILTIN_IDS if agent_id != "decision"]
    assert manager.get("decision") is None
    assert manager.installed_builtins() == set(BUILTIN_IDS)


def test_always_on_agents_carry_a_prompt_tools_memory_and_always_on_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    manager.create("main", name="Main", make_default=True)
    from openprogram.programs._host.runtime import all_tools
    from openprogram.runtime.agent_registry.builtin_agents import BUILTINS, create_builtin_agents
    known = {tool.name for tool in all_tools()}
    create_builtin_agents()
    for entry in BUILTINS:
        if entry["id"] == "main":
            continue  # Assistant already existed here; its own test covers it
        saved = manager.get(entry["id"])
        assert saved.name == entry["name"] and saved.description == entry["description"]
        assert saved.default is False
        allowed = set(saved.tools.get("allowed") or [])
        assert allowed <= known, (entry["id"], sorted(allowed - known))
        if "system_prompt" not in entry:
            continue
        prompt = saved.system_prompt
        assert prompt.startswith("Job: ") and "\nDeliverable: " in prompt and "\nNever:\n- " in prompt
        assert "\nDone when: " in prompt and "\nWorking style:\n- " in prompt and "\nFirst task: " in prompt
        # Butler has every tool, like Assistant; a job Agent only its job's.
        assert saved.tools["mode"] == ("automatic" if entry["id"] == "butler" else "selected")
        assert bool(allowed) == (entry["id"] != "butler")
        assert saved.memory["mode"] == entry["memory"] and saved.memory["write_space"] == "self"
        assert saved.always_on["enabled"] is True
        assert saved.always_on["autonomy"] == "report"
        assert saved.always_on["wake_minutes"] == entry["always_on"]["wake_minutes"]
        assert saved.always_on["research"] is entry["always_on"]["research"]
        assert saved.hold_outbound is False
        assert saved.skills["disabled"] == [] and saved.mcp["disabled"] == []
    # The verifier never edits; the producer does.
    assert not {"edit", "write", "apply_patch"} & set(manager.get("pr-verifier").tools["allowed"])
    assert {"edit", "write"} <= set(manager.get("pr-producer").tools["allowed"])


def test_butler_and_the_job_agents_are_always_on_from_the_install_and_the_chat_agents_are_not(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    manager.create("main", name="Assistant", make_default=True)
    from openprogram.runtime.agent_registry.builtin_agents import BUILTINS, create_builtin_agents
    create_builtin_agents()
    butler = manager.get("butler")
    assert butler.name == "Butler" and butler.default is False
    assert butler.always_on["enabled"] is True and butler.always_on["research"] is True
    # Every built-in schedules its own wakes: none has a fixed interval.
    assert butler.always_on["autonomy"] == "report"
    assert all(manager.get(entry["id"]).always_on["wake_minutes"] is None for entry in BUILTINS if "always_on" in entry)
    assert butler.tools == {"mode": "automatic", "web_search": True}
    assert butler.memory["mode"] == "read_write"
    assert "watch" in butler.system_prompt and butler.hold_outbound is False
    jobs = [entry["id"] for entry in BUILTINS if "system_prompt" in entry and entry["id"] != "butler"]
    assert len(jobs) == 10
    # The always-on switch is what tells the two kinds apart: on for Butler
    # and the job Agents, off for Assistant and the chat specialists.
    assert all(manager.get(agent_id).always_on["enabled"] is True for agent_id in jobs)
    # Each can report, set its own next wake, keep report preferences and
    # save finished work as a routine.
    always_on_tools = {"always_on_report", "always_on_schedule", "always_on_preference", "save_routine"}
    assert all(always_on_tools <= set(manager.get(agent_id).tools["allowed"]) for agent_id in jobs)
    assert manager.get("main").always_on["enabled"] is False
    assert all(manager.get(agent_id).always_on["enabled"] is False for agent_id in ("decision", "utility", "planner"))


def test_builtin_agents_install_in_chinese_and_refuse_other_languages(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    manager.create("main", name="Main", make_default=True)
    import pytest
    from openprogram.runtime.agent_registry.builtin_agents import create_builtin_agents
    with pytest.raises(ValueError):
        create_builtin_agents(language="fr")
    create_builtin_agents(language="zh")
    assert manager.get("research-desk").name == "研究简报"
    assert manager.get("research-desk").description == "跟踪一个主题，阅读新来源，按计划写带引用的简报。"
    assert manager.get("research-desk").system_prompt.startswith("Job: ")



def test_a_fresh_install_gets_assistant_as_default_with_the_other_builtins(tmp_path, monkeypatch):
    # Assistant is a built-in like the rest: the server installs all of them
    # at startup, so an install only ever opened in the App has every one.
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    from openprogram.runtime.agent_registry.builtin_agents import BUILTIN_IDS, create_builtin_agents, ensure_default_agent
    assert BUILTIN_IDS[0] == "main" and len(BUILTIN_IDS) == 16
    assert manager.list_all() == []
    create_builtin_agents()
    assert {row.id for row in manager.list_all()} == set(BUILTIN_IDS)
    main = manager.get_default()
    assert main.id == "main" and main.name == "Assistant" and main.description
    probe = manager.create("probe")
    assert main.tools["mode"] == probe.tools["mode"]  # a new record's defaults
    assert main.thinking_effort == probe.thinking_effort
    manager.delete("probe")
    assert sum(row.default for row in manager.list_all()) == 1
    # A deleted Assistant stays deleted like any built-in.
    manager.delete("main")
    create_builtin_agents()
    assert manager.get("main") is None
    # With every Agent gone, a chat still gets an Assistant to run on.
    for row in manager.list_all():
        manager.delete(row.id)
    again = ensure_default_agent()
    assert again.id == "main" and again.description and again.default


def test_an_upgraded_install_does_not_bring_back_a_deleted_assistant(tmp_path, monkeypatch):
    # Before Assistant joined the built-ins the index recorded only the other
    # fourteen; a main deleted back then must stay deleted after the upgrade.
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    from openprogram.runtime.agent_registry.builtin_agents import BUILTIN_IDS, create_builtin_agents
    others = [agent_id for agent_id in BUILTIN_IDS if agent_id != "main"]
    for agent_id in others:
        manager.create(agent_id)
    manager.record_builtins(others)
    manager.set_default(others[0])
    create_builtin_agents()
    assert manager.get("main") is None
    assert "main" in manager.installed_builtins()
    assert manager.get_default().id == others[0]


def _ship_new_prompt(monkeypatch, agent_id, text):
    from openprogram.runtime.agent_registry.builtin_agents import builtin
    monkeypatch.setitem(builtin(agent_id), "system_prompt", text)


def test_every_shipped_definition_is_a_known_fingerprint_in_each_language(tmp_path, monkeypatch):
    """Fails when a definition changes without adding its new fingerprints."""
    from openprogram.runtime.agent_registry import builtin_agents as module
    for language in ("en", "zh"):
        root = tmp_path / language
        root.mkdir()
        monkeypatch.setattr(manager, "_state_root", lambda: root)
        module.create_builtin_agents(language=language)
        for agent_id in module.BUILTIN_IDS:
            installed = module._fingerprint(manager.get(agent_id))
            assert installed == module._fingerprint(module._shipped_record(agent_id, language))
            assert module._KNOWN_FINGERPRINTS[agent_id][installed] == language


def test_the_fingerprint_covers_every_field_a_definition_sets(tmp_path, monkeypatch):
    from openprogram.runtime.agent_registry import builtin_agents as module
    covered = {"description", "system_prompt", "tools", "skills", "mcp", "memory", "always_on", "hold_outbound"}
    for agent_id in module.BUILTIN_IDS:
        patch = module.builtin_configuration(agent_id)
        assert set(patch) <= covered
        assert set(patch.get("always_on", {})) <= set(module._FINGERPRINT_ALWAYS_ON)


def test_an_untouched_builtin_follows_a_new_definition_once_and_keeps_what_is_not_defined(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    from openprogram.runtime.agent_registry.builtin_agents import create_builtin_agents
    create_builtin_agents(model_refs={"butler": manager.AgentModelRef("fixture", "large")})
    # Settings of the owner's that the definition does not set.
    manager.update("butler", {"name": "Jeeves", "always_on": {"goal": "Watch the inbox"}})
    _ship_new_prompt(monkeypatch, "butler", "The new instructions")
    create_builtin_agents()
    butler = manager.get("butler")
    assert butler.system_prompt == "The new instructions"
    assert (butler.name, butler.model.id, butler.always_on["goal"]) == ("Jeeves", "large", "Watch the inbox")
    assert butler.always_on["enabled"] is True
    assert manager.get("writer").revision == 1  # written once at install: not rewritten


def test_a_copy_on_an_older_shipped_tool_list_is_recognised_and_updated(tmp_path, monkeypatch):
    """Installed job Agents predate the always-on tools; nobody edited them."""
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    from openprogram.runtime.agent_registry import builtin_agents as module
    module.create_builtin_agents()
    tools = manager.get("writer").tools
    # Those copies also predate send_message taking over the handoff tool.
    older = dict(tools, allowed=["handoff" if name == "send_message" else name
                                 for name in tools["allowed"] if name not in module._ALWAYS_ON_TOOLS])
    prompt = manager.get("writer").system_prompt
    older_prompt = prompt.replace(module._HANDOFF_NOTE, (
        "When a step belongs to another Agent, hand it to that Agent's "
        "conversation by its title with the handoff tool if you have it; "
        "otherwise name the Agent that should take it and stop there."
    )).replace(" This is for a new conversation, not for later wakes.", "")
    manager.update("writer", {"name": "Scribe", "tools": older, "system_prompt": older_prompt},
                   replace_tool_policy=True)
    module.create_builtin_agents()
    writer = manager.get("writer")
    assert writer.tools == tools
    assert writer.system_prompt == prompt
    assert writer.name == "Scribe"


def test_a_setting_the_definition_does_not_name_lands_on_the_current_version_once(tmp_path, monkeypatch):
    """Specialists installed when new records woke hourly; their patch never sets it."""
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    from openprogram.runtime.agent_registry import builtin_agents as module
    module.create_builtin_agents()
    manager.update("utility", {"always_on": {"wake_minutes": 60, "goal": "Mine"}})
    module.create_builtin_agents()
    utility = manager.get("utility")
    assert utility.always_on["wake_minutes"] is None
    assert utility.always_on["goal"] == "Mine"
    assert module._fingerprint(utility) == module._fingerprint(module._shipped_record("utility", "en"))
    module.create_builtin_agents()
    assert manager.get("utility").revision == utility.revision


def test_a_setting_a_new_definition_drops_goes_back_to_a_new_records_value(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    from openprogram.runtime.agent_registry import builtin_agents as module
    module.create_builtin_agents()
    entry = module.builtin("butler")
    monkeypatch.setitem(entry, "always_on", {"research": False, "wake_minutes": 30})
    first = module._fingerprint(module._shipped_record("butler", "en"))
    monkeypatch.setitem(module._KNOWN_FINGERPRINTS["butler"], first, "en")
    module.create_builtin_agents()
    assert manager.get("butler").always_on["wake_minutes"] == 30
    monkeypatch.setitem(entry, "always_on", {})
    module.create_builtin_agents()
    butler = manager.get("butler")
    assert (butler.always_on["research"], butler.always_on["wake_minutes"]) == (True, None)
    assert module._fingerprint(butler) == module._fingerprint(module._shipped_record("butler", "en"))


def test_a_copy_already_on_the_current_definition_is_not_rewritten(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    from openprogram.runtime.agent_registry.builtin_agents import BUILTIN_IDS, create_builtin_agents
    create_builtin_agents()
    before = {agent_id: manager.get(agent_id).to_dict() for agent_id in BUILTIN_IDS}
    create_builtin_agents()
    assert {agent_id: manager.get(agent_id).to_dict() for agent_id in BUILTIN_IDS} == before


def test_an_untouched_chinese_copy_stays_chinese(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    from openprogram.runtime.agent_registry.builtin_agents import builtin, create_builtin_agents
    create_builtin_agents(language="zh")
    monkeypatch.setitem(builtin("writer"), "description_zh", "新的说明")
    monkeypatch.setitem(builtin("writer"), "description", "New description")
    create_builtin_agents()
    assert manager.get("writer").description == "新的说明"


def test_an_edited_builtin_stays_as_the_owner_left_it(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    from openprogram.runtime.agent_registry.builtin_agents import create_builtin_agents
    create_builtin_agents()
    edits = {
        "butler": {"system_prompt": "Mine"},
        "writer": {"always_on": {"enabled": False}},
        "research-desk": {"description": "My desk"},
        "utility": {"system_prompt": "Owner instructions"},
    }
    for agent_id, patch in edits.items():
        manager.update(agent_id, patch)
    saved = {agent_id: manager.get(agent_id).to_dict() for agent_id in edits}
    for agent_id in ("butler", "writer", "research-desk"):
        _ship_new_prompt(monkeypatch, agent_id, "The new instructions")
    create_builtin_agents()
    assert {agent_id: manager.get(agent_id).to_dict() for agent_id in edits} == saved


def test_a_deleted_builtin_is_not_brought_back_by_a_new_definition(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    from openprogram.runtime.agent_registry.builtin_agents import create_builtin_agents
    create_builtin_agents()
    manager.delete("writer")
    _ship_new_prompt(monkeypatch, "writer", "The new instructions")
    create_builtin_agents()
    assert manager.get("writer") is None


def test_a_builtin_that_fails_to_install_leaves_no_record_and_installs_next_time(tmp_path, monkeypatch):
    from openprogram.runtime.agent_registry import builtin_agents as module
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    good = module.builtin_configuration

    def broken(agent_id, *, language="en"):
        if agent_id == "research-desk":
            return {**good(agent_id, language=language), "tools": "not a policy"}
        return good(agent_id, language=language)

    monkeypatch.setattr(module, "builtin_configuration", broken)
    with pytest.raises(Exception):
        module.create_builtin_agents()
    assert manager.get("research-desk") is None
    assert not (tmp_path / "agents" / "research-desk").exists()

    monkeypatch.setattr(module, "builtin_configuration", good)
    module.create_builtin_agents()
    installed = manager.get("research-desk")
    assert installed.system_prompt == module.builtin("research-desk")["system_prompt"]


def test_a_builtin_is_written_once_already_configured(tmp_path, monkeypatch):
    from openprogram.runtime.agent_registry import builtin_agents as module
    monkeypatch.setattr(manager, "_state_root", lambda: tmp_path)
    module.create_builtin_agents()
    assert manager.get("butler").revision == 1


def test_builtin_text_does_not_promise_what_wakes_do_not_do():
    from openprogram.runtime.agent_registry import builtin_agents as module
    prompts = [entry.get("system_prompt", "") for entry in module.BUILTINS]
    assert not any("no news" in prompt for prompt in prompts)
    # send_message reaches an Agent by its name, so the note names it.
    assert all("send_message" in prompt and "not for later wakes" in prompt
               for prompt in prompts if prompt)
    chief = module.builtin("chief-of-staff")
    assert "once a day" not in chief["description"] and "每天" not in chief["description_zh"]
