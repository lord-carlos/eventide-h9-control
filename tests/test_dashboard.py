from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from h9control.app.state import DashboardState, KnobBarState
from h9control.app.ui.qt_dashboard import DashboardWidget


def test_dashboard_renders_raw_knob_value() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    widget = DashboardWidget()
    widget.apply_state(
        DashboardState(
            connected=True,
            status_text="Simulated H9",
            preset_number=42,
            preset_name="PRISTINE DIGITAL",
            algorithm_key="DIGDLY",
            bpm=125.0,
            live_bpm=138.4,
            knobs=(
                KnobBarState(
                    name="DLY-A",
                    percent=50,
                    raw_value=12345,
                ),
            ),
        )
    )

    assert widget._knob_slots[0]._raw_value.text() == "12345"
    assert widget._status_dot.toolTip() == "Simulated H9"
    assert widget._status_text.text() == "Simulated H9"
    assert widget._preset_number.text() == "P042"
    assert widget._algorithm_name.text() == "DIGITAL DELAY"
    assert widget._algorithm_key.text() == "DIGDLY"
    assert widget._btn_bpm._value.text() == "125"
    assert widget._lbl_live_bpm._value.text() == "138.4"
    assert widget._knob_slots[0]._value.text() == "50%"
    widget.deleteLater()
    app.processEvents()


def test_dashboard_actions_remain_touchable() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    widget = DashboardWidget()

    actions: list[str] = []
    widget.prev_requested.connect(lambda: actions.append("prev"))
    widget.next_requested.connect(lambda: actions.append("next"))
    widget.connect_refresh_requested.connect(lambda: actions.append("refresh"))
    widget.sync_live_bpm_requested.connect(lambda: actions.append("sync"))
    widget.settings_requested.connect(lambda: actions.append("settings"))

    widget._btn_prev.click()
    widget._btn_next.click()
    widget._btn_bpm.click()
    widget._lbl_live_bpm.click()
    widget._btn_settings.click()

    assert actions == ["prev", "next", "refresh", "sync", "settings"]
    widget.deleteLater()
    app.processEvents()


def test_dashboard_marks_locked_secondary_knobs() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    widget = DashboardWidget()
    widget.apply_state(
        DashboardState(
            connected=True,
            status_text="Connected",
            lock_delay=True,
            knobs=(
                KnobBarState("DLY-A", 50, 100),
                KnobBarState("DLY-B", 50, 100),
            ),
        )
    )

    assert widget._knob_slots[0]._lock_badge.isHidden()
    assert not widget._knob_slots[1]._lock_badge.isHidden()
    widget.deleteLater()
    app.processEvents()


def test_dashboard_surfaces_connection_errors() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    widget = DashboardWidget()
    widget.apply_state(
        DashboardState(
            connected=False,
            status_text="Connect failed: no MIDI device",
        )
    )

    assert widget._status_text.text().startswith("Connect failed:")
    assert widget._status_text.toolTip() == "Connect failed: no MIDI device"
    assert "#ed6a68" in widget._status_dot.styleSheet()
    widget.deleteLater()
    app.processEvents()
