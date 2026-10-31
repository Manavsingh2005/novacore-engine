#!/data/data/com.termux/files/usr/bin/bash
# ==========================================
# NovaCore: Termux 24/7 Background Runner
# ==========================================

echo "=========================================="
echo " Starting NovaCore on Android (Termux) "
echo "=========================================="

# Prevent Android OS from sleeping while on battery or Wi-Fi
termux-wake-lock

# Activate Python environment or run directly
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Run automation engine
python main.py
