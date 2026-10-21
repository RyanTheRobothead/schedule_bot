"""A tiny HTTP health endpoint for uptime monitors (e.g. Gatus).

``GET /health`` answers 200 while the bot is connected to Discord and its
housekeeping loop (reminders, auto-closing) is running, and 503 otherwise.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from .bot import ScheduleBot


def health_status(bot: ScheduleBot) -> dict:
    connected = bot.is_ready() and not bot.is_closed() and math.isfinite(bot.latency)
    housekeeping = bot.housekeeping.is_running()
    return {
        "ok": connected and housekeeping,
        "connected": connected,
        "housekeeping": housekeeping,
        "latency_ms": round(bot.latency * 1000) if math.isfinite(bot.latency) else None,
    }


async def start_health_server(bot: ScheduleBot, host: str, port: int) -> web.AppRunner:
    async def health(request: web.Request) -> web.Response:
        status = health_status(bot)
        return web.json_response(status, status=200 if status["ok"] else 503)

    app = web.Application()
    app.router.add_get("/health", health)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner
