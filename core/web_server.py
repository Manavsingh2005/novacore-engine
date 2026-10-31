"""
NovaCore: Micro Web Health & Storage Management Server
Runs a lightweight aiohttp server bound to port 7860 (or $PORT).
Features:
- GET /              : Sleek dark-mode dashboard with live metrics & Storage Manager.
- GET /api/storage   : Real-time JSON breakdown of disk space & temporary files.
- POST /api/clean-tmp: Instant one-click purge of all temporary download files.
- GET /health        : Health check endpoint for 24/7 uptime monitoring.
- GET /ping          : Instant text pong.
- GET /stats         : Live system footprint, threads, and AI pool status.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple

import psutil
from aiohttp import web

from core.ai_pool import ai_pool
from helpers.formatters import format_bytes, format_uptime

logger = logging.getLogger("novacore.web_server")


def get_disk_info() -> Dict[str, Any]:
    """Retrieves disk partition usage statistics."""
    try:
        disk = psutil.disk_usage("/")
        return {
            "total": format_bytes(disk.total),
            "used": format_bytes(disk.used),
            "free": format_bytes(disk.free),
            "percent": round(disk.percent, 1),
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
        }
    except Exception as e:
        logger.error("Failed to read disk usage: %s", e)
        return {
            "total": "N/A", "used": "N/A", "free": "N/A", "percent": 0.0,
            "total_bytes": 0, "used_bytes": 0, "free_bytes": 0,
        }


def inspect_temp_storage() -> Tuple[List[Dict[str, Any]], int]:
    """
    Scans the system temp directory for NovaCore download folders and residual media files.
    Returns (list of file objects, total_bytes).
    """
    temp_dir = tempfile.gettempdir()
    candidates: List[Dict[str, Any]] = []
    total_bytes = 0

    try:
        if os.path.exists(temp_dir):
            for entry in os.scandir(temp_dir):
                is_novacore = entry.name.startswith("novacore_")
                is_residual = entry.name.endswith((".part", ".ytdl")) or (
                    entry.is_file()
                    and entry.name.endswith((".mp4", ".mp3", ".webm", ".m4a"))
                    and "novacore" in entry.name.lower()
                )

                if is_novacore or is_residual:
                    size = 0
                    if entry.is_file(follow_symlinks=False):
                        try:
                            size = entry.stat().st_size
                        except OSError:
                            pass
                    elif entry.is_dir(follow_symlinks=False):
                        for root, _, files in os.walk(entry.path):
                            for f in files:
                                fp = os.path.join(root, f)
                                try:
                                    size += os.path.getsize(fp)
                                except OSError:
                                    pass

                    try:
                        mtime_str = datetime.fromtimestamp(entry.stat().st_mtime).strftime("%d %b %H:%M:%S")
                    except Exception:
                        mtime_str = "Unknown"

                    candidates.append({
                        "name": entry.name,
                        "path": entry.path,
                        "is_dir": entry.is_dir(),
                        "size_bytes": size,
                        "size": format_bytes(size),
                        "mtime": mtime_str,
                    })
                    total_bytes += size
    except Exception as e:
        logger.error("Error inspecting temp storage: %s", e)

    # Sort descending by size
    candidates.sort(key=lambda x: x["size_bytes"], reverse=True)
    return candidates, total_bytes


def purge_temp_storage() -> Tuple[int, int]:
    """
    Deletes all temporary files, residual partial downloads, and temp folders.
    Returns (removed_count, total_bytes_freed).
    """
    candidates, total_bytes = inspect_temp_storage()
    removed_count = 0

    for item in candidates:
        try:
            if item["is_dir"]:
                shutil.rmtree(item["path"], ignore_errors=True)
            else:
                os.remove(item["path"])
            removed_count += 1
        except Exception as e:
            logger.warning("Could not delete %s: %s", item["path"], e)

    # Also check data/cookies_temp.txt if left behind
    cookies_temp = os.path.join("data", "cookies_temp.txt")
    if os.path.exists(cookies_temp):
        try:
            os.remove(cookies_temp)
            removed_count += 1
        except Exception:
            pass

    return removed_count, total_bytes


HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NovaCore Control Hub</title>
    <style>
        :root {
            --bg: #0d1117;
            --card: #161b22;
            --border: #30363d;
            --text: #c9d1d9;
            --accent: #58a6ff;
            --green: #3fb950;
            --red: #f85149;
            --orange: #d29922;
        }
        * { box-sizing: border-box; }
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
        }
        .container {
            max-width: 680px;
            width: 100%;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }
        .card {
            background-color: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.4);
        }
        .header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 20px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 14px;
        }
        .title {
            font-size: 1.35rem;
            font-weight: 700;
            color: #fff;
            margin: 0;
            display: flex;
            align-items: center;
            gap: 8px;
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
            gap: 12px;
            margin-bottom: 20px;
        }
        .stat-box {
            background-color: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px;
        }
        .stat-label {
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #8b949e;
            margin-bottom: 4px;
        }
        .stat-value {
            font-size: 1.05rem;
            font-weight: 600;
            color: #fff;
        }
        /* Storage Section */
        .section-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-top: 10px;
            margin-bottom: 14px;
        }
        .section-title {
            font-size: 1.05rem;
            font-weight: 600;
            color: #fff;
            margin: 0;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .progress-bar-bg {
            background-color: #21262d;
            border-radius: 6px;
            height: 10px;
            overflow: hidden;
            margin: 8px 0 16px 0;
            border: 1px solid var(--border);
        }
        .progress-bar-fill {
            background: linear-gradient(90deg, var(--accent), #2ea043);
            height: 100%;
            border-radius: 6px;
            transition: width 0.4s ease;
        }
        .actions-bar {
            display: flex;
            gap: 10px;
            margin-top: 16px;
        }
        .btn {
            background-color: #21262d;
            color: var(--text);
            border: 1px solid var(--border);
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.2s ease;
        }
        .btn:hover {
            background-color: #30363d;
            border-color: #8b949e;
            color: #fff;
        }
        .btn-danger {
            background-color: rgba(248, 81, 73, 0.1);
            color: var(--red);
            border-color: rgba(248, 81, 73, 0.4);
        }
        .btn-danger:hover {
            background-color: var(--red);
            color: #fff;
            border-color: var(--red);
        }
        .file-list {
            margin-top: 14px;
            border: 1px solid var(--border);
            border-radius: 6px;
            background-color: rgba(0, 0, 0, 0.2);
            max-height: 200px;
            overflow-y: auto;
        }
        .file-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            font-size: 0.82rem;
            font-family: monospace;
        }
        .file-item:last-child {
            border-bottom: none;
        }
        .file-name {
            color: var(--accent);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            max-width: 320px;
        }
        .file-meta {
            color: #8b949e;
            font-size: 0.78rem;
            display: flex;
            gap: 10px;
        }
        .empty-state {
            padding: 16px;
            text-align: center;
            color: var(--green);
            font-size: 0.85rem;
        }
        /* Toast notification */
        #toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            padding: 12px 20px;
            border-radius: 8px;
            background-color: #21262d;
            color: #fff;
            border: 1px solid var(--border);
            box-shadow: 0 4px 16px rgba(0,0,0,0.5);
            font-size: 0.88rem;
            opacity: 0;
            transform: translateY(10px);
            transition: all 0.3s ease;
            pointer-events: none;
            z-index: 1000;
        }
        #toast.show {
            opacity: 1;
            transform: translateY(0);
        }
        .footer {
            font-size: 0.78rem;
            color: #8b949e;
            text-align: center;
            margin-top: 8px;
        }
        .endpoint-link {
            color: var(--accent);
            text-decoration: none;
            font-family: monospace;
        }
        .endpoint-link:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <div class="header">
                <h1 class="title">🤖 NovaCore Control Hub</h1>
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

            <!-- Storage Management Section -->
            <div class="section-header">
                <h2 class="section-title">💾 Cloud Disk & Temp Storage</h2>
                <span id="diskPercent" style="font-size: 0.82rem; color: #8b949e;">{disk_used} / {disk_total} ({disk_percent}% used)</span>
            </div>

            <div class="progress-bar-bg">
                <div id="diskBar" class="progress-bar-fill" style="width: {disk_percent}%;"></div>
            </div>

            <div style="display: flex; justify-content: space-between; font-size: 0.82rem; margin-bottom: 8px;">
                <span>Free Disk: <b style="color: var(--green);">{disk_free}</b></span>
                <span id="tempSummary">Temporary Files: <b>{temp_count} files ({temp_total})</b></span>
            </div>

            <!-- File List Container -->
            <div class="file-list" id="fileList">
                {file_items_html}
            </div>

            <!-- Actions Bar -->
            <div class="actions-bar">
                <button class="btn btn-danger" onclick="purgeTempFiles()">
                    🧹 Purge Temp Storage
                </button>
                <button class="btn" onclick="refreshStorage()">
                    🔄 Refresh Status
                </button>
            </div>

            <div class="footer">
                <p>24/7 AWS Control Gateway &nbsp;•&nbsp; <a class="endpoint-link" href="/health">/health</a> &nbsp;•&nbsp; <a class="endpoint-link" href="/stats">/stats</a> &nbsp;•&nbsp; <a class="endpoint-link" href="/api/storage">/api/storage</a></p>
            </div>
        </div>
    </div>

    <div id="toast"></div>

    <script>
        function showToast(message, isError = false) {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.style.borderColor = isError ? 'var(--red)' : 'var(--green)';
            toast.classList.add('show');
            setTimeout(() => toast.classList.remove('show'), 3500);
        }

        async function purgeTempFiles() {
            try {
                showToast('Purging temporary files...');
                const res = await fetch('/api/clean-tmp', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    showToast(`✅ ${data.message}`);
                    await refreshStorage();
                } else {
                    showToast(`❌ Error: ${data.message}`, true);
                }
            } catch (err) {
                showToast(`❌ Network error: ${err.message}`, true);
            }
        }

        async function refreshStorage() {
            try {
                const res = await fetch('/api/storage');
                const data = await res.json();
                
                // Update disk bar
                document.getElementById('diskPercent').textContent = `${data.disk.used} / ${data.disk.total} (${data.disk.percent}% used)`;
                document.getElementById('diskBar').style.width = `${data.disk.percent}%`;
                
                // Update temp summary
                document.getElementById('tempSummary').innerHTML = `Temporary Files: <b>${data.temp_count} files (${data.temp_total})</b>`;

                // Update file list
                const listContainer = document.getElementById('fileList');
                if (data.temp_files.length === 0) {
                    listContainer.innerHTML = '<div class="empty-state">✨ All Clean! Zero dangling temporary files on disk.</div>';
                } else {
                    let html = '';
                    for (const f of data.temp_files) {
                        html += `
                        <div class="file-item">
                            <span class="file-name" title="${f.path}">${f.is_dir ? '📁' : '📄'} ${f.name}</span>
                            <span class="file-meta">
                                <span>${f.size}</span>
                                <span>${f.mtime}</span>
                            </span>
                        </div>`;
                    }
                    listContainer.innerHTML = html;
                }
            } catch (err) {
                showToast(`Failed to refresh: ${err.message}`, true);
            }
        }
    </script>
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
        self.app.router.add_get("/api/storage", self.api_storage_handler)
        self.app.router.add_post("/api/clean-tmp", self.api_clean_tmp_handler)

    async def dashboard_handler(self, request: web.Request) -> web.Response:
        uptime_str = format_uptime(self.start_time)
        process = psutil.Process(os.getpid())
        mem_str = format_bytes(process.memory_info().rss)

        disk = get_disk_info()
        temp_files, temp_bytes = inspect_temp_storage()

        if not temp_files:
            file_items_html = '<div class="empty-state">✨ All Clean! Zero dangling temporary files on disk.</div>'
        else:
            rows = []
            for f in temp_files:
                icon = "📁" if f["is_dir"] else "📄"
                rows.append(
                    f'<div class="file-item">'
                    f'<span class="file-name" title="{f["path"]}">{icon} {f["name"]}</span>'
                    f'<span class="file-meta">'
                    f'<span>{f["size"]}</span>'
                    f'<span>{f["mtime"]}</span>'
                    f'</span>'
                    f'</div>'
                )
            file_items_html = "\n".join(rows)

        html = (
            HTML_DASHBOARD.replace("{uptime}", uptime_str)
            .replace("{keys_count}", str(ai_pool.key_count))
            .replace("{memory_rss}", mem_str)
            .replace("{disk_total}", disk["total"])
            .replace("{disk_used}", disk["used"])
            .replace("{disk_free}", disk["free"])
            .replace("{disk_percent}", str(disk["percent"]))
            .replace("{temp_count}", str(len(temp_files)))
            .replace("{temp_total}", format_bytes(temp_bytes))
            .replace("{file_items_html}", file_items_html)
        )
        return web.Response(text=html, content_type="text/html")

    async def api_storage_handler(self, request: web.Request) -> web.Response:
        """Returns JSON breakdown of disk usage and temporary files."""
        disk = get_disk_info()
        temp_files, temp_bytes = inspect_temp_storage()

        data = {
            "disk": disk,
            "temp_count": len(temp_files),
            "temp_total": format_bytes(temp_bytes),
            "temp_bytes": temp_bytes,
            "temp_files": temp_files,
            "timestamp": time.time(),
        }
        return web.json_response(data)

    async def api_clean_tmp_handler(self, request: web.Request) -> web.Response:
        """Purges all dangling temp files and returns status."""
        removed_count, bytes_freed = purge_temp_storage()
        logger.info("Storage purge triggered via Web Hub: %d items removed, %s freed", removed_count, format_bytes(bytes_freed))

        data = {
            "success": True,
            "removed_count": removed_count,
            "bytes_freed": bytes_freed,
            "freed_formatted": format_bytes(bytes_freed),
            "message": f"Successfully cleaned {removed_count} temporary items (freed {format_bytes(bytes_freed)})!",
            "timestamp": time.time(),
        }
        return web.json_response(data)

    async def health_check_handler(self, request: web.Request) -> web.Response:
        uptime_str = format_uptime(self.start_time)
        data = {
            "status": "online",
            "bot": "NovaCore",
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
            "bot": "NovaCore",
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
