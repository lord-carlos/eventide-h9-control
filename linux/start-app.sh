#!/bin/bash
# This script is executed by labwc on startup

# Optional: Disable screen blanking
# swayidle -w timeout 300 'swaymsg "output * dpms off"' resume 'swaymsg "output * dpms on"' &

# Run the application using uv
# We use --qt-wayland to ensure it uses the Wayland backend if needed, 
# though PySide6 usually detects it.
cd /home/pi/eventide-h9-control
/home/pi/.local/bin/uv run python ui_main.py
