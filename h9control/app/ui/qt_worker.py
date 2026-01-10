from __future__ import annotations

import logging
import threading
import time
import dataclasses
from collections import deque
from collections.abc import Callable

from PySide6 import QtCore

from h9control.app.config import ConfigManager
from h9control.app.state import DashboardState, KnobBarState
from h9control.app.h9_backend import H9Backend
from h9control.domain.algorithms import H9FullAlgorithmData
from h9control.domain.knob_display import format_knob_value, step_timefactor_delay_note_raw
from h9control.domain.preset import PresetSnapshot, parse_preset_dump_text
from h9control.protocol.codes import H9SysexCodes
from h9control.protocol.codes import MAX_KNOB_VALUE_14BIT
from h9control.protocol.sysex import SysexFrame, build_eventide_sysex, decode_eventide_sysex
from h9control.transport.midi_transport import MidiTransport
from h9control.transport.gpio_input import GpioInputManager
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
        config: ConfigManager,
        device_prefix: str = "H9 Pedal",
        device_id: int = 1,
        midi_channel: int = 0,
    ) -> None:
        super().__init__()
        self._logger = logging.getLogger(self.__class__.__name__)

        self._config = config
        self._device_prefix = device_prefix
        self._device_id = device_id
        self._midi_channel = midi_channel

        self._midi: H9Midi | None = None
        self._transport: MidiTransport | None = None
        self._connected_device_id: int = device_id
        self._gpio: GpioInputManager = GpioInputManager()

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

        self._last_state = DashboardState(
            connected=False,
            status_text="Disconnected",
            lock_delay=self._config.lock_delay,
            lock_feedback=self._config.lock_feedback,
            lock_pitch=self._config.lock_pitch,
        )
        self._current_program: int = 0
        self._knob_overrides: dict[str, int] = {}
        self._last_good_bpm: float | None = None
        self._live_bpm: float | None = None
        self._last_sent_auto_bpm: int | None = None

        self._backend = H9Backend(
            send_eventide=self._send_eventide,
            wait_for_frame=lambda predicate, timeout_s: self._wait_for_frame(
                predicate, timeout_s=timeout_s
            ),
        )

        self._setup_gpio_bindings()

    def _setup_gpio_bindings(self) -> None:
        """Load GPIO bindings from config and wire them to Qt signals.
        
        GPIO actions can have both tap and hold variants:
        - "action_name" -> fires on short press (tap)
        - "action_name_hold" -> fires on long press (hold)
        """
        if not self._gpio.is_available():
            self._logger.info("GPIO not available, skipping GPIO bindings")
            return

        gpio_config = self._config.config.shortcuts.gpio
        if not gpio_config:
            self._logger.info("No GPIO bindings configured")
            return

        # Map action names to callables
        action_map: dict[str, Callable[[], None]] = {
            "next_preset": lambda: self._invoke_on_main_thread(self.next_preset),
            "prev_preset": lambda: self._invoke_on_main_thread(self.prev_preset),
            "connect_refresh": lambda: self._invoke_on_main_thread(self.connect_or_refresh),
            "sync_live_bpm": lambda: self._invoke_on_main_thread(self.sync_live_bpm),
            "adjust_bpm_up": lambda: self._invoke_on_main_thread(self.adjust_bpm, 1),
            "adjust_bpm_down": lambda: self._invoke_on_main_thread(self.adjust_bpm, -1),
            "adjust_knob_1_up": lambda: self._invoke_on_main_thread(self.adjust_knob_slot, 0, 1),
            "adjust_knob_1_down": lambda: self._invoke_on_main_thread(self.adjust_knob_slot, 0, -1),
            "adjust_knob_2_up": lambda: self._invoke_on_main_thread(self.adjust_knob_slot, 1, 1),
            "adjust_knob_2_down": lambda: self._invoke_on_main_thread(self.adjust_knob_slot, 1, -1),
            "adjust_knob_3_up": lambda: self._invoke_on_main_thread(self.adjust_knob_slot, 2, 1),
            "adjust_knob_3_down": lambda: self._invoke_on_main_thread(self.adjust_knob_slot, 2, -1),
            "adjust_knob_4_up": lambda: self._invoke_on_main_thread(self.adjust_knob_slot, 3, 1),
            "adjust_knob_4_down": lambda: self._invoke_on_main_thread(self.adjust_knob_slot, 3, -1),
        }

        # Group actions by pin (tap vs hold variants)
        pin_actions: dict[int, dict] = {}
        for action_name, gpio_cfg in gpio_config.items():
            pin = gpio_cfg.pin
            
            # Check if this is a "hold" variant
            is_hold = action_name.endswith("_hold")
            base_action = action_name[:-5] if is_hold else action_name
            
            handler = action_map.get(base_action)
            if handler is None:
                self._logger.warning(f"Unknown GPIO action: {action_name}")
                continue
            
            self._logger.debug(f"Mapped GPIO action '{action_name}' -> '{base_action}' (handler: {handler})")
            
            if pin not in pin_actions:
                pin_actions[pin] = {
                    "tap": None,
                    "hold": None,
                    "config": gpio_cfg,
                }
            
            if is_hold:
                pin_actions[pin]["hold"] = handler
            else:
                pin_actions[pin]["tap"] = handler

        # Bind each pin with its tap/hold actions
        for pin, actions in pin_actions.items():
            cfg = actions["config"]
            self._logger.info(
                f"Binding GPIO pin {pin}: tap={actions['tap'] is not None}, "
                f"hold={actions['hold'] is not None}"
            )
            self._gpio.bind_action(
                pin=pin,
                tap_action=actions["tap"],
                hold_action=actions["hold"],
                pull_up=(cfg.pull == "up"),
                debounce_ms=cfg.debounce_ms,
                hold_threshold_ms=cfg.hold_threshold_ms,
            )

    def _invoke_on_main_thread(self, method: Callable, *args) -> None:
        """Invoke a Qt slot on the main thread from GPIO callback."""
        def wrapper():
            try:
                self._logger.debug(f"_invoke_on_main_thread executing: {method.__name__} with args {args}")
                method(*args)
            except Exception:
                self._logger.exception(f"Failed to invoke {method.__name__} on main thread")
        
        QtCore.QTimer.singleShot(0, wrapper)

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

        Updates the UI model in discrete steps and pushes the change to the pedal
        via MIDI CC.
        
        Supports any knob name from the current preset. Lock behavior applies to
        DLY-A/B and FBK-A/B pairs when enabled.
        """

        name = knob_name.strip().upper()
        
        # Validate that the knob exists in the current state
        knob_exists = any(k.name.upper() == name for k in self._last_state.knobs)
        if not knob_exists:
            return

        # Check if lock mode is enabled and apply to both channels
        knobs_to_adjust = [name]
        if self._config.lock_delay and name in {"DLY-A", "DLY-B"}:
            # When delay is locked, adjust both A and B
            knobs_to_adjust = ["DLY-A", "DLY-B"]
        elif self._config.lock_feedback and name in {"FBK-A", "FBK-B"}:
            # When feedback is locked, adjust both A and B
            knobs_to_adjust = ["FBK-A", "FBK-B"]
        elif self._config.lock_pitch and name in {"PICH-A", "PICH-B"}:
            # When pitch is locked, adjust both A and B
            knobs_to_adjust = ["PICH-A", "PICH-B"]

        for knob in knobs_to_adjust:
            self._adjust_single_knob(knob, delta)
        
        self._emit_state(self._state_with_overrides())

    @QtCore.Slot(int, int)
    def adjust_knob_slot(self, slot_index: int, delta: int) -> None:
        """Slot-based knob adjustment (0-3 map to first 4 knobs in state.knobs)."""
        self._logger.debug(
            f"adjust_knob_slot: slot_index={slot_index}, delta={delta}, "
            f"num_knobs={len(self._last_state.knobs)}, "
            f"connected={self._last_state.connected}"
        )
        
        if slot_index < 0 or slot_index >= len(self._last_state.knobs):
            self._logger.warning(
                f"Knob slot {slot_index} out of range (have {len(self._last_state.knobs)} knobs)"
            )
            return
        
        knob_name = self._last_state.knobs[slot_index].name
        self._logger.debug(f"Adjusting knob '{knob_name}' by {delta}")
        self.adjust_knob(knob_name, delta)

    def _adjust_single_knob(self, name: str, delta: int) -> None:
        """Adjust a single knob and send MIDI CC."""
        algo_key = (self._last_state.algorithm_key or "").upper()
        
        self._logger.debug(
            f"_adjust_single_knob: name={name}, delta={delta}, algo_key={algo_key or 'NONE'}"
        )

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
            self._logger.warning(f"Could not find current value for knob '{name}'")
            return

        if name in {"DLY-A", "DLY-B"} and algo_key in {"DIGDLY", "VNTAGE", "TAPE", "MODDLY"}:
            new_raw = step_timefactor_delay_note_raw(current_raw, delta=delta)
        else:
            # Coarse stepping for other knobs.
            step = int(round(MAX_KNOB_VALUE_14BIT * 0.05))  # 5%
            new_raw = max(0, min(MAX_KNOB_VALUE_14BIT, current_raw + (step * (1 if delta > 0 else -1))))

        # Try to push to device via MIDI CC.
        # H9 maps knobs to CC: Knob 1 = CC 22, Knob 2 = CC 23, ..., Knob 10 = CC 31
        if self._transport is None:
            self._connect()
        if self._transport is not None and algo_key:
            self._logger.debug(f"Sending CC for knob '{name}' (algo: {algo_key})")
            knob_names = H9FullAlgorithmData.knob_names(algo_key)
            knob_names.reverse()
            
            knob_names_upper = [k.upper() for k in knob_names]
            if name in knob_names_upper:
                knob_index_1based = knob_names_upper.index(name) + 1
                cc_number = 21 + knob_index_1based  # CC 22 for knob 1, CC 31 for knob 10
                # Convert 14-bit raw value to 7-bit MIDI CC value (0-127)
                cc_value = int(round((new_raw / MAX_KNOB_VALUE_14BIT) * 127.0))
                cc_value = max(0, min(127, cc_value))
                try:
                    self._transport.send_control_change(
                        control=cc_number,
                        value=cc_value,
                        channel=self._midi_channel,
                    )
                except Exception:
                    self._logger.exception(
                        "Failed to send CC for knob %s (idx=%s, CC=%d, value=%d)",
                        name,
                        knob_index_1based,
                        cc_number,
                        cc_value,
                    )
        elif self._transport is None:
            self._logger.warning(f"Cannot send CC for '{name}': not connected")
        elif not algo_key:
            self._logger.warning(f"Cannot send CC for '{name}': no algorithm_key loaded")

        self._knob_overrides[name] = new_raw

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

    @QtCore.Slot()
    def sync_live_bpm(self) -> None:
        if self._transport is None:
            self._connect()
        if self._transport is None:
            return

        if self._live_bpm is None:
            return

        target = int(round(self._live_bpm))
        target = max(20, min(300, target))

        try:
            self._backend.set_bpm(target)
            self._last_sent_auto_bpm = target
        except Exception:
            self._logger.exception("Failed to set BPM")
            return

        # Re-read state so UI stays in sync with the pedal.
        self._refresh_state()

    def _check_auto_bpm_sync(self) -> int | None:
        """Check if auto BPM sync should fire and send BPM if needed.
        
        Returns the newly sent BPM value if successful, None otherwise.
        """
        if self._config.auto_bpm_mode != "continuous":
            return None
        
        if self._live_bpm is None:
            return None
        
        if self._transport is None:
            return None
        
        rounded_bpm = int(round(self._live_bpm))
        
        # Only send if the rounded value has changed
        if rounded_bpm == self._last_sent_auto_bpm:
            return None
        
        # Clamp to valid range
        target = max(20, min(300, rounded_bpm))
        
        try:
            self._backend.set_bpm(target)
            self._last_sent_auto_bpm = target
            self._logger.info(f"Auto-synced BPM: {target}")
            return target
        except Exception:
            self._logger.exception("Failed to auto-sync BPM")
            return None

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
        self._gpio.unbind_all()
        
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
            DashboardState(
                connected=False,
                status_text="Connecting…",
                lock_delay=self._config.lock_delay,
                lock_feedback=self._config.lock_feedback,
                lock_pitch=self._config.lock_pitch,
            )
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
                DashboardState(
                    connected=True,
                    status_text="Connected",
                    lock_delay=self._config.lock_delay,
                    lock_feedback=self._config.lock_feedback,
                    lock_pitch=self._config.lock_pitch,
                )
            )
        except Exception as exc:
            self._logger.exception("Failed to connect")
            self._midi = None
            self._transport = None
            self._emit_state(
                DashboardState(
                    connected=False,
                    status_text=f"Connect failed: {exc}",
                    lock_delay=self._config.lock_delay,
                    lock_feedback=self._config.lock_feedback,
                    lock_pitch=self._config.lock_pitch,
                )
            )

    def _refresh_state(self) -> None:
        try:
            self._logger.debug(f"_refresh_state called")
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
            wanted_order = self._config.knob_order
            if preset.knobs_by_name:
                for name in wanted_order:
                    if name not in preset.knobs_by_name:
                        continue
                    override = self._knob_overrides.get(name)
                    raw = int(preset.knobs_by_name[name] if override is None else override)
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
                    lock_delay=self._config.lock_delay,
                    lock_feedback=self._config.lock_feedback,
                    lock_pitch=self._config.lock_pitch,
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
                    lock_delay=self._config.lock_delay,
                    lock_feedback=self._config.lock_feedback,
                    lock_pitch=self._config.lock_pitch,
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
                    lock_delay=self._config.lock_delay,
                    lock_feedback=self._config.lock_feedback,
                    lock_pitch=self._config.lock_pitch,
                )
            )

    def _emit_state(self, state: DashboardState) -> None:
        if state.live_bpm != self._live_bpm:
            state = dataclasses.replace(state, live_bpm=self._live_bpm)
            # Check if auto-sync sent a new BPM and update state accordingly
            new_bpm = self._check_auto_bpm_sync()
            if new_bpm is not None:
                state = dataclasses.replace(state, bpm=float(new_bpm))
        self._last_state = state
        self.state_changed.emit(state)

    @QtCore.Slot(float)
    def update_live_bpm(self, bpm: float) -> None:
        if self._live_bpm == bpm:
            return
        self._live_bpm = bpm
        self._emit_state(self._state_with_overrides())

    @QtCore.Slot()
    def refresh_ui_state(self) -> None:
        """Refresh UI state when settings change (e.g., lock toggles)."""
        self._emit_state(self._state_with_overrides())

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
            live_bpm=self._live_bpm,
            knobs=tuple(updated),
            lock_delay=self._config.lock_delay,
            lock_feedback=self._config.lock_feedback,
            lock_pitch=self._config.lock_pitch,
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
