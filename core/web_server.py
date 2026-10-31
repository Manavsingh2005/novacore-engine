"""
Ultroid-Neo: Micro Web Health Server for 24/7 PaaS Hosting
Runs a lightweight aiohttp server bound to $PORT (e.g. 8080).
Allows free-tier platforms (Render, Koyeb, Hugging Face Spaces) to stay awake 24/7
when pinged by free uptime services (e.g. UptimeRobot or Cron-job.org).
Provides:
- GET /        : Sleek dark-mode HTML web status dashboard.
- GET /health  : High-speed JSON health endpoint for uptime pingers.
- GET /ping    : Instant text pong.
- GET /stats   : Live resource footprint and AI pool status.
"""

from __future__ import annotations

import logging
import os
import platform
import psutil
import time
from aiohttp import web
from core.config import settings
from core.ai_pool import ai_pool
from helpers.formatters import format_uptime, format_bytes

logger = logging.getLogger("ultroid_neo.web_server")

HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ultroid-Neo Daemon</title>
    <style>
        :root {
            --bg: #0d1117;
            --card: #161b22;
            --border: #30363d;
            --text: #c9d1d9;
            --accent: #58a6ff;
            --green: #3fb950;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
            box-sizing: border-box;
        }
        .card {
            background-color: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 32px;
            max-width: 520px;
            width: 100%;
            box-shadow: 0 8px 24px rgba(0,0,0,0.4);
        }
        .header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 16px;
        }
        .title {
            font-size: 1.4rem;
            font-weight: 700;
            color: #fff;
            margin: 0;
        }
        .status-badge {
            background-color: rgba(63, 185, 80, 0.15);
            color: var(--green);
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .status-badge::before {
            content: '';
            width: 8px;
            height: 8px;
            background-color: var(--green);
            border-radius: 50%;
            display: inline-block;
            box-shadow: 0 0 8px var(--green);
        }
        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-bottom: 24px;
        }
        .stat-box {
            background-color: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 14px;
        }
        .stat-label {
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #8b949e;
            margin-bottom: 6px;
        }
        .stat-value {
            font-size: 1.1rem;
            font-weight: 600;
            color: #fff;
        }
        .footer {
            font-size: 0.8rem;
            color: #8b949e;
            text-align: center;
            line-height: 1.4;
        }
        .endpoint-link {
            color: var(--accent);
            text-decoration: none;
            font-family: monospace;
        }
        .endpoint-link:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="card">
        <div class="header">
            <h1 class="title">🤖 Ultroid-Neo</h1>
            <div class="status-badge">ONLINE</div>
        </div>
        <div class="grid">
            <div class="stat-box">
                <div class="stat-label">Uptime</div>
                <div class="stat-value">{uptime}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">AI Key Pool</div>
                <div class="stat-value">{keys_count} Keys (Groq)</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Memory Footprint</div>
                <div class="stat-value">{memory_rss}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">Guardian Shield</div>
                <div class="stat-value" style="color: var(--green);">Armed & Active</div>
            </div>
        </div>
        <div class="footer">
            <p>24/7 Forever-Free Keepalive Gateway</p>
            <p>Ping Endpoint: <a class="endpoint-link" href="/health">/health</a> | <a class="endpoint-link" href="/stats">/stats</a></p>
        </div>
    </div>
</body>
</html>
"""


class WebHealthServer:
    def __init__(self, port: int, start_time: float):
        self.port = port or int(os.environ.get("PORT", "8080"))
        self.start_time = start_time
        self.app = web.Application()
        self.runner = None
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/", self.dashboard_handler)
        self.app.router.add_get("/health", self.health_check_handler)
        self.app.router.add_get("/ping", self.ping_handler)
        self.app.router.add_get("/stats", self.stats_handler)

    async def dashboard_handler(self, request: web.Request) -> web.Response:
        uptime_str = format_uptime(self.start_time)
        process = psutil.Process(os.getpid())
        mem_str = format_bytes(process.memory_info().rss)

        html = (
            HTML_DASHBOARD.replace("{uptime}", uptime_str)
            .replace("{keys_count}", str(ai_pool.key_count))
            .replace("{memory_rss}", mem_str)
        )
        return web.Response(text=html, content_type="text/html")

    async def health_check_handler(self, request: web.Request) -> web.Response:
        uptime_str = format_uptime(self.start_time)
        data = {
            "status": "online",
            "bot": "Ultroid-Neo",
            "version": "1.0.0",
            "uptime": uptime_str,
            "active_ai_keys": ai_pool.key_count,
            "guardian_shield": "active",
            "timestamp": time.time(),
        }
        return web.json_response(data)

    async def ping_handler(self, request: web.Request) -> web.Response:
        return web.Response(text="pong", content_type="text/plain")

    async def stats_handler(self, request: web.Request) -> web.Response:
        process = psutil.Process(os.getpid())
        mem_bytes = process.memory_info().rss
        current_model = await ai_pool.get_current_model()

        data = {
            "bot": "Ultroid-Neo",
            "version": "1.0.0",
            "uptime_seconds": int(time.time() - self.start_time),
            "uptime": format_uptime(self.start_time),
            "ai": {
                "provider": "Groq",
                "active_model": current_model,
                "pool_size": ai_pool.key_count,
            },
            "system": {
                "process_memory": format_bytes(mem_bytes),
                "memory_bytes": mem_bytes,
                "threads": process.num_threads(),
                "os": f"{platform.system()} {platform.release()}",
            },
            "guardian_shield": {
                "anti_ban": True,
                "floodwait_breaker": True,
            },
        }
        return web.json_response(data)

    async def start(self):
        logger.info("Starting micro-web health server on port %d...", self.port)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await site.start()
        logger.info("Micro-web health server listening at http://0.0.0.0:%d/health", self.port)

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
            logger.info("Micro-web health server stopped.")
