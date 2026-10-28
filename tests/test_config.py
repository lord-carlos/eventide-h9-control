from __future__ import annotations

import json
from pathlib import Path

import pytest

from h9control.app.config import (
    AppConfig,
    AudioConfig,
    ConfigManager,
    GpioBindingConfig,
    RotaryEncoderConfig,
    ShortcutsConfig,
)


def test_missing_config_file_returns_defaults(tmp_path: Path) -> None:
    manager = ConfigManager(tmp_path / "missing.json")
    assert manager.config == AppConfig.default()
    assert manager.auto_bpm_mode == "manual"
    assert manager.knob_order == ("DLY-A", "DLY-B", "FBK-A", "FBK-B")
    assert manager.theme_mode == "system"
    assert "adjust_knob_1_up" in manager.config.shortcuts.keyboard


def test_corrupt_json_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not valid json!!")
    manager = ConfigManager(path)
    assert manager.config == AppConfig.default()


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)
    manager.audio_input_device_id = 3
    manager.audio_selected_channels = [2, 3]
    manager.lock_delay = True
    manager.lock_feedback = True
    manager.lock_pitch = True
    manager.knob_order = ("DLY-A", "DLY-B")
    manager.theme_mode = "dark"
    manager.auto_bpm_mode = "continuous"

    reloaded = ConfigManager(path)
    assert reloaded.audio_input_device_id == 3
    assert reloaded.audio_selected_channels == [2, 3]
    assert reloaded.lock_delay is True
    assert reloaded.lock_feedback is True
    assert reloaded.lock_pitch is True
    assert reloaded.knob_order == ("DLY-A", "DLY-B")
    assert reloaded.theme_mode == "dark"
    assert reloaded.auto_bpm_mode == "continuous"
    assert reloaded.audio_min_bpm == 90.0
    assert reloaded.audio_max_bpm == 150.0


def test_load_full_custom_config(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "audio": {
                    "input_device_id": 5,
                    "input_channels": 2,
                    "auto_bpm_mode": "continuous",
                    "selected_channels": [1, 2],
                    "min_bpm": 70.0,
                    "max_bpm": 180.0,
                },
                "shortcuts": {
                    "keyboard": {"next_preset": ["Right"]},
                    "gpio": {
                        "next_preset": {
                            "pin": 17,
                            "pull": "down",
                            "edge": "rising",
                            "debounce_ms": 80,
                            "hold_threshold_ms": 700,
                        }
                    },
                    "rotary_encoders": {
                        "main": {
                            "clk_pin": 23,
                            "dt_pin": 24,
                            "action_cw": "next_preset",
                            "action_ccw": "prev_preset",
                            "modifiers": {
                                "shift": {"action_cw": "adjust_knob_1_up"}
                            },
                        }
                    },
                },
                "lock_delay": True,
                "knob_order": ["DLY-A"],
                "theme_mode": "darker",
            }
        )
    )

    config = ConfigManager(path).config
    assert config.audio == AudioConfig(
        input_device_id=5,
        input_channels=2,
        auto_bpm_mode="continuous",
        selected_channels=[1, 2],
        min_bpm=70.0,
        max_bpm=180.0,
    )
    assert config.shortcuts.keyboard == {"next_preset": ["Right"]}
    assert config.shortcuts.gpio == {
        "next_preset": GpioBindingConfig(
            pin=17, pull="down", edge="rising", debounce_ms=80, hold_threshold_ms=700
        )
    }
    assert config.shortcuts.rotary_encoders == {
        "main": RotaryEncoderConfig(
            clk_pin=23,
            dt_pin=24,
            action_cw="next_preset",
            action_ccw="prev_preset",
            modifiers={"shift": {"action_cw": "adjust_knob_1_up"}},
        )
    }
    assert config.lock_delay is True
    assert config.lock_feedback is False
    assert config.knob_order == ("DLY-A",)
    assert config.theme_mode == "darker"


def test_empty_shortcuts_falls_back_to_default_keyboard(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"shortcuts": {}}))
    config = ConfigManager(path).config
    assert config.shortcuts.keyboard == ShortcutsConfig.default().keyboard


def test_partial_audio_config_uses_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"audio": {"input_device_id": 2}}))
    config = ConfigManager(path).config
    assert config.audio.input_device_id == 2
    assert config.audio.input_channels == 1
    assert config.audio.min_bpm == 90.0


def test_save_to_unwritable_path_does_not_raise(tmp_path: Path) -> None:
    manager = ConfigManager(tmp_path)
    manager.theme_mode = "dark"
    assert not (tmp_path / "config.json").exists()


def test_property_setters_update_config_in_memory(tmp_path: Path) -> None:
    manager = ConfigManager(tmp_path / "config.json")
    manager.audio_min_bpm = 75.0
    manager.audio_max_bpm = 160.0
    assert manager.config.audio.min_bpm == 75.0
    assert manager.config.audio.max_bpm == 160.0


def test_default_shortcuts_have_expected_actions() -> None:
    keyboard = ShortcutsConfig.default().keyboard
    assert keyboard["sync_live_bpm"] == ["D"]
    assert keyboard["settings"] == ["S"]
    assert len(keyboard) == 20


def test_default_shortcuts_cover_preset_navigation() -> None:
    keyboard = ShortcutsConfig.default().keyboard
    assert keyboard["next_preset"] == ["Right"]
    assert keyboard["prev_preset"] == ["Left"]
    assert keyboard["connect_refresh"] == ["F5"]
    for index in range(1, 6):
        assert keyboard[f"jump_to_preset_{index}"]

    # No navigation key may collide with the knob/BPM/settings bindings.
    navigation_keys: set[str] = set()
    for action in (
        "next_preset",
        "prev_preset",
        "connect_refresh",
        *(f"jump_to_preset_{i}" for i in range(1, 6)),
    ):
        navigation_keys.update(keyboard[action])
    previously_defined = {"1", "Q", "2", "W", "3", "E", "4", "R", "5", "T", "D", "S"}
    assert not navigation_keys & previously_defined


def test_corrupt_json_backed_up_and_reports_load_error(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    raw = "{not valid json!!"
    path.write_text(raw)
    manager = ConfigManager(path)
    assert manager.config == AppConfig.default()
    assert manager.load_error  # error message exposed for the UI
    backup = path.with_suffix(".json.bak")
    assert backup.exists()
    assert backup.read_text() == raw


def test_load_error_cleared_after_successful_load(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{broken")
    manager = ConfigManager(path)
    assert manager.load_error
    path.write_text(json.dumps({"theme_mode": "dark"}))
    reloaded = ConfigManager(path)
    assert reloaded.load_error is None
    assert reloaded.config.theme_mode == "dark"


def test_clean_and_missing_configs_report_no_load_error(tmp_path: Path) -> None:
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps({"theme_mode": "dark"}))
    assert ConfigManager(valid).load_error is None
    assert ConfigManager(tmp_path / "missing.json").load_error is None
