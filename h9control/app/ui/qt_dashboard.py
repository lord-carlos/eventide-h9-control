from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

from h9control.app.config import ConfigManager
from h9control.app.state import DashboardState


# === CONFIGURATION CONSTANTS ===

# Window
_DASHBOARD_SIZE = QtCore.QSize(1280, 720)

# Typography (point sizes)
_FONT_SIZE_TITLE = 36  # Preset name
_FONT_SIZE_SUBTITLE = 22  # Algorithm, knob labels
_FONT_SIZE_VALUE = 20  # BPM/Live numbers
_FONT_SIZE_LABEL = 12  # "BPM"/"Live" text
_FONT_SIZE_RAW_VALUE = 11  # Raw value below progress bar

# Layout spacing & margins
_ROOT_MARGIN = 32  # Outer margin around entire dashboard
_SECTION_SPACING = 24  # Vertical spacing between sections
_KNOB_GROUP_SPACING = 32  # Horizontal spacing between DLY-A/B and FBK-A/B
_KNOB_INTERNAL_SPACING = 12  # Vertical spacing inside knob widget (label -> bar -> value)

# Layout stretch factors (vertical proportions)
_STRETCH_TOP = 1  # Top section (DLY knobs)
_STRETCH_CENTER = 2  # Center section (preset/algorithm)
_STRETCH_BOTTOM = 1  # Bottom section (FBK knobs + BPM)
_STRETCH_CENTER_TEXT_TOP = 1  # Stretch above preset name
_STRETCH_CENTER_TEXT_BOTTOM = 1  # Stretch below algorithm

# Widget dimensions
_PROGRESS_BAR_HEIGHT = 18  # Progress bar thickness
_BUTTON_PREV_NEXT_WIDTH = 120  # Width of ◀/▶ buttons
_BUTTON_PREV_NEXT_HEIGHT = 120  # Height of ◀/▶ buttons
_BUTTON_BPM_WIDTH = 180  # Width of BPM button
_BUTTON_BPM_HEIGHT = 90  # Height of BPM button
_STATUS_DOT_SIZE = 32  # Status indicator dot


@dataclass(frozen=True)
class _Fonts:
    title: QtGui.QFont  # Preset name
    subtitle: QtGui.QFont  # Algorithm, knob labels
    value: QtGui.QFont  # BPM/Live numbers
    label: QtGui.QFont  # "BPM"/"Live" text
    raw_value: QtGui.QFont  # Raw value below progress bar


def _make_fonts() -> _Fonts:
    title = QtGui.QFont()
    title.setPointSize(_FONT_SIZE_TITLE)
    title.setBold(True)

    subtitle = QtGui.QFont()
    subtitle.setPointSize(_FONT_SIZE_SUBTITLE)
    subtitle.setBold(True)

    value = QtGui.QFont()
    value.setPointSize(_FONT_SIZE_VALUE)
    value.setBold(True)

    label = QtGui.QFont()
    label.setPointSize(_FONT_SIZE_LABEL)
    label.setBold(False)

    raw_value = QtGui.QFont()
    raw_value.setPointSize(_FONT_SIZE_RAW_VALUE)
    raw_value.setBold(False)

    return _Fonts(title=title, subtitle=subtitle, value=value, label=label, raw_value=raw_value)


class _LabeledProgress(QtWidgets.QWidget):
    def __init__(self, fonts: _Fonts, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        self._label = QtWidgets.QLabel("—")
        self._label.setFont(fonts.subtitle)

        self._bar = QtWidgets.QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(_PROGRESS_BAR_HEIGHT)
        radius = _PROGRESS_BAR_HEIGHT // 2
        self._bar.setStyleSheet(
            "\n".join(
                (
                    "QProgressBar {",
                    "  border: 0px;",
                    "  background: palette(mid);",
                    f"  border-radius: {radius}px;",
                    "}",
                    "QProgressBar::chunk {",
                    "  background: palette(highlight);",
                    f"  border-radius: {radius}px;",
                    "}",
                )
            )
        )

        self._raw_value = QtWidgets.QLabel("")
        self._raw_value.setFont(fonts.raw_value)
        self._raw_value.setStyleSheet("color: #888;")
        self._raw_value.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_KNOB_INTERNAL_SPACING)
        layout.addWidget(self._label)
        layout.addWidget(self._bar)
        layout.addWidget(self._raw_value)

    def set_state(self, *, name: str, percent: int, pretty: str | None, raw_value: int | None = None) -> None:
        if pretty:
            # Example: "DLY-A  1/8 note" -> user-friendly label
            self._label.setText(f"{name}  {pretty}")
        else:
            self._label.setText(name)
        self._bar.setValue(max(0, min(100, percent)))
        
        if raw_value is not None:
            self._raw_value.setText(f"{raw_value}")
        else:
            self._raw_value.setText("")

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the widget (grayed out when disabled)."""
        self.setEnabled(enabled)
        radius = _PROGRESS_BAR_HEIGHT // 2
        if enabled:
            self._label.setStyleSheet("")
            self._raw_value.setStyleSheet("color: #888;")
            self._bar.setStyleSheet(
                "\n".join(
                    (
                        "QProgressBar {",
                        "  border: 0px;",
                        "  background: palette(mid);",
                        f"  border-radius: {radius}px;",
                        "}",
                        "QProgressBar::chunk {",
                        "  background: palette(highlight);",
                        f"  border-radius: {radius}px;",
                        "}",
                    )
                )
            )
        else:
            self._label.setStyleSheet("color: #888;")
            self._raw_value.setStyleSheet("color: #555;")
            self._bar.setStyleSheet(
                "\n".join(
                    (
                        "QProgressBar {",
                        "  border: 0px;",
                        "  background: #444;",
                        f"  border-radius: {radius}px;",
                        "}",
                        "QProgressBar::chunk {",
                        "  background: #666;",
                        f"  border-radius: {radius}px;",
                        "}",
                    )
                )
            )

class DashboardWidget(QtWidgets.QWidget):
    connect_refresh_requested = QtCore.Signal()
    next_requested = QtCore.Signal()
    prev_requested = QtCore.Signal()
    adjust_knob_requested = QtCore.Signal(str, int)
    adjust_knob_slot_requested = QtCore.Signal(int, int)  # slot_index, delta
    adjust_bpm_requested = QtCore.Signal(int)
    sync_live_bpm_requested = QtCore.Signal()
    settings_requested = QtCore.Signal()

    def __init__(self, config: ConfigManager | None = None) -> None:
        super().__init__()
        self._config = config
        fonts = _make_fonts()

        # --- widgets ---
        self._status_dot = QtWidgets.QLabel("●")
        self._status_dot.setFont(fonts.subtitle)
        self._status_dot.setFixedSize(_STATUS_DOT_SIZE, _STATUS_DOT_SIZE)
        self._status_dot.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self._status_dot.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._status_dot.mousePressEvent = lambda e: self.settings_requested.emit()

        # Create 4 knob slots (populated dynamically from state.knobs)
        self._knob_slots = [_LabeledProgress(fonts) for _ in range(4)]

        self._preset_name = QtWidgets.QLabel("—")
        self._preset_name.setFont(fonts.title)
        self._preset_name.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self._algorithm_key = QtWidgets.QLabel("—")
        self._algorithm_key.setFont(fonts.subtitle)
        self._algorithm_key.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self._btn_prev = QtWidgets.QPushButton("◀")
        self._btn_prev.setFont(fonts.title)
        self._btn_prev.setFixedSize(_BUTTON_PREV_NEXT_WIDTH, _BUTTON_PREV_NEXT_HEIGHT)
        self._btn_prev.clicked.connect(self.prev_requested.emit)

        self._btn_next = QtWidgets.QPushButton("▶")
        self._btn_next.setFont(fonts.title)
        self._btn_next.setFixedSize(_BUTTON_PREV_NEXT_WIDTH, _BUTTON_PREV_NEXT_HEIGHT)
        self._btn_next.clicked.connect(self.next_requested.emit)

        self._btn_bpm = QtWidgets.QPushButton("— BPM")
        self._btn_bpm.setFont(fonts.value)
        self._btn_bpm.setFixedSize(_BUTTON_BPM_WIDTH, _BUTTON_BPM_HEIGHT)
        self._btn_bpm.clicked.connect(self.connect_refresh_requested.emit)

        self._lbl_live_bpm = QtWidgets.QLabel("— Live")
        self._lbl_live_bpm.setFont(fonts.value)
        self._lbl_live_bpm.setFixedSize(_BUTTON_BPM_WIDTH, _BUTTON_BPM_HEIGHT)
        self._lbl_live_bpm.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._lbl_live_bpm.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._lbl_live_bpm.setStyleSheet("border: 1px solid #444; border-radius: 4px;")

        self._fonts = fonts

        top_line = QtWidgets.QFrame()
        top_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        top_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)

        bottom_line = QtWidgets.QFrame()
        bottom_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        bottom_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)

        # --- top section ---
        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)

        dly_row = QtWidgets.QHBoxLayout()
        dly_row.setSpacing(_KNOB_GROUP_SPACING)

        dly_group = QtWidgets.QWidget()
        dly_group_layout = QtWidgets.QHBoxLayout(dly_group)
        dly_group_layout.setContentsMargins(0, 0, 0, 0)
        dly_group_layout.setSpacing(_KNOB_GROUP_SPACING)
        dly_group_layout.addWidget(self._knob_slots[0], 1)
        dly_group_layout.addWidget(self._knob_slots[1], 1)
        # Store reference to ensure FBK group matches this width
        self._dly_group = dly_group

        top_right = QtWidgets.QWidget()
        top_right.setFixedWidth(self._btn_bpm.width())
        top_right_layout = QtWidgets.QHBoxLayout(top_right)
        top_right_layout.setContentsMargins(0, 0, 0, 0)
        top_right_layout.addStretch(1)
        top_right_layout.addWidget(self._status_dot, 0, alignment=QtCore.Qt.AlignmentFlag.AlignRight)

        # Left-bound group (~50% width), empty spacer, then a fixed-width right area.
        dly_row.addWidget(dly_group, 1, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        dly_row.addStretch(1)
        dly_row.addWidget(top_right, 0, alignment=QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignRight)
        top_layout.addLayout(dly_row)

        # --- center section ---
        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QHBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(_SECTION_SPACING)

        mid_text = QtWidgets.QWidget()
        mid_text_layout = QtWidgets.QVBoxLayout(mid_text)
        mid_text_layout.setContentsMargins(0, 0, 0, 0)
        mid_text_layout.setSpacing(16)
        mid_text_layout.addStretch(_STRETCH_CENTER_TEXT_TOP)
        mid_text_layout.addWidget(self._preset_name)
        mid_text_layout.addWidget(self._algorithm_key)
        mid_text_layout.addStretch(_STRETCH_CENTER_TEXT_BOTTOM)

        center_layout.addWidget(self._btn_prev, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
        center_layout.addStretch(1)
        center_layout.addWidget(mid_text)
        center_layout.addStretch(1)
        center_layout.addWidget(self._btn_next, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

        # --- bottom section ---
        bottom = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(0)

        fbk_group = QtWidgets.QWidget()
        fbk_group_layout = QtWidgets.QHBoxLayout(fbk_group)
        fbk_group_layout.setContentsMargins(0, 0, 0, 0)
        fbk_group_layout.setSpacing(_KNOB_GROUP_SPACING)
        fbk_group_layout.addWidget(self._knob_slots[2], 1)
        fbk_group_layout.addWidget(self._knob_slots[3], 1)
        # Store reference to sync width with DLY group
        self._fbk_group = fbk_group

        bottom_layout.addWidget(fbk_group, 1, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self._lbl_live_bpm, 0, alignment=QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignRight)
        bottom_layout.addSpacing(_KNOB_GROUP_SPACING)
        bottom_layout.addWidget(self._btn_bpm, 0, alignment=QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignRight)

        # --- root layout ---
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(_ROOT_MARGIN, _ROOT_MARGIN, _ROOT_MARGIN, _ROOT_MARGIN)
        layout.setSpacing(_SECTION_SPACING)
        layout.addWidget(top, _STRETCH_TOP)
        layout.addWidget(top_line)
        layout.addWidget(center, _STRETCH_CENTER)
        layout.addWidget(bottom_line)
        layout.addWidget(bottom, _STRETCH_BOTTOM)

        self._apply_state(DashboardState(connected=False, status_text="Disconnected"))

        self._install_shortcuts()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        """Keep DLY and FBK groups the same width."""
        super().resizeEvent(event)
        # Match FBK group width to DLY group width
        if hasattr(self, '_dly_group') and hasattr(self, '_fbk_group'):
            dly_width = self._dly_group.width()
            if dly_width > 0:
                self._fbk_group.setMaximumWidth(dly_width)
                self._fbk_group.setMinimumWidth(dly_width)

    def _install_shortcuts(self) -> None:
        # Map action names to callables that trigger signals
        action_map: dict[str, Callable[[], None]] = {
            "next_preset": lambda: self.next_requested.emit(),
            "prev_preset": lambda: self.prev_requested.emit(),
            "connect_refresh": lambda: self.connect_refresh_requested.emit(),
            "settings": lambda: self.settings_requested.emit(),
            "sync_live_bpm": lambda: self.sync_live_bpm_requested.emit(),
            "adjust_bpm_up": lambda: self.adjust_bpm_requested.emit(+1),
            "adjust_bpm_down": lambda: self.adjust_bpm_requested.emit(-1),
            "adjust_knob_1_up": lambda: self.adjust_knob_slot_requested.emit(0, +1),
            "adjust_knob_1_down": lambda: self.adjust_knob_slot_requested.emit(0, -1),
            "adjust_knob_2_up": lambda: self.adjust_knob_slot_requested.emit(1, +1),
            "adjust_knob_2_down": lambda: self.adjust_knob_slot_requested.emit(1, -1),
            "adjust_knob_3_up": lambda: self.adjust_knob_slot_requested.emit(2, +1),
            "adjust_knob_3_down": lambda: self.adjust_knob_slot_requested.emit(2, -1),
            "adjust_knob_4_up": lambda: self.adjust_knob_slot_requested.emit(3, +1),
            "adjust_knob_4_down": lambda: self.adjust_knob_slot_requested.emit(3, -1),
        }

        # Get keyboard shortcuts from config or use empty dict if no config
        keyboard_shortcuts = {}
        if self._config is not None:
            keyboard_shortcuts = self._config.config.shortcuts.keyboard

        # Build a map: key_sequence -> list of actions
        # This supports one key triggering multiple actions
        key_to_actions: dict[str, list[Callable[[], None]]] = {}
        for action_name, key_sequences in keyboard_shortcuts.items():
            handler = action_map.get(action_name)
            if handler is None:
                continue  # Unknown action, skip
            
            for key_seq in key_sequences:
                if key_seq not in key_to_actions:
                    key_to_actions[key_seq] = []
                key_to_actions[key_seq].append(handler)

        # Create QShortcut for each unique key, triggering all bound actions
        for key_seq, handlers in key_to_actions.items():
            sc = QtGui.QShortcut(QtGui.QKeySequence(key_seq), self)
            sc.setContext(QtCore.Qt.ShortcutContext.WindowShortcut)
            
            # Trigger all actions bound to this key
            def make_multi_handler(funcs: list[Callable[[], None]]) -> Callable[[], None]:
                def multi_handler() -> None:
                    for func in funcs:
                        func()
                return multi_handler
            
            sc.activated.connect(make_multi_handler(handlers))

    def apply_state(self, state: DashboardState) -> None:
        self._apply_state(state)

    def _apply_state(self, state: DashboardState) -> None:
        # status dot
        if state.connected:
            self._status_dot.setStyleSheet("color: #2ecc71;")
        else:
            self._status_dot.setStyleSheet("color: #999999;")

        # center text
        self._preset_name.setText(state.preset_name or "—")
        self._algorithm_key.setText(state.algorithm_key or "—")

        # BPM displays (buttons don't support rich text, so use simpler formatting)
        if state.bpm is None:
            self._btn_bpm.setText("— BPM")
        else:
            self._btn_bpm.setText(f"{state.bpm:.0f} BPM")

        if state.live_bpm is None:
            self._lbl_live_bpm.setText("—")
        else:
            live_html = f'<span style="font-size:{_FONT_SIZE_VALUE}pt; font-weight:bold;">{state.live_bpm:.1f}</span> <span style="font-size:{_FONT_SIZE_LABEL}pt;">Live</span>'
            self._lbl_live_bpm.setText(live_html)


        # Apply knobs to slots with smart greying for locked pairs
        for slot_index, widget in enumerate(self._knob_slots):
            if slot_index >= len(state.knobs):
                # No knob data for this slot - hide it
                widget.setVisible(False)
                continue
            
            knob = state.knobs[slot_index]
            name = knob.name
            
            # Determine if this knob should be greyed out (secondary in locked pair)
            enabled = True
            if state.lock_delay and name == "DLY-B":
                enabled = False
            elif state.lock_feedback and name == "FBK-B":
                enabled = False
            elif state.lock_pitch and name == "PICH-B":
                enabled = False
            
            self._apply_knob(widget, knob, fallback_label=name, enabled=enabled)

    @staticmethod
    def _apply_knob(widget: "_LabeledProgress", knob: object | None, *, fallback_label: str, enabled: bool = True) -> None:
        if knob is None:
            widget.setVisible(False)
            return
        widget.setVisible(True)
        name = getattr(knob, "name", fallback_label)
        percent = int(getattr(knob, "percent", 0))
        pretty = getattr(knob, "pretty", None)
        raw_value = getattr(knob, "value", None)
        widget.set_state(name=name, percent=percent, pretty=pretty, raw_value=raw_value)
        widget.set_enabled(enabled)


from h9control.app.config import ConfigManager
from h9control.app.ui.qt_settings import SettingsWidget


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self.setWindowTitle("H9 Dashboard")
        self.resize(_DASHBOARD_SIZE)
        self.setMinimumSize(QtCore.QSize(360, 640))

        self.stack = QtWidgets.QStackedWidget()
        self.setCentralWidget(self.stack)

        self.dashboard = DashboardWidget(config)
        self.settings = SettingsWidget(config)

        self.stack.addWidget(self.dashboard)
        self.stack.addWidget(self.settings)

        self.dashboard.settings_requested.connect(self._show_settings)
        self.settings.back_requested.connect(self._show_dashboard)

    def _show_settings(self) -> None:
        self.stack.setCurrentWidget(self.settings)

    def _show_dashboard(self) -> None:
        self.stack.setCurrentWidget(self.dashboard)



def configure_fullscreen(window: QtWidgets.QMainWindow) -> None:
    window.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
    window.showFullScreen()


def fit_window_to_screen(window: QtWidgets.QWidget, *, preferred: QtCore.QSize = _DASHBOARD_SIZE) -> None:
    screen = window.screen() or QtGui.QGuiApplication.primaryScreen()
    if screen is None:
        window.resize(preferred)
        return

    avail = screen.availableGeometry()
    width = min(preferred.width(), avail.width())
    height = min(preferred.height(), avail.height())
    window.resize(QtCore.QSize(width, height))
