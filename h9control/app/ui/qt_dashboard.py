from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

from h9control.app.config import ConfigManager
from h9control.app.state import DashboardState
from h9control.domain.algorithms import H9FullAlgorithmData


# === CONFIGURATION CONSTANTS ===

# Window
_DASHBOARD_SIZE = QtCore.QSize(1280, 720)

# Typography (point sizes)
_FONT_SIZE_TITLE = 42  # Preset name
_FONT_SIZE_SUBTITLE = 17  # Algorithm and knob labels
_FONT_SIZE_KNOB_VALUE = 21  # Human-readable knob values
_FONT_SIZE_VALUE = 24  # BPM numbers
_FONT_SIZE_LABEL = 12  # Metric labels
_FONT_SIZE_RAW_VALUE = 9  # Raw value retained for tooltips/debugging
_FONT_SIZE_BUTTON = 16  # Button labels
_FONT_SIZE_STATUS = 13  # Connection status

# Layout spacing & margins
_ROOT_MARGIN = 24  # Outer margin around entire dashboard
_SECTION_SPACING = 14  # Vertical spacing between sections
_KNOB_GROUP_SPACING = 28  # Horizontal spacing between DLY-A/B and FBK-A/B
_KNOB_INTERNAL_SPACING = 6  # Vertical spacing inside a knob meter

# Layout stretch factors (vertical proportions)
_STRETCH_TOP = 0  # Top section (DLY knobs)
_STRETCH_CENTER = 1  # Center section (preset/algorithm)
_STRETCH_BOTTOM = 0  # Bottom section (FBK knobs + BPM)
_STRETCH_CENTER_TEXT_TOP = 1  # Stretch above preset name
_STRETCH_CENTER_TEXT_BOTTOM = 1  # Stretch below algorithm

# Widget dimensions
_PROGRESS_BAR_HEIGHT = 16  # Progress bar thickness
_BUTTON_PREV_NEXT_WIDTH = 116  # Width of previous/next buttons
_BUTTON_PREV_NEXT_HEIGHT = 144  # Height of previous/next buttons
_BUTTON_BPM_WIDTH = 170  # Width of BPM metric buttons
_BUTTON_BPM_HEIGHT = 82  # Height of BPM metric buttons
_STATUS_DOT_SIZE = 24  # Status indicator dot
_HEADER_HEIGHT = _BUTTON_BPM_HEIGHT  # Connection, BPM, and navigation status row


@dataclass(frozen=True)
class _Fonts:
    title: QtGui.QFont  # Preset name
    subtitle: QtGui.QFont  # Algorithm, knob labels
    knob_value: QtGui.QFont  # Human-readable knob values
    value: QtGui.QFont  # BPM/Live numbers
    label: QtGui.QFont  # "BPM"/"Live" text
    raw_value: QtGui.QFont  # Raw value retained for tooltips/debugging
    button: QtGui.QFont  # Navigation and settings buttons
    status: QtGui.QFont  # Connection status


def _make_fonts() -> _Fonts:
    title = QtGui.QFont()
    title.setPointSize(_FONT_SIZE_TITLE)
    title.setBold(True)

    subtitle = QtGui.QFont()
    subtitle.setPointSize(_FONT_SIZE_SUBTITLE)
    subtitle.setBold(True)

    knob_value = QtGui.QFont()
    knob_value.setPointSize(_FONT_SIZE_KNOB_VALUE)
    knob_value.setBold(True)

    value = QtGui.QFont()
    value.setPointSize(_FONT_SIZE_VALUE)
    value.setBold(True)

    label = QtGui.QFont()
    label.setPointSize(_FONT_SIZE_LABEL)
    label.setBold(False)

    raw_value = QtGui.QFont()
    raw_value.setPointSize(_FONT_SIZE_RAW_VALUE)
    raw_value.setBold(False)

    button = QtGui.QFont()
    button.setPointSize(_FONT_SIZE_BUTTON)
    button.setBold(True)

    status = QtGui.QFont()
    status.setPointSize(_FONT_SIZE_STATUS)
    status.setBold(True)

    return _Fonts(
        title=title,
        subtitle=subtitle,
        knob_value=knob_value,
        value=value,
        label=label,
        raw_value=raw_value,
        button=button,
        status=status,
    )


class _LabeledProgress(QtWidgets.QWidget):
    def __init__(self, fonts: _Fonts, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        self._label = QtWidgets.QLabel("—")
        self._label.setFont(fonts.subtitle)

        self._value = QtWidgets.QLabel("—")
        self._value.setFont(fonts.knob_value)
        self._value.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        self._lock_badge = QtWidgets.QLabel("LOCKED")
        self._lock_badge.setObjectName("lock-badge")
        self._lock_badge.setFont(fonts.label)
        self._lock_badge.setStyleSheet("color: #d7a74a;")
        self._lock_badge.setVisible(False)

        self._bar = QtWidgets.QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(_PROGRESS_BAR_HEIGHT)

        self._raw_value = QtWidgets.QLabel("")
        self._raw_value.setFont(fonts.raw_value)
        self._raw_value.setStyleSheet("color: #77818b;")
        self._raw_value.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
        self._raw_value.setVisible(False)

        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_KNOB_INTERNAL_SPACING)
        value_row = QtWidgets.QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(8)
        value_row.addWidget(self._label)
        value_row.addStretch(1)
        value_row.addWidget(self._value)
        value_row.addWidget(self._lock_badge)
        layout.addLayout(value_row)
        layout.addWidget(self._bar)
        layout.addWidget(self._raw_value)

    def set_state(
        self,
        *,
        name: str,
        percent: int,
        pretty: str | None,
        raw_value: int | None = None,
    ) -> None:
        display_value = pretty or f"{percent}%"
        self._label.setText(name)
        self._value.setText(display_value)
        self._bar.setValue(max(0, min(100, percent)))

        if raw_value is not None:
            self._raw_value.setText(f"{raw_value}")
            self.setToolTip(f"{name}: {display_value}\nRaw MIDI value: {raw_value}")
        else:
            self._raw_value.setText("")
            self.setToolTip(f"{name}: {display_value}")

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the widget (grayed out when disabled)."""
        self.setEnabled(enabled)
        self._lock_badge.setVisible(not enabled)
        radius = _PROGRESS_BAR_HEIGHT // 2
        if enabled:
            self._label.setStyleSheet("")
            self._value.setStyleSheet("color: palette(highlight);")
            self._raw_value.setStyleSheet("color: #77818b;")
            background = "palette(mid)"
            chunk = "palette(highlight)"
        else:
            self._label.setStyleSheet("color: #7f8992;")
            self._value.setStyleSheet("color: #7f8992;")
            self._raw_value.setStyleSheet("color: #58616a;")
            background = "#3d454d"
            chunk = "#68727c"

        self._bar.setStyleSheet(
            "\n".join(
                (
                    "QProgressBar {",
                    "  border: 0px;",
                    f"  background: {background};",
                    f"  border-radius: {radius}px;",
                    "}",
                    "QProgressBar::chunk {",
                    f"  background: {chunk};",
                    f"  border-radius: {radius}px;",
                    "}",
                )
            )
        )


class _MetricButton(QtWidgets.QPushButton):
    """A clearly labeled, touch-sized metric that can trigger an action."""

    def __init__(self, title: str, fonts: _Fonts) -> None:
        super().__init__()
        self.setObjectName("metric-button")
        self.setFixedSize(_BUTTON_BPM_WIDTH, _BUTTON_BPM_HEIGHT)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(title)

        self._title = QtWidgets.QLabel(title)
        self._title.setObjectName("metric-title")
        self._title.setFont(fonts.label)
        self._title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self._value = QtWidgets.QLabel("—")
        self._value.setObjectName("metric-value")
        self._value.setFont(fonts.value)
        self._value.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        for label in (self._title, self._value):
            label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        layout.addWidget(self._title)
        layout.addWidget(self._value, 1)

    def set_value(self, value: str) -> None:
        self._value.setText(value)


def _human_algorithm_name(
    algorithm_key: str | None, algorithm_name: str | None
) -> str:
    """Prefer a readable algorithm name while retaining the H9 key separately."""
    key = (algorithm_key or "").strip()
    name = (algorithm_name or "").strip()
    if name and (not key or name.upper() != key.upper()):
        return name

    if key:
        display_names = H9FullAlgorithmData.get_info(key).get("display_names", [])
        for candidate in reversed(display_names):
            if candidate.upper() != key.upper() and " " in candidate:
                return candidate
        for candidate in display_names:
            if candidate.upper() != key.upper():
                return candidate

    return name or key or "—"


class DashboardWidget(QtWidgets.QWidget):
    connect_refresh_requested = QtCore.Signal()
    next_requested = QtCore.Signal()
    prev_requested = QtCore.Signal()
    jump_to_preset_1_requested = QtCore.Signal()
    jump_to_preset_2_requested = QtCore.Signal()
    jump_to_preset_3_requested = QtCore.Signal()
    jump_to_preset_4_requested = QtCore.Signal()
    jump_to_preset_5_requested = QtCore.Signal()
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
        self._status_dot = QtWidgets.QPushButton("●")
        dot_font = QtGui.QFont()
        dot_font.setPointSize(16)
        dot_font.setBold(True)
        self._status_dot.setObjectName("status-dot")
        self._status_dot.setFont(dot_font)
        self._status_dot.setFixedSize(_STATUS_DOT_SIZE, _STATUS_DOT_SIZE)
        self._status_dot.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._status_dot.clicked.connect(self.settings_requested.emit)

        self._status_text = QtWidgets.QLabel("Disconnected")
        self._status_text.setObjectName("status-text")
        self._status_text.setFont(fonts.status)
        self._status_text.setMinimumWidth(120)
        self._status_text.setMaximumWidth(300)

        self._preset_number = QtWidgets.QLabel("P---")
        self._preset_number.setObjectName("header-preset")
        self._preset_number.setFont(fonts.status)
        self._preset_number.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._preset_number.setMinimumWidth(72)

        self._btn_settings = QtWidgets.QPushButton("SETTINGS")
        self._btn_settings.setObjectName("settings-button")
        self._btn_settings.setFont(fonts.button)
        self._btn_settings.setMinimumSize(116, 42)
        self._btn_settings.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_settings.clicked.connect(self.settings_requested.emit)

        # Create 4 knob slots (populated dynamically from state.knobs)
        self._knob_slots = [_LabeledProgress(fonts) for _ in range(4)]

        self._preset_name = QtWidgets.QLabel("—")
        self._preset_name.setFont(fonts.title)
        self._preset_name.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._preset_name.setWordWrap(True)

        self._algorithm_name = QtWidgets.QLabel("—")
        self._algorithm_name.setObjectName("algorithm-name")
        self._algorithm_name.setFont(fonts.subtitle)
        self._algorithm_name.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self._algorithm_key = QtWidgets.QLabel("—")
        self._algorithm_key.setObjectName("algorithm-key")
        self._algorithm_key.setFont(fonts.label)
        self._algorithm_key.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self._btn_prev = QtWidgets.QPushButton("◀\nPREV")
        self._btn_prev.setObjectName("nav-button")
        self._btn_prev.setFont(fonts.button)
        self._btn_prev.setFixedSize(_BUTTON_PREV_NEXT_WIDTH, _BUTTON_PREV_NEXT_HEIGHT)
        self._btn_prev.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_prev.setAccessibleName("Previous preset")
        self._btn_prev.clicked.connect(self.prev_requested.emit)

        self._btn_next = QtWidgets.QPushButton("NEXT\n▶")
        self._btn_next.setObjectName("nav-button")
        self._btn_next.setFont(fonts.button)
        self._btn_next.setFixedSize(_BUTTON_PREV_NEXT_WIDTH, _BUTTON_PREV_NEXT_HEIGHT)
        self._btn_next.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._btn_next.setAccessibleName("Next preset")
        self._btn_next.clicked.connect(self.next_requested.emit)

        self._btn_bpm = _MetricButton("PRESET BPM", fonts)
        self._btn_bpm.setToolTip("Refresh the current preset and BPM")
        self._btn_bpm.clicked.connect(self.connect_refresh_requested.emit)

        self._lbl_live_bpm = _MetricButton("LIVE BPM", fonts)
        self._lbl_live_bpm.setToolTip("Sync the live BPM to the current preset")
        self._lbl_live_bpm.clicked.connect(self.sync_live_bpm_requested.emit)

        self._fonts = fonts

        # --- header ---
        header = QtWidgets.QWidget()
        header.setFixedHeight(_HEADER_HEIGHT)
        header_layout = QtWidgets.QGridLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setHorizontalSpacing(10)
        header_layout.setVerticalSpacing(0)
        header_layout.setColumnStretch(0, 1)
        header_layout.setColumnStretch(2, 1)

        status_group = QtWidgets.QWidget()
        status_group_layout = QtWidgets.QHBoxLayout(status_group)
        status_group_layout.setContentsMargins(0, 0, 0, 0)
        status_group_layout.setSpacing(8)
        status_group_layout.addWidget(self._status_dot)
        status_group_layout.addWidget(self._status_text)

        tempo_group = QtWidgets.QWidget()
        tempo_layout = QtWidgets.QHBoxLayout(tempo_group)
        tempo_layout.setContentsMargins(0, 0, 0, 0)
        tempo_layout.setSpacing(12)
        tempo_layout.addWidget(self._btn_bpm)
        tempo_layout.addWidget(self._lbl_live_bpm)

        header_actions = QtWidgets.QWidget()
        header_actions_layout = QtWidgets.QHBoxLayout(header_actions)
        header_actions_layout.setContentsMargins(0, 0, 0, 0)
        header_actions_layout.setSpacing(10)
        header_actions_layout.addWidget(self._preset_number)
        header_actions_layout.addWidget(self._btn_settings)

        header_layout.addWidget(
            status_group, 0, 0, alignment=QtCore.Qt.AlignmentFlag.AlignLeft
        )
        header_layout.addWidget(
            tempo_group, 0, 1, alignment=QtCore.Qt.AlignmentFlag.AlignCenter
        )
        header_layout.addWidget(
            header_actions, 0, 2, alignment=QtCore.Qt.AlignmentFlag.AlignRight
        )

        top_line = QtWidgets.QFrame()
        top_line.setObjectName("section-line")
        top_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        top_line.setFrameShadow(QtWidgets.QFrame.Shadow.Plain)

        bottom_line = QtWidgets.QFrame()
        bottom_line.setObjectName("section-line")
        bottom_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        bottom_line.setFrameShadow(QtWidgets.QFrame.Shadow.Plain)

        # --- top section ---
        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)

        top_group = QtWidgets.QWidget()
        top_group.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )

        top_group_layout = QtWidgets.QHBoxLayout(top_group)
        top_group_layout.setContentsMargins(0, 0, 0, 0)
        top_group_layout.setSpacing(_KNOB_GROUP_SPACING)
        top_group_layout.addWidget(self._knob_slots[0], 1)
        top_group_layout.addWidget(self._knob_slots[1], 1)
        top_layout.addWidget(top_group)

        # --- center section ---
        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QHBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(_SECTION_SPACING)

        preset_panel = QtWidgets.QFrame()
        preset_panel.setObjectName("preset-panel")
        mid_text_layout = QtWidgets.QVBoxLayout(preset_panel)
        mid_text_layout.setContentsMargins(24, 12, 24, 12)
        mid_text_layout.setSpacing(4)
        current_label = QtWidgets.QLabel("CURRENT PRESET")
        current_label.setObjectName("current-label")
        current_label.setFont(fonts.label)
        current_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        mid_text_layout.addWidget(current_label)
        mid_text_layout.addStretch(_STRETCH_CENTER_TEXT_TOP)
        mid_text_layout.addWidget(self._preset_name)
        mid_text_layout.addWidget(self._algorithm_name)
        mid_text_layout.addWidget(self._algorithm_key)
        mid_text_layout.addStretch(_STRETCH_CENTER_TEXT_BOTTOM)

        center_layout.addWidget(
            self._btn_prev, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        center_layout.addWidget(preset_panel, 1)
        center_layout.addWidget(
            self._btn_next, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter
        )

        # --- bottom section ---
        bottom = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(0)

        bottom_group = QtWidgets.QWidget()
        bottom_group.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )

        bottom_group_layout = QtWidgets.QHBoxLayout(bottom_group)
        bottom_group_layout.setContentsMargins(0, 0, 0, 0)
        bottom_group_layout.setSpacing(_KNOB_GROUP_SPACING)
        bottom_group_layout.addWidget(self._knob_slots[2], 1)
        bottom_group_layout.addWidget(self._knob_slots[3], 1)

        bottom_layout.addWidget(bottom_group, 1)

        self.setStyleSheet(
            """
            QFrame#preset-panel {
                background-color: palette(window);
                border: 1px solid #384550;
                border-radius: 8px;
            }
            QFrame#section-line {
                color: #4d5964;
                background-color: #4d5964;
                min-height: 1px;
                max-height: 1px;
                border: 0;
            }
            QLabel#current-label, QLabel#algorithm-key, QLabel#metric-title {
                color: #9aa5af;
            }
            QLabel#algorithm-name {
                color: palette(highlight);
            }
            QLabel#header-preset {
                color: #f2f5f7;
            }
            QPushButton#status-dot {
                border: none;
                background: transparent;
                padding: 0;
            }
            QPushButton#settings-button, QPushButton#nav-button,
            QPushButton#metric-button {
                color: palette(button-text);
                background-color: palette(button);
                border: 1px solid palette(mid);
                border-radius: 7px;
                padding: 6px;
            }
            QPushButton#settings-button:hover, QPushButton#nav-button:hover,
            QPushButton#metric-button:hover {
                border-color: palette(highlight);
            }
            QPushButton#settings-button:pressed, QPushButton#nav-button:pressed,
            QPushButton#metric-button:pressed {
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
            """
        )

        # --- root layout ---
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(
            _ROOT_MARGIN, _ROOT_MARGIN, _ROOT_MARGIN, _ROOT_MARGIN
        )
        layout.setSpacing(_SECTION_SPACING)
        layout.addWidget(header, 0)
        layout.addWidget(top, _STRETCH_TOP)
        layout.addWidget(top_line)
        layout.addWidget(center, _STRETCH_CENTER)
        layout.addWidget(bottom_line)
        layout.addWidget(bottom, _STRETCH_BOTTOM)

        self._apply_state(DashboardState(connected=False, status_text="Disconnected"))

        self._install_shortcuts()

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
            "jump_to_preset_1": lambda: self.jump_to_preset_1_requested.emit(),
            "jump_to_preset_2": lambda: self.jump_to_preset_2_requested.emit(),
            "jump_to_preset_3": lambda: self.jump_to_preset_3_requested.emit(),
            "jump_to_preset_4": lambda: self.jump_to_preset_4_requested.emit(),
            "jump_to_preset_5": lambda: self.jump_to_preset_5_requested.emit(),
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
            def make_multi_handler(
                funcs: list[Callable[[], None]],
            ) -> Callable[[], None]:
                def multi_handler() -> None:
                    for func in funcs:
                        func()

                return multi_handler

            sc.activated.connect(make_multi_handler(handlers))

    def apply_state(self, state: DashboardState) -> None:
        self._apply_state(state)

    def _apply_state(self, state: DashboardState) -> None:
        status_text = state.status_text or (
            "Connected" if state.connected else "Disconnected"
        )
        status_lower = status_text.lower()
        if state.connected:
            status_color = "#31d17c"
        elif "connect" in status_lower and "fail" not in status_lower:
            status_color = "#d7a74a"
        elif any(word in status_lower for word in ("fail", "error")):
            status_color = "#ed6a68"
        else:
            status_color = "#9aa5af"

        self._status_dot.setToolTip(status_text)
        self._status_dot.setStyleSheet(
            "QPushButton#status-dot {"
            " border: none; background: transparent; padding: 0;"
            f" color: {status_color};"
            "}"
        )
        self._status_text.setToolTip(status_text)
        self._status_text.setText(
            QtGui.QFontMetrics(self._status_text.font()).elidedText(
                status_text,
                QtCore.Qt.TextElideMode.ElideRight,
                self._status_text.maximumWidth(),
            )
        )
        self._status_text.setStyleSheet(f"color: {status_color};")

        if state.preset_number is None:
            self._preset_number.setText("P---")
        else:
            self._preset_number.setText(f"P{state.preset_number:03d}")

        self._preset_name.setText(state.preset_name or "—")
        display_name = _human_algorithm_name(
            state.algorithm_key, state.algorithm_name
        )
        self._algorithm_name.setText(display_name)
        algorithm_key = (state.algorithm_key or "").strip()
        show_algorithm_key = bool(
            algorithm_key and algorithm_key.upper() != display_name.upper()
        )
        self._algorithm_key.setText(algorithm_key)
        self._algorithm_key.setVisible(show_algorithm_key)

        if state.bpm is None:
            self._btn_bpm.set_value("—")
        else:
            self._btn_bpm.set_value(f"{state.bpm:.0f}")

        if state.live_bpm is None:
            self._lbl_live_bpm.set_value("—")
        else:
            self._lbl_live_bpm.set_value(f"{state.live_bpm:.1f}")

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
    def _apply_knob(
        widget: "_LabeledProgress",
        knob: object | None,
        *,
        fallback_label: str,
        enabled: bool = True,
    ) -> None:
        if knob is None:
            widget.setVisible(False)
            return
        widget.setVisible(True)
        name = getattr(knob, "name", fallback_label)
        percent = int(getattr(knob, "percent", 0))
        pretty = getattr(knob, "pretty", None)
        raw_value = getattr(knob, "raw_value", None)
        widget.set_state(name=name, percent=percent, pretty=pretty, raw_value=raw_value)
        widget.set_enabled(enabled)


from h9control.app.ui.qt_settings import SettingsWidget


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self.setWindowTitle("H9 Dashboard")
        self.resize(_DASHBOARD_SIZE)
        self.setMinimumSize(QtCore.QSize(960, 540))

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


def fit_window_to_screen(
    window: QtWidgets.QWidget, *, preferred: QtCore.QSize = _DASHBOARD_SIZE
) -> None:
    screen = window.screen() or QtGui.QGuiApplication.primaryScreen()
    if screen is None:
        window.resize(preferred)
        return

    avail = screen.availableGeometry()
    width = min(preferred.width(), avail.width())
    height = min(preferred.height(), avail.height())
    window.resize(QtCore.QSize(width, height))
