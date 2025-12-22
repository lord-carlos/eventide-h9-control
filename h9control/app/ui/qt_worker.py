from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

from PySide6 import QtCore

from h9control.app.state import DashboardState, KnobBarState
from h9control.app.h9_backend import H9Backend
from h9control.domain.knob_display import format_knob_value, step_timefactor_delay_note_raw
from h9control.domain.preset import PresetSnapshot, parse_preset_dump_text
from h9control.protocol.codes import H9SysexCodes
from h9control.protocol.codes import MAX_KNOB_VALUE_14BIT
from h9control.protocol.sysex import SysexFrame, build_eventide_sysex, decode_eventide_sysex
from h9control.transport.midi_transport import MidiTransport
from midi import H9Midi


class _FrameWaiter:
    def __init__(self, predicate: Callable[[SysexFrame], bool]) -> None:
        self.predicate = predicate
        self._event = threading.Event()
        self._frame: SysexFrame | None = None

    def try_set(self, frame: SysexFrame) -> bool:
        if self._event.is_set():
            return False
        if not self.predicate(frame):
            return False
        self._frame = frame
        self._event.set()
        return True

    def wait(self, timeout_s: float) -> SysexFrame | None:
        self._event.wait(timeout_s)
        return self._frame


class _PresetChangeDetector:
    def __init__(self) -> None:
        self._recent_prefixes: deque[tuple[float, bytes]] = deque()

    def observe(self, frame: SysexFrame) -> bool:
        if frame.command != 0x60:
            return False

        # Ignore short button-down/up style events (observed as: 07 00 5C ...).
        if len(frame.payload) >= 3 and frame.payload[0:3] == bytes([0x07, 0x00, 0x5C]):
            return False

        if len(frame.payload) < 3:
            return False

        now = time.monotonic()
        prefix = bytes(frame.payload[0:3])
        self._recent_prefixes.append((now, prefix))

        window_s = 0.15
        while self._recent_prefixes and (now - self._recent_prefixes[0][0]) > window_s:
            self._recent_prefixes.popleft()

        unique = {p for _, p in self._recent_prefixes}
        # Heuristic: preset changes emit a burst of different message shapes.
        return len(unique) >= 2


class H9DeviceWorker(QtCore.QObject):
    state_changed = QtCore.Signal(object)
    preset_change_detected = QtCore.Signal()

    def __init__(
        self,
        *,
        device_prefix: str = "H9 Pedal",
        device_id: int = 1,
        midi_channel: int = 0,
    ) -> None:
        super().__init__()
        self._logger = logging.getLogger(self.__class__.__name__)

        self._device_prefix = device_prefix
        self._device_id = device_id
        self._midi_channel = midi_channel

        self._midi: H9Midi | None = None
        self._transport: MidiTransport | None = None
        self._connected_device_id: int = device_id

        self._rx_thread: threading.Thread | None = None
        self._rx_stop = threading.Event()
        self._waiters_lock = threading.Lock()
        self._waiters: list[_FrameWaiter] = []
        self._preset_detector = _PresetChangeDetector()
        self._event_refresh_timer = QtCore.QTimer(self)
        self._event_refresh_timer.setSingleShot(True)
        self._event_refresh_timer.timeout.connect(self._refresh_after_event)
        self.preset_change_detected.connect(self._on_preset_change_detected)

        # Guardrails: the 0x60 stream includes knob activity and other chatter.
        # We debounce and also rate-limit refreshes to avoid spamming PROGRAM_WANT.
        self._event_refresh_cooldown_s = 1.5
        self._last_event_refresh_at = 0.0
        self._event_refresh_in_progress = False

        self._last_state = DashboardState(connected=False, status_text="Disconnected")
        self._current_program: int = 0
        self._knob_overrides: dict[str, int] = {}
        self._last_good_bpm: float | None = None

        self._backend = H9Backend(
            send_eventide=self._send_eventide,
            wait_for_frame=lambda predicate, timeout_s: self._wait_for_frame(
                predicate, timeout_s=timeout_s
            ),
        )

    @QtCore.Slot()
    def connect_or_refresh(self) -> None:
        if self._transport is None:
            self._connect()

        if self._transport is None:
            return

        self._refresh_state()

    @QtCore.Slot()
    def next_preset(self) -> None:
        self._change_preset(delta=1)

    @QtCore.Slot()
    def prev_preset(self) -> None:
        self._change_preset(delta=-1)

    @QtCore.Slot(str, int)
    def adjust_knob(self, knob_name: str, delta: int) -> None:
        """Keyboard-driven knob tweak.

        For now this updates the UI model in discrete steps.
        (We don't yet have the SysEx key IDs needed to push individual knob values via VALUE_PUT.)
        """

        name = knob_name.strip().upper()
        if name not in {"DLY-A", "DLY-B", "FBK-A", "FBK-B"}:
            return

        algo_key = (self._last_state.algorithm_key or "").upper()

        # Determine current raw value.
        current_raw: int | None = None
        if name in self._knob_overrides:
            current_raw = self._knob_overrides[name]
        else:
            for k in self._last_state.knobs:
                if k.name.upper() == name:
                    current_raw = int(getattr(k, "raw_value", 0))
                    break
        if current_raw is None:
            return

        if name in {"DLY-A", "DLY-B"} and algo_key in {"DIGDLY", "VNTAGE", "TAPE", "MODDLY"}:
            new_raw = step_timefactor_delay_note_raw(current_raw, delta=delta)
        else:
            # Coarse stepping for other knobs.
            step = int(round(MAX_KNOB_VALUE_14BIT * 0.05))  # 5%
            new_raw = max(0, min(MAX_KNOB_VALUE_14BIT, current_raw + (step * (1 if delta > 0 else -1))))

        self._knob_overrides[name] = new_raw
        self._emit_state(self._state_with_overrides())

    @QtCore.Slot(int)
    def adjust_bpm(self, delta_bpm: int) -> None:
        if self._transport is None:
            self._connect()
        if self._transport is None:
            return

        bpm_now = self._sanitize_bpm(self._last_state.bpm)
        if bpm_now is None:
            return

        target = int(round(bpm_now)) + int(delta_bpm)
        target = max(20, min(300, target))

        try:
            self._backend.set_bpm(target)
        except Exception:
            self._logger.exception("Failed to set BPM")
            return

        # Re-read state so UI stays in sync with the pedal.
        self._refresh_state()

    def _sanitize_bpm(self, bpm: float | None) -> float | None:
        if bpm is None:
            return self._last_good_bpm

        # Guardrail: we have observed occasional bogus tempo reads.
        if bpm < 20.0 or bpm > 300.0:
            self._logger.debug(
                "Ignoring implausible BPM reading: %s (last_good=%s)",
                bpm,
                self._last_good_bpm,
            )
            return self._last_good_bpm

        self._last_good_bpm = bpm
        return bpm

    @QtCore.Slot()
    def shutdown(self) -> None:
        self._rx_stop.set()
        if self._rx_thread is not None and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=1.0)
        self._rx_thread = None

        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                self._logger.exception("Error while closing transport")
        self._transport = None
        self._midi = None

        with self._waiters_lock:
            self._waiters.clear()

    def _connect(self) -> None:
        self._emit_state(
            DashboardState(connected=False, status_text="Connecting…")
        )

        try:
            midi = H9Midi(device_prefix=self._device_prefix)
            midi.connect()
            transport = MidiTransport(midi)

            self._midi = midi
            self._transport = transport
            self._connected_device_id = self._device_id
            self._start_rx_thread_if_needed()

            self._emit_state(
                DashboardState(connected=True, status_text="Connected")
            )
        except Exception as exc:
            self._logger.exception("Failed to connect")
            self._midi = None
            self._transport = None
            self._emit_state(
                DashboardState(connected=False, status_text=f"Connect failed: {exc}")
            )

    def _refresh_state(self) -> None:
        try:
            preset = self._request_current_program(timeout_s=2.0)

            # If the preset changed, drop any UI overrides.
            if (
                preset.preset_number is not None
                and preset.preset_number != self._last_state.preset_number
            ):
                self._knob_overrides.clear()

            bpm: float | None = None
            try:
                bpm = self._backend.get_bpm(timeout_s=1.0)
            except Exception:
                bpm = None
            bpm = self._sanitize_bpm(bpm)

            current_program = self._current_program
            if preset.preset_number is not None:
                current_program = max(0, preset.preset_number - 1)
            self._current_program = current_program

            knobs: list[KnobBarState] = []
            wanted_order = ("DLY-A", "DLY-B", "FBK-A", "FBK-B")
            if preset.knobs_by_name:
                for name in wanted_order:
                    if name not in preset.knobs_by_name:
                        override = self._knob_overrides.get(name)
                        raw = int(preset.knobs_by_name[name] if override is None else override)
                    raw = int(self._knob_overrides.get(name, preset.knobs_by_name[name]))
                    pct = int(round((raw / MAX_KNOB_VALUE_14BIT) * 100.0))
                    pretty = format_knob_value(
                        algorithm_key=preset.algorithm_key,
                        knob_name=name,
                        raw_value=raw,
                    )
                    knobs.append(
                        KnobBarState(
                            name=name,
                            percent=max(0, min(100, pct)),
                            raw_value=raw,
                            pretty=(pretty.label if pretty is not None else None),
                        )
                    )

            self._emit_state(
                DashboardState(
                    connected=True,
                    status_text="Connected",
                    preset_number=preset.preset_number,
                    preset_name=preset.preset_name,
                    algorithm_name=preset.algorithm_name,
                    algorithm_key=preset.algorithm_key,
                    bpm=bpm,
                    knobs=tuple(knobs),
                )
            )
        except Exception as exc:
            self._logger.exception("Refresh failed")
            prev = self._last_state
            self._emit_state(
                DashboardState(
                    connected=prev.connected,
                    status_text=f"Refresh failed: {exc}",
                    preset_number=prev.preset_number,
                    preset_name=prev.preset_name,
                    algorithm_name=prev.algorithm_name,
                    algorithm_key=prev.algorithm_key,
                    bpm=prev.bpm,
                    knobs=prev.knobs,
                )
            )

    def _change_preset(self, *, delta: int) -> None:
        if self._transport is None:
            self._connect()
        if self._transport is None:
            return

        try:
            next_program = (self._current_program + delta) % 128
            self._transport.send_program_change(program=next_program, channel=self._midi_channel)
            self._current_program = next_program
            time.sleep(0.3)
            self._refresh_state()
        except Exception as exc:
            self._logger.exception("Program change failed")
            prev = self._last_state
            self._emit_state(
                DashboardState(
                    connected=prev.connected,
                    status_text=f"Program change failed: {exc}",
                    preset_number=prev.preset_number,
                    preset_name=prev.preset_name,
                    algorithm_name=prev.algorithm_name,
                    algorithm_key=prev.algorithm_key,
                    bpm=prev.bpm,
                    knobs=prev.knobs,
                )
            )

    def _emit_state(self, state: DashboardState) -> None:
        self._last_state = state
        self.state_changed.emit(state)

    def _state_with_overrides(self) -> DashboardState:
        prev = self._last_state
        if not prev.knobs:
            return prev

        updated: list[KnobBarState] = []
        for k in prev.knobs:
            name = k.name
            raw = int(self._knob_overrides.get(name.upper(), k.raw_value))
            pct = int(round((raw / MAX_KNOB_VALUE_14BIT) * 100.0))
            pretty = format_knob_value(
                algorithm_key=prev.algorithm_key,
                knob_name=name,
                raw_value=raw,
            )
            updated.append(
                KnobBarState(
                    name=name,
                    percent=max(0, min(100, pct)),
                    raw_value=raw,
                    pretty=(pretty.label if pretty is not None else k.pretty),
                )
            )

        return DashboardState(
            connected=prev.connected,
            status_text=prev.status_text,
            preset_number=prev.preset_number,
            preset_name=prev.preset_name,
            algorithm_name=prev.algorithm_name,
            algorithm_key=prev.algorithm_key,
            bpm=prev.bpm,
            knobs=tuple(updated),
        )

    def _start_rx_thread_if_needed(self) -> None:
        if self._transport is None:
            return
        if self._rx_thread is not None and self._rx_thread.is_alive():
            return

        self._rx_stop.clear()
        self._rx_thread = threading.Thread(target=self._rx_loop, name="h9-rx", daemon=True)
        self._rx_thread.start()

    def _rx_loop(self) -> None:
        self._logger.info("RX loop started")
        while not self._rx_stop.is_set():
            transport = self._transport
            if transport is None:
                time.sleep(0.05)
                continue

            for msg in transport.receive_pending():
                frame = decode_eventide_sysex(msg)
                if frame is None:
                    continue

                if frame.device_id not in (0, self._connected_device_id):
                    continue

                claimed = self._try_deliver_to_waiters(frame)
                if (
                    not claimed
                    and not self._event_refresh_in_progress
                    and self._preset_detector.observe(frame)
                ):
                    self.preset_change_detected.emit()

            time.sleep(0.005)
        self._logger.info("RX loop stopped")

    def _try_deliver_to_waiters(self, frame: SysexFrame) -> bool:
        with self._waiters_lock:
            for idx, waiter in enumerate(list(self._waiters)):
                if waiter.try_set(frame):
                    try:
                        self._waiters.pop(idx)
                    except IndexError:
                        pass
                    return True
        return False

    def _wait_for_frame(self, predicate: Callable[[SysexFrame], bool], *, timeout_s: float) -> SysexFrame:
        waiter = _FrameWaiter(predicate)
        with self._waiters_lock:
            self._waiters.append(waiter)

        frame = waiter.wait(timeout_s)
        if frame is not None:
            return frame

        with self._waiters_lock:
            if waiter in self._waiters:
                self._waiters.remove(waiter)
        raise TimeoutError("Timed out waiting for matching SysEx response")

    def _send_eventide(self, command: int, payload: bytes = b"") -> None:
        if self._transport is None:
            raise RuntimeError("Not connected")
        msg = build_eventide_sysex(self._connected_device_id, command, payload)
        self._transport.send_sysex(msg)

    def _request_current_program(self, *, timeout_s: float) -> PresetSnapshot:
        self._logger.info("Requesting current program")
        self._send_eventide(H9SysexCodes.SYSEXC_TJ_PROGRAM_WANT)

        frame = self._wait_for_frame(
            lambda f: f.command == H9SysexCodes.SYSEXC_TJ_PROGRAM_DUMP,
            timeout_s=timeout_s,
        )

        raw_text = frame.payload.decode("ascii", errors="replace")
        return parse_preset_dump_text(raw_text)

    @QtCore.Slot()
    def _on_preset_change_detected(self) -> None:
        if self._transport is None:
            return

        now = time.monotonic()
        if (now - self._last_event_refresh_at) < self._event_refresh_cooldown_s:
            return

        # Debounce: preset changes often emit multiple 0x60 frames.
        self._event_refresh_timer.start(250)

    @QtCore.Slot()
    def _refresh_after_event(self) -> None:
        if self._transport is None:
            return

        self._last_event_refresh_at = time.monotonic()
        self._event_refresh_in_progress = True
        try:
            self._logger.info("Preset change detected; refreshing program dump")
            self._refresh_state()
        finally:
            self._event_refresh_in_progress = False
