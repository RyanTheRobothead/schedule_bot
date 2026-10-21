import asyncio
from types import SimpleNamespace

import aiohttp

from schedule_bot.health import start_health_server


def stub_bot(ready=True, latency=0.05, housekeeping=True):
    return SimpleNamespace(
        is_ready=lambda: ready,
        is_closed=lambda: False,
        latency=latency,
        housekeeping=SimpleNamespace(is_running=lambda: housekeeping),
    )


async def fetch(bot):
    runner = await start_health_server(bot, "127.0.0.1", 0)
    try:
        host, port = runner.addresses[0][:2]
        async with aiohttp.ClientSession() as session, session.get(f"http://{host}:{port}/health") as resp:
            return resp.status, await resp.json()
    finally:
        await runner.cleanup()


def test_healthy():
    status, body = asyncio.run(fetch(stub_bot()))
    assert status == 200
    assert body == {"ok": True, "connected": True, "housekeeping": True, "latency_ms": 50}


def test_disconnected():
    status, body = asyncio.run(fetch(stub_bot(latency=float("inf"))))
    assert status == 503 and body["connected"] is False and body["latency_ms"] is None


def test_not_ready_or_housekeeping_stopped():
    assert asyncio.run(fetch(stub_bot(ready=False)))[0] == 503
    assert asyncio.run(fetch(stub_bot(housekeeping=False)))[0] == 503
