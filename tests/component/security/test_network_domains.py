"""Optional network domain policy cannot silently degrade into open networking."""
import json

import pytest

from openprogram.host import sandbox


def test_policy_roundtrip_and_wrap_preserve_domain_restrictions(tmp_path, monkeypatch):
    data = {'network': True, 'network_domains': {'*.example.com': 'allow', 'blocked.example.com': 'deny'}}
    policy = sandbox.policy_from_dict(data)
    assert sandbox.policy_to_dict(policy)['network_domains'] == data['network_domains']
    monkeypatch.setattr(sandbox.sys, 'platform', 'darwin')
    argv, shell = sandbox.wrap_command('curl https://a.example.com', str(tmp_path), policy)
    assert not shell and 'openprogram.host.sandbox.network_runner' in argv
    assert json.loads(argv[-1])['policy']['network_domains'] == data['network_domains']


@pytest.mark.parametrize('domains', [{'example.com': 'yes'}, {'*': 'allow'}, {'*.com': 'allow'}, ['example.com']])
def test_invalid_domain_policy_rejected(domains):
    with pytest.raises(ValueError):
        sandbox.policy_from_dict({'network': True, 'network_domains': domains})


def test_unsupported_domain_sandbox_fails_closed(tmp_path, monkeypatch):
    policy = sandbox.policy_from_dict({'network': True, 'network_domains': {'example.com': 'allow'}})
    monkeypatch.setattr(sandbox.sys, 'platform', 'linux')
    with pytest.raises(sandbox.SandboxUnavailable, match='domain'):
        sandbox.wrap_command('true', str(tmp_path), policy)


def test_domain_decisions_and_nonpublic_dns_are_rejected():
    from openprogram.host.sandbox.network_policy import parse_domains, permits, public_addresses
    import socket
    rules = parse_domains({'*.example.com': 'allow', 'blocked.example.com': 'deny'})
    assert permits(rules, 'deep.a.example.com') and not permits(rules, 'example.com')
    assert not permits(rules, 'blocked.example.com') and not permits(rules, 'example.com.evil.test')
    for address in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '224.0.0.1', '0.0.0.0']:
        with pytest.raises(PermissionError):
            public_addresses([(socket.AF_INET, socket.SOCK_STREAM, 0, '', (address, 80))])


def test_missing_sandbox_never_uses_warn_fallback_for_domains(tmp_path, monkeypatch):
    from openprogram.host.backend.local import _invocation
    policy = sandbox.policy_from_dict({'network': True, 'network_domains': {'example.com': 'allow'}})
    monkeypatch.setattr(sandbox, 'unavailable_reason', lambda: 'fixture unavailable')
    monkeypatch.setattr(sandbox, 'unavailable_policy', lambda: 'warn')
    with pytest.raises(sandbox.SandboxUnavailable):
        _invocation('true', cwd=str(tmp_path), policy=policy)
