from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

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


def _make_widget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str | None
) -> SettingsWidget:
    """Build a SettingsWidget offscreen against a temp config path."""
    monkeypatch.setattr(qt_settings.sd, "query_devices", lambda device=None, kind=None: [])
    path = tmp_path / "config.json"
    if raw is not None:
        path.write_text(raw)
    app = _ensure_qapplication()
    widget = SettingsWidget(ConfigManager(path))
    widget.deleteLater()
    return widget


def test_corrupt_config_shows_red_banner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    widget = _make_widget(monkeypatch, tmp_path, raw="{not valid json!!")

    assert widget.config.load_error
    label = widget._config_status
    assert not label.isHidden()
    assert "config.json could not be loaded" in label.text()
    assert "config.json.bak" in label.text()
    assert "color: #b00020" in label.styleSheet()
    widget.deleteLater()


def test_clean_config_keeps_banner_hidden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    widget = _make_widget(monkeypatch, tmp_path, raw='{"theme_mode": "dark"}')

    assert widget.config.load_error is None
    assert widget._config_status.isHidden()
    widget.deleteLater()


def test_missing_config_keeps_banner_hidden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A file that does not exist is not an error: defaults load silently.
    widget = _make_widget(monkeypatch, tmp_path, raw=None)

    assert widget.config.load_error is None
    assert widget._config_status.isHidden()
    widget.deleteLater()


def test_show_event_refreshes_banner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    widget = _make_widget(monkeypatch, tmp_path, raw="{broken")
    assert not widget._config_status.isHidden()

    # Simulate a successful config reload on the same manager instance.
    widget.config.load_error = None
    widget.show()
    app = _ensure_qapplication()
    app.processEvents()

    assert widget._config_status.isHidden()
    widget.deleteLater()
    app.processEvents()
