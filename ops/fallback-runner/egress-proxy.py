#!/usr/bin/env python3
"""Allowlisting HTTPS egress proxy for the fallback runner VM (runs on the Mac).

The VM may need the Mac's local HTTP proxy to reach GitHub and Docker Hub, but
that proxy also forwards to the Mac's own loopback services (for example the
browser's debugging port). This proxy sits in front of it and accepts only
``CONNECT <allowlisted host>:443``; everything else is refused. pf restricts the
VM's account to this one port (see ops/self-hosted-fallback-runner.md).

    python3 egress-proxy.py --listen 127.0.0.1:18080 [--upstream 127.0.0.1:1082]

Without --upstream it connects directly; with it, a host the upstream proxy
cannot reach right now (refusal, 503, timeout) is tried directly once. Refused
and failed requests are logged (host and port only). Standard library only.
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import re
import sys
import time

# Hosts the trusted runtime needs: GitHub (runner service, checkout, OIDC,
# artifact and cache services, release downloads), container registries,
# Python packages for setup-python, gVisor packages, and the job API.
ALLOWED = re.compile(r"""^(
    ([a-z0-9-]+\.)*github\.com
  | ([a-z0-9-]+\.)*githubusercontent\.com
  | ([a-z0-9-]+\.)*githubapp\.com
  | ([a-z0-9-]+\.)*blob\.core\.windows\.net
  | ghcr\.io | ([a-z0-9-]+\.)*docker\.io | ([a-z0-9-]+\.)*docker\.com
  | pypi\.org | files\.pythonhosted\.org
  | gvisor\.dev | storage\.googleapis\.com
  | [a-z0-9]+\.supabase\.co
)$""", re.X)
LIMIT = 8192


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%S%z"), message, file=sys.stderr, flush=True)


def allowed(host: str, port: int) -> bool:
    host = host.lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return False  # names only: an IP literal could point anywhere, loopback included
    except ValueError:
        pass
    return port == 443 and bool(ALLOWED.match(host))


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle(reader, writer, upstream):
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
        writer.close()
        return
    request = head.split(b"\r\n", 1)[0].decode("latin-1")
    match = re.fullmatch(r"CONNECT ([A-Za-z0-9.-]{1,253}):(\d{1,5}) HTTP/1\.[01]", request)
    if not match or not allowed(match[1], int(match[2])):
        log("refused " + (f"{match[1]}:{match[2]}" if match else "a non-CONNECT request"))
        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        return
    host, port = match[1], int(match[2])
    errors = (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError)
    try:
        try:
            if not upstream:
                raise ConnectionError("no upstream")
            up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(*upstream), 15)
            up_writer.write(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
            await up_writer.drain()
            reply = await asyncio.wait_for(up_reader.readuntil(b"\r\n\r\n"), 30)
            if not re.match(rb"HTTP/1\.[01] 200", reply):
                up_writer.close()
                raise ConnectionError("upstream refused")
        except errors:
            # The same allowlisted name, connected directly.
            up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(host, port), 15)
    except errors:
        log(f"failed {host}:{port}")
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        return
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))


def address(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return host, int(port)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", type=address, default=("127.0.0.1", 18080))
    parser.add_argument("--upstream", type=address)
    args = parser.parse_args()
    server = await asyncio.start_server(lambda r, w: handle(r, w, args.upstream), *args.listen, limit=LIMIT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
