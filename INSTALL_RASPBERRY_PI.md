# Raspberry Pi Installation Guide (Lite / Kiosk)

This guide describes how to set up the Eventide H9 Control UI on a fresh installation of **Raspberry Pi OS Lite (64-bit)**.

## 1. System Preparation

Update your system and install the necessary system dependencies for running a Wayland-based GUI and MIDI:

```bash
sudo apt update
sudo apt upgrade -y

# Install Git and build tools
sudo apt install -y git build-essential

# Install Wayland compositor (labwc) and GUI dependencies
sudo apt install -y labwc libegl1 libgles2 libwayland-client0 \
    libxkbcommon0 libglib2.0-0 libfontconfig1 libdbus-1-3 \
    libasound2-dev libjack-jackd2-dev # For MIDI (rtmidi)
```

## 2. Install `uv`

We use `uv` to manage the Python environment and dependencies.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env
```

## 3. Clone and Setup Project

```bash
git clone https://github.com/YOUR_USERNAME/eventide-h9-control.git
cd eventide-h9-control

# Install Python dependencies
uv sync
```

## 4. Configure Autostart (Systemd)

To have the UI start automatically on boot:

1.  **Enable Autologin**:
    Run `sudo raspi-config`, go to `System Options` -> `Boot / Auto Login` and select `Console Autologin`.

2.  **Install the Service**:
    Copy the provided service file to the systemd directory:

    ```bash
    sudo cp linux/h9-control.service /etc/systemd/system/
    sudo chmod +x linux/start-app.sh
    ```

3.  **Enable and Start**:

    ```bash
    sudo systemctl daemon-reload
    sudo systemctl enable h9-control
    sudo systemctl start h9-control
    ```

## 5. Troubleshooting

- **Logs**: Check the application logs using `journalctl -u h9-control -f`.
- **Permissions**: Ensure the user `pi` has access to MIDI devices (usually in the `audio` group).
  ```bash
  sudo usermod -a -G audio pi
  ```
- **Display**: If you are using a specific touchscreen, you might need to configure the output in `labwc` or via `wlr-randr`.
