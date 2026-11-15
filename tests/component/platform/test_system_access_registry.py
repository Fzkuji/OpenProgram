from openprogram.host import system_access


def test_registry_is_unique_and_report_contains_all_declared_capabilities(monkeypatch):
    system_access.validate_capability_registry()
    monkeypatch.setattr(system_access.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        system_access,
        "_native_probe",
        lambda: {
            "identity": {"executable": "test"},
            "capabilities": {
                "screen_recording": {"status": "granted", "detail": "fresh"},
                "accessibility": {"status": "granted", "detail": "fresh"},
            },
        },
    )
    rows = {row["id"]: row for row in system_access.report()["capabilities"]}
    report = system_access.report()
    assert report["schema"] == system_access.SYSTEM_ACCESS_SCHEMA
    assert report["version"] == system_access.SYSTEM_ACCESS_VERSION
    assert report["identity"]["executable"]
    assert set(rows) == {spec.id for spec in system_access.capability_registry()}
    assert rows["apple_events"]["request_mode"] == "targeted"
    assert rows["apple_events"]["identity_scope"] == "target_app"
    assert rows["apple_events"]["identity"]["bundle_id"] == "ai.openprogram.runtime"
    assert rows["calendar"]["label_zh"] == "日历"
    assert rows["accessibility"]["identity"]["bundle_id"] == "ai.openprogram.runtime"
    assert rows["screen_recording"]["identity"]["bundle_id"] == "ai.openprogram.runtime"
    assert rows["file_read"]["subject"] == "user_selected_path"
    assert rows["file_read"]["settings_available"] is False
    assert rows["microphone"]["request_mode"] == "native"
    assert rows["calendar"]["request_mode"] == "native"


def test_native_request_uses_the_registry_probe(monkeypatch):
    monkeypatch.setattr(system_access.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(system_access, "_native_probe", lambda *args, **kwargs: {
        "identity": {"executable": "test"},
        "capabilities": {
            capability: {"status": "granted", "detail": "fresh"}
            for capability in system_access._MAC
        },
    })
    row = system_access.request_access("calendar")
    assert row["id"] == "calendar"
    assert row["status"] == "granted"
    assert row["can_request"] is False


def test_gui_manifest_selects_only_operation_capabilities(monkeypatch):
    monkeypatch.setattr(system_access.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        system_access,
        "report",
        lambda: {
            "platform": "Darwin",
            "capabilities": [
                {"id": "screen_recording", "status": "not_granted"},
                {"id": "accessibility", "status": "granted"},
                {"id": "apple_events", "status": "unknown"},
            ],
        },
    )
    manifest = system_access.access_manifest_for_tool(
        "computer_use_agent", {"target": "app", "action": "click", "args": {"app": "Notes"}})
    assert manifest["required_capabilities"] == ["screen_recording"]
