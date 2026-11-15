from __future__ import annotations

import pytest

from openprogram.operations.self_update.delivery.activation import (
    ActivationPlan,
    classify,
    component_of,
    load_plan,
)

REV = "a" * 40


@pytest.mark.parametrize(
    ("path", "component"),
    [
        ("apps/web/components/chat/composer.tsx", "web"),
        ("docs/start/GETTING_STARTED.html", "docs"),
        ("scripts/docs_site/build.py", "docs"),
        ("apps/cli/src/index.tsx", "tui"),
        ("openprogram/interfaces/cli/__init__.py", "python"),
        ("openprogram/interfaces/server/server.py", "python"),
        ("openprogram/runtime/agent/loop.py", "python"),
        ("setup.py", "python"),
        ("apps/desktop/main.js", "desktop"),
        ("apps/desktop/main/preload.js", "desktop"),
        ("apps/desktop/main/web-views/tab-transfer/page-history.js", "desktop"),
        ("apps/desktop/main/terminal/shell-integration/.zshrc", "desktop"),
        ("tests/unit/self_update/test_store.py", None),
        ("apps/web/tests/foo.test.ts", None),
        ("apps/desktop/tests/check-packaged-files.js", None),
        ("apps/desktop/tests/webtab-checks/preload.js", None),
        ("README.md", None),
        ("scripts/smoke_entity_layer.py", None),
        ("uv.lock", "full"),
        ("apps/web/package.json", "full"),
        ("scripts/release/stage-release-assets.sh", "full"),
        ("apps/desktop/scripts/install-app.sh", "full"),
        ("apps/desktop/build/icon.icns", "full"),
        ("somewhere/new.txt", "full"),
    ],
)
def test_component_of(path, component):
    assert component_of(path) == component


def test_web_and_docs_hot_swap():
    plan = classify(["apps/web/app/page.tsx", "docs/start/faq.html"], REV)
    assert (plan.scope, plan.restart, plan.components) == ("incremental", "hot", ("web", "docs"))


def test_python_restarts_worker_and_rebuilds_docs():
    plan = classify(["openprogram/runtime/agent/loop.py"], REV)
    assert (plan.restart, plan.components) == ("worker", ("docs", "python"))


def test_desktop_restarts_app_incrementally():
    plan = classify(["apps/desktop/main.js", "apps/web/app/page.tsx"], REV)
    assert (plan.scope, plan.restart, plan.components) == ("incremental", "app", ("web", "desktop"))


def test_desktop_main_modules_restart_app_and_desktop_checks_do_not():
    plan = classify(["apps/desktop/main/preload.js"], REV)
    assert (plan.scope, plan.restart, plan.components) == ("incremental", "app", ("desktop",))
    plan = classify(["apps/desktop/tests/check-preload-fanout.js"], REV)
    assert (plan.scope, plan.restart, plan.components) == ("incremental", "hot", ("docs",))


def test_dependency_change_needs_complete_build():
    plan = classify(["apps/web/app/page.tsx", "uv.lock"], REV)
    assert (plan.scope, plan.restart) == ("full", "app")
    assert "uv.lock" in plan.reason


def test_unknown_installed_revision_needs_complete_build():
    assert classify(["docs/start/faq.html"], None).scope == "full"


def test_test_only_change_still_records_revision():
    plan = classify(["tests/unit/test_x.py"], REV)
    assert (plan.restart, plan.components) == ("hot", ("docs",))


def test_plan_round_trips(tmp_path):
    plan = classify(["openprogram/x.py"], REV)
    assert ActivationPlan.from_dict(plan.to_dict()) == plan
    assert load_plan(tmp_path) is None
    (tmp_path / "activation.json").write_text(__import__("json").dumps(plan.to_dict()))
    assert load_plan(tmp_path) == plan


def test_plan_rejects_inconsistent_documents():
    document = classify(["openprogram/x.py"], REV).to_dict()
    with pytest.raises(ValueError):
        ActivationPlan.from_dict({**document, "restart": "reboot"})
    with pytest.raises(ValueError):
        ActivationPlan.from_dict({**document, "scope": "full", "restart": "hot"})
