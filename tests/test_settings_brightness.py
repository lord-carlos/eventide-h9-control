from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtWidgets

from h9control.app.config import ConfigManager
from h9control.app.ui import qt_settings
from h9control.app.ui.qt_settings import SettingsWidget


def _ensure_qapplication() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _widget_with_backlight(
    monkeypatch: pytest.MonkeyPatch,
    config: ConfigManager,
    *,
    available: bool = True,
    writes: list[int],
    write_result: bool = True,
) -> SettingsWidget:
    monkeypatch.setattr(qt_settings.sd, "query_devices", lambda device=None, kind=None: [])
    app = _ensure_qapplication()
    widget = SettingsWidget(config)
    controller = widget._backlight
    monkeypatch.setattr(controller, "is_available", lambda: available)
    monkeypatch.setattr(
        controller,
        "set_brightness_percent",
        lambda value: writes.append(value) or write_result,
    )
    return widget


def test_brightness_slider_debounces_to_single_write(
    monkeypatch: pytest.MonkeyPatch, config: ConfigManager
) -> None:
    app = _ensure_qapplication()
    writes: list[int] = []
    widget = _widget_with_backlight(monkeypatch, config, writes=writes)

    # Two rapid slider ticks: nothing is written until the debounce fires.
    widget._brightness_slider.setValue(40)
    widget._brightness_slider.setValue(75)
    assert writes == []

    # Simulate the single-shot debounce timer expiring: exactly one write
    # with the last selected value.
    widget._apply_pending_brightness()

    assert writes == [75]
    widget.deleteLater()
    app.processEvents()


def test_brightness_debounce_applies_final_value_after_drag(
    monkeypatch: pytest.MonkeyPatch, config: ConfigManager
) -> None:
    app = _ensure_qapplication()
    writes: list[int] = []
    widget = _widget_with_backlight(monkeypatch, config, writes=writes)

    for value in (20, 45, 60, 90):
        widget._brightness_slider.setValue(value)
        assert writes == []  # still debouncing

    widget._apply_pending_brightness()
    assert writes == [90]
    widget.deleteLater()
    app.processEvents()


def test_brightness_write_failure_disables_slider(
    monkeypatch: pytest.MonkeyPatch, config: ConfigManager
) -> None:
    app = _ensure_qapplication()
    writes: list[int] = []
    widget = _widget_with_backlight(
        monkeypatch, config, writes=writes, write_result=False
    )

    widget._brightness_slider.setValue(55)
    widget._apply_pending_brightness()

    assert writes == [55]
    assert not widget._brightness_slider.isEnabled()
    widget.deleteLater()
    app.processEvents()


def test_brightness_without_backlight_never_writes(
    monkeypatch: pytest.MonkeyPatch, config: ConfigManager
) -> None:
    app = _ensure_qapplication()
    writes: list[int] = []
    widget = _widget_with_backlight(
        monkeypatch, config, writes=writes, available=False
    )
    assert not widget._brightness_slider.isEnabled()

    widget._brightness_slider.setValue(60)
    widget._apply_pending_brightness()

    assert writes == []
    widget.deleteLater()
    app.processEvents()
