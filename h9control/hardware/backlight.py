"""Simple Linux backlight control."""

from __future__ import annotations

import logging
import os
from pathlib import Path


class BacklightController:
    """Simple backlight controller for Linux."""

    def __init__(self) -> None:
        self.device_path: Path | None = None
        self.max_brightness: int = 100
        self._detect_device()

    def _detect_device(self) -> None:
        """Detect first available backlight device."""
        backlight_dir = Path("/sys/class/backlight")
        if not backlight_dir.exists():
            return

        # Find first available device
        for device in backlight_dir.iterdir():
            if device.is_dir():
                self.device_path = device
                # Read max brightness
                max_file = device / "max_brightness"
                if max_file.exists():
                    try:
                        self.max_brightness = int(max_file.read_text().strip())
                        logging.info(
                            f"Detected backlight: {device.name}, max={self.max_brightness}"
                        )
                        return
                    except (ValueError, IOError):
                        pass
                self.device_path = None

    def is_available(self) -> bool:
        """Check if backlight control is available."""
        return self.device_path is not None

    def get_brightness_percent(self) -> int | None:
        """Get current brightness as percentage (10-100)."""
        if not self.device_path:
            return None

        try:
            actual_file = self.device_path / "actual_brightness"
            if actual_file.exists():
                actual = int(actual_file.read_text().strip())
            else:
                # Fallback to brightness file
                brightness_file = self.device_path / "brightness"
                actual = int(brightness_file.read_text().strip())

            # Convert to percentage (min 10%)
            percent = int((actual / self.max_brightness) * 100)
            return max(10, min(100, percent))
        except (ValueError, IOError) as e:
            logging.error(f"Failed to read brightness: {e}")
            return None

    def set_brightness_percent(self, percent: int) -> bool:
        """Set brightness from percentage (10-100)."""
        if not self.device_path:
            return False

        try:
            # Clamp to 10-100%
            percent = max(10, min(100, percent))

            # Convert percentage to hardware value
            value = int((percent / 100) * self.max_brightness)

            brightness_file = self.device_path / "brightness"
            brightness_file.write_text(str(value))
            logging.debug(
                f"Set brightness to {percent}% ({value}/{self.max_brightness})"
            )
            return True
        except PermissionError:
            logging.error(
                f"Permission denied writing to {self.device_path}/brightness. "
                "Run with sudo or add user to video group."
            )
            return False
        except (ValueError, IOError) as e:
            logging.error(f"Failed to set brightness: {e}")
            return False
