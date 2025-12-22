from __future__ import annotations

from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

from h9control.app.state import DashboardState


_DASHBOARD_SIZE = QtCore.QSize(720, 1280)


@dataclass(frozen=True)
class _Fonts:
    title: QtGui.QFont
    subtitle: QtGui.QFont
    normal: QtGui.QFont
    bar: QtGui.QFont


def _make_fonts() -> _Fonts:
    title = QtGui.QFont()
    title.setPointSize(28)
    title.setBold(True)

    subtitle = QtGui.QFont()
    subtitle.setPointSize(18)
    subtitle.setBold(True)

    normal = QtGui.QFont()
    normal.setPointSize(14)

    bar = QtGui.QFont("Courier New")
    bar.setPointSize(16)
    bar.setBold(True)

    return _Fonts(title=title, subtitle=subtitle, normal=normal, bar=bar)


class _LabeledProgress(QtWidgets.QWidget):
    def __init__(self, fonts: _Fonts, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        self._label = QtWidgets.QLabel("—")
        self._label.setFont(fonts.subtitle)

        self._bar = QtWidgets.QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        bar_height = 12
        self._bar.setFixedHeight(bar_height)
        radius = bar_height // 2
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

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._label)
        layout.addWidget(self._bar)

    def set_state(self, *, name: str, percent: int, pretty: str | None) -> None:
        if pretty:
            # Example: "DLY-A  1/8 note" -> user-friendly label
            self._label.setText(f"{name}  {pretty}")
        else:
            self._label.setText(name)
        self._bar.setValue(max(0, min(100, percent)))


class DashboardWindow(QtWidgets.QMainWindow):
    connect_refresh_requested = QtCore.Signal()
    next_requested = QtCore.Signal()
    prev_requested = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("H9 Dashboard")
        self.resize(_DASHBOARD_SIZE)
        self.setMinimumSize(QtCore.QSize(360, 640))

        fonts = _make_fonts()

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)

        # --- widgets ---
        self._status_dot = QtWidgets.QLabel("●")
        self._status_dot.setFont(fonts.subtitle)
        self._status_dot.setFixedSize(24, 24)
        self._status_dot.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)

        self._dly_a = _LabeledProgress(fonts)
        self._dly_b = _LabeledProgress(fonts)
        self._fbk_a = _LabeledProgress(fonts)
        self._fbk_b = _LabeledProgress(fonts)

        self._preset_name = QtWidgets.QLabel("—")
        self._preset_name.setFont(fonts.title)
        self._preset_name.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self._algorithm_key = QtWidgets.QLabel("—")
        self._algorithm_key.setFont(fonts.subtitle)
        self._algorithm_key.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self._btn_prev = QtWidgets.QPushButton("◀")
        self._btn_prev.setFont(fonts.title)
        self._btn_prev.setFixedWidth(90)
        self._btn_prev.clicked.connect(self.prev_requested.emit)

        self._btn_next = QtWidgets.QPushButton("▶")
        self._btn_next.setFont(fonts.title)
        self._btn_next.setFixedWidth(90)
        self._btn_next.clicked.connect(self.next_requested.emit)

        self._btn_bpm = QtWidgets.QPushButton("— BPM")
        self._btn_bpm.setFont(fonts.subtitle)
        self._btn_bpm.setFixedHeight(70)
        self._btn_bpm.setFixedWidth(160)
        self._btn_bpm.clicked.connect(self.connect_refresh_requested.emit)

        top_line = QtWidgets.QFrame()
        top_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        top_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)

        bottom_line = QtWidgets.QFrame()
        bottom_line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        bottom_line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)

        # --- top (25%) ---
        top = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(12)

        # One row: DLY-A + DLY-B centered-ish, status dot on far right.
        dly_row = QtWidgets.QHBoxLayout()
        dly_row.setSpacing(24)

        dly_group = QtWidgets.QWidget()
        dly_group_layout = QtWidgets.QHBoxLayout(dly_group)
        dly_group_layout.setContentsMargins(0, 0, 0, 0)
        dly_group_layout.setSpacing(24)
        dly_group_layout.addWidget(self._dly_a, 1)
        dly_group_layout.addWidget(self._dly_b, 1)

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

        # --- center (50%) ---
        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QHBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(12)

        mid_text = QtWidgets.QWidget()
        mid_text_layout = QtWidgets.QVBoxLayout(mid_text)
        mid_text_layout.setContentsMargins(0, 0, 0, 0)
        mid_text_layout.setSpacing(10)
        mid_text_layout.addStretch(2)
        mid_text_layout.addWidget(self._preset_name)
        mid_text_layout.addWidget(self._algorithm_key)
        mid_text_layout.addStretch(3)

        center_layout.addWidget(self._btn_prev, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
        center_layout.addStretch(1)
        center_layout.addWidget(mid_text)
        center_layout.addStretch(1)
        center_layout.addWidget(self._btn_next, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)

        # --- bottom (25%) ---
        bottom = QtWidgets.QWidget()
        bottom_layout = QtWidgets.QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(24)

        fbk_group = QtWidgets.QWidget()
        fbk_group_layout = QtWidgets.QHBoxLayout(fbk_group)
        fbk_group_layout.setContentsMargins(0, 0, 0, 0)
        fbk_group_layout.setSpacing(24)
        fbk_group_layout.addWidget(self._fbk_a, 1)
        fbk_group_layout.addWidget(self._fbk_b, 1)

        # Left-bound group (~50% width), empty spacer, then BPM button.
        bottom_layout.addWidget(fbk_group, 1, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self._btn_bpm, 0, alignment=QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignRight)

        # --- root layout ---
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        layout.addWidget(top, 1)
        layout.addWidget(top_line)
        layout.addWidget(center, 2)
        layout.addWidget(bottom_line)
        layout.addWidget(bottom, 1)

        self._apply_state(DashboardState(connected=False, status_text="Disconnected"))

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

        # bottom-right BPM button
        if state.bpm is None:
            self._btn_bpm.setText("— BPM")
        else:
            self._btn_bpm.setText(f"{state.bpm:.0f} BPM")

        knobs_by_name = {k.name: k for k in state.knobs}
        self._apply_knob(self._dly_a, knobs_by_name.get("DLY-A"), fallback_label="DLY-A")
        self._apply_knob(self._dly_b, knobs_by_name.get("DLY-B"), fallback_label="DLY-B")
        self._apply_knob(self._fbk_a, knobs_by_name.get("FBK-A"), fallback_label="FBK-A")
        self._apply_knob(self._fbk_b, knobs_by_name.get("FBK-B"), fallback_label="FBK-B")

    @staticmethod
    def _apply_knob(widget: "_LabeledProgress", knob: object | None, *, fallback_label: str) -> None:
        if knob is None:
            widget.setVisible(False)
            return
        widget.setVisible(True)
        name = getattr(knob, "name", fallback_label)
        percent = int(getattr(knob, "percent", 0))
        pretty = getattr(knob, "pretty", None)
        widget.set_state(name=name, percent=percent, pretty=pretty)


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
