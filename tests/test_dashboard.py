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
            preset_name="PRISTINE DIGITAL",
            algorithm_key="DIGDLY",
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
    widget.deleteLater()
    app.processEvents()
