from __future__ import annotations

import json
import os
import time
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
    manager.flush()  # setters persist via the debounced save

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
    # The debounced save must fail gracefully too (target path is a directory).
    manager.flush()
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
    assert keyboard["show_all_knobs"] == ["K"]
    assert len(keyboard) == 21


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


def _count_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """Patch ConfigManager._write_file to count real writes."""
    calls: list[str] = []
    original = ConfigManager._write_file

    def counting(self: ConfigManager, serialized: str) -> None:
        calls.append(serialized)
        original(self, serialized)

    monkeypatch.setattr(ConfigManager, "_write_file", counting)
    return calls


def test_save_batches_multiple_setters_into_one_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)
    writes = _count_writes(monkeypatch)

    manager.lock_delay = True
    manager.lock_feedback = True
    manager.lock_pitch = True
    # Not written yet: setters only mark the config dirty.
    assert not path.exists()
    manager.flush()
    assert path.exists()
    assert len(writes) == 1
    data = json.loads(path.read_text())
    assert data["lock_delay"] is True
    assert data["lock_feedback"] is True
    assert data["lock_pitch"] is True


def test_flush_without_changes_skips_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)
    writes = _count_writes(monkeypatch)

    manager.theme_mode = "dark"
    manager.flush()
    assert len(writes) == 1
    # Unchanged setter value → flush is a no-op.
    manager.theme_mode = "dark"
    manager.flush()
    assert len(writes) == 1
    # A second flush with no further changes writes nothing either.
    manager.flush()
    assert len(writes) == 1


def test_debounced_timer_persists_after_quiet_period(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    monkeypatch.setattr(ConfigManager, "_SAVE_DEBOUNCE_SECONDS", 0.05)
    manager = ConfigManager(path)
    manager.theme_mode = "dark"
    time.sleep(0.3)
    assert path.exists()
    assert json.loads(path.read_text())["theme_mode"] == "dark"


def test_save_synchronous_and_not_scheduled_by_debounce(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)
    manager.theme_mode = "dark"
    manager.save()  # public, immediate save still available
    assert path.exists()
    assert manager._save_timer is None
    assert manager._dirty is False


def test_autosave_disabled_keeps_disk_untouched_after_flush(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ConfigManager, "_SAVE_DEBOUNCE_SECONDS", 0.05)
    path = tmp_path / "config.json"
    raw = json.dumps({"theme_mode": "dark"})
    path.write_text(raw)
    manager = ConfigManager(path, autosave=False)

    manager.theme_mode = "light"
    manager.lock_delay = True
    manager.audio_input_device_id = 7
    # In-memory behavior is unchanged even though persistence is suppressed.
    assert manager.theme_mode == "light"
    manager.flush()
    assert len(list(tmp_path.glob("*.json*"))) == 1  # no .bak written either
    assert path.read_text() == raw


def test_autosave_disabled_save_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path, autosave=False)
    writes = _count_writes(monkeypatch)

    manager.theme_mode = "dark"
    manager.save()
    assert not path.exists()
    assert len(writes) == 0


def test_autosave_disabled_never_schedules_debounce_timer(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path, autosave=False)

    manager.theme_mode = "dark"
    assert manager._dirty is False  # request_save does not mark the config dirty
    assert manager._save_timer is None
    time.sleep(0.15)  # any scheduled debounce write would have fired by now
    assert not path.exists()


def test_autosave_disabled_on_missing_file_keeps_file_absent(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    manager = ConfigManager(path, autosave=False)
    assert manager.config == AppConfig.default()
    manager.audio_min_bpm = 75.0
    manager.flush()
    assert not path.exists()


def _temp_path_for(path: Path) -> Path:
    """The sibling temp file ConfigManager writes before os.replace."""
    return path.with_name(path.name + ".tmp")


def test_successful_save_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)
    manager.theme_mode = "dark"
    manager.flush()

    assert list(tmp_path.glob("*.tmp")) == []
    assert json.loads(path.read_text(encoding="utf-8"))["theme_mode"] == "dark"


def test_write_goes_through_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)
    replacements: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def recording_replace(src: str | Path, dst: str | Path) -> None:
        replacements.append((Path(src), Path(dst)))
        original_replace(src, dst)

    monkeypatch.setattr("h9control.app.config.os.replace", recording_replace)

    manager.theme_mode = "dark"
    manager.flush()

    assert replacements == [(_temp_path_for(path), path)]
    assert path.exists()


def test_write_failure_reports_save_error_and_keeps_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"theme_mode": "dark"}))
    previous_bytes = path.read_bytes()
    manager = ConfigManager(path)
    assert manager.save_error is None

    def failing_replace(src: str | Path, dst: str | Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("h9control.app.config.os.replace", failing_replace)

    manager.theme_mode = "light"
    manager.flush()

    assert manager.save_error == "disk full"
    assert manager._dirty is True
    assert manager.theme_mode == "light"  # change kept in memory
    assert path.read_bytes() == previous_bytes  # old file untouched
    assert not _temp_path_for(path).exists()  # temp file cleaned up


def test_save_error_cleared_after_successful_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(path)

    def failing_replace(src: str | Path, dst: str | Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("h9control.app.config.os.replace", failing_replace)
    manager.theme_mode = "dark"
    manager.flush()
    assert manager.save_error == "disk full"

    monkeypatch.undo()
    manager.flush()

    assert manager.save_error is None
    assert manager._dirty is False
    assert json.loads(path.read_text(encoding="utf-8"))["theme_mode"] == "dark"


def test_load_removes_stale_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"theme_mode": "dark"}))
    stale_temp = _temp_path_for(path)
    stale_temp.write_text('{"theme_mode": "lig')  # crash before os.replace

    manager = ConfigManager(path)

    assert not stale_temp.exists()
    assert manager.config.theme_mode == "dark"

