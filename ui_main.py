from __future__ import annotations

import argparse

from PySide6 import QtCore, QtWidgets

from h9control.app.config import ConfigManager
from h9control.app.theme import apply_theme
from h9control.app.ui.qt_dashboard import (
    MainWindow,
    configure_fullscreen,
    fit_window_to_screen,
)
from h9control.app.ui.qt_worker import H9DeviceWorker
from h9control.audio.beat_detector import BeatDetector
from h9control.logging_setup import configure_logging


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log-level",
        default=None,
        help="Logging level (DEBUG, INFO, WARNING, ERROR). Can also use H9_LOG_LEVEL env var.",
    )
    parser.add_argument(
        "--midi-channel",
        type=int,
        default=0,
        help="MIDI channel for Program Change (0-15). Default: 0.",
    )
    parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="Launch in fullscreen mode (removes window frame and maximizes).",
    )
    args = parser.parse_args()

    configure_logging(cli_level=args.log_level)

    config = ConfigManager()

    app = QtWidgets.QApplication([])

    # Apply initial theme from config
    apply_theme(app, config.theme_mode)

    window = MainWindow(config)
    if args.fullscreen:
        configure_fullscreen(window)
    else:
        fit_window_to_screen(window)

    thread = QtCore.QThread()
    worker = H9DeviceWorker(config=config, midi_channel=args.midi_channel)
    worker.moveToThread(thread)

    window.dashboard.connect_refresh_requested.connect(
        worker.connect_or_refresh,
        QtCore.Qt.ConnectionType.QueuedConnection,
    )
    window.dashboard.next_requested.connect(
        worker.next_preset, QtCore.Qt.ConnectionType.QueuedConnection
    )
    window.dashboard.prev_requested.connect(
        worker.prev_preset, QtCore.Qt.ConnectionType.QueuedConnection
    )
    window.dashboard.jump_to_preset_1_requested.connect(
        lambda: worker.jump_to_preset(0), QtCore.Qt.ConnectionType.QueuedConnection
    )
    window.dashboard.jump_to_preset_2_requested.connect(
        lambda: worker.jump_to_preset(1), QtCore.Qt.ConnectionType.QueuedConnection
    )
    window.dashboard.jump_to_preset_3_requested.connect(
        lambda: worker.jump_to_preset(2), QtCore.Qt.ConnectionType.QueuedConnection
    )
    window.dashboard.jump_to_preset_4_requested.connect(
        lambda: worker.jump_to_preset(3), QtCore.Qt.ConnectionType.QueuedConnection
    )
    window.dashboard.jump_to_preset_5_requested.connect(
        lambda: worker.jump_to_preset(4), QtCore.Qt.ConnectionType.QueuedConnection
    )

    window.dashboard.adjust_knob_requested.connect(
        worker.adjust_knob, QtCore.Qt.ConnectionType.QueuedConnection
    )
    window.dashboard.adjust_knob_slot_requested.connect(
        worker.adjust_knob_slot, QtCore.Qt.ConnectionType.QueuedConnection
    )
    window.dashboard.adjust_bpm_requested.connect(
        worker.adjust_bpm, QtCore.Qt.ConnectionType.QueuedConnection
    )
    window.settings.settings_changed.connect(
        worker.refresh_ui_state, QtCore.Qt.ConnectionType.QueuedConnection
    )
    window.dashboard.sync_live_bpm_requested.connect(
        worker.sync_live_bpm, QtCore.Qt.ConnectionType.QueuedConnection
    )

    worker.state_changed.connect(window.dashboard.apply_state)

    beat_detector = BeatDetector(config)
    beat_detector.bpm_detected.connect(
        worker.update_live_bpm, QtCore.Qt.ConnectionType.QueuedConnection
    )
    beat_detector.start()

    # Restart beat detector when audio settings change
    def restart_beat_detector() -> None:
        beat_detector.stop()
        beat_detector.start()

    window.settings.audio_settings_changed.connect(
        restart_beat_detector, QtCore.Qt.ConnectionType.QueuedConnection
    )

    # Reapply theme when settings change (hot reload)
    def reapply_theme() -> None:
        apply_theme(app, config.theme_mode)

    window.settings.settings_changed.connect(
        reapply_theme, QtCore.Qt.ConnectionType.QueuedConnection
    )

    app.aboutToQuit.connect(worker.shutdown)
    app.aboutToQuit.connect(beat_detector.stop)
    app.aboutToQuit.connect(thread.quit)
    app.aboutToQuit.connect(lambda: thread.wait(2000))

    thread.start()

    window.show()

    QtCore.QTimer.singleShot(0, window.dashboard.connect_refresh_requested.emit)

    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
