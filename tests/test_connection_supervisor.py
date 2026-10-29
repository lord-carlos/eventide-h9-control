"""Connection supervisor, RX fault isolation and truthful state tests.

No event loop and no real timers: transitions are driven by calling the
worker methods directly, and the supervisor timer is inspected (interval /
isActive) instead of being allowed to fire.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from collections.abc import Callable

import mido
import pytest
from PySide6 import QtCore

from h9control.app.config import ConfigManager
from h9control.app.connection import ConnectionState, RetryBackoff
from h9control.app.ui import qt_worker
from h9control.app.ui.qt_worker import (
    H9DeviceWorker,
    _FrameWaiter,
    _PresetChangeDetector,
)
from h9control.protocol.codes import H9SysexCodes
from h9control.protocol.sysex import SysexFrame, decode_eventide_sysex
from h9control.transport.simulated_h9 import SimulatedH9Transport

# Fast but still generous compared to the RX poll interval (~20 ms idle), so
# a waiter is always registered before the poll that delivers the reply.
_FAST_TIMEOUT_S = 0.2


class FakeH9Transport:
    """Test double that behaves like a real H9 behind a replaceable port.

    All instances in one harness share the same `SimulatedH9Transport`, so a
    reconnect sees the device exactly as it was left (including a preset the
    user changed while the link was down).
    """

    def __init__(self, device: SimulatedH9Transport) -> None:
        self._device = device
        self.rx_error: Exception | None = None
        self.send_error: Exception | None = None
        self.present = True
        self.closed = False
        # Swallow requests so the worker times out.
        self.drop_program_want = False
        self.drop_value_want = False
        self.sent_commands: list[int] = []

    def close(self) -> None:
        self.closed = True

    def is_port_present(self) -> bool:
        return self.present

    def receive_pending(self) -> list[mido.Message]:
        if self.rx_error is not None:
            raise self.rx_error
        return self._device.receive_pending()

    def send_sysex(self, framed_or_unframed) -> None:
        if self.send_error is not None:
            raise self.send_error

        data = list(framed_or_unframed)
        if data and data[0] == 0xF0:
            data = data[1:]
        if data and data[-1] == 0xF7:
            data = data[:-1]

        frame = decode_eventide_sysex(mido.Message("sysex", data=data))
        assert frame is not None
        self.sent_commands.append(frame.command)

        if frame.command == H9SysexCodes.SYSEXC_TJ_PROGRAM_WANT and self.drop_program_want:
            return
        if frame.command == H9SysexCodes.SYSEXC_VALUE_WANT and self.drop_value_want:
            return

        self._device.send_sysex(framed_or_unframed)

    def send_program_change(self, program: int, channel: int = 0) -> None:
        self._device.send_program_change(program, channel)

    def send_control_change(self, control: int, value: int, channel: int = 0) -> None:
        self._device.send_control_change(control, value, channel)


class Harness:
    """Worker wired to a monkeypatched `_open_transport` seam."""

    def __init__(self, config: ConfigManager, monkeypatch: pytest.MonkeyPatch) -> None:
        self.device = SimulatedH9Transport(device_id=1, initial_program=0)
        self.transports: list[FakeH9Transport] = []
        self.open_attempts = 0
        self.open_error: Exception | None = None
        self.configure: Callable[[FakeH9Transport], None] | None = None
        self.worker = H9DeviceWorker(
            config=config, simulate_h9=False, enable_gpio=False
        )
        monkeypatch.setattr(
            H9DeviceWorker, "_open_transport", self._open_transport, raising=True
        )

    def _open_transport(self) -> tuple[None, FakeH9Transport]:
        self.open_attempts += 1
        if self.open_error is not None:
            raise self.open_error

        transport = FakeH9Transport(self.device)
        if self.configure is not None:
            self.configure(transport)
        self.transports.append(transport)
        return None, transport

    @property
    def transport(self) -> FakeH9Transport:
        return self.transports[-1]

    def shutdown(self) -> None:
        self.worker.shutdown()


@pytest.fixture()
def harness(
    config: ConfigManager,
    qapp: QtCore.QCoreApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> Harness:
    monkeypatch.setattr(qt_worker, "_PROGRAM_DUMP_TIMEOUT_S", _FAST_TIMEOUT_S)
    monkeypatch.setattr(qt_worker, "_BPM_TIMEOUT_S", _FAST_TIMEOUT_S)
    h = Harness(config, monkeypatch)
    yield h
    h.shutdown()


def _frame(command: int, payload: bytes = b"") -> SysexFrame:
    return SysexFrame(
        manufacturer_id=0x1C,
        model_id=0x70,
        device_id=1,
        command=command,
        payload=payload,
    )


def _register_waiter(worker: H9DeviceWorker, *, connection_id: int | None = None) -> _FrameWaiter:
    waiter = _FrameWaiter(
        lambda frame: True,
        connection_id=worker._connection_id if connection_id is None else connection_id,
    )
    with worker._waiters_lock:
        worker._waiters.append(waiter)
    return waiter


# ---------------------------------------------------------------------------
# Pure retry policy
# ---------------------------------------------------------------------------


def test_connection_states_are_named() -> None:
    assert {state.name for state in ConnectionState} == {
        "DISCONNECTED",
        "CONNECTING",
        "CONNECTED",
        "RECONNECTING",
    }


def test_retry_backoff_sequence_caps_and_resets() -> None:
    backoff = RetryBackoff()

    assert backoff.delays == (1, 2, 5, 10, 30)
    assert [backoff.next_delay() for _ in range(7)] == [1, 2, 5, 10, 30, 30, 30]
    assert backoff.delay_for_attempt(99) == 30
    assert backoff.delay_for_attempt(-3) == 1

    backoff.reset()

    assert backoff.attempt == 0
    assert backoff.next_delay() == 1


def test_retry_backoff_rejects_empty_sequence() -> None:
    with pytest.raises(ValueError):
        RetryBackoff(())


def test_preset_detector_reset_clears_window() -> None:
    detector = _PresetChangeDetector(now=lambda: 0.0)

    assert detector.observe(_frame(0x60, b"\x01\x02\x03")) is False
    detector.reset()
    assert detector.observe(_frame(0x60, b"\x04\x05\x06")) is False


# ---------------------------------------------------------------------------
# RX fault isolation
# ---------------------------------------------------------------------------


def test_rx_failure_cancels_waiters_and_reports_loss(harness: Harness) -> None:
    worker = harness.worker
    assert worker._attempt_connect() is True
    assert worker._connection_state is ConnectionState.CONNECTED

    waiter = _register_waiter(worker)

    harness.transport.rx_error = RuntimeError("USB unplugged")

    # Cancellation is performed by the RX thread itself, so this unblocks
    # without any Qt event loop.
    assert waiter.wait(2.0) is None
    with pytest.raises(RuntimeError, match="Disconnected"):
        waiter.raise_if_cancelled()

    rx_thread = worker._rx_thread
    assert rx_thread is not None
    rx_thread.join(2.0)
    assert not rx_thread.is_alive()

    # The queued `_connection_lost` slot does not run without an event loop:
    # call it the way the worker thread would receive it.
    worker._on_connection_lost("USB unplugged")

    assert worker._connection_state is ConnectionState.RECONNECTING
    assert worker._last_state.connected is False
    assert worker._last_state.status_text == "Disconnected: USB unplugged"
    assert worker._last_state.preset_name == "PRISTINE DIGITAL"
    assert worker._transport is None
    assert worker._supervisor_timer.isActive()


def test_port_presence_check_is_throttled_and_detects_loss(harness: Harness) -> None:
    worker = harness.worker
    assert worker._attempt_connect() is True
    transport = harness.transport

    transport.present = True
    worker._last_port_check_at = 0.0
    assert worker._port_still_present(transport) is True
    assert worker._last_port_check_at > 0.0

    # Too soon after the last check: the cached answer is used.
    transport.present = False
    assert worker._port_still_present(transport) is True

    # Once the interval has elapsed the missing port is reported.
    worker._last_port_check_at = 0.0
    assert worker._port_still_present(transport) is False

    # ...and the loss path is the same one the RX thread uses.
    worker._on_connection_lost("MIDI port disappeared")
    assert worker._connection_state is ConnectionState.RECONNECTING
    assert worker._last_state.status_text == "Disconnected: MIDI port disappeared"


# ---------------------------------------------------------------------------
# Retry scheduling
# ---------------------------------------------------------------------------


def test_failed_connect_schedules_single_capped_retry_timer(harness: Harness) -> None:
    worker = harness.worker
    harness.open_error = RuntimeError("no MIDI port")

    intervals: list[int] = []
    for _ in range(6):
        assert worker._attempt_connect() is False
        assert worker._connection_state is ConnectionState.DISCONNECTED
        assert worker._last_state.connected is False
        assert worker._last_state.status_text.startswith("Connect failed:")
        intervals.append(worker._supervisor_timer.interval())

    assert intervals == [1000, 2000, 5000, 10000, 30000, 30000]
    assert worker._backoff.attempt == 6
    assert worker._supervisor_timer.isSingleShot()
    active_timers = [t for t in worker.findChildren(QtCore.QTimer) if t.isActive()]
    assert len(active_timers) == 1


def test_initial_program_dump_failure_stays_disconnected(harness: Harness) -> None:
    worker = harness.worker
    harness.configure = lambda transport: setattr(transport, "drop_program_want", True)

    assert worker._attempt_connect() is False

    assert worker._connection_state is ConnectionState.DISCONNECTED
    assert worker._last_state.connected is False
    assert worker._last_state.status_text.startswith("Connect failed:")
    assert worker._transport is None
    assert worker._supervisor_timer.isActive()


def test_shutdown_stops_pending_retry_and_never_reopens(harness: Harness) -> None:
    worker = harness.worker
    harness.open_error = RuntimeError("no MIDI port")
    assert worker._attempt_connect() is False
    assert worker._supervisor_timer.isActive()

    worker.shutdown()

    assert worker._supervisor_timer.isActive() is False
    attempts_before = harness.open_attempts

    # The timer slot and every public entry point must refuse to reopen.
    assert worker._attempt_connect() is False
    worker.connect_or_refresh()
    assert harness.open_attempts == attempts_before
    assert worker._transport is None


# ---------------------------------------------------------------------------
# Reconnect / resync
# ---------------------------------------------------------------------------


def test_successful_reconnect_resets_backoff_and_resyncs(harness: Harness) -> None:
    worker = harness.worker
    assert worker.connect_or_refresh() is None
    assert worker._last_state.preset_name == "PRISTINE DIGITAL"
    first_transport = harness.transport

    worker._knob_overrides["DLY-A"] = 123
    worker._last_sent_auto_bpm = 140

    # The user changes the preset on the pedal while the link is down.
    harness.device.send_program_change(2)
    worker._on_connection_lost("USB unplugged")
    assert worker._connection_state is ConnectionState.RECONNECTING
    assert worker._last_state.preset_name == "PRISTINE DIGITAL"
    assert worker._backoff.attempt == 1

    assert worker._attempt_connect() is True

    assert worker._connection_state is ConnectionState.CONNECTED
    assert worker._backoff.attempt == 0
    assert worker._supervisor_timer.isActive() is False
    assert worker._knob_overrides == {}
    assert worker._last_sent_auto_bpm is None
    # Re-read from the device instead of trusting `_current_program`.
    assert worker._last_state.preset_name == "DARK TAPE"
    assert worker._last_state.preset_number == 3
    assert worker._current_program == 2
    assert first_transport.closed is True
    assert harness.open_attempts == 2


def test_on_connection_lost_is_idempotent(harness: Harness) -> None:
    worker = harness.worker
    assert worker._attempt_connect() is True

    worker._on_connection_lost("USB unplugged")
    after_first = worker._last_state
    connection_id = worker._connection_id

    worker._on_connection_lost("second reason")

    assert worker._last_state == after_first
    assert worker._last_state.status_text == "Disconnected: USB unplugged"
    assert worker._connection_state is ConnectionState.RECONNECTING
    assert worker._connection_id == connection_id
    active_timers = [t for t in worker.findChildren(QtCore.QTimer) if t.isActive()]
    assert len(active_timers) == 1


# ---------------------------------------------------------------------------
# Generations / stale waiters
# ---------------------------------------------------------------------------


def test_stale_connection_id_frame_is_not_delivered(harness: Harness) -> None:
    worker = harness.worker
    assert worker._attempt_connect() is True

    stale = _register_waiter(worker)
    worker._connection_id += 1  # simulate a reconnect
    fresh = _register_waiter(worker)

    frame = _frame(H9SysexCodes.SYSEXC_TJ_PROGRAM_DUMP, b"payload")

    assert worker._try_deliver_to_waiters(frame) is True
    assert fresh.wait(0.01) == frame
    assert stale.wait(0.01) is None
    with pytest.raises(RuntimeError, match="Disconnected"):
        stale.raise_if_cancelled()
    assert worker._waiters == []


def test_scheduled_refresh_is_ignored_after_generation_change(harness: Harness) -> None:
    worker = harness.worker
    assert worker._attempt_connect() is True
    generation = worker._connection_id
    sent_before = len(harness.transport.sent_commands)

    worker._connection_id += 1
    worker._refresh_if_current(generation)

    assert len(harness.transport.sent_commands) == sent_before


# ---------------------------------------------------------------------------
# Truthful refresh states
# ---------------------------------------------------------------------------


def test_one_program_dump_timeout_retries_then_two_trigger_loss(harness: Harness) -> None:
    worker = harness.worker
    assert worker._attempt_connect() is True
    harness.transport.drop_program_want = True

    worker._refresh_state()

    state = worker._last_state
    assert state.connected is True
    assert state.status_text == "H9 not responding (retrying)"
    assert state.preset_name == "PRISTINE DIGITAL"
    assert worker._connection_state is ConnectionState.CONNECTED
    assert worker._consecutive_refresh_failures == 1
    # The idempotent read is retried once per refresh.
    assert harness.transport.sent_commands.count(
        H9SysexCodes.SYSEXC_TJ_PROGRAM_WANT
    ) >= 2

    worker._refresh_state()

    state = worker._last_state
    assert worker._connection_state is ConnectionState.RECONNECTING
    assert state.connected is False
    assert state.status_text == "Disconnected: No response from H9"
    assert state.preset_name == "PRISTINE DIGITAL"
    assert worker._transport is None


def test_bpm_only_failure_keeps_connection_and_last_good_bpm(harness: Harness) -> None:
    worker = harness.worker
    assert worker._attempt_connect() is True
    assert worker._last_good_bpm == 125.0

    harness.transport.drop_value_want = True
    worker._refresh_state()

    state = worker._last_state
    assert state.connected is True
    assert state.status_text == "Connected"
    assert state.bpm == 125.0
    assert worker._connection_state is ConnectionState.CONNECTED
    assert worker._consecutive_refresh_failures == 0
    assert worker._supervisor_timer.isActive() is False


def test_bpm_unavailable_note_without_last_good_bpm(harness: Harness) -> None:
    harness.configure = lambda transport: setattr(transport, "drop_value_want", True)

    assert harness.worker._attempt_connect() is True

    state = harness.worker._last_state
    assert state.connected is True
    assert state.status_text == "Connected (BPM unavailable)"
    assert state.bpm is None


def test_send_failure_takes_loss_path(harness: Harness) -> None:
    worker = harness.worker
    assert worker._attempt_connect() is True
    harness.transport.send_error = RuntimeError("USB write failed")

    worker._refresh_state()

    assert worker._connection_state is ConnectionState.RECONNECTING
    assert worker._last_state.connected is False
    assert worker._last_state.status_text == "Disconnected: USB write failed"
