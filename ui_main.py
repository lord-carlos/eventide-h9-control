from __future__ import annotations

import argparse
import logging
from pathlib import Path

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


logger = logging.getLogger(__name__)


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
    parser.add_argument(
        "--simulate-h9",
        action="store_true",
        help="Use an in-memory H9 fixture instead of real MIDI hardware.",
    )
    parser.add_argument(
        "--simulate-preset",
        type=int,
        default=0,
        help="Starting simulated preset index (0-3). Default: 0.",
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        help="Save a screenshot of the dashboard after the initial state loads.",
    )
    parser.add_argument(
        "--exit-after-screenshot",
        action="store_true",
        help="Exit after saving --screenshot.",
    )
    parser.add_argument(
        "--screenshot-delay-ms",
        type=int,
        default=1500,
        help="Delay before taking the screenshot. Default: 1500 ms.",
    )
    parser.add_argument(
        "--screenshot-width",
        type=int,
        default=1180,
        help="Window width used in screenshot mode. Default: 1180.",
    )
    parser.add_argument(
        "--screenshot-height",
        type=int,
        default=840,
        help="Window height used in screenshot mode. Default: 840.",
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="Do not start audio capture (useful for deterministic screenshots).",
    )
    parser.add_argument(
        "--no-gpio",
        action="store_true",
        help="Do not initialize GPIO inputs (useful on development machines).",
    )
    args = parser.parse_args()

    if args.exit_after_screenshot and args.screenshot is None:
        parser.error("--exit-after-screenshot requires --screenshot")
    if args.screenshot_delay_ms < 0:
        parser.error("--screenshot-delay-ms must be non-negative")
    if args.screenshot_width < 360 or args.screenshot_height < 640:
        parser.error("screenshot dimensions are too small for the dashboard")
    if args.simulate_preset < 0 or args.simulate_preset > 3:
        parser.error("--simulate-preset must be between 0 and 3")
    if args.fullscreen and args.screenshot is not None:
        parser.error("--fullscreen cannot be combined with --screenshot")

    configure_logging(cli_level=args.log_level)

    config = ConfigManager()

    app = QtWidgets.QApplication([])

    # Apply initial theme from config
    apply_theme(app, config.theme_mode)

    window = MainWindow(config)
    if args.screenshot is not None:
        window.resize(args.screenshot_width, args.screenshot_height)
    elif args.fullscreen:
        configure_fullscreen(window)
    else:
        fit_window_to_screen(window)

    thread = QtCore.QThread()
    worker = H9DeviceWorker(
        config=config,
        midi_channel=args.midi_channel,
        simulate_h9=args.simulate_h9,
        simulate_preset=args.simulate_preset,
        enable_gpio=not args.no_gpio,
    )
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

    beat_detector: BeatDetector | None = None
    if not args.no_audio:
        beat_detector = BeatDetector(config)
        beat_detector.bpm_detected.connect(
            worker.update_live_bpm, QtCore.Qt.ConnectionType.QueuedConnection
        )
        beat_detector.start()

    # Restart beat detector when audio settings change
    def restart_beat_detector() -> None:
        if beat_detector is None:
            return
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
    if beat_detector is not None:
        app.aboutToQuit.connect(beat_detector.stop)
    app.aboutToQuit.connect(thread.quit)
    app.aboutToQuit.connect(lambda: thread.wait(2000))

    thread.start()

    window.show()

    QtCore.QTimer.singleShot(0, window.dashboard.connect_refresh_requested.emit)

    if args.screenshot is not None:
        screenshot_path = args.screenshot

        def save_screenshot() -> None:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            saved = window.grab().save(str(screenshot_path))
            if not saved:
                logger.error("Failed to save screenshot to %s", screenshot_path)
                if args.exit_after_screenshot:
                    app.exit(1)
                return
            else:
                logger.info("Saved screenshot to %s", screenshot_path)
            if args.exit_after_screenshot:
                app.quit()

        QtCore.QTimer.singleShot(args.screenshot_delay_ms, save_screenshot)

    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
