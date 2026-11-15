"""An application operation keeps its end status after execution retention
deleted its execution record (30 days after the end)."""
from __future__ import annotations

import json

import pytest

from openprogram.programs._host.ui import service as service_module
from openprogram.programs._host.ui import state


class _NoExecutions:
    def get_execution(self, _execution_id):
        return None


@pytest.mark.parametrize(
    ("result", "error", "status"),
    [
        (json.dumps({"rows": 3}), None, "completed"),
        (json.dumps(None), None, "completed"),
        (None, "The application failed", "failed"),
        (None, service_module._WORKER_STOPPED, "interrupted"),
        (None, None, "cancelled"),
    ],
)
def test_an_operation_whose_execution_was_deleted_reports_how_it_ended(
    tmp_path, monkeypatch, result, error, status,
):
    monkeypatch.setattr(state, "home", lambda: tmp_path)
    monkeypatch.setattr(state, "get_instance", lambda key: {"id": key, "app_id": "app-1"})
    monkeypatch.setattr(service_module.catalog, "get", lambda _app_id: {"id": "app-1"})
    monkeypatch.setattr(service_module, "default_store", lambda: _NoExecutions())
    with state.connect() as db:
        db.execute(
            "INSERT INTO operations(id,instance_id,request_key,fingerprint,definition,operation,input,result,error) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("app_old", "instance-1", "key", "f", "{}", "run", "{}", result, error),
        )
    application = service_module.ApplicationService.__new__(service_module.ApplicationService)

    described = application.describe("app_old")

    assert described["status"] == status
    assert described["error"] == error
