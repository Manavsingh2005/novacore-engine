"""
Tests for Micro Web Health Server (24/7 PaaS Keep-Alive & Endpoints)
"""

import time
import aiohttp
import pytest
from core.web_server import WebHealthServer


@pytest.mark.asyncio
async def test_web_health_server_endpoints():
    test_port = 8999
    server = WebHealthServer(port=test_port, start_time=time.time())
    await server.start()

    try:
        async with aiohttp.ClientSession() as session:
            # 1. Test /health
            async with session.get(f"http://127.0.0.1:{test_port}/health") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "online"
                assert data["bot"] == "Ultroid-Neo"
                assert "uptime" in data

            # 2. Test /ping
            async with session.get(f"http://127.0.0.1:{test_port}/ping") as resp:
                assert resp.status == 200
                text = await resp.text()
                assert text == "pong"

            # 3. Test / (HTML Dashboard)
            async with session.get(f"http://127.0.0.1:{test_port}/") as resp:
                assert resp.status == 200
                assert "text/html" in resp.headers.get("Content-Type", "")
                html = await resp.text()
                assert "Ultroid-Neo" in html
                assert "Guardian Shield" in html

            # 4. Test /stats (JSON analytics)
            async with session.get(f"http://127.0.0.1:{test_port}/stats") as resp:
                assert resp.status == 200
                stats = await resp.json()
                assert "uptime_seconds" in stats
                assert "system" in stats
    finally:
        await server.stop()
