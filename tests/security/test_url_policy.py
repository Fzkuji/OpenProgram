from __future__ import annotations

import ipaddress
import socket

import pytest

from openprogram.security.url_policy import (
    OwnerURLException,
    URLPolicyError,
    URLTrustClass,
    evaluate_url,
    normalize_origin,
    normalize_url,
)


PUBLIC_IP = "93.184.216.34"
PUBLIC_METHODS = frozenset({"GET", "HEAD"})
PUBLIC_PORTS = frozenset({80, 443})


def answers(*values: str):
    return lambda _hostname, _port: values


def evaluate_public(url: str, *, resolver=answers(PUBLIC_IP), method: str = "GET"):
    return evaluate_url(
        "tool.web_fetch",
        method,
        url,
        trust_class=URLTrustClass.UNTRUSTED_PUBLIC,
        allowed_methods=PUBLIC_METHODS,
        allowed_ports=PUBLIC_PORTS,
        resolver=resolver,
    )


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://127.0.0.1/", "NON_GLOBAL_ADDRESS"),
        ("http://[::1]/", "NON_GLOBAL_ADDRESS"),
        ("http://[::ffff:127.0.0.1]/", "NON_GLOBAL_ADDRESS"),
        ("http://169.254.169.254/latest/meta-data/", "METADATA_ADDRESS"),
        ("http://2130706433/", "AMBIGUOUS_HOST"),
        ("http://017700000001/", "AMBIGUOUS_HOST"),
        ("http://0x7f000001/", "AMBIGUOUS_HOST"),
        ("http://127.1/", "AMBIGUOUS_HOST"),
        ("http://user:pass@example.com/", "USERINFO_FORBIDDEN"),
        ("http://example.com:22/", "PORT_FORBIDDEN"),
        ("http://example.com\\@127.0.0.1/", "INVALID_URL"),
        ("http://example.com/%0aHost:x", "CONTROL_CHARACTER"),
        ("http://[fe80::1%25en0]/", "ZONE_ID_FORBIDDEN"),
        ("ftp://example.com/file", "SCHEME_FORBIDDEN"),
        ("http://example.com:0/", "INVALID_PORT"),
        ("http://example.com:bad/", "INVALID_PORT"),
        ("http://example.com:70000/", "INVALID_PORT"),
    ],
)
def test_untrusted_public_rejects_unsafe_url(url, reason):
    with pytest.raises(URLPolicyError) as exc:
        evaluate_public(url)
    assert exc.value.reason == reason


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "10.0.0.1",
        "100.64.0.1",
        "127.0.0.1",
        "169.254.1.1",
        "192.0.2.1",
        "224.0.0.1",
        "240.0.0.1",
        "::",
        "::1",
        "2001:db8::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
    ],
)
def test_untrusted_public_rejects_every_non_global_address_category(address):
    with pytest.raises(URLPolicyError) as exc:
        evaluate_public("https://example.com/resource", resolver=answers(address))
    assert exc.value.reason == "NON_GLOBAL_ADDRESS"


def test_normalization_canonicalizes_hostname_idna_and_default_port():
    normalized = normalize_url(
        "HTTPS://B\N{LATIN SMALL LETTER U WITH DIAERESIS}CHER.Example.:443/a?q=1#frag"
    )
    assert normalized.normalized_url == "https://xn--bcher-kva.example/a?q=1#frag"
    assert normalized.origin == "https://xn--bcher-kva.example"
    assert normalized.hostname == "xn--bcher-kva.example"
    assert normalized.port == 443
    assert normalize_origin(
        "HTTPS://B\N{LATIN SMALL LETTER U WITH DIAERESIS}CHER.Example.:443/path"
    ) == ("https://xn--bcher-kva.example")


def test_evaluation_preserves_normalized_hostname_and_deduplicates_answers():
    calls: list[tuple[str, int]] = []

    def resolver(hostname: str, port: int):
        calls.append((hostname, port))
        return (PUBLIC_IP, PUBLIC_IP, "::ffff:93.184.216.34")

    decision = evaluate_public("HTTPS://EXAMPLE.COM.:443/path", resolver=resolver)

    assert calls == [("example.com", 443)]
    assert decision.hostname == "example.com"
    assert decision.normalized_url == "https://example.com/path"
    assert decision.origin == "https://example.com"
    assert decision.resolved_ips == (ipaddress.ip_address(PUBLIC_IP),)


def test_public_policy_normalizes_method_and_rejects_disallowed_method():
    assert evaluate_public("https://example.com", method="head").method == "HEAD"
    with pytest.raises(URLPolicyError) as exc:
        evaluate_public("https://example.com", method="POST")
    assert exc.value.reason == "METHOD_FORBIDDEN"


@pytest.mark.parametrize("trust_class", ["unknown", None, 0])
def test_invalid_trust_class_is_rejected_before_dns(trust_class):
    calls: list[tuple[str, int]] = []

    def resolver(hostname: str, port: int):
        calls.append((hostname, port))
        return ("127.0.0.1",)

    with pytest.raises(URLPolicyError) as exc:
        evaluate_url(
            "tool.web_fetch",
            "GET",
            "http://internal.example/private/path-token?q=secret#fragment",
            trust_class=trust_class,
            allowed_methods=PUBLIC_METHODS,
            allowed_ports=PUBLIC_PORTS,
            resolver=resolver,
        )

    assert exc.value.reason == "TRUST_CLASS_INVALID"
    assert exc.value.safe_url == "http://internal.example"
    assert str(exc.value) == "TRUST_CLASS_INVALID: http://internal.example"
    assert calls == []


@pytest.mark.parametrize(
    ("resolver", "reason"),
    [
        (answers(), "DNS_EMPTY_RESULT"),
        (
            lambda _host, _port: (_ for _ in ()).throw(socket.gaierror("NXDOMAIN")),
            "DNS_ERROR",
        ),
        (
            lambda _host, _port: (_ for _ in ()).throw(TimeoutError("timeout")),
            "DNS_ERROR",
        ),
    ],
)
def test_dns_failures_are_closed(resolver, reason):
    with pytest.raises(URLPolicyError) as exc:
        evaluate_public("https://example.com", resolver=resolver)
    assert exc.value.reason == reason


def test_mixed_public_and_private_dns_answers_fail_closed():
    with pytest.raises(URLPolicyError) as exc:
        evaluate_public(
            "https://example.com",
            resolver=answers(PUBLIC_IP, "10.0.0.1"),
        )
    assert exc.value.reason == "NON_GLOBAL_ADDRESS"


def test_errors_and_safe_urls_do_not_expose_secrets():
    url = "https://alice:password@example.com/private/token?q=secret#fragment-secret"
    with pytest.raises(URLPolicyError) as exc:
        evaluate_public(url)

    rendered = str(exc.value)
    assert exc.value.safe_url == "https://example.com"
    for secret in (
        "alice",
        "password",
        "private",
        "token",
        "secret",
        "fragment-secret",
    ):
        assert secret not in rendered


def test_path_credential_is_not_copied_into_policy_error():
    path_credential = "bot123456:ABC-SECRET"
    with pytest.raises(URLPolicyError) as exc:
        evaluate_public(
            f"https://api.telegram.org/{path_credential}/getUpdates",
            resolver=answers("10.0.0.1"),
        )

    assert exc.value.safe_url == "https://api.telegram.org"
    assert path_credential not in str(exc.value)


def test_encoded_control_character_is_not_copied_into_safe_url():
    with pytest.raises(URLPolicyError) as exc:
        evaluate_public("https://example.com/%0aHost:injected?q=secret")

    assert exc.value.safe_url == "<invalid-url>"
    assert "%0a" not in str(exc.value).lower()


def test_configured_service_requires_exact_origin_but_allows_private_address():
    exception = OwnerURLException(
        consumer="provider.configured_api",
        origin="http://localhost:11434",
    )
    decision = evaluate_url(
        "provider.configured_api",
        "POST",
        "HTTP://LOCALHOST:11434/v1/models?token=secret",
        trust_class=URLTrustClass.CONFIGURED_SERVICE,
        allowed_methods=frozenset({"GET", "POST"}),
        allowed_ports=None,
        configured_origin="http://localhost:11434/base",
        exceptions=(exception,),
        resolver=answers("127.0.0.1"),
    )
    assert decision.origin == "http://localhost:11434"
    assert decision.resolved_ips == (ipaddress.ip_address("127.0.0.1"),)

    with pytest.raises(URLPolicyError) as exc:
        evaluate_url(
            "provider.configured_api",
            "GET",
            "http://localhost:11435/v1/models",
            trust_class=URLTrustClass.CONFIGURED_SERVICE,
            allowed_methods=frozenset({"GET"}),
            allowed_ports=None,
            configured_origin="http://localhost:11434",
            resolver=answers("127.0.0.1"),
        )
    assert exc.value.reason == "CONFIGURED_ORIGIN_MISMATCH"


def test_public_trust_classes_reject_owner_exceptions_before_dns():
    exception = OwnerURLException(
        consumer="skills.configured.catalog",
        network=ipaddress.ip_network("10.0.0.0/8"),
    )
    for trust_class in (
        URLTrustClass.UNTRUSTED_PUBLIC,
        URLTrustClass.FIXED_PUBLIC_SERVICE,
    ):
        calls: list[tuple[str, int]] = []

        def resolver(hostname: str, port: int):
            calls.append((hostname, port))
            return ("10.1.2.3",)

        kwargs = {}
        if trust_class == URLTrustClass.FIXED_PUBLIC_SERVICE:
            kwargs["fixed_origins"] = frozenset({"https://catalog.example.test"})
        with pytest.raises(URLPolicyError) as exc:
            evaluate_url(
                "skills.configured.catalog",
                "GET",
                "https://catalog.example.test/skills",
                trust_class=trust_class,
                allowed_methods=PUBLIC_METHODS,
                allowed_ports=PUBLIC_PORTS,
                exceptions=(exception,),
                resolver=resolver,
                **kwargs,
            )
        assert exc.value.reason == "EXCEPTIONS_FORBIDDEN"
        assert calls == []


def test_configured_private_address_requires_a_matching_owner_exception():
    kwargs = {
        "trust_class": URLTrustClass.CONFIGURED_SERVICE,
        "allowed_methods": PUBLIC_METHODS,
        "allowed_ports": None,
        "configured_origin": "https://catalog.example.test",
        "resolver": answers("10.1.2.3"),
    }
    with pytest.raises(URLPolicyError) as exc:
        evaluate_url(
            "skills.configured.catalog",
            "GET",
            "https://catalog.example.test/skills",
            **kwargs,
        )
    assert exc.value.reason == "NON_GLOBAL_ADDRESS"

    exception = OwnerURLException(
        consumer="skills.configured.catalog",
        origin="https://catalog.example.test",
    )
    decision = evaluate_url(
        "skills.configured.catalog",
        "GET",
        "https://catalog.example.test/skills",
        exceptions=(exception,),
        **kwargs,
    )
    assert decision.resolved_ips == (ipaddress.ip_address("10.1.2.3"),)


def test_configured_service_never_allows_metadata_through_owner_exception():
    metadata_exception = OwnerURLException(
        consumer="skills.configured.catalog",
        origin="http://169.254.169.254",
    )
    with pytest.raises(URLPolicyError) as exc:
        evaluate_url(
            "skills.configured.catalog",
            "GET",
            "http://169.254.169.254/latest/meta-data",
            trust_class=URLTrustClass.CONFIGURED_SERVICE,
            allowed_methods=PUBLIC_METHODS,
            allowed_ports=None,
            configured_origin="http://169.254.169.254",
            exceptions=(metadata_exception,),
            resolver=answers("169.254.169.254"),
        )
    assert exc.value.reason == "METADATA_ADDRESS"


def test_fixed_service_rejects_unlisted_origin_before_dns():
    calls: list[tuple[str, int]] = []

    def resolver(hostname: str, port: int):
        calls.append((hostname, port))
        return (PUBLIC_IP,)

    with pytest.raises(URLPolicyError) as exc:
        evaluate_url(
            "channel.telegram.api",
            "GET",
            "https://attacker.example/getUpdates",
            trust_class=URLTrustClass.FIXED_PUBLIC_SERVICE,
            allowed_methods=PUBLIC_METHODS,
            allowed_ports=PUBLIC_PORTS,
            fixed_origins=frozenset({"https://api.telegram.org"}),
            resolver=resolver,
        )
    assert exc.value.reason == "FIXED_ORIGIN_MISMATCH"
    assert calls == []


def test_callback_rejects_https_before_dns_when_registry_allows_only_http():
    calls: list[tuple[str, int]] = []

    def resolver(hostname: str, port: int):
        calls.append((hostname, port))
        return ("127.0.0.1",)

    with pytest.raises(URLPolicyError) as exc:
        evaluate_url(
            "mcp.loopback.callback",
            "GET",
            "https://127.0.0.1:9005/callback",
            trust_class=URLTrustClass.LOOPBACK_CALLBACK,
            allowed_schemes=frozenset({"http"}),
            allowed_methods=frozenset({"GET"}),
            allowed_ports=None,
            callback_origin="https://127.0.0.1:9005",
            resolver=resolver,
        )
    assert exc.value.reason == "SCHEME_FORBIDDEN"
    assert calls == []


def test_loopback_callback_requires_exact_ip_and_port():
    kwargs = {
        "trust_class": URLTrustClass.LOOPBACK_CALLBACK,
        "allowed_methods": frozenset({"GET"}),
        "allowed_ports": None,
        "callback_origin": "http://127.0.0.1:9005",
    }
    decision = evaluate_url(
        "mcp.loopback.callback",
        "GET",
        "http://127.0.0.1:9005/callback?code=secret",
        resolver=answers("127.0.0.1"),
        **kwargs,
    )
    assert decision.origin == "http://127.0.0.1:9005"

    for url in ("http://localhost:9005/callback", "http://127.0.0.1:9006/callback"):
        with pytest.raises(URLPolicyError) as exc:
            evaluate_url(
                "mcp.loopback.callback",
                "GET",
                url,
                resolver=answers("127.0.0.1"),
                **kwargs,
            )
        assert exc.value.reason == "CALLBACK_ORIGIN_MISMATCH"


def test_loopback_callback_rejects_a_non_loopback_exact_origin():
    kwargs = {
        "trust_class": URLTrustClass.LOOPBACK_CALLBACK,
        "allowed_methods": frozenset({"GET"}),
        "allowed_ports": None,
        "callback_origin": f"http://{PUBLIC_IP}:9005",
    }
    with pytest.raises(URLPolicyError) as exc:
        evaluate_url(
            "mcp.loopback.callback",
            "GET",
            f"http://{PUBLIC_IP}:9005/callback",
            resolver=answers("127.0.0.1"),
            **kwargs,
        )
    assert exc.value.reason == "CALLBACK_ADDRESS_MISMATCH"
