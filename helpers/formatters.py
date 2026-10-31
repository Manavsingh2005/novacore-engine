"""
Ultroid-Neo: Tier-Adaptive Formatter & Visual Styling Engine
Dynamically formats messages based on user account tier (Free vs Premium).
Guarantees clean, aesthetic Unicode formatting for non-subscription accounts
with zero broken fallback entity IDs.
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple


def format_uptime(start_time: float) -> str:
    """Formats elapsed seconds into a readable uptime string (e.g. 2d 5h 14m 20s)."""
    elapsed = max(int(time.time() - start_time), 0)
    days, remainder = divmod(elapsed, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def format_bytes(size_bytes: int) -> str:
    """Formats raw bytes into human-readable B, KB, MB, GB."""
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024.0 and i < len(units) - 1:
        size /= 1024.0
        i += 1
    return f"{size:.2f} {units[i]}"


def format_progress_bar(percentage: float, length: int = 12) -> str:
    """Renders a sleek Unicode progress bar: [████████░░░░] 66%"""
    clamped = max(0.0, min(100.0, percentage))
    filled_len = int(round(length * clamped / 100))
    bar = "█" * filled_len + "░" * (length - filled_len)
    return f"[{bar}] {int(clamped)}%"


def get_dc_location(dc_id: Optional[int]) -> str:
    """Maps Telegram Data Center ID to geographical server location."""
    dc_map = {
        1: "DC 1 (Miami, US)",
        2: "DC 2 (Amsterdam, NL)",
        3: "DC 3 (Miami, US)",
        4: "DC 4 (Amsterdam, NL)",
        5: "DC 5 (Singapore, SG)",
    }
    return dc_map.get(dc_id, f"DC {dc_id} (Global)") if dc_id else "DC 2 (Default)"


def get_tier_badge(is_premium: bool) -> str:
    """Returns aesthetic status badge based on user tier."""
    if is_premium:
        return "💎 **Telegram Premium**"
    return "⚡ **Standard (Free Tier)**"


def make_card(
    title: str,
    fields: List[Tuple[str, str]],
    footer: Optional[str] = None,
    is_premium: bool = False,
) -> str:
    """
    Renders an elegant, high-contrast information card.
    Uses clean box-drawing glyphs that render beautifully on both mobile and desktop Telegram.
    """
    lines = [
        f"╭── 「 {title} 」",
    ]
    for label, val in fields:
        lines.append(f"│  • **{label}:** `{val}`")

    if footer:
        lines.append("├──────────────────────────────")
        lines.append(f"│  💡 _{footer}_")

    lines.append("╰──────────────────────────────")
    return "\n".join(lines)
