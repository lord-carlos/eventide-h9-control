#!/bin/bash
# This script is executed by labwc on startup

# Disable screen blanking (optional)
# swayidle -w timeout 300 'swaymsg "output * dpms off"' resume 'swaymsg "output * dpms on"' &

# Determine the actual user and project directory
INSTALL_USER="${USER:-pi}"
INSTALL_HOME=$(eval echo ~$INSTALL_USER)
PROJECT_DIR="${INSTALL_HOME}/eventide-h9-control"

# Touch rotation calibration for rotated displays
# Uncomment and adjust if your touchscreen needs rotation
export WLR_LIBINPUT_CALIBRATION_MATRIX="0 -1 1 1 0 0 0 0 1"  # For 90° rotation

# Optional: Apply display rotation using wlr-randr
# Uncomment and adjust output name and transform as needed
sleep 1  # Give compositor time to start
# wlr-randr --output DSI-2 --transform 90

# Run the application in fullscreen using uv
cd "$PROJECT_DIR"
"${INSTALL_HOME}/.local/bin/uv" run h9-control --fullscreen
