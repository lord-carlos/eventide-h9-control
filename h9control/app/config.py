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
    selected_channels: list[int] = field(default_factory=lambda: [0, 1])


@dataclass
class GpioBindingConfig:
    pin: int
    pull: str | None = "up"  # "up", "down", None
    edge: str = "falling"  # "rising", "falling", "both"
    debounce_ms: int = 50
    hold_threshold_ms: int = 500  # Time to distinguish tap from hold
    is_modifier: bool = (
        False  # If True, this button acts as a modifier for rotary encoders
    )
    modifier_name: str | None = (
        None  # Name to reference this modifier in rotary encoder configs
    )


@dataclass
class RotaryEncoderConfig:
    """Configuration for a quadrature-encoded rotary encoder."""

    clk_pin: int  # CLK pin (rotary encoder A signal)
    dt_pin: int  # DT pin (rotary encoder B signal)
    action_cw: str  # Action name for clockwise rotation
    action_ccw: str  # Action name for counter-clockwise rotation
    modifiers: dict[str, dict[str, str]] = field(
        default_factory=dict
    )  # modifier_name -> {action_cw, action_ccw}


@dataclass
class ShortcutsConfig:
    # Maps action names to list of key sequences. Each action can have multiple keys.
    # Each key can appear in multiple actions (one key → multiple actions).
    keyboard: dict[str, list[str]] = field(default_factory=dict)

    # Maps action names to GPIO config. Each pin can only bind to one action,
    # but actions can have both "tap" and "hold" variants (e.g., "next_preset" and "next_preset_hold").
    gpio: dict[str, GpioBindingConfig] = field(default_factory=dict)

    # Maps encoder names to rotary encoder config. Each encoder triggers two actions (CW/CCW).
    rotary_encoders: dict[str, RotaryEncoderConfig] = field(default_factory=dict)

    @classmethod
    def default(cls) -> ShortcutsConfig:
        # Default keyboard shortcuts matching current hardcoded bindings
        return cls(
            keyboard={
                "adjust_knob_1_up": ["1"],
                "adjust_knob_1_down": ["Q"],
                "adjust_knob_2_up": ["2"],
                "adjust_knob_2_down": ["W"],
                "adjust_knob_3_up": ["3"],
                "adjust_knob_3_down": ["E"],
                "adjust_knob_4_up": ["4"],
                "adjust_knob_4_down": ["R"],
                "adjust_bpm_up": ["5"],
                "adjust_bpm_down": ["T"],
                "sync_live_bpm": ["D"],
                "settings": ["S"],
            },
            gpio={},
        )


@dataclass
class AppConfig:
    audio: AudioConfig = field(default_factory=AudioConfig)
    shortcuts: ShortcutsConfig = field(default_factory=ShortcutsConfig.default)
    lock_delay: bool = False
    lock_feedback: bool = False
    lock_pitch: bool = False
    knob_order: tuple[str, ...] = ("DLY-A", "DLY-B", "FBK-A", "FBK-B")

    @classmethod
    def default(cls) -> AppConfig:
        return cls()


class ConfigManager:
    def __init__(self, config_path: Path | str = "config.json") -> None:
        self.config_path = Path(config_path)
        self.config = self.load()

    def load(self) -> AppConfig:
        if not self.config_path.exists():
            logging.info(
                f"Config file not found at {self.config_path}, using defaults."
            )
            return AppConfig.default()

        try:
            with open(self.config_path, "r") as f:
                data = json.load(f)

                audio_data = data.get("audio", {})
                audio_config = AudioConfig(
                    input_device_id=audio_data.get("input_device_id"),
                    input_channels=audio_data.get("input_channels", 1),
                    auto_bpm_mode=audio_data.get("auto_bpm_mode", "manual"),
                    selected_channels=audio_data.get("selected_channels", [0, 1]),
                )

                shortcuts_data = data.get("shortcuts", {})
                keyboard_data = shortcuts_data.get("keyboard", {})
                gpio_data = shortcuts_data.get("gpio", {})
                rotary_encoders_data = shortcuts_data.get("rotary_encoders", {})

                # Parse GPIO bindings
                gpio_bindings = {}
                for action, gpio_cfg in gpio_data.items():
                    gpio_bindings[action] = GpioBindingConfig(
                        pin=gpio_cfg["pin"],
                        pull=gpio_cfg.get("pull", "up"),
                        edge=gpio_cfg.get("edge", "falling"),
                        debounce_ms=gpio_cfg.get("debounce_ms", 50),
                        hold_threshold_ms=gpio_cfg.get("hold_threshold_ms", 500),
                        is_modifier=gpio_cfg.get("is_modifier", False),
                        modifier_name=gpio_cfg.get("modifier_name"),
                    )

                # Parse rotary encoder bindings
                rotary_encoder_bindings = {}
                for encoder_name, encoder_cfg in rotary_encoders_data.items():
                    rotary_encoder_bindings[encoder_name] = RotaryEncoderConfig(
                        clk_pin=encoder_cfg["clk_pin"],
                        dt_pin=encoder_cfg["dt_pin"],
                        action_cw=encoder_cfg["action_cw"],
                        action_ccw=encoder_cfg["action_ccw"],
                        modifiers=encoder_cfg.get("modifiers", {}),
                    )

                shortcuts_config = ShortcutsConfig(
                    keyboard=keyboard_data
                    if keyboard_data
                    else ShortcutsConfig.default().keyboard,
                    gpio=gpio_bindings,
                    rotary_encoders=rotary_encoder_bindings,
                )

                lock_delay = data.get("lock_delay", False)
                lock_feedback = data.get("lock_feedback", False)
                lock_pitch = data.get("lock_pitch", False)
                knob_order_list = data.get(
                    "knob_order", ["DLY-A", "DLY-B", "FBK-A", "FBK-B"]
                )
                knob_order = tuple(knob_order_list)

                return AppConfig(
                    audio=audio_config,
                    shortcuts=shortcuts_config,
                    lock_delay=lock_delay,
                    lock_feedback=lock_feedback,
                    lock_pitch=lock_pitch,
                    knob_order=knob_order,
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

    @property
    def audio_selected_channels(self) -> list[int]:
        return self.config.audio.selected_channels

    @audio_selected_channels.setter
    def audio_selected_channels(self, value: list[int]) -> None:
        self.config.audio.selected_channels = value
        self.save()

    @property
    def lock_delay(self) -> bool:
        return self.config.lock_delay

    @lock_delay.setter
    def lock_delay(self, value: bool) -> None:
        self.config.lock_delay = value
        self.save()

    @property
    def lock_feedback(self) -> bool:
        return self.config.lock_feedback

    @lock_feedback.setter
    def lock_feedback(self, value: bool) -> None:
        self.config.lock_feedback = value
        self.save()

    @property
    def lock_pitch(self) -> bool:
        return self.config.lock_pitch

    @lock_pitch.setter
    def lock_pitch(self, value: bool) -> None:
        self.config.lock_pitch = value
        self.save()

    @property
    def knob_order(self) -> tuple[str, ...]:
        return self.config.knob_order

    @knob_order.setter
    def knob_order(self, value: tuple[str, ...]) -> None:
        self.config.knob_order = value
        self.save()
