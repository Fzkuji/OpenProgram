from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import socket
import ssl
import threading

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from openprogram.security.safe_http import (
    OutboundSecurityConfig,
    SafeAsyncClient,
    SafeClient,
    safe_async_client,
    safe_client,
)
from openprogram.security.url_policy import OwnerURLException


class _TLSServer:
    def __init__(self, certfile: str, keyfile: str):
        self.host_headers: list[str] = []
        self.peer_addresses: list[str] = []
        self.sni_names: list[str | None] = []
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen()
        self._socket.settimeout(0.2)
        self.port = self._socket.getsockname()[1]
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(certfile, keyfile)
        self._context.set_servername_callback(self._record_sni)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _record_sni(self, _socket, server_name, _context) -> None:
        self.sni_names.append(server_name)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                raw, _address = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                raise
            threading.Thread(target=self._handle, args=(raw,), daemon=True).start()

    def _handle(self, raw: socket.socket) -> None:
        try:
            with self._context.wrap_socket(raw, server_side=True) as connection:
                connection.settimeout(2)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = connection.recv(4096)
                    if not chunk:
                        return
                    data += chunk
                headers = {}
                for line in data.split(b"\r\n")[1:]:
                    if not line:
                        break
                    name, value = line.decode("latin-1").split(":", 1)
                    headers[name.lower()] = value.strip()
                self.peer_addresses.append(connection.getsockname()[0])
                self.host_headers.append(headers["host"])
                connection.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                    b"Connection: close\r\n\r\nok"
                )
        except ssl.SSLError:
            raw.close()

    def close(self) -> None:
        self._stop.set()
        self._socket.close()
        self._thread.join(timeout=2)


@pytest.fixture
def tls_material(tmp_path):
    now = dt.datetime.now(dt.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "safe.test")])
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("safe.test")]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = tmp_path / "ca.pem"
    cert_path = tmp_path / "server.pem"
    key_path = tmp_path / "server-key.pem"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca_path, cert_path, key_path


@pytest.fixture
def tls_server(tls_material):
    ca_path, cert_path, key_path = tls_material
    server = _TLSServer(str(cert_path), str(key_path))
    try:
        yield server, ca_path
    finally:
        server.close()


def _security(ca_path, hostname: str):
    return OutboundSecurityConfig(
        resolver=lambda resolved_hostname, _port: (
            ("127.0.0.1",) if resolved_hostname == hostname else ()
        ),
        owner_exceptions=(
            OwnerURLException(
                consumer="runtime.local_probe",
                network=ipaddress.ip_network("127.0.0.0/8"),
            ),
        ),
        ca_bundle=str(ca_path),
    )


def test_sync_tls_preserves_original_host_sni_and_certificate_name(tls_server):
    server, ca_path = tls_server
    url = f"https://safe.test:{server.port}/resource"
    with safe_client(
        "runtime.local_probe",
        configured_origin=url,
        security=_security(ca_path, "safe.test"),
    ) as client:
        response = client.get(url)

    assert response.text == "ok"
    assert server.peer_addresses == ["127.0.0.1"]
    assert server.host_headers == [f"safe.test:{server.port}"]
    assert server.sni_names == ["safe.test"]


def test_async_tls_preserves_original_host_sni_and_certificate_name(tls_server):
    server, ca_path = tls_server
    url = f"https://safe.test:{server.port}/resource"

    async def exercise():
        async with safe_async_client(
            "runtime.local_probe",
            configured_origin=url,
            security=_security(ca_path, "safe.test"),
        ) as client:
            return await client.get(url)

    response = asyncio.run(exercise())
    assert response.text == "ok"
    assert server.peer_addresses == ["127.0.0.1"]
    assert server.host_headers == [f"safe.test:{server.port}"]
    assert server.sni_names == ["safe.test"]


def test_sync_tls_replaces_hostile_host_and_sni_metadata(tls_server):
    server, ca_path = tls_server
    url = f"https://safe.test:{server.port}/resource"
    with safe_client(
        "runtime.local_probe",
        configured_origin=url,
        security=_security(ca_path, "safe.test"),
    ) as client:
        request = client.build_request(
            "GET",
            url,
            headers={"Host": "hostile.test"},
            extensions={"sni_hostname": "hostile.test"},
        )
        response = client.send(request)

    assert response.status_code == 200
    assert server.host_headers == [f"safe.test:{server.port}"]
    assert server.sni_names == ["safe.test"]


def test_async_tls_replaces_hostile_host_and_sni_metadata(tls_server):
    server, ca_path = tls_server
    url = f"https://safe.test:{server.port}/resource"

    async def exercise():
        async with safe_async_client(
            "runtime.local_probe",
            configured_origin=url,
            security=_security(ca_path, "safe.test"),
        ) as client:
            request = client.build_request(
                "GET",
                url,
                headers={"Host": "hostile.test"},
                extensions={"sni_hostname": "hostile.test"},
            )
            return await client.send(request)

    response = asyncio.run(exercise())
    assert response.status_code == 200
    assert server.host_headers == [f"safe.test:{server.port}"]
    assert server.sni_names == ["safe.test"]


def test_tls_verifies_the_original_hostname(tls_server):
    server, ca_path = tls_server
    url = f"https://other.test:{server.port}/resource"
    with safe_client(
        "runtime.local_probe",
        configured_origin=url,
        security=_security(ca_path, "other.test"),
    ) as client:
        with pytest.raises(httpx.ConnectError):
            client.get(url)

    assert server.host_headers == []
    assert server.sni_names == ["other.test"]


def test_factories_do_not_expose_a_verify_false_escape_hatch():
    with pytest.raises(TypeError):
        safe_client("tool.web_fetch", verify=False)
    with pytest.raises(TypeError):
        safe_async_client("tool.web_fetch", verify=False)


def test_safe_client_rejects_unmanaged_direct_transports():
    transports = [
        httpx.MockTransport(lambda _request: httpx.Response(200)),
        httpx.HTTPTransport(verify=False),
    ]
    for transport in transports:
        try:
            with pytest.raises(TypeError, match="ManagedHTTPTransport"):
                SafeClient(transport)
        finally:
            transport.close()


def test_safe_async_client_rejects_unmanaged_direct_transports():
    async def exercise():
        transports = [
            httpx.MockTransport(lambda _request: httpx.Response(200)),
            httpx.AsyncHTTPTransport(verify=False),
        ]
        for transport in transports:
            try:
                with pytest.raises(TypeError, match="AsyncManagedHTTPTransport"):
                    SafeAsyncClient(transport)
            finally:
                await transport.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "context",
    [
        ssl.create_default_context(),
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
    ],
)
def test_security_config_rejects_caller_owned_ssl_context(context):
    with pytest.raises(TypeError, match="CA bundle path"):
        OutboundSecurityConfig(ca_bundle=context)
