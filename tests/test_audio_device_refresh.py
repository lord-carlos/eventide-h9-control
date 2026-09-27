from __future__ import annotations

import pytest
from PySide6 import QtWidgets

from h9control.app.config import ConfigManager
from h9control.app.ui import qt_settings
from h9control.app.ui.qt_settings import SettingsWidget
from h9control.audio import device_refresh


def _ensure_qapplication() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _mock_audio_devices(monkeypatch, devices: list[dict]):
    current_devices = {"items": devices}

    def query_devices(device=None, kind=None):
        items = current_devices["items"]
        if device is None:
            return items
        return items[device]

    monkeypatch.setattr(qt_settings.sd, "query_devices", query_devices)
    return current_devices


def test_refresh_portaudio_device_list_reinitializes_portaudio(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(device_refresh.sd, "_terminate", lambda: calls.append("stop"))
    monkeypatch.setattr(
        device_refresh.sd, "_initialize", lambda: calls.append("initialize")
    )

    device_refresh.refresh_portaudio_device_list()

    assert calls == ["stop", "initialize"]


def test_refresh_audio_devices_stops_stream_before_refresh_and_restarts(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class Detector:
        running = True

        def stop(self) -> None:
            calls.append("stop")

        def start(self) -> None:
            calls.append("start")

    monkeypatch.setattr(
        device_refresh,
        "refresh_portaudio_device_list",
        lambda: calls.append("portaudio"),
    )

    def update_device_list() -> int:
        calls.append("settings")
        return 2

    result = device_refresh.refresh_audio_devices(Detector(), update_device_list)

    assert result == 2
    assert calls == ["stop", "portaudio", "settings", "start"]


def test_refresh_audio_devices_restarts_stream_if_list_update_fails(monkeypatch) -> None:
    calls: list[str] = []

    class Detector:
        running = True

        def stop(self) -> None:
            calls.append("stop")

        def start(self) -> None:
            calls.append("start")

    monkeypatch.setattr(
        device_refresh,
        "refresh_portaudio_device_list",
        lambda: calls.append("portaudio"),
    )

    def fail_to_update() -> int:
        calls.append("settings")
        raise RuntimeError("device query failed")

    with pytest.raises(RuntimeError, match="device query failed"):
        device_refresh.refresh_audio_devices(Detector(), fail_to_update)

    assert calls == ["stop", "portaudio", "settings", "start"]


def test_refresh_does_not_start_detector_that_was_idle(monkeypatch) -> None:
    calls: list[str] = []

    class Detector:
        running = False

        def stop(self) -> None:
            calls.append("stop")

        def start(self) -> None:
            calls.append("start")

    monkeypatch.setattr(
        device_refresh,
        "refresh_portaudio_device_list",
        lambda: calls.append("portaudio"),
    )

    device_refresh.refresh_audio_devices(
        Detector(), lambda: calls.append("settings") or 1
    )

    assert calls == ["stop", "portaudio", "settings"]


def test_refresh_discovers_device_and_preserves_selected_device_after_index_shift(
    monkeypatch, config: ConfigManager
) -> None:
    app = _ensure_qapplication()
    devices = _mock_audio_devices(
        monkeypatch,
        [
            {"name": "Built-in Input", "max_input_channels": 4, "hostapi": 0},
            {"name": "Output Only", "max_input_channels": 0, "hostapi": 0},
        ],
    )
    config.audio_input_device_id = 0
    config.audio_selected_channels = [1, 2]
    widget = SettingsWidget(config)
    audio_changes: list[bool] = []
    widget.audio_settings_changed.connect(lambda: audio_changes.append(True))

    devices["items"] = [
        {"name": "New USB Input", "max_input_channels": 2, "hostapi": 0},
        {"name": "Built-in Input", "max_input_channels": 4, "hostapi": 0},
        {"name": "Output Only", "max_input_channels": 0, "hostapi": 0},
    ]
    device_count = widget.refresh_devices()

    assert device_count == 2
    assert widget._device_combo.findText("New USB Input") >= 0
    assert widget._device_combo.currentText() == "Built-in Input"
    assert widget._device_combo.currentData() == 1
    assert config.audio_input_device_id == 1
    assert config.audio_selected_channels == [1, 2]
    assert widget._channel_left_combo.currentData() == 1
    assert widget._channel_right_combo.currentData() == 2
    assert audio_changes == []
    assert "2 input devices" in widget._device_refresh_status.text()

    widget.deleteLater()
    app.processEvents()


def test_refresh_keeps_newly_discovered_device_unselected(
    monkeypatch, config: ConfigManager
) -> None:
    app = _ensure_qapplication()
    devices = _mock_audio_devices(monkeypatch, [])
    widget = SettingsWidget(config)
    assert widget._device_combo.currentIndex() == -1

    devices["items"] = [
        {"name": "New USB Input", "max_input_channels": 2, "hostapi": 0}
    ]
    widget.refresh_devices()

    assert widget._device_combo.count() == 1
    assert widget._device_combo.currentIndex() == -1
    assert config.audio_input_device_id is None

    widget._device_combo.setCurrentIndex(0)
    assert config.audio_input_device_id == 0

    widget.deleteLater()
    app.processEvents()


def test_refresh_button_emits_app_level_refresh_request(config: ConfigManager) -> None:
    app = _ensure_qapplication()
    widget = SettingsWidget(config)
    requests: list[bool] = []
    widget.audio_devices_refresh_requested.connect(lambda: requests.append(True))

    widget._btn_refresh_devices.click()

    assert requests == [True]
    widget.deleteLater()
    app.processEvents()
