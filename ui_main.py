from __future__ import annotations

import argparse

from PySide6 import QtCore, QtWidgets

from h9control.app.config import ConfigManager
from h9control.app.ui.qt_dashboard import MainWindow, fit_window_to_screen
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
    args = parser.parse_args()

    configure_logging(cli_level=args.log_level)

    config = ConfigManager()

    app = QtWidgets.QApplication([])

    window = MainWindow(config)
    fit_window_to_screen(window)

    thread = QtCore.QThread()
    worker = H9DeviceWorker(midi_channel=args.midi_channel)
    worker.moveToThread(thread)

    window.dashboard.connect_refresh_requested.connect(
        worker.connect_or_refresh,
        QtCore.Qt.ConnectionType.QueuedConnection,
    )
    window.dashboard.next_requested.connect(worker.next_preset, QtCore.Qt.ConnectionType.QueuedConnection)
    window.dashboard.prev_requested.connect(worker.prev_preset, QtCore.Qt.ConnectionType.QueuedConnection)

    window.dashboard.adjust_knob_requested.connect(worker.adjust_knob, QtCore.Qt.ConnectionType.QueuedConnection)
    window.dashboard.adjust_bpm_requested.connect(worker.adjust_bpm, QtCore.Qt.ConnectionType.QueuedConnection)

    worker.state_changed.connect(window.dashboard.apply_state)

    beat_detector = BeatDetector(config)
    beat_detector.bpm_detected.connect(worker.update_live_bpm, QtCore.Qt.ConnectionType.QueuedConnection)
    beat_detector.start()

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
