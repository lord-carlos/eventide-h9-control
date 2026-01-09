#!/bin/bash
# This script is executed by labwc on startup

# Disable screen blanking (optional)
# swayidle -w timeout 300 'swaymsg "output * dpms off"' resume 'swaymsg "output * dpms on"' &

# Determine the actual user and project directory
INSTALL_USER="${USER:-pi}"
INSTALL_HOME=$(eval echo ~$INSTALL_USER)
PROJECT_DIR="${INSTALL_HOME}/eventide-h9-control"

# Run the application in fullscreen using uv
cd "$PROJECT_DIR"
"${INSTALL_HOME}/.local/bin/uv" run h9-control --fullscreen
