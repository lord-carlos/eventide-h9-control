from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from h9control.app.state import DashboardState, KnobBarState
from h9control.app.ui.qt_knobs import KnobPageWidget
from h9control.domain.knob_control import midi_cc_from_raw
from h9control.protocol.codes import MAX_KNOB_VALUE_14BIT

_KNOB_NAMES = (
    "MIX",
    "DLYMIX",
    "DLY-A",
    "DLY-B",
    "FBK-A",
    "FBK-B",
    "XFADE",
    "DEPTH",
    "SPEED",
    "FILTER",
)


def _ensure_qapplication() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


def _knob_states(raw_value: int = 0x4000) -> tuple[KnobBarState, ...]:
    percent = round(raw_value / MAX_KNOB_VALUE_14BIT * 100)
    return tuple(
        KnobBarState(name=name, percent=percent, raw_value=raw_value)
        for name in _KNOB_NAMES
    )


def _state(**overrides) -> DashboardState:
    knobs = _knob_states()
    values = {
        "connected": True,
        "status_text": "Simulated H9",
        "preset_number": 42,
        "preset_name": "PRISTINE DIGITAL",
        "algorithm_name": "DIGDLY",
        "algorithm_key": "DIGDLY",
        "knobs": knobs[:4],
        "all_knobs": knobs,
    }
    values.update(overrides)
    return DashboardState(**values)


def test_knob_page_renders_every_knob() -> None:
    app = _ensure_qapplication()
    page = KnobPageWidget()
    page.apply_state(_state())

    assert len(page._tiles) == 10
    assert [tile.knob_name() for tile in page._tiles] == list(_KNOB_NAMES)
    assert page._preset_label.text() == "P042"
    assert page._algorithm_label.text() == "DIGITAL DELAY"
    assert page._status_label.text() == "Simulated H9"

    expected_cc = midi_cc_from_raw(0x4000)
    assert page._tiles[0]._slider.value() == expected_cc
    assert page._tiles[0]._slider.isEnabled()

    page.deleteLater()
    app.processEvents()


def test_knob_page_tiles_show_pretty_values() -> None:
    app = _ensure_qapplication()
    page = KnobPageWidget()
    page.apply_state(
        _state(
            all_knobs=(
                KnobBarState("MIX", 100, MAX_KNOB_VALUE_14BIT, pretty="100%"),
                KnobBarState("DLY-A", 50, 0x4000, pretty="1/4"),
            ),
            knobs=(),
        )
    )

    assert page._tiles[0]._value_label.text() == "100%"
    assert page._tiles[1]._value_label.text() == "1/4"

    page.deleteLater()
    app.processEvents()


def test_step_buttons_emit_named_delta() -> None:
    app = _ensure_qapplication()
    page = KnobPageWidget()
    page.apply_state(_state())
    deltas: list[tuple[str, int]] = []
    page.adjust_knob_requested.connect(lambda name, delta: deltas.append((name, delta)))

    page._tiles[2]._btn_up.click()
    page._tiles[2]._btn_down.click()

    assert deltas == [("DLY-A", 1), ("DLY-A", -1)]
    page.deleteLater()
    app.processEvents()


def test_slider_sends_absolute_cc_after_debounce() -> None:
    app = _ensure_qapplication()
    page = KnobPageWidget()
    page.apply_state(_state())
    sent: list[tuple[str, int]] = []
    page.set_knob_requested.connect(lambda name, cc: sent.append((name, cc)))

    tile = page._tiles[0]
    tile._slider.setValue(100)

    # Debounced: nothing is sent until the timer fires (or the drag ends).
    assert sent == []
    assert tile._value_label.text() == "79%"

    tile._flush_pending()
    assert sent == [("MIX", 100)]
    assert tile._value_label.text() == "79%"

    page.deleteLater()
    app.processEvents()


def test_slider_drag_survives_state_refresh() -> None:
    app = _ensure_qapplication()
    page = KnobPageWidget()
    page.apply_state(_state())
    tile = page._tiles[0]
    original = tile._slider.value()

    tile._slider.sliderPressed.emit()
    assert tile.is_dragging() is True

    # A refresh landing mid-drag must not move the slider under the finger.
    page.apply_state(_state(all_knobs=_knob_states(MAX_KNOB_VALUE_14BIT)))
    assert tile._slider.value() == original

    tile._slider.sliderReleased.emit()
    assert tile.is_dragging() is False

    page.apply_state(_state(all_knobs=_knob_states(MAX_KNOB_VALUE_14BIT)))
    assert tile._slider.value() == 127

    page.deleteLater()
    app.processEvents()


def test_locked_secondary_knob_is_disabled() -> None:
    app = _ensure_qapplication()
    page = KnobPageWidget()
    page.apply_state(_state(lock_feedback=True))

    # FBK-B is the secondary feedback knob.
    assert page._tiles[5].knob_name() == "FBK-B"
    assert page._tiles[5].isEnabled() is False
    assert not page._tiles[5]._lock_badge.isHidden()
    assert page._tiles[4].isEnabled() is True

    page.deleteLater()
    app.processEvents()


def test_missing_knobs_are_disabled() -> None:
    app = _ensure_qapplication()
    page = KnobPageWidget()
    page.apply_state(_state(all_knobs=_knob_states()[:3], knobs=()))

    assert page._tiles[2].isEnabled() is True
    assert page._tiles[3].isEnabled() is False
    assert page._tiles[3].knob_name() == ""

    page.deleteLater()
    app.processEvents()


def test_back_button_emits() -> None:
    app = _ensure_qapplication()
    page = KnobPageWidget()
    backs: list[bool] = []
    page.back_requested.connect(lambda: backs.append(True))

    page._btn_back.click()

    assert backs == [True]
    page.deleteLater()
    app.processEvents()
