"""Every application's registrations carry its ``application:<name>`` owner."""


def test_the_browser_service_is_provided_by_the_browser_application():
    from openprogram.programs._host import services as _services
    assert _services.lookup("browser") is not None
    row = next(r for r in _services.describe() if r["name"] == "browser")
    assert row["provider"] == "application:browser"


def test_plugin_services_keep_their_plugin_owner():
    from openprogram.programs._host import services as _services
    from openprogram.programs._host.context import ProgramContext
    ctx = ProgramContext("owner-test")
    ctx.provide("owner-test-service", object())
    try:
        row = next(r for r in _services.describe() if r["name"] == "owner-test-service")
        assert row["provider"] == "plugin:owner-test"
    finally:
        ctx.dispose()
    assert "owner-test-service" not in _services.names()
