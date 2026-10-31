"""
Ultroid-Neo Plugin: Advanced System Resource Monitor (.sysinfo)
Displays real-time host RAM, CPU load, disk storage, OS kernel,
and bot process footprint with visual progress bars.
"""

from __future__ import annotations

import os
import platform
import psutil
from core.dispatcher import client_cmd, SafeMessageWrapper
from helpers.formatters import make_card, format_bytes, format_progress_bar


@client_cmd(
    pattern="sysinfo",
    description="Display host and bot resource consumption with visual bars",
    module="system",
)
async def sysinfo_cmd(event: SafeMessageWrapper):
    # Host memory
    vm = psutil.virtual_memory()
    total_ram = format_bytes(vm.total)
    used_ram = format_bytes(vm.used)
    ram_bar = format_progress_bar(vm.percent, length=10)

    # CPU load
    cpu_pct = psutil.cpu_percent(interval=0.1)
    cpu_bar = format_progress_bar(cpu_pct, length=10)

    # Disk usage
    disk_path = "C:\\" if platform.system() == "Windows" else "/"
    disk = psutil.disk_usage(disk_path)
    total_disk = format_bytes(disk.total)
    used_disk = format_bytes(disk.used)
    disk_bar = format_progress_bar(disk.percent, length=10)

    # Bot process footprint
    process = psutil.Process(os.getpid())
    bot_ram = format_bytes(process.memory_info().rss)

    # OS Info
    os_name = f"{platform.system()} {platform.release()} ({platform.machine()})"

    card = make_card(
        title="📊 System Resource Monitor",
        fields=[
            ("Bot Footprint", f"{bot_ram} (Sub-100MB Architecture)"),
            ("Host RAM", f"{used_ram} / {total_ram} {ram_bar}"),
            ("Host CPU", f"{cpu_pct}% {cpu_bar}"),
            ("Disk Storage", f"{used_disk} / {total_disk} {disk_bar}"),
            ("OS Platform", os_name),
            ("Active Threads", str(process.num_threads())),
        ],
        footer="Low-overhead async daemon optimized for 24/7 free containers.",
    )
    await event.edit(card)
