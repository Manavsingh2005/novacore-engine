#!/data/data/com.termux/files/usr/bin/bash
# ==========================================
# Ultroid-Neo: Termux 24/7 Background Runner
# ==========================================

echo "=========================================="
echo " Starting Ultroid-Neo on Android (Termux) "
echo "=========================================="

# Prevent Android OS from sleeping while on battery or Wi-Fi
termux-wake-lock

# Activate Python environment or run directly
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Run userbot
python main.py
