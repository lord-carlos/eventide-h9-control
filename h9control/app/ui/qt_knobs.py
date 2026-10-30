from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from h9control.app.state import DashboardState, KnobBarState
from h9control.app.ui.qt_settings import TouchScrollHandler
from h9control.domain.algorithms import human_algorithm_name
from h9control.domain.knob_control import (
    midi_cc_from_raw,
    percent_from_raw,
    raw_from_midi_cc,
)
from h9control.domain.knob_display import format_knob_value


# === CONFIGURATION CONSTANTS ===

# Layout
_KNOB_PAGE_COLUMNS = 2
_KNOB_PAGE_MARGIN = 20
_KNOB_PAGE_SPACING = 12
_KNOB_GRID_SPACING = 14
_KNOB_TILE_MIN_HEIGHT = 152
_KNOB_SLIDER_HEIGHT = 44
_KNOB_STEP_BUTTON_HEIGHT = 52

# Typography (point sizes)
_FONT_SIZE_PAGE_TITLE = 24
_FONT_SIZE_TILE_LABEL = 20
_FONT_SIZE_TILE_VALUE = 26
_FONT_SIZE_TILE_BADGE = 14
_FONT_SIZE_STEP_BUTTON = 26
_FONT_SIZE_HEADER_LABEL = 14
_FONT_SIZE_STATUS = 13

# A drag emits at most one CC per interval; the final value is flushed on
# release so the device always lands on what the finger left behind.
_KNOB_SEND_DEBOUNCE_MS = 40

# Secondary knob of a locked pair, matching the dashboard's lock semantics.
_LOCKED_SECONDARIES = {
    "lock_delay": "DLY-B",
    "lock_feedback": "FBK-B",
    "lock_pitch": "PICH-B",
}


def _make_tile_style() -> str:
    return """
        QFrame#knob-tile {
            background-color: palette(window);
            border: 1px solid #384550;
            border-radius: 8px;
        }
        QFrame#knob-tile QLabel {
            color: palette(window-text);
        }
        QFrame#knob-tile QLabel:disabled {
            color: #7f8992;
        }
        QLabel#knob-name {
            color: #9aa5af;
        }
        QLabel#knob-name:disabled {
            color: #7f8992;
        }
        QLabel#knob-value {
            color: palette(highlight);
        }
        QLabel#knob-value:disabled {
            color: #7f8992;
        }
        QLabel#knob-lock-badge {
            color: #d7a74a;
        }
        QPushButton#knob-step-button {
            color: palette(button-text);
            background-color: palette(button);
            border: 1px solid palette(mid);
            border-radius: 7px;
            padding: 4px;
        }
        QPushButton#knob-step-button:pressed {
            background-color: palette(highlight);
            color: palette(highlighted-text);
        }
        QPushButton#knob-step-button:disabled {
            color: #7f8992;
        }
        QSlider::groove:horizontal {
            height: 14px;
            background: #3d454d;
            border-radius: 7px;
        }
        QSlider::sub-page:horizontal {
            background: palette(highlight);
            border-radius: 7px;
        }
        QSlider::handle:horizontal {
            width: 46px;
            margin: -17px 0;
            border-radius: 12px;
            background: #f2f5f7;
            border: 2px solid #9aa5af;
        }
        QSlider::handle:horizontal:disabled {
            background: #68727c;
            border-color: #58616a;
        }
        QSlider::sub-page:horizontal:disabled {
            background: #68727c;
        }
    """


class _KnobTile(QtWidgets.QFrame):
    """One touch-friendly knob: name, value, slider and fine-step buttons."""

    set_requested = QtCore.Signal(str, int)  # knob name, absolute CC (0-127)
    step_requested = QtCore.Signal(str, int)  # knob name, delta

    def __init__(self, fonts: QtGui.QFont, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("knob-tile")
        self.setMinimumHeight(_KNOB_TILE_MIN_HEIGHT)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )

        self._knob_name = ""
        self._algorithm_key: str | None = None
        self._cc_value = 0
        self._dragging = False
        self._updating = False
        self._pending_cc: int | None = None

        self._name_label = QtWidgets.QLabel("—")
        self._name_label.setObjectName("knob-name")
        self._name_label.setFont(fonts)

        self._lock_badge = QtWidgets.QLabel("LOCKED")
        self._lock_badge.setObjectName("knob-lock-badge")
        badge_font = QtGui.QFont(fonts)
        badge_font.setPointSize(_FONT_SIZE_TILE_BADGE)
        badge_font.setBold(True)
        self._lock_badge.setFont(badge_font)
        self._lock_badge.setVisible(False)

        value_font = QtGui.QFont(fonts)
        value_font.setPointSize(_FONT_SIZE_TILE_VALUE)
        value_font.setBold(True)
        self._value_label = QtWidgets.QLabel("—")
        self._value_label.setObjectName("knob-value")
        self._value_label.setFont(value_font)
        self._value_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        self._slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self._slider.setRange(0, 127)
        self._slider.setValue(0)
        self._slider.setMinimumHeight(_KNOB_SLIDER_HEIGHT)
        self._slider.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._slider.setAccessibleName("Knob value")

        button_font = QtGui.QFont(fonts)
        button_font.setPointSize(_FONT_SIZE_STEP_BUTTON)
        button_font.setBold(True)

        self._btn_down = QtWidgets.QPushButton("−")
        self._btn_up = QtWidgets.QPushButton("+")
        for button in (self._btn_down, self._btn_up):
            button.setObjectName("knob-step-button")
            button.setFont(button_font)
            button.setFixedHeight(_KNOB_STEP_BUTTON_HEIGHT)
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

        self._btn_down.clicked.connect(
            lambda: self.step_requested.emit(self._knob_name, -1)
        )
        self._btn_up.clicked.connect(
            lambda: self.step_requested.emit(self._knob_name, +1)
        )

        self._send_timer = QtCore.QTimer(self)
        self._send_timer.setSingleShot(True)
        self._send_timer.setInterval(_KNOB_SEND_DEBOUNCE_MS)
        self._send_timer.timeout.connect(self._flush_pending)

        self._slider.valueChanged.connect(self._on_slider_changed)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addWidget(self._name_label)
        header.addStretch(1)
        header.addWidget(self._lock_badge)
        header.addWidget(self._value_label)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(10)
        buttons.addWidget(self._btn_down, 1)
        buttons.addWidget(self._btn_up, 1)

        layout.addLayout(header)
        layout.addWidget(self._slider)
        layout.addLayout(buttons)

        self.setStyleSheet(_make_tile_style())

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def knob_name(self) -> str:
        return self._knob_name

    def is_dragging(self) -> bool:
        return self._dragging

    def apply_state(
        self,
        knob: KnobBarState | None,
        *,
        algorithm_key: str | None,
        enabled: bool = True,
    ) -> None:
        """Render one knob; None disables the tile (no data for the slot)."""
        self._algorithm_key = algorithm_key
        self._updating = True
        try:
            if knob is None:
                self._knob_name = ""
                self._name_label.setText("—")
                self._value_label.setText("—")
                self._cc_value = 0
                self._slider.setValue(0)
                self._lock_badge.setVisible(False)
                self.setEnabled(False)
                return

            self._knob_name = knob.name
            self._name_label.setText(knob.name)
            self._value_label.setText(
                knob.pretty if knob.pretty is not None else f"{knob.percent}%"
            )

            cc = midi_cc_from_raw(knob.raw_value)
            self._cc_value = cc
            # Never fight the finger: a slider being dragged keeps its value
            # until release, even if a refresh lands in between.
            if not self._dragging:
                self._slider.setValue(cc)

            self.setEnabled(enabled)
            self._lock_badge.setVisible(not enabled)
        finally:
            self._updating = False

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _on_slider_pressed(self) -> None:
        self._dragging = True

    def _on_slider_released(self) -> None:
        self._dragging = False
        self._flush_pending()

    def _on_slider_changed(self, value: int) -> None:
        if self._updating:
            return
        self._cc_value = value
        self._pending_cc = value
        self._value_label.setText(self._format_cc(value))
        self._send_timer.start()

    def _flush_pending(self) -> None:
        self._send_timer.stop()
        if self._pending_cc is None:
            return
        cc = self._pending_cc
        self._pending_cc = None
        if self._knob_name:
            self.set_requested.emit(self._knob_name, cc)

    def _format_cc(self, cc: int) -> str:
        raw = raw_from_midi_cc(cc)
        formatted = format_knob_value(
            algorithm_key=self._algorithm_key,
            knob_name=self._knob_name,
            raw_value=raw,
        )
        if formatted is not None:
            return formatted.label
        return f"{percent_from_raw(raw)}%"


class KnobPageWidget(QtWidgets.QWidget):
    """Full-knob page: every knob of the current algorithm, scrollable."""

    back_requested = QtCore.Signal()
    set_knob_requested = QtCore.Signal(str, int)
    adjust_knob_requested = QtCore.Signal(str, int)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._tiles: list[_KnobTile] = []
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(
            _KNOB_PAGE_MARGIN,
            _KNOB_PAGE_MARGIN,
            _KNOB_PAGE_MARGIN,
            _KNOB_PAGE_MARGIN,
        )
        layout.setSpacing(_KNOB_PAGE_SPACING)

        title_font = QtGui.QFont()
        title_font.setPointSize(_FONT_SIZE_PAGE_TITLE)
        title_font.setBold(True)

        header_font = QtGui.QFont()
        header_font.setPointSize(_FONT_SIZE_HEADER_LABEL)

        self._btn_back = QtWidgets.QPushButton("◀ BACK")
        self._btn_back.setObjectName("knob-back-button")
        self._btn_back.setMinimumSize(120, 46)
        self._btn_back.setFont(header_font)
        self._btn_back.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_back.clicked.connect(self.back_requested.emit)

        self._title = QtWidgets.QLabel("ALL KNOBS")
        self._title.setFont(title_font)

        self._preset_label = QtWidgets.QLabel("P---")
        self._preset_label.setFont(header_font)
        self._preset_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        self._algorithm_label = QtWidgets.QLabel("—")
        self._algorithm_label.setObjectName("knob-page-algorithm")
        subtle_font = QtGui.QFont(header_font)
        subtle_font.setBold(True)
        self._algorithm_label.setFont(subtle_font)

        self._status_label = QtWidgets.QLabel("")
        status_font = QtGui.QFont()
        status_font.setPointSize(_FONT_SIZE_STATUS)
        self._status_label.setFont(status_font)
        self._status_label.setStyleSheet("color: #9aa5af;")
        self._status_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )

        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(12)
        top_bar.addWidget(self._btn_back)
        top_bar.addWidget(self._title)
        top_bar.addStretch(1)
        top_bar.addWidget(self._preset_label)

        info_bar = QtWidgets.QHBoxLayout()
        info_bar.setContentsMargins(0, 0, 0, 0)
        info_bar.setSpacing(12)
        info_bar.addWidget(self._algorithm_label)
        info_bar.addStretch(1)
        info_bar.addWidget(self._status_label)

        layout.addLayout(top_bar)
        layout.addLayout(info_bar)

        # Scrollable grid: 2 columns keep the sliders wide enough for a finger,
        # so all ten knobs scroll vertically instead of being squeezed.
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll_area.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        scroll_area.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_AcceptTouchEvents, True
        )
        scroll_area.viewport().setAttribute(
            QtCore.Qt.WidgetAttribute.WA_AcceptTouchEvents, True
        )
        scroll_area.grabGesture(QtCore.Qt.GestureType.PanGesture)
        scroll_area.setStyleSheet(
            """
            QScrollArea { border: none; }
            QScrollBar:vertical {
                width: 24px;
                background: transparent;
            }
            QScrollBar::handle:vertical {
                background: #888888;
                border-radius: 12px;
                min-height: 40px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            """
        )
        self._scroll_handler = TouchScrollHandler(scroll_area)

        content = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(content)
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setHorizontalSpacing(_KNOB_GRID_SPACING)
        grid.setVerticalSpacing(_KNOB_GRID_SPACING)
        for column in range(_KNOB_PAGE_COLUMNS):
            grid.setColumnStretch(column, 1)

        tile_font = QtGui.QFont()
        tile_font.setPointSize(_FONT_SIZE_TILE_LABEL)
        tile_font.setBold(True)

        for index in range(10):
            tile = _KnobTile(tile_font)
            tile.set_requested.connect(self.set_knob_requested.emit)
            tile.step_requested.connect(self.adjust_knob_requested.emit)
            grid.addWidget(
                tile, index // _KNOB_PAGE_COLUMNS, index % _KNOB_PAGE_COLUMNS
            )
            self._tiles.append(tile)

        # Keep the tiles at their natural height when the viewport is taller.
        grid.setRowStretch((len(self._tiles) // _KNOB_PAGE_COLUMNS) + 1, 1)
        scroll_area.setWidget(content)
        self._scroll_area = scroll_area

        layout.addWidget(scroll_area, 1)

        self.setStyleSheet(
            """
            QLabel#knob-page-algorithm {
                color: palette(highlight);
            }
            QPushButton#knob-back-button {
                color: palette(button-text);
                background-color: palette(button);
                border: 1px solid palette(mid);
                border-radius: 7px;
                padding: 6px;
            }
            QPushButton#knob-back-button:pressed {
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
            """
        )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def apply_state(self, state: DashboardState) -> None:
        knobs = state.all_knobs or state.knobs

        if state.preset_number is None:
            self._preset_label.setText("P---")
        else:
            self._preset_label.setText(f"P{state.preset_number:03d}")

        self._algorithm_label.setText(
            human_algorithm_name(state.algorithm_key, state.algorithm_name)
        )
        self._status_label.setText(state.status_text or "")

        for index, tile in enumerate(self._tiles):
            knob = knobs[index] if index < len(knobs) else None
            enabled = knob is not None and self._is_knob_enabled(state, knob.name)
            tile.apply_state(knob, algorithm_key=state.algorithm_key, enabled=enabled)

    @staticmethod
    def _is_knob_enabled(state: DashboardState, knob_name: str) -> bool:
        name = knob_name.upper()
        for lock_flag, secondary in _LOCKED_SECONDARIES.items():
            if getattr(state, lock_flag) and name == secondary:
                return False
        return True
