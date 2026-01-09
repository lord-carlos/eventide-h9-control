# Raspberry Pi Installation

Quick setup for Raspberry Pi OS (Raspbian/Synthion OS).

## 1. Clone and Install

```bash
git clone https://github.com/YOUR_USERNAME/eventide-h9-control.git
cd eventide-h9-control
sudo ./linux/install.sh
```

The install script will:
- Install system dependencies (Wayland compositor, Qt6, MIDI, audio libraries, GPIO libraries)
- Install `uv` (Python environment manager)
- Install Python dependencies
- Create a default `config.json`
- Add your user to the `audio` and `gpio` groups (for MIDI and GPIO access)
- Install the systemd service

## 2. Enable Auto-Start

```bash
sudo systemctl enable h9-control
sudo systemctl start h9-control
```

## 3. View Logs

```bash
journalctl -u h9-control -f
```

## Manual Testing

To run manually without systemd:

```bash
~/.local/bin/uv run python ui_main.py --fullscreen
```

## Configuration

Edit `config.json` to customize:
- Audio input device (configure from Settings UI)
- Keyboard shortcuts
- GPIO pins (see `config.example-with-gpio.json`)
- Knob lock settings

## GPIO Hardware Buttons

If using GPIO buttons, ensure you have the required permissions:
- The installer adds your user to the `gpio` group automatically
- You must **log out and back in** (or reboot) for group changes to take effect
- GPIO buttons require `rpi-lgpio` package (already in dependencies)
- System libraries (`libgpiod2`) are installed automatically

**Note:** Log out and back in after installation for group permissions to take effect.
