"""Per-command authenticated HTTP proxy with a proxy-only macOS child sandbox.

No TLS interception: CONNECT authorizes a destination, not HTTPS request content.
"""
from __future__ import annotations

import asyncio
import base64
import logging
from contextlib import suppress
from dataclasses import replace
import hmac
import json
import os
import secrets
import signal
import socket
import sys
from urllib.parse import urlsplit

from .network_policy import canonical_host, permits, public_addresses

_log = logging.getLogger(__name__)


class DomainProxy:
    def __init__(self, domains, token: str):
        self.domains = domains
        self.authorization = 'Basic ' + base64.b64encode(('openprogram:' + token).encode()).decode()
        self.tasks = set()
        self.server = None
        self.closing = False

    async def start(self) -> int:
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0, limit=65536)
        return self.server.sockets[0].getsockname()[1]

    async def close(self):
        self.closing = True
        self.server.close()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.server.wait_closed()

    async def connect(self, host: str, port: int):
        records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        records = public_addresses(records)
        last = None
        for family, kind, protocol, _, address in records:
            sock = socket.socket(family, kind, protocol)
            sock.setblocking(False)
            try:
                await asyncio.wait_for(asyncio.get_running_loop().sock_connect(sock, address), 10)
                return await asyncio.open_connection(sock=sock)
            except (OSError, TimeoutError) as exc:
                last = exc
                sock.close()
            except BaseException:
                sock.close()
                raise
        raise OSError('network connection failed') from last

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        if self.closing or len(self.tasks) >= 32:
            writer.transport.abort()
            await writer.wait_closed()
            return
        self.tasks.add(task)
        remote_writer = None
        response_started = False
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
            rows = header.decode('iso-8859-1').split('\r\n')
            method, target, version = rows[0].split(' ')
            if (version not in {'HTTP/1.0', 'HTTP/1.1'} or not method.isascii() or not method.isalpha()
                    or any(ord(c) <= 32 or ord(c) == 127 for c in target)):
                raise ValueError('invalid request line')
            headers = {}
            for row in rows[1:-2]:
                name, value = row.split(':', 1)
                name = name.lower()
                if not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in name) or name in headers:
                    raise ValueError('invalid or duplicate header')
                if any((ord(c) < 32 and c != '\t') or ord(c) == 127 for c in value):
                    raise ValueError('invalid header value')
                headers[name] = value.strip()
            if not hmac.compare_digest(headers.get('proxy-authorization', ''), self.authorization):
                writer.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="OpenProgram"\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
                await writer.drain()
                return
            if method == 'CONNECT':
                if any(c in target for c in '/@?#\\'):
                    raise ValueError('invalid CONNECT target')
                url = urlsplit('//' + target)
                port = url.port
                if port != 443:
                    raise PermissionError('CONNECT requires port 443')
            else:
                url = urlsplit(target)
                port = url.port or 80
                if url.scheme != 'http' or port != 80 or url.fragment:
                    raise PermissionError('plain HTTP requires port 80')
            if url.username is not None or url.password is not None or not url.hostname:
                raise ValueError('invalid proxy target')
            host = canonical_host(url.hostname)
            if not permits(self.domains, host):
                raise PermissionError('domain policy denied')
            if method != 'CONNECT':
                declared = urlsplit('//' + headers.get('host', url.netloc))
                if canonical_host(declared.hostname or '') != host or (declared.port or 80) != port:
                    raise ValueError('Host header differs from destination')
                if any(key in headers for key in ('transfer-encoding', 'upgrade', 'expect')):
                    raise ValueError('chunked uploads, Upgrade and Expect are unsupported')
                raw_length = headers.get('content-length', '0')
                if not raw_length.isascii() or not raw_length.isdigit():
                    raise ValueError('invalid HTTP body length')
                length = int(raw_length)
                if length < 0 or length > 64 * 1024 * 1024:
                    raise ValueError('invalid HTTP body length')
            nominated = {item.strip().lower() for item in headers.get('connection', '').split(',')}
            if method != 'CONNECT' and nominated & {'host', 'content-length'}:
                raise ValueError('cannot remove request framing headers')
            remote_reader, remote_writer = await asyncio.wait_for(self.connect(host, port), 15)
            if method == 'CONNECT':
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                await writer.drain()
                response_started = True
                pumps = [asyncio.create_task(self.copy(reader, remote_writer)),
                         asyncio.create_task(self.copy(remote_reader, writer))]
                try:
                    await asyncio.gather(*pumps)
                finally:
                    for pump in pumps:
                        pump.cancel()
                    await asyncio.gather(*pumps, return_exceptions=True)
            else:
                path = url.path or '/'
                if url.query:
                    path += '?' + url.query
                stripped = {'proxy-authorization', 'proxy-connection', 'connection', 'keep-alive'}
                stripped.update(nominated)
                headers['host'] = host
                forwarded = ''.join(f'{key}: {value}\r\n' for key, value in headers.items() if key not in stripped)
                remote_writer.write(f'{method} {path} HTTP/1.1\r\n{forwarded}Connection: close\r\n\r\n'.encode('iso-8859-1'))
                await remote_writer.drain()
                remaining = length
                while remaining:
                    chunk = await asyncio.wait_for(reader.read(min(65536, remaining)), 30)
                    if not chunk:
                        raise ValueError('incomplete request body')
                    remote_writer.write(chunk)
                    await remote_writer.drain()
                    remaining -= len(chunk)
                response_started = True
                await self.copy(remote_reader, writer)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not response_started:
                code = 403 if isinstance(exc, PermissionError) else 400 if isinstance(exc, (ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError)) else 502
                writer.write(f'HTTP/1.1 {code} Proxy request rejected\r\nConnection: close\r\nContent-Length: 0\r\n\r\n'.encode())
                with suppress(OSError):
                    await writer.drain()
        finally:
            streams = [stream for stream in (remote_writer, writer) if stream is not None]
            try:
                for stream in streams:
                    if self.closing or task.cancelling():
                        stream.transport.abort()
                    else:
                        stream.close()
                for stream in streams:
                    with suppress(OSError):
                        await stream.wait_closed()
            finally:
                # Cancellation can arrive while normal close is flushing a
                # slow peer. Never leave either transport waiting on it.
                for stream in streams:
                    stream.transport.abort()
                self.tasks.discard(task)

    @staticmethod
    async def copy(reader, writer):
        while chunk := await asyncio.wait_for(reader.read(65536), 60):
            writer.write(chunk)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()


async def run(payload) -> int:
    from . import _seatbelt_profile, policy_from_dict, SandboxUnavailable
    if sys.platform != 'darwin':
        raise SandboxUnavailable('network domain restrictions currently require macOS')
    policy = policy_from_dict(payload['policy'])
    policy = replace(policy, host_process_info=bool(payload.get('host_process_info')))
    if not policy.network or policy.network_domains is None:
        raise ValueError('managed proxy requires an active domain policy')
    token = secrets.token_urlsafe(32)
    proxy = DomainProxy(policy.network_domains, token)
    port = await proxy.start()
    proc = None
    try:
        profile = _seatbelt_profile(payload['cwd'], policy, proxy_port=port,
            private_tmp=payload.get('private_tmp', False), allow_subprocesses=payload.get('allow_subprocesses', True))
        env = dict(os.environ)
        proxy_url = f'http://openprogram:{token}@127.0.0.1:{port}'
        for key in ('http_proxy', 'https_proxy', 'all_proxy', 'ws_proxy', 'wss_proxy'):
            env[key] = env[key.upper()] = proxy_url
        env['no_proxy'] = env['NO_PROXY'] = ''
        proc = await asyncio.create_subprocess_exec('/usr/bin/sandbox-exec', '-p', profile,
                    '/bin/bash', '-c', payload['command'], cwd=payload['cwd'], env=env)
        return await proc.wait()
    finally:
        if proc is not None and proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
        await proxy.close()


def main():
    async def entry():
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        try:
            return await run(json.loads(sys.argv[1]))
        except asyncio.CancelledError:
            return 130
    try:
        return asyncio.run(entry())
    except Exception as exc:
        _log.error('Network sandbox failed: %s: %s', type(exc).__name__, exc)
        return 1


if __name__ == '__main__':
    sys.exit(main())
