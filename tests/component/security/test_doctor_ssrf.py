from __future__ import annotations

from openprogram.cli.commands import doctor
from openprogram.security import runtime_http_audit


def _inventory(**overrides):
    values = {
        "unregistered": (),
        "active_unmanaged_transports": (),
        "registry_without_consumer": (),
        "stale_exclusions": (),
    }
    values.update(overrides)
    return runtime_http_audit.RuntimeHTTPInventory(**values)


def test_doctor_reports_owner_policy_and_recent_sanitized_denials():
    runtime_http_audit.clear_runtime_http_audit()
    runtime_http_audit.record_runtime_http_denial(
        consumer="tool.web_fetch",
        reason="PRIVATE_ADDRESS",
        url="https://user:BEARER-TOKEN@example.com/path?token=QUERY-SECRET",
        delegated_to_policy_proxy=True,
    )
    config = {
        "security": {
            "outbound_url": {
                "exceptions": [
                    {
                        "consumer": "skills.configured.catalog",
                        "origin": "https://catalog.corp.example:8443",
                    },
                    {
                        "consumer": "provider.configured_api",
                        "cidr": "10.20.0.0/16",
                    },
                ],
                "policy_proxy": {
                    "url": "http://127.0.0.1:3128",
                    "enforces_target_policy": True,
                },
            }
        }
    }

    rows = doctor.runtime_http_checks(config=config, inventory=_inventory())
    by_label = {label: (ok, detail) for ok, label, detail in rows}

    assert set(by_label) == {
        "runtime-http-registry",
        "runtime-http-owner-exceptions",
        "runtime-http-policy-proxy",
        "runtime-http-recent-denials",
        "runtime-http-unmanaged-transport",
    }
    assert by_label["runtime-http-registry"][0]
    assert (
        "skills.configured.catalog=https://catalog.corp.example:8443"
        in by_label["runtime-http-owner-exceptions"][1]
    )
    assert (
        "provider.configured_api=10.20.0.0/16"
        in by_label["runtime-http-owner-exceptions"][1]
    )
    assert by_label["runtime-http-policy-proxy"] == (
        True,
        "delegated to http://127.0.0.1:3128; target-policy enforcement asserted",
    )
    assert "PRIVATE_ADDRESS" in by_label["runtime-http-recent-denials"][1]
    output = repr(rows)
    assert "BEARER-TOKEN" not in output
    assert "QUERY-SECRET" not in output


def test_doctor_fails_closed_for_invalid_config_and_inventory_gaps():
    invalid = {
        "security": {
            "outbound_url": {
                "exceptions": [
                    {
                        "consumer": "skills.configured.catalog",
                        "origin": "https://user:BEARER-TOKEN@example.com/path?token=QUERY-SECRET",
                    }
                ]
            }
        }
    }
    issue = runtime_http_audit.RuntimeHTTPCall(
        path="runtime/raw.py",
        line=9,
        kind="requests.get",
    )
    rows = doctor.runtime_http_checks(
        config=invalid,
        inventory=_inventory(
            unregistered=(issue,),
            active_unmanaged_transports=("channel.slack.gateway_sdk",),
        ),
    )
    by_label = {label: (ok, detail) for ok, label, detail in rows}

    assert not by_label["runtime-http-registry"][0]
    assert not by_label["runtime-http-owner-exceptions"][0]
    assert not by_label["runtime-http-policy-proxy"][0]
    assert not by_label["runtime-http-unmanaged-transport"][0]
    output = repr(rows)
    assert "BEARER-TOKEN" not in output
    assert "QUERY-SECRET" not in output


def test_doctor_scans_all_installed_application_packages():
    rows = doctor.runtime_http_checks(config={})
    by_label = {label: (ok, detail) for ok, label, detail in rows}

    assert by_label["runtime-http-registry"] == (
        True,
        "all Runtime HTTP calls and registry consumers classified",
    )


def test_run_checks_reuses_one_runtime_http_inventory(monkeypatch):
    calls = 0

    def fake_runtime_checks():
        nonlocal calls
        calls += 1
        return [
            (True, "runtime-http-registry", "classified"),
            (True, "runtime-http-owner-exceptions", "none"),
            (True, "runtime-http-policy-proxy", "disabled"),
            (True, "runtime-http-recent-denials", "none"),
            (True, "runtime-http-unmanaged-transport", "none active"),
        ]

    monkeypatch.setattr(doctor, "CHECKS", ())
    monkeypatch.setattr(doctor, "runtime_http_checks", fake_runtime_checks)
    monkeypatch.setattr(
        "openprogram._compat.platform_environment_advisories", lambda _path: []
    )

    rows = doctor.run_checks()

    assert calls == 1
    assert [row["label"] for row in rows] == [
        "runtime-http-registry",
        "runtime-http-owner-exceptions",
        "runtime-http-policy-proxy",
        "runtime-http-recent-denials",
        "runtime-http-unmanaged-transport",
    ]
