#!/bin/bash
set -e

echo "========================================="
echo "Eventide H9 Control - Raspberry Pi Setup"
echo "========================================="
echo ""

# Detect current user (in case run with sudo)
INSTALL_USER="${SUDO_USER:-$USER}"
INSTALL_HOME=$(eval echo ~$INSTALL_USER)
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "Installing for user: $INSTALL_USER"
echo "Project directory: $PROJECT_DIR"
echo ""

# 1. Update system
echo "[1/7] Updating system packages..."
sudo apt update

# 2. Install system dependencies
echo "[2/7] Installing system dependencies..."
sudo apt install -y \
    git \
    build-essential \
    labwc \
    wlr-randr \
    libegl1 \
    libgles2 \
    libwayland-client0 \
    libxkbcommon0 \
    libglib2.0-0 \
    libfontconfig1 \
    libdbus-1-3 \
    libasound2-dev \
    libjack-jackd2-dev \
    portaudio19-dev \
    libaubio-dev \
    libaubio5 \
    libgpiod2 \
    python3-dev \
    gpiod

# 3. Install uv (Python package manager)
echo "[3/7] Installing uv..."
if ! command -v uv &> /dev/null; then
    sudo -u $INSTALL_USER bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
    export PATH="$INSTALL_HOME/.local/bin:$PATH"
else
    echo "uv already installed"
fi

# 4. Install Python dependencies
echo "[4/8] Installing Python dependencies..."
cd "$PROJECT_DIR"
sudo -u $INSTALL_USER bash -c "export PATH=\"$INSTALL_HOME/.local/bin:\$PATH\" && uv sync"

# 5. Create labwc configuration for display rotation
echo "[5/8] Setting up labwc display configuration..."
sudo -u $INSTALL_USER mkdir -p "$INSTALL_HOME/.config/labwc"
sudo -u $INSTALL_USER cat > "$INSTALL_HOME/.config/labwc/rc.xml" << 'EOF'
<?xml version="1.0"?>
<labwc_config>
  <core>
    <gap>0</gap>
  </core>
  <!-- Screen rotation configuration -->
  <!-- Uncomment and adjust as needed for your display -->
  <!--
  <output>
    <name>DSI-2</name>
    <transform>90</transform>
  </output>
  -->
</labwc_config>
EOF
echo "Created labwc config at $INSTALL_HOME/.config/labwc/rc.xml"
echo "Edit this file to enable display rotation if needed"

# 6. Add user to audio and gpio groups for MIDI/GPIO access
echo "[6/8] Configuring user permissions..."
sudo usermod -a -G audio $INSTALL_USER
sudo usermod -a -G gpio $INSTALL_USER

# 7. Install systemd service
echo "[7/7] Installing systemd service..."
sudo cp "$PROJECT_DIR/linux/h9-control.service" /etc/systemd/system/
# Get UID for XDG_RUNTIME_DIR
INSTALL_UID=$(id -u $INSTALL_USER)
# Substitute template variables with actual values
sudo sed -i "s|__PROJECT_DIR__|$PROJECT_DIR|g" /etc/systemd/system/h9-control.service
sudo sed -i "s|__INSTALL_USER__|$INSTALL_USER|g" /etc/systemd/system/h9-control.service
sudo sed -i "s|__INSTALL_UID__|$INSTALL_UID|g" /etc/systemd/system/h9-control.service

sudo chmod +x "$PROJECT_DIR/linux/start-app.sh"
sudo systemctl daemon-reload

echo ""
echo "========================================="
echo "Installation complete!"
echo "========================================="
echo ""
echo "To enable auto-start on boot:"
echo "  sudo systemctl enable h9-control"
echo ""
echo "To start the service now:"
echo "  sudo systemctl start h9-control"
echo ""
echo "To view logs:"
echo "  journalctl -u h9-control -f"
echo ""
echo "To test manually (without systemd):"
echo "  $INSTALL_HOME/.local/bin/uv run python ui_main.py --fullscreen"
echo ""
echo "Note: You may need to log out and back in for group changes to take effect."
echo ""
