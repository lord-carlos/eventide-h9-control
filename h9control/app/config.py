from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class AudioConfig:
    input_device_id: int | None = None
    input_channels: int = 1
    auto_bpm_mode: str = "manual"  # "manual" or "continuous"


@dataclass
class AppConfig:
    audio: AudioConfig = field(default_factory=AudioConfig)

    @classmethod
    def default(cls) -> AppConfig:
        return cls()


class ConfigManager:
    def __init__(self, config_path: Path | str = "config.json") -> None:
        self.config_path = Path(config_path)
        self.config = self.load()

    def load(self) -> AppConfig:
        if not self.config_path.exists():
            logging.info(f"Config file not found at {self.config_path}, using defaults.")
            return AppConfig.default()

        try:
            with open(self.config_path, "r") as f:
                data = json.load(f)
                audio_data = data.get("audio", {})
                return AppConfig(
                    audio=AudioConfig(
                        input_device_id=audio_data.get("input_device_id"),
                        input_channels=audio_data.get("input_channels", 1),
                        auto_bpm_mode=audio_data.get("auto_bpm_mode", "manual"),
                    )
                )
        except Exception as e:
            logging.error(f"Failed to load config: {e}")
            return AppConfig.default()

    def save(self) -> None:
        try:
            with open(self.config_path, "w") as f:
                json.dump(asdict(self.config), f, indent=4)
        except Exception as e:
            logging.error(f"Failed to save config: {e}")

    @property
    def audio_input_device_id(self) -> int | None:
        return self.config.audio.input_device_id

    @audio_input_device_id.setter
    def audio_input_device_id(self, value: int | None) -> None:
        self.config.audio.input_device_id = value
        self.save()

    @property
    def audio_input_channels(self) -> int:
        return self.config.audio.input_channels

    @audio_input_channels.setter
    def audio_input_channels(self, value: int) -> None:
        self.config.audio.input_channels = value
        self.save()

    @property
    def auto_bpm_mode(self) -> str:
        return self.config.audio.auto_bpm_mode

    @auto_bpm_mode.setter
    def auto_bpm_mode(self, value: str) -> None:
        self.config.audio.auto_bpm_mode = value
        self.save()
