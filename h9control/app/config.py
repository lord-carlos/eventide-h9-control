from __future__ import annotations

import atexit
import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class AudioConfig:
    input_device_id: int | None = None
    input_channels: int = 1
    auto_bpm_mode: str = "manual"  # "manual" or "continuous"
    selected_channels: list[int] = field(default_factory=lambda: [0, 1])
    min_bpm: float = 90.0  # Minimum detectable BPM for beat detection
    max_bpm: float = 150.0  # Maximum detectable BPM for beat detection


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
                # Preset navigation / refresh defaults, matching the keys used
                # in the example config.json (digits 1-5 are taken by the
                # knob adjustments, so jumps use the Z/X/C/V/B row instead).
                "next_preset": ["Right"],
                "prev_preset": ["Left"],
                "connect_refresh": ["F5"],
                "jump_to_preset_1": ["Z"],
                "jump_to_preset_2": ["X"],
                "jump_to_preset_3": ["C"],
                "jump_to_preset_4": ["V"],
                "jump_to_preset_5": ["B"],
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
    theme_mode: str = "system"  # "system", "light", or "dark"

    @classmethod
    def default(cls) -> AppConfig:
        return cls()


class ConfigManager:
    _SAVE_DEBOUNCE_SECONDS = 0.75

    def __init__(
        self,
        config_path: Path | str = "config.json",
        autosave: bool = True,
    ) -> None:
        self.config_path = Path(config_path)
        # When autosave is False (dev/sim/screenshot runs via --no-config-save),
        # setters still update the in-memory config but nothing is ever written
        # back to the config file: save()/flush() are disk-write no-ops and
        # request_save() does not schedule timers.
        self._autosave = autosave
        # Message of the last load failure (None if the current config loaded
        # cleanly). Intended to be surfaced by the settings dialog so a
        # corrupted config.json is not silently swallowed by the UI.
        self.load_error: str | None = None
        # Save-batching state, guarded by _lock. Setters only mark the config
        # dirty; request_save() schedules a single debounced flush so many
        # toggles in quick succession result in one disk write.
        self._lock = threading.RLock()
        self._dirty = False
        self._last_written: str | None = None
        self._save_timer: threading.Timer | None = None
        self.config = self.load()
        # Safety net so pending changes are not lost on a quick app exit.
        if self._autosave:
            atexit.register(self.flush)

    def load(self) -> AppConfig:
        if not self.config_path.exists():
            logging.info(
                f"Config file not found at {self.config_path}, using defaults."
            )
            self.load_error = None
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
                    min_bpm=audio_data.get("min_bpm", 90.0),
                    max_bpm=audio_data.get("max_bpm", 150.0),
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
                theme_mode = data.get("theme_mode", "system")

                self.load_error = None
                return AppConfig(
                    audio=audio_config,
                    shortcuts=shortcuts_config,
                    lock_delay=lock_delay,
                    lock_feedback=lock_feedback,
                    lock_pitch=lock_pitch,
                    knob_order=knob_order,
                    theme_mode=theme_mode,
                )
        except Exception as e:
            logging.error(f"Failed to load config from {self.config_path}: {e}")
            self.load_error = str(e)
            self._backup_corrupt_file()
            return AppConfig.default()

    def _backup_corrupt_file(self) -> None:
        """Preserve a failing config file as ``<name>.json.bak`` if possible."""
        if not self.config_path.exists():
            return
        backup_path = self.config_path.with_suffix(".json.bak")
        try:
            backup_path.write_bytes(self.config_path.read_bytes())
            logging.info(f"Preserved failing config as {backup_path}")
        except OSError as e:
            logging.error(f"Could not back up failing config file: {e}")

    def _serialize(self) -> str:
        return json.dumps(asdict(self.config), indent=4)

    def _write_file(self, serialized: str) -> None:
        with open(self.config_path, "w") as f:
            f.write(serialized)

    def save(self) -> None:
        """Write the config to disk immediately (skipping a no-change write)."""
        if not self._autosave:
            return
        with self._lock:
            self._cancel_timer_locked()
            self._write_locked()

    def request_save(self) -> None:
        """Mark the config dirty and schedule a single debounced save.

        Every scheduled save is cancelled/replaced by the next call, so a burst
        of setters writes the file only once, after the debounce interval.
        """
        if not self._autosave:
            return
        with self._lock:
            self._dirty = True
            self._cancel_timer_locked()
            timer = threading.Timer(self._SAVE_DEBOUNCE_SECONDS, self.flush)
            timer.daemon = True
            self._save_timer = timer
        timer.start()

    def flush(self) -> None:
        """Persist pending changes now (no-op if nothing changed)."""
        if not self._autosave:
            return
        with self._lock:
            self._cancel_timer_locked()
            if not self._dirty:
                return
            self._write_locked()

    def _write_locked(self) -> None:
        # Caller must hold self._lock. Skips the write when the serialized
        # content is identical to the last successful write.
        serialized = self._serialize()
        if serialized == self._last_written:
            self._dirty = False
            return
        try:
            self._write_file(serialized)
        except Exception as e:
            logging.error(f"Failed to save config: {e}")
            self._dirty = True
            return
        self._last_written = serialized
        self._dirty = False

    def _cancel_timer_locked(self) -> None:
        # Caller must hold self._lock.
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None

    @property
    def audio_input_device_id(self) -> int | None:
        return self.config.audio.input_device_id

    @audio_input_device_id.setter
    def audio_input_device_id(self, value: int | None) -> None:
        self.config.audio.input_device_id = value
        self.request_save()

    @property
    def audio_input_channels(self) -> int:
        return self.config.audio.input_channels

    @audio_input_channels.setter
    def audio_input_channels(self, value: int) -> None:
        self.config.audio.input_channels = value
        self.request_save()

    @property
    def auto_bpm_mode(self) -> str:
        return self.config.audio.auto_bpm_mode

    @auto_bpm_mode.setter
    def auto_bpm_mode(self, value: str) -> None:
        self.config.audio.auto_bpm_mode = value
        self.request_save()

    @property
    def audio_selected_channels(self) -> list[int]:
        return self.config.audio.selected_channels

    @audio_selected_channels.setter
    def audio_selected_channels(self, value: list[int]) -> None:
        self.config.audio.selected_channels = value
        self.request_save()

    @property
    def audio_min_bpm(self) -> float:
        return self.config.audio.min_bpm

    @audio_min_bpm.setter
    def audio_min_bpm(self, value: float) -> None:
        self.config.audio.min_bpm = value
        self.request_save()

    @property
    def audio_max_bpm(self) -> float:
        return self.config.audio.max_bpm

    @audio_max_bpm.setter
    def audio_max_bpm(self, value: float) -> None:
        self.config.audio.max_bpm = value
        self.request_save()

    @property
    def lock_delay(self) -> bool:
        return self.config.lock_delay

    @lock_delay.setter
    def lock_delay(self, value: bool) -> None:
        self.config.lock_delay = value
        self.request_save()

    @property
    def lock_feedback(self) -> bool:
        return self.config.lock_feedback

    @lock_feedback.setter
    def lock_feedback(self, value: bool) -> None:
        self.config.lock_feedback = value
        self.request_save()

    @property
    def lock_pitch(self) -> bool:
        return self.config.lock_pitch

    @lock_pitch.setter
    def lock_pitch(self, value: bool) -> None:
        self.config.lock_pitch = value
        self.request_save()

    @property
    def knob_order(self) -> tuple[str, ...]:
        return self.config.knob_order

    @knob_order.setter
    def knob_order(self, value: tuple[str, ...]) -> None:
        self.config.knob_order = value
        self.request_save()

    @property
    def theme_mode(self) -> str:
        return self.config.theme_mode

    @theme_mode.setter
    def theme_mode(self, value: str) -> None:
        self.config.theme_mode = value
        self.request_save()
