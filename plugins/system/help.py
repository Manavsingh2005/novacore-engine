"""
NovaCore Plugin: Dynamic Help Reference & Search (.help)
Introspects COMMAND_REGISTRY to provide clean, categorized documentation.
Supports module browsing (.help ai) and direct command lookup (.help v2t).
"""

from __future__ import annotations

from core.dispatcher import client_cmd, SafeMessageWrapper, COMMAND_REGISTRY
from core.config import settings
from helpers.formatters import make_card


@client_cmd(
    pattern="help",
    description="Show available commands, module help, or search command (.help [module/command])",
    module="system",
)
async def help_cmd(event: SafeMessageWrapper, query_arg: str = ""):
    pfx = settings.COMMAND_PREFIX
    query = query_arg.lower().strip() if query_arg else ""

    # Group commands by module
    modules: dict[str, list[tuple[str, str]]] = {}
    for cmd_name, info in COMMAND_REGISTRY.items():
        mod = info.get("module", "general")
        modules.setdefault(mod, []).append((cmd_name, info.get("description", "")))

    # 1. Direct Command Search
    if query and query in COMMAND_REGISTRY:
        info = COMMAND_REGISTRY[query]
        card = make_card(
            title=f"Command: {pfx}{query}",
            fields=[
                ("Module", info.get("module", "general").upper()),
                ("Description", info.get("description", "No description.")),
                ("Syntax Trigger", f"`{pfx}{query}`"),
            ],
            footer=f"Type {pfx}help to return to main menu.",
        )
        await event.edit(card)
        return

    # 2. Module Detail View
    if query and query in modules:
        lines = [
            f"📖 **Help: Module `{query.upper()}`**",
            "──────────────────────────────",
        ]
        for cmd_name, desc in sorted(modules[query]):
            lines.append(f"• `{pfx}{cmd_name}` — {desc}")
        lines.append("──────────────────────────────")
        lines.append(f"💡 *Type `{pfx}help` to return to main menu.*")
        await event.edit("\n".join(lines))
        return

    # 3. Main Directory Menu
    lines = [
        "🌟 **NovaCore Command Center (2026 Edition)**",
        "──────────────────────────────",
    ]

    module_icons = {
        "system": "⚙️",
        "ai": "🧠",
        "pmpermit": "🛡️",
        "media": "🎬",
        "utility": "📌",
        "admin": "🔨",
    }

    for mod, cmds in sorted(modules.items()):
        icon = module_icons.get(mod, "📦")
        cmd_list = ", ".join(f"`{pfx}{c[0]}`" for c in sorted(cmds))
        lines.append(f"{icon} **{mod.title()}** ({len(cmds)} commands):\n  {cmd_list}\n")

    lines.append("──────────────────────────────")
    lines.append(f"💡 *Usage:* `{pfx}help <module>` (e.g. `{pfx}help ai`) or `{pfx}help <command>`")
    lines.append("🛡️ *The Guardian Anti-Ban Shield protects all outbound actions.*")

    await event.edit("\n".join(lines))
