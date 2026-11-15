"""Loopback proxy protocol and actual macOS direct-connect enforcement."""
import asyncio
import subprocess
import shlex
import socket
import sys

import pytest

from openprogram.host.sandbox.network_runner import DomainProxy
from openprogram.host.sandbox.network_policy import parse_domains


def test_proxy_forwards_one_authorized_request_without_proxy_credentials():
    async def run():
        received = []
        origin_done = asyncio.Event()
        async def origin(reader, writer):
            data = await reader.readuntil(b'\r\n\r\n')
            data += await reader.readexactly(4)
            received.append(data)
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            origin_done.set()
        server = await asyncio.start_server(origin, '127.0.0.1', 0)
        proxy = DomainProxy(parse_domains({'example.com': 'allow'}), 'fixture')
        port = await proxy.start()
        async def connect(host, port):
            assert host == 'example.com' and port == 80
            return await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
        proxy.connect = connect
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(f'POST http://example.com/path HTTP/1.1\r\nHost: example.com\r\nProxy-Authorization: {proxy.authorization}\r\nContent-Length: 4\r\n\r\nbodyEXTRA PIPELINED REQUEST'.encode())
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 30)
            writer.close()
            await writer.wait_closed()
            await asyncio.wait_for(origin_done.wait(), 30)
            assert response.endswith(b'ok')
            assert b'proxy-authorization' not in received[0].lower()
            assert received[0].endswith(b'body') and b'EXTRA' not in received[0]
        finally:
            await asyncio.wait_for(proxy.close(), 30)
            server.close()
            await server.wait_closed()
        assert not proxy.tasks
    asyncio.run(run())


@pytest.mark.parametrize('wire_request,code', [
    ('GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n', 407),
    ('GET http://blocked.example.com/ HTTP/1.1\r\nHost: blocked.example.com\r\nAUTH', 403),
    ('CONNECT example.com:22 HTTP/1.1\r\nAUTH', 403),
    ('GET http://example.com/ HTTP/1.1\r\nHost: other.example.com\r\nAUTH', 400),
    ('POST http://example.com/ HTTP/1.1\r\nTransfer-Encoding: chunked\r\nAUTH', 400),
])
def test_proxy_rejects_unauthorized_requests_before_connect(wire_request, code):
    async def run():
        proxy = DomainProxy(parse_domains({'*.example.com': 'allow', 'example.com': 'allow', 'blocked.example.com': 'deny'}), 'fixture')
        async def unexpected(*args):
            pytest.fail('rejected request must not connect')
        proxy.connect = unexpected
        port = await proxy.start()
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            text = wire_request.replace('AUTH', f'Proxy-Authorization: {proxy.authorization}\r\n')
            writer.write((text + '\r\n').encode())
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 30)
            writer.close()
            await writer.wait_closed()
            assert response.startswith(f'HTTP/1.1 {code} '.encode())
        finally:
            await asyncio.wait_for(proxy.close(), 30)
    asyncio.run(run())


def test_proxy_shutdown_closes_active_tunnel():
    async def run():
        done = asyncio.Event()
        async def origin(reader, writer):
            await reader.read()
            writer.close()
            await writer.wait_closed()
            done.set()
        server = await asyncio.start_server(origin, '127.0.0.1', 0)
        proxy = DomainProxy(parse_domains({'example.com': 'allow'}), 'fixture')
        async def connect(*args):
            return await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
        proxy.connect = connect
        port = await proxy.start()
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(f'CONNECT example.com:443 HTTP/1.1\r\nProxy-Authorization: {proxy.authorization}\r\n\r\n'.encode())
            await writer.drain()
            assert b'200 Connection' in await reader.readuntil(b'\r\n\r\n')
            await asyncio.wait_for(proxy.close(), 30)
            assert await asyncio.wait_for(reader.read(), 30) == b''
            await asyncio.wait_for(done.wait(), 30)
            writer.close()
            await writer.wait_closed()
            assert not proxy.tasks
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(run())


@pytest.mark.sandbox
@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS proxy-only sandbox')
def test_macos_runner_allows_proxy_but_blocks_direct_sockets(tmp_path):
    from openprogram.host import sandbox
    if sandbox.unavailable_reason():
        pytest.skip(sandbox.unavailable_reason())
    policy = sandbox.policy_from_dict({'network': True, 'network_domains': {}})
    def run(command):
        argv, shell = sandbox.wrap_command(command, str(tmp_path), policy)
        return subprocess.run(argv, shell=shell, cwd=tmp_path, env=sandbox.child_env(policy),
                              capture_output=True, text=True, timeout=15)
    hello = run('printf network-runner-ready')
    assert hello.returncode == 0 and hello.stdout == 'network-runner-ready', hello.stderr
    proxied = run("/usr/bin/curl --max-time 5 -s -o /dev/null -w '%{http_code}' http://example.invalid")
    assert proxied.returncode == 0 and proxied.stdout == '403', proxied.stderr
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        script = f"import socket; socket.create_connection(('127.0.0.1', {listener.getsockname()[1]}), timeout=2)"
        direct = run(shlex.join([sys.executable, '-c', script]))
    assert direct.returncode != 0 and 'Operation not permitted' in direct.stderr


def test_outbound_socket_uses_only_the_validated_dns_address(monkeypatch):
    async def run():
        done = asyncio.Event()
        async def origin(reader, writer):
            writer.write(b'ok')
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            done.set()
        server = await asyncio.start_server(origin, '127.0.0.1', 0)
        loop = asyncio.get_running_loop()
        original_connect = loop.sock_connect
        resolutions = []
        async def resolve(host, port, **kwargs):
            resolutions.append((host, port))
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('1.1.1.1', 80))]
        async def connect(sock, address):
            assert address == ('1.1.1.1', 80)
            await original_connect(sock, ('127.0.0.1', server.sockets[0].getsockname()[1]))
        monkeypatch.setattr(loop, 'getaddrinfo', resolve)
        monkeypatch.setattr(loop, 'sock_connect', connect)
        try:
            proxy = DomainProxy(parse_domains({'example.com': 'allow'}), 'fixture')
            reader, writer = await proxy.connect('example.com', 80)
            assert await reader.read() == b'ok'
            writer.close()
            await writer.wait_closed()
            await done.wait()
            assert resolutions == [('example.com', 80)]
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(run())


@pytest.mark.sandbox
@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS proxy-only sandbox')
def test_runner_ignores_untrusted_working_directory_and_pythonpath(tmp_path):
    from openprogram.host import sandbox
    if sandbox.unavailable_reason():
        pytest.skip(sandbox.unavailable_reason())
    injected = tmp_path / 'openprogram'
    injected.mkdir()
    marker = tmp_path / 'host-import-executed'
    injected.joinpath('__init__.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("outside sandbox")\nraise RuntimeError("untrusted import executed")\n')
    tmp_path.joinpath('sitecustomize.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("site hook executed")\n')
    policy = sandbox.policy_from_dict({'network': True, 'network_domains': {}})
    argv, shell = sandbox.wrap_command('printf trusted-runner', str(tmp_path), policy)
    env = sandbox.child_env(policy)
    env['PYTHONPATH'] = str(tmp_path)
    result = subprocess.run(argv, shell=shell, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and result.stdout == 'trusted-runner', result.stderr
    assert not marker.exists()


def test_proxy_shutdown_aborts_backpressured_tunnel():
    async def run():
        origins, upstreams = [], []
        def origin(reader, writer):
            origins.append(writer)
            writer.transport.pause_reading()
        server = await asyncio.start_server(origin, '127.0.0.1', 0)
        proxy = DomainProxy(parse_domains({'example.com': 'allow'}), 'fixture')
        async def connect(*args):
            pair = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
            upstreams.append(pair[1])
            return pair
        proxy.connect = connect
        port = await proxy.start()
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        close_task = None
        try:
            writer.write(f'CONNECT example.com:443 HTTP/1.1\r\nProxy-Authorization: {proxy.authorization}\r\n\r\n'.encode())
            await writer.drain()
            await reader.readuntil(b'\r\n\r\n')
            writer.write(b'x' * (16 * 1024 * 1024))
            deadline = asyncio.get_running_loop().time() + 3
            while upstreams[0].transport.get_write_buffer_size() <= 65536:
                assert asyncio.get_running_loop().time() < deadline, 'fixture did not establish backpressure'
                await asyncio.sleep(0)
            close_task = asyncio.create_task(proxy.close())
            await asyncio.wait_for(asyncio.shield(close_task), 30)
            assert not proxy.tasks and upstreams[0].is_closing()
        finally:
            for stream in [writer, *upstreams, *origins]:
                stream.transport.abort()
            if close_task is None:
                close_task = asyncio.create_task(proxy.close())
            await asyncio.wait_for(close_task, 30)
            server.close()
            await server.wait_closed()
    asyncio.run(run())
