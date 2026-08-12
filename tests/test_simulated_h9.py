from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore

from h9control.app.config import ConfigManager
from h9control.app.state import DashboardState
from h9control.app.ui.qt_worker import H9DeviceWorker
from h9control.domain.preset import parse_preset_dump_text
from h9control.protocol.codes import H9SysexCodes, H9SystemKeys, MAX_KNOB_VALUE_14BIT
from h9control.protocol.sysex import build_eventide_sysex, decode_eventide_sysex
from h9control.transport.simulated_h9 import SimulatedH9Transport


def _application() -> QtCore.QCoreApplication:
    app = QtCore.QCoreApplication.instance()
    if app is None:
        app = QtCore.QCoreApplication([])
    return app


def _next_frame(transport: SimulatedH9Transport):
    messages = transport.receive_pending()
    assert len(messages) == 1
    frame = decode_eventide_sysex(messages[0])
    assert frame is not None
    return frame


def test_simulated_program_dump_parses_into_dashboard_data() -> None:
    transport = SimulatedH9Transport()
    transport.send_sysex(build_eventide_sysex(1, H9SysexCodes.SYSEXC_TJ_PROGRAM_WANT))

    frame = _next_frame(transport)
    assert frame.command == H9SysexCodes.SYSEXC_TJ_PROGRAM_DUMP

    preset = parse_preset_dump_text(frame.payload.decode("ascii"))
    assert preset.preset_number == 1
    assert preset.algorithm_key == "DIGDLY"
    assert preset.preset_name == "PRISTINE DIGITAL"
    assert preset.knobs_by_name is not None
    assert set(("DLY-A", "DLY-B", "FBK-A", "FBK-B")) <= set(preset.knobs_by_name)


def test_simulated_bpm_and_cc_updates_are_reflected_in_responses() -> None:
    transport = SimulatedH9Transport()

    transport.send_sysex(
        build_eventide_sysex(
            1,
            H9SysexCodes.SYSEXC_VALUE_WANT,
            b"302",
        )
    )
    frame = _next_frame(transport)
    assert frame.payload == b"302 12500"

    transport.send_sysex(
        build_eventide_sysex(
            1,
            H9SysexCodes.SYSEXC_VALUE_PUT,
            b"302 30D4",
        )
    )
    transport.send_control_change(control=24, value=127)
    transport.send_sysex(build_eventide_sysex(1, H9SysexCodes.SYSEXC_TJ_PROGRAM_WANT))

    preset = parse_preset_dump_text(_next_frame(transport).payload.decode("ascii"))
    assert preset.knobs_by_name is not None
    assert preset.knobs_by_name["DLY-A"] == MAX_KNOB_VALUE_14BIT

    transport.send_sysex(
        build_eventide_sysex(
            1,
            H9SysexCodes.SYSEXC_VALUE_WANT,
            f"{H9SystemKeys.KEY_SP_TEMPO:X}".encode("ascii"),
        )
    )
    assert _next_frame(transport).payload == b"302 12500"


def test_worker_populates_state_without_midi_hardware(tmp_path) -> None:
    _application()
    config = ConfigManager(tmp_path / "missing-config.json")
    worker = H9DeviceWorker(config=config, simulate_h9=True, simulate_preset=1)
    states: list[DashboardState] = []
    worker.state_changed.connect(states.append)

    try:
        worker.connect_or_refresh()
        assert states[-1].connected is True
        assert states[-1].status_text == "Simulated H9"
        assert states[-1].preset_name == "WARM ECHO"
        assert states[-1].algorithm_key == "VNTAGE"
        assert states[-1].bpm == 125.0
        assert len(states[-1].knobs) == 4
    finally:
        worker.shutdown()
