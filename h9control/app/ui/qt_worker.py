from __future__ import annotations

import logging
import threading
import time
import dataclasses
from collections import deque
from collections.abc import Callable

from PySide6 import QtCore

from h9control.app.bpm_sync import BpmSyncMixin, sanitize_live_bpm
from h9control.app.config import ConfigManager
from h9control.app.connection import ConnectionState, RetryBackoff
from h9control.app.state import DashboardState, KnobBarState
from h9control.app.h9_backend import H9Backend
from h9control.app.preset_jump import PresetJump
from h9control.domain.algorithms import H9FullAlgorithmData
from h9control.domain.knob_display import (
    format_knob_value,
    step_timefactor_delay_note_raw,
)
from h9control.domain.preset import PresetSnapshot, parse_preset_dump_text
from h9control.protocol.codes import H9SysexCodes
from h9control.protocol.codes import MAX_KNOB_VALUE_14BIT
from h9control.protocol.sysex import (
    SysexFrame,
    build_eventide_sysex,
    decode_eventide_sysex,
)
from h9control.transport.midi_transport import H9Transport, MidiTransport
from h9control.transport.gpio_input import GpioInputManager
from h9control.transport.simulated_h9 import SimulatedH9Transport
from midi import H9Midi

# Real hardware needs about this long to settle after a program change
# before a fresh preset dump returns the new state.
_PRESET_SETTLE_MS = 300

# Per-request timeouts (PLAN_RESILIENCE.md §3): 2 s for the program dump,
# 1 s for the tempo read.
_PROGRAM_DUMP_TIMEOUT_S = 2.0
_BPM_TIMEOUT_S = 1.0

# The program dump and the tempo read are both reads, so each may be
# re-requested once on timeout. Relative commands and VALUE_PUT never are.
_PROGRAM_DUMP_ATTEMPTS = 2
_BPM_ATTEMPTS = 2

# How often the RX thread checks (while idle) that the MIDI port still exists.
_PORT_CHECK_INTERVAL_S = 3.0

_RX_BUSY_SLEEP_S = 0.005
_RX_IDLE_SLEEP_S = 0.02


class _FrameWaiter:
    """A one-shot wait for a matching SysEx frame.

    Waiters are tagged with the connection they were created for and can be
    cancelled from any thread (the RX thread cancels them on a fault so the
    worker thread does not stay blocked in `wait()`).
    """

    def __init__(
        self, predicate: Callable[[SysexFrame], bool], *, connection_id: int = 0
    ) -> None:
        self.predicate = predicate
        self.connection_id = connection_id
        self._event = threading.Event()
        self._frame: SysexFrame | None = None
        self._cancel_error: BaseException | None = None

    def try_set(self, frame: SysexFrame) -> bool:
        if self._event.is_set():
            return False
        if not self.predicate(frame):
            return False
        self._frame = frame
        self._event.set()
        return True

    def cancel(self, exc: BaseException) -> None:
        """Unblock the waiter. Safe to call repeatedly."""
        if self._event.is_set():
            return
        self._cancel_error = exc
        self._event.set()

    def wait(self, timeout_s: float) -> SysexFrame | None:
        """Return the frame, or None on timeout *and* on cancellation."""
        self._event.wait(timeout_s)
        return self._frame

    def raise_if_cancelled(self) -> None:
        if self._cancel_error is not None:
            raise self._cancel_error


class _PresetChangeDetector:
    def __init__(self, now: Callable[[], float] | None = None) -> None:
        self._recent_prefixes: deque[tuple[float, bytes]] = deque()
        self._now = now if now is not None else time.monotonic

    def reset(self) -> None:
        """Forget observed prefixes (called on every (re)connect)."""
        self._recent_prefixes.clear()

    def observe(self, frame: SysexFrame) -> bool:
        if frame.command != 0x60:
            return False

        # Ignore short button-down/up style events (observed as: 07 00 5C ...).
        if len(frame.payload) >= 3 and frame.payload[0:3] == bytes([0x07, 0x00, 0x5C]):
            return False

        if len(frame.payload) < 3:
            return False

        now = self._now()
        prefix = bytes(frame.payload[0:3])
        self._recent_prefixes.append((now, prefix))

        window_s = 0.15
        while self._recent_prefixes and (now - self._recent_prefixes[0][0]) > window_s:
            self._recent_prefixes.popleft()

        unique = {p for _, p in self._recent_prefixes}
        # Heuristic: preset changes emit a burst of different message shapes.
        return len(unique) >= 2


class H9DeviceWorker(BpmSyncMixin, QtCore.QObject):
    state_changed = QtCore.Signal(object)
    preset_change_detected = QtCore.Signal()

    # Emitted by the RX thread when it dies (USB unplug, backend error); the
    # connected slot performs the teardown on the worker thread.
    _connection_lost = QtCore.Signal(str)

    # Internal signals for GPIO callbacks (thread-safe)
    _gpio_adjust_knob_slot_signal = QtCore.Signal(int, int)
    _gpio_next_preset_signal = QtCore.Signal()
    _gpio_prev_preset_signal = QtCore.Signal()
    _gpio_connect_refresh_signal = QtCore.Signal()
    _gpio_sync_live_bpm_signal = QtCore.Signal()
    _gpio_adjust_bpm_signal = QtCore.Signal(int)
    _gpio_jump_to_preset_1_signal = QtCore.Signal()
    _gpio_jump_to_preset_2_signal = QtCore.Signal()
    _gpio_jump_to_preset_3_signal = QtCore.Signal()
    _gpio_jump_to_preset_4_signal = QtCore.Signal()
    _gpio_jump_to_preset_5_signal = QtCore.Signal()

    def __init__(
        self,
        *,
        config: ConfigManager,
        device_prefix: str = "H9 Pedal",
        device_id: int = 1,
        midi_channel: int = 0,
        simulate_h9: bool = False,
        simulate_preset: int = 0,
        enable_gpio: bool = True,
    ) -> None:
        super().__init__()
        self._logger = logging.getLogger(self.__class__.__name__)

        self._config = config
        self._device_prefix = device_prefix
        self._device_id = device_id
        self._midi_channel = midi_channel
        self._simulate_h9 = simulate_h9
        self._simulate_preset = simulate_preset
        self._enable_gpio = enable_gpio

        self._midi: H9Midi | None = None
        self._transport: H9Transport | None = None
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

        # Connection supervision: the worker owns the lifecycle so a dead or
        # unplugged transport is replaced instead of silently reporting
        # connected=True (PLAN_RESILIENCE.md §1).
        self._connection_state = ConnectionState.DISCONNECTED
        self._connection_id = 0
        self._backoff = RetryBackoff()
        self._shutting_down = False
        self._consecutive_refresh_failures = 0
        self._last_port_check_at = 0.0
        self._supervisor_timer = QtCore.QTimer(self)
        self._supervisor_timer.setSingleShot(True)
        self._supervisor_timer.timeout.connect(self._attempt_connect)
        self._connection_lost.connect(self._on_connection_lost)

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
        self._connection_status = "Disconnected"

        self._backend = H9Backend(
            send_eventide=self._send_eventide,
            wait_for_frame=lambda predicate, timeout_s: self._wait_for_frame(
                predicate, timeout_s=timeout_s
            ),
        )

        self._setup_gpio_bindings()

        # Connect internal GPIO signals to slots
        self._gpio_adjust_knob_slot_signal.connect(self.adjust_knob_slot)
        self._gpio_next_preset_signal.connect(self.next_preset)
        self._gpio_prev_preset_signal.connect(self.prev_preset)
        self._gpio_connect_refresh_signal.connect(self.connect_or_refresh)
        self._gpio_sync_live_bpm_signal.connect(self.sync_live_bpm)
        self._gpio_adjust_bpm_signal.connect(self.adjust_bpm)
        self._gpio_jump_to_preset_1_signal.connect(lambda: self.jump_to_preset(0))
        self._gpio_jump_to_preset_2_signal.connect(lambda: self.jump_to_preset(1))
        self._gpio_jump_to_preset_3_signal.connect(lambda: self.jump_to_preset(2))
        self._gpio_jump_to_preset_4_signal.connect(lambda: self.jump_to_preset(3))
        self._gpio_jump_to_preset_5_signal.connect(lambda: self.jump_to_preset(4))

    def _setup_gpio_bindings(self) -> None:
        """Load GPIO bindings from config and wire them to Qt signals.

        GPIO actions can have both tap and hold variants:
        - "action_name" -> fires on short press (tap)
        - "action_name_hold" -> fires on long press (hold)
        """
        if not self._enable_gpio:
            self._logger.info("GPIO disabled")
            return

        if not self._gpio.is_available():
            self._logger.info("GPIO not available, skipping GPIO bindings")
            return

        gpio_config = self._config.config.shortcuts.gpio
        if not gpio_config:
            self._logger.info("No GPIO bindings configured")
            return

        # Map action names to callables
        action_map: dict[str, Callable[[], None]] = {
            "next_preset": lambda: self._gpio_next_preset_signal.emit(),
            "prev_preset": lambda: self._gpio_prev_preset_signal.emit(),
            "connect_refresh": lambda: self._gpio_connect_refresh_signal.emit(),
            "sync_live_bpm": lambda: self._gpio_sync_live_bpm_signal.emit(),
            "adjust_bpm_up": lambda: self._gpio_adjust_bpm_signal.emit(1),
            "adjust_bpm_down": lambda: self._gpio_adjust_bpm_signal.emit(-1),
            "adjust_knob_1_up": lambda: self._gpio_adjust_knob_slot_signal.emit(0, 1),
            "adjust_knob_1_down": lambda: self._gpio_adjust_knob_slot_signal.emit(
                0, -1
            ),
            "adjust_knob_2_up": lambda: self._gpio_adjust_knob_slot_signal.emit(1, 1),
            "adjust_knob_2_down": lambda: self._gpio_adjust_knob_slot_signal.emit(
                1, -1
            ),
            "adjust_knob_3_up": lambda: self._gpio_adjust_knob_slot_signal.emit(2, 1),
            "adjust_knob_3_down": lambda: self._gpio_adjust_knob_slot_signal.emit(
                2, -1
            ),
            "adjust_knob_4_up": lambda: self._gpio_adjust_knob_slot_signal.emit(3, 1),
            "adjust_knob_4_down": lambda: self._gpio_adjust_knob_slot_signal.emit(
                3, -1
            ),
            "jump_to_preset_1": lambda: self._gpio_jump_to_preset_1_signal.emit(),
            "jump_to_preset_2": lambda: self._gpio_jump_to_preset_2_signal.emit(),
            "jump_to_preset_3": lambda: self._gpio_jump_to_preset_3_signal.emit(),
            "jump_to_preset_4": lambda: self._gpio_jump_to_preset_4_signal.emit(),
            "jump_to_preset_5": lambda: self._gpio_jump_to_preset_5_signal.emit(),
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

            self._logger.debug(
                f"Mapped GPIO action '{action_name}' -> '{base_action}' (handler: {handler})"
            )

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
                f"hold={actions['hold'] is not None}, "
                f"modifier_name={cfg.modifier_name}"
            )
            self._gpio.bind_action(
                pin=pin,
                tap_action=actions["tap"],
                hold_action=actions["hold"],
                pull_up=(cfg.pull == "up"),
                debounce_ms=cfg.debounce_ms,
                hold_threshold_ms=cfg.hold_threshold_ms,
                modifier_name=cfg.modifier_name,
            )

        # Bind rotary encoders
        rotary_encoder_config = self._config.config.shortcuts.rotary_encoders
        if rotary_encoder_config:
            for encoder_name, encoder_cfg in rotary_encoder_config.items():
                # Resolve action names to handlers
                cw_handler = action_map.get(encoder_cfg.action_cw)
                ccw_handler = action_map.get(encoder_cfg.action_ccw)

                if cw_handler is None:
                    self._logger.warning(
                        f"Unknown rotary encoder CW action for '{encoder_name}': "
                        f"{encoder_cfg.action_cw}"
                    )
                if ccw_handler is None:
                    self._logger.warning(
                        f"Unknown rotary encoder CCW action for '{encoder_name}': "
                        f"{encoder_cfg.action_ccw}"
                    )

                # Resolve modifier actions
                modifier_actions = {}
                for mod_name, mod_actions_cfg in encoder_cfg.modifiers.items():
                    mod_cw_action = mod_actions_cfg.get("action_cw")
                    mod_ccw_action = mod_actions_cfg.get("action_ccw")

                    mod_cw_handler = None
                    mod_ccw_handler = None

                    if mod_cw_action:
                        mod_cw_handler = action_map.get(mod_cw_action)
                        if mod_cw_handler is None:
                            self._logger.warning(
                                f"Unknown rotary encoder modifier CW action for '{encoder_name}' "
                                f"modifier '{mod_name}': {mod_cw_action}"
                            )

                    if mod_ccw_action:
                        mod_ccw_handler = action_map.get(mod_ccw_action)
                        if mod_ccw_handler is None:
                            self._logger.warning(
                                f"Unknown rotary encoder modifier CCW action for '{encoder_name}' "
                                f"modifier '{mod_name}': {mod_ccw_action}"
                            )

                    if mod_cw_handler is not None or mod_ccw_handler is not None:
                        modifier_actions[mod_name] = (mod_cw_handler, mod_ccw_handler)
                        self._logger.debug(
                            f"Resolved modifier '{mod_name}' for encoder '{encoder_name}': "
                            f"CW={mod_cw_action}, CCW={mod_ccw_action}"
                        )

                if cw_handler is None and ccw_handler is None and not modifier_actions:
                    continue

                self._logger.info(
                    f"Binding rotary encoder '{encoder_name}': "
                    f"CLK={encoder_cfg.clk_pin}, DT={encoder_cfg.dt_pin}, "
                    f"CW={encoder_cfg.action_cw}, CCW={encoder_cfg.action_ccw}, "
                    f"modifiers={list(modifier_actions.keys())}"
                )
                self._gpio.bind_rotary_encoder(
                    encoder_name=encoder_name,
                    clk_pin=encoder_cfg.clk_pin,
                    dt_pin=encoder_cfg.dt_pin,
                    action_cw=cw_handler,
                    action_ccw=ccw_handler,
                    modifier_actions=modifier_actions if modifier_actions else None,
                )

    @QtCore.Slot()
    def connect_or_refresh(self) -> None:
        """Single entry point for F5 / GPIO / startup.

        A manual request always resets the backoff and attempts immediately
        rather than waiting for the supervisor timer.
        """
        if self._shutting_down:
            return

        self._backoff.reset()
        if self._connection_state is not ConnectionState.CONNECTED:
            self._attempt_connect()
        if self._connection_state is not ConnectionState.CONNECTED:
            return

        self._refresh_state()

    @QtCore.Slot()
    def next_preset(self) -> None:
        self._change_preset(delta=1)

    @QtCore.Slot()
    def prev_preset(self) -> None:
        self._change_preset(delta=-1)

    @QtCore.Slot(int)
    def jump_to_preset(self, program: int) -> None:
        if self._transport is None:
            self._connect()
        if self._transport is None:
            return

        try:
            preset_jump = PresetJump(self._transport, self._midi_channel)
            preset_jump.jump_to_preset(program)
            self._current_program = program
            self._schedule_state_refresh()
        except Exception as exc:
            self._logger.exception("Preset jump failed")
            prev = self._last_state
            self._emit_state(
                DashboardState(
                    connected=prev.connected,
                    status_text=f"Preset jump failed: {exc}",
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

        if name in {"DLY-A", "DLY-B"} and algo_key in {
            "DIGDLY",
            "VNTAGE",
            "TAPE",
            "MODDLY",
        }:
            new_raw = step_timefactor_delay_note_raw(current_raw, delta=delta)
        else:
            # Coarse stepping for other knobs.
            step = int(round(MAX_KNOB_VALUE_14BIT * 0.05))  # 5%
            new_raw = max(
                0,
                min(
                    MAX_KNOB_VALUE_14BIT,
                    current_raw + (step * (1 if delta > 0 else -1)),
                ),
            )

        # Convert the 14-bit raw value to the 7-bit MIDI CC grid (0-127).
        # The H9 sets knob values via 7-bit CC, so its internal 14-bit value
        # lands on that coarser grid, not on the exact `new_raw` we compute.
        cc_value = max(0, min(127, int(round((new_raw / MAX_KNOB_VALUE_14BIT) * 127.0))))

        # Effect knobs are controlled via MIDI CC. The 0x200 SysEx keys are
        # system-variable byte parameters, not current preset knob values.
        #
        # CC numbers follow the H9's hardware knob row, but ALGO_MAP lists
        # knob names bottom-left → top-left (i.e. most prominent knob last),
        # so the list must be reversed before indexing: after reversal,
        # DLY-A is knob #3 → CC 21+3 = 24, matching the H9's real mapping
        # verified empirically with scripts/test_delay_cc.py (CC 24 changes
        # Delay A time, CC 25 changes Delay B time).
        if self._transport is None:
            self._connect()
        if self._transport is not None and algo_key:
            self._logger.debug(f"Sending CC for knob '{name}' (algo: {algo_key})")
            knob_names = H9FullAlgorithmData.knob_names(algo_key)
            knob_names.reverse()

            knob_names_upper = [k.upper() for k in knob_names]
            if name in knob_names_upper:
                knob_index_1based = knob_names_upper.index(name) + 1
                cc_number = 21 + knob_index_1based
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
            else:
                self._logger.debug(
                    f"Knob '{name}' not mapped to a CC for algo '{algo_key}'"
                )
        elif self._transport is None:
            self._logger.warning(f"Cannot send CC for '{name}': not connected")
        elif not algo_key:
            self._logger.warning(
                f"Cannot send CC for '{name}': no algorithm_key loaded"
            )

        # Store the override on the 7-bit grid the device actually received
        # (reconstructed from the CC value we sent — identical to `cc_value`
        # since that is what the 7-bit conversion produces). Storing the raw
        # 14-bit value would leave the UI showing a value the device does
        # not have, and repeated small adjustments would never converge.
        # When nothing was sent (no transport/unmapped knob) the same CC
        # value is used so stored overrides stay on one consistent grid.
        stored_raw = max(
            0,
            min(MAX_KNOB_VALUE_14BIT, int(round((cc_value / 127.0) * MAX_KNOB_VALUE_14BIT))),
        )
        self._knob_overrides[name] = stored_raw

    @QtCore.Slot()
    def shutdown(self) -> None:
        # Set the flag first: every other entry point checks it and must stop
        # scheduling work as soon as shutdown begins.
        self._shutting_down = True
        self._supervisor_timer.stop()
        self._event_refresh_timer.stop()

        self._gpio.unbind_all()

        self._cancel_waiters(RuntimeError("Shutting down"))
        self._teardown_connection()

        self._connection_state = ConnectionState.DISCONNECTED
        self._connection_status = "Disconnected"
        with self._waiters_lock:
            self._waiters.clear()
        # Deliberately no state emission here: the UI is going away.

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _connect(self) -> None:
        """Thin `-> None` wrapper: `BpmSyncMixin` and call sites use this."""
        self._attempt_connect()

    def _open_transport(self) -> tuple[H9Midi | None, H9Transport]:
        """Open a transport.

        Single seam: tests replace this one method instead of the MIDI
        backend, and the simulate-mode path stays a pure in-memory object.
        """
        if self._simulate_h9:
            return None, SimulatedH9Transport(
                device_id=self._device_id,
                initial_program=self._simulate_preset,
            )

        midi = H9Midi(device_prefix=self._device_prefix)
        midi.connect()
        return midi, MidiTransport(midi)

    def _attempt_connect(self) -> bool:
        """Bring up a working connection synchronously.

        Returns True only when the transport is open *and* the initial
        program dump succeeded; `_last_state.connected` reports the same
        truth to the UI.
        """
        if self._shutting_down:
            return False

        self._supervisor_timer.stop()
        self._cancel_waiters(RuntimeError("Disconnected"))
        self._teardown_connection()

        self._connection_id += 1
        connection_id = self._connection_id
        self._connection_state = ConnectionState.CONNECTING
        self._connection_status = "Connecting…"
        self._logger.info(
            "Connecting (connection_id=%d, prefix=%r, simulate=%s)",
            connection_id,
            self._device_prefix,
            self._simulate_h9,
        )
        self._emit_connection_status(connected=False, status_text=self._connection_status)

        try:
            midi, transport = self._open_transport()
        except Exception as exc:
            self._logger.exception(
                "Failed to open transport (connection_id=%d, prefix=%r)",
                connection_id,
                self._device_prefix,
            )
            self._teardown_connection()
            self._fail_connect(connection_id, exc)
            return False

        self._midi = midi
        self._transport = transport
        self._connected_device_id = self._device_id
        self._reset_session_bookkeeping()
        self._start_rx_thread_if_needed()
        self._drain_stale_input()

        try:
            preset = self._request_current_program(timeout_s=_PROGRAM_DUMP_TIMEOUT_S)
        except Exception as exc:
            self._logger.warning(
                "Initial program dump failed (connection_id=%d): %s",
                connection_id,
                exc,
            )
            self._teardown_connection()
            self._fail_connect(connection_id, exc)
            return False

        bpm = self._read_bpm_best_effort()
        self._connection_state = ConnectionState.CONNECTED
        self._connection_status = "Simulated H9" if self._simulate_h9 else "Connected"
        self._backoff.reset()
        self._consecutive_refresh_failures = 0
        self._apply_current_program(preset)
        self._logger.info(
            "Connected (connection_id=%d, program=%s, bpm=%s)",
            connection_id,
            preset.preset_number,
            bpm,
        )
        self._emit_state(self._preset_state(preset, bpm))
        return True

    def _fail_connect(self, connection_id: int, exc: BaseException) -> None:
        """Report a failed attempt truthfully and arm the retry timer."""
        self._connection_state = ConnectionState.DISCONNECTED
        self._connection_status = f"Connect failed: {exc}"
        self._logger.warning("Connect failed (connection_id=%d): %s", connection_id, exc)
        self._emit_connection_status(
            connected=False, status_text=self._connection_status
        )
        self._schedule_retry()

    def _schedule_retry(self) -> None:
        """Arm the single-shot supervisor timer with the next backoff delay."""
        if self._simulate_h9 or self._shutting_down:
            return

        delay = self._backoff.next_delay()
        self._supervisor_timer.start(int(delay * 1000))
        self._logger.info(
            "Retry scheduled in %.0fs (retry_attempt=%d, connection_id=%d)",
            delay,
            self._backoff.attempt,
            self._connection_id,
        )

    @QtCore.Slot(str)
    def _on_connection_lost(self, reason: str) -> None:
        """Single entry point for every loss source (RX, send, port check)."""
        if (
            self._shutting_down
            or self._connection_state is not ConnectionState.CONNECTED
        ):
            return

        self._logger.warning(
            "Connection lost (connection_id=%d, prefix=%r): %s",
            self._connection_id,
            self._device_prefix,
            reason,
        )
        self._connection_state = ConnectionState.RECONNECTING
        self._connection_status = f"Disconnected: {reason}"
        self._event_refresh_timer.stop()
        self._cancel_waiters(RuntimeError("Disconnected"))
        self._teardown_connection()
        self._consecutive_refresh_failures = 0
        # Emitted after the transport is gone: `_emit_state` triggers
        # auto-BPM sync, which must not try to send on a dead handle.
        self._emit_connection_status(
            connected=False, status_text=self._connection_status
        )
        self._schedule_retry()

    def _teardown_connection(self) -> None:
        """Stop the RX thread and close the transport. Idempotent."""
        self._rx_stop.set()
        thread = self._rx_thread
        self._rx_thread = None
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=1.0)

        transport = self._transport
        self._transport = None
        self._midi = None
        if transport is not None:
            try:
                transport.close()
            except Exception:
                self._logger.exception("Error while closing transport")

    def _reset_session_bookkeeping(self) -> None:
        """Forget per-connection state so nothing from the old link leaks in."""
        self._knob_overrides.clear()
        self._last_sent_auto_bpm = None
        self._last_event_refresh_at = 0.0
        self._last_port_check_at = 0.0
        self._consecutive_refresh_failures = 0
        self._preset_detector.reset()

    def _drain_stale_input(self) -> None:
        """Discard anything the transport buffered before this connection."""
        transport = self._transport
        if transport is None:
            return
        try:
            dropped = transport.receive_pending()
        except Exception:
            self._logger.debug("Failed to drain stale input", exc_info=True)
            return
        if dropped:
            self._logger.debug("Dropped %d stale input message(s)", len(dropped))

    # ------------------------------------------------------------------
    # State refresh
    # ------------------------------------------------------------------

    def _refresh_state(self) -> None:
        if self._shutting_down or self._transport is None:
            self._logger.debug("Refresh skipped: no active connection")
            self._emit_connection_status(
                connected=False, status_text=self._connection_status
            )
            return

        try:
            self._refresh_state_once()
        except TimeoutError as exc:
            self._on_program_dump_timeout(exc)
        except Exception as exc:
            self._logger.exception("Refresh failed")
            if self._transport is None:
                # A send failure already took the loss path and reported it.
                return
            self._emit_state(
                self._state_from_prev(
                    connected=self._last_state.connected,
                    status_text=f"Refresh failed: {exc}",
                )
            )

    def _refresh_state_once(self) -> None:
        self._logger.debug("_refresh_state called")
        preset = self._request_current_program(timeout_s=_PROGRAM_DUMP_TIMEOUT_S)
        self._consecutive_refresh_failures = 0

        bpm = self._read_bpm_best_effort()
        if self._transport is None:
            # A send failure inside the tempo read already took the loss path;
            # do not publish a state that claims the connection is still up.
            return

        # If the preset changed, drop any UI overrides.
        if (
            preset.preset_number is not None
            and preset.preset_number != self._last_state.preset_number
        ):
            self._knob_overrides.clear()

        self._apply_current_program(preset)
        self._emit_state(self._preset_state(preset, bpm))

    def _read_bpm_best_effort(self) -> float | None:
        """Read the tempo, retrying the idempotent read once.

        A failure must never take the connection down: fall back to the last
        good value. If the *send* failed, `_on_connection_lost` has already
        run, so stop immediately instead of retrying against a dead handle.
        """
        for attempt in range(1, _BPM_ATTEMPTS + 1):
            if self._transport is None:
                return self._sanitize_bpm(None)
            try:
                bpm = self._backend.get_bpm(timeout_s=_BPM_TIMEOUT_S)
            except Exception:
                self._logger.warning(
                    "BPM read failed (attempt=%d, connection_id=%d)",
                    attempt,
                    self._connection_id,
                    exc_info=True,
                )
                if self._transport is None:
                    return self._sanitize_bpm(None)
                continue
            return self._sanitize_bpm(bpm)

        return self._sanitize_bpm(None)

    def _on_program_dump_timeout(self, exc: BaseException) -> None:
        """One missed dump is a stall; two in a row mean the link is dead."""
        self._consecutive_refresh_failures += 1
        failures = self._consecutive_refresh_failures
        self._logger.warning(
            "Program dump timed out (connection_id=%d, consecutive_failures=%d): %s",
            self._connection_id,
            failures,
            exc,
        )

        if failures >= 2:
            self._on_connection_lost("No response from H9")
            return

        self._emit_state(
            self._state_from_prev(
                connected=True, status_text="H9 not responding (retrying)"
            )
        )

    def _apply_current_program(self, preset: PresetSnapshot) -> None:
        current_program = self._current_program
        if preset.preset_number is not None:
            current_program = max(0, preset.preset_number - 1)
        self._current_program = current_program

    def _preset_state(
        self, preset: PresetSnapshot, bpm: float | None
    ) -> DashboardState:
        status_text = self._connection_status
        if bpm is None:
            # Truthful note: we are connected but have no usable tempo.
            status_text = f"{status_text} (BPM unavailable)"

        return DashboardState(
            connected=True,
            status_text=status_text,
            preset_number=preset.preset_number,
            preset_name=preset.preset_name,
            algorithm_name=preset.algorithm_name,
            algorithm_key=preset.algorithm_key,
            bpm=bpm,
            knobs=tuple(self._build_knobs(preset)),
            lock_delay=self._config.lock_delay,
            lock_feedback=self._config.lock_feedback,
            lock_pitch=self._config.lock_pitch,
        )

    def _build_knobs(self, preset: PresetSnapshot) -> list[KnobBarState]:
        knobs: list[KnobBarState] = []
        if not preset.knobs_by_name:
            return knobs

        for name in self._config.knob_order:
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
        return knobs

    def _state_from_prev(self, *, connected: bool, status_text: str) -> DashboardState:
        """Keep the last preset snapshot so the UI can grey it out."""
        prev = self._last_state
        return DashboardState(
            connected=connected,
            status_text=status_text,
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

    def _emit_connection_status(self, *, connected: bool, status_text: str) -> None:
        self._emit_state(
            self._state_from_prev(connected=connected, status_text=status_text)
        )

    def _change_preset(self, *, delta: int) -> None:
        if self._transport is None:
            self._connect()
        if self._transport is None:
            return

        try:
            next_program = (self._current_program + delta) % 128
            self._transport.send_program_change(
                program=next_program, channel=self._midi_channel
            )
            self._current_program = next_program
            self._schedule_state_refresh()
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

    def _schedule_state_refresh(self) -> None:
        """Schedule the post-preset-change refresh instead of blocking.

        Real hardware needs ~300 ms to settle after a program change, but
        sleeping in-place blocks the worker thread and drops queued knob
        tweaks / BPM adjusts / GPIO events. Schedule the refresh via the
        worker thread's event loop instead (the worker is a QObject that
        lives on that thread).

        The scheduled call captures the connection generation so a refresh
        armed before a reconnect cannot run against the new connection.

        The simulated transport answers instantly and tests drive the
        worker without a running Qt event loop (timers would never fire),
        so it refreshes synchronously.
        """
        if self._simulate_h9:
            self._refresh_state()
            return

        generation = self._connection_id
        QtCore.QTimer.singleShot(
            _PRESET_SETTLE_MS, lambda: self._refresh_if_current(generation)
        )

    def _refresh_if_current(self, generation: int) -> None:
        """Run a scheduled refresh only for the connection that armed it."""
        if (
            self._shutting_down
            or generation != self._connection_id
            or self._transport is None
        ):
            self._logger.debug(
                "Skipping stale scheduled refresh (generation=%d, current=%d)",
                generation,
                self._connection_id,
            )
            return
        self._refresh_state()

    def _emit_state(self, state: DashboardState) -> None:
        # self._logger.debug(f"_emit_state called: {state}")
        # Always update live_bpm in state if available, and check for auto-sync
        if self._live_bpm is not None:
            state = dataclasses.replace(state, live_bpm=self._live_bpm)
            # Check if auto-sync should send BPM to device
            new_bpm = self._check_auto_bpm_sync()
            if new_bpm is not None:
                state = dataclasses.replace(state, bpm=float(new_bpm))
        self._last_state = state
        self.state_changed.emit(state)

    @QtCore.Slot(float)
    def update_live_bpm(self, bpm: float) -> None:
        live = sanitize_live_bpm(bpm)
        if self._live_bpm == live:
            return
        self._live_bpm = live
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

        # A fresh stop event per connection: a lingering thread from a
        # previous connection keeps its own (already set) event and can never
        # be revived by the new one.
        stop_event = threading.Event()
        self._rx_stop = stop_event
        self._rx_thread = threading.Thread(
            target=self._rx_loop,
            args=(stop_event,),
            name=f"h9-rx-{self._connection_id}",
            daemon=True,
        )
        self._rx_thread.start()

    def _rx_loop(self, stop_event: threading.Event) -> None:
        connection_id = self._connection_id
        self._logger.info("RX loop started (connection_id=%d)", connection_id)
        while not stop_event.is_set():
            transport = self._transport
            if transport is None:
                time.sleep(0.05)
                continue

            try:
                msgs = transport.receive_pending()
            except Exception as exc:
                self._logger.error(
                    "RX failed (connection_id=%d, prefix=%r): %s",
                    connection_id,
                    self._device_prefix,
                    exc,
                    exc_info=True,
                )
                self._report_rx_failure(connection_id, stop_event, str(exc))
                break

            has_traffic = bool(msgs)
            for msg in msgs:
                frame = decode_eventide_sysex(msg)
                if frame is None:
                    # Malformed / foreign frame: discard, never fatal.
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

            if not has_traffic and not self._port_still_present(transport):
                self._logger.warning(
                    "MIDI port disappeared (connection_id=%d, prefix=%r)",
                    connection_id,
                    self._device_prefix,
                )
                self._report_rx_failure(
                    connection_id, stop_event, "MIDI port disappeared"
                )
                break

            with self._waiters_lock:
                await_frame = bool(self._waiters)

            # Poll quickly while a SysEx response is being awaited or the
            # device is streaming (keeps waiter latency low); back off when
            # idle to cut CPU churn on the Pi.
            time.sleep(
                _RX_BUSY_SLEEP_S if (has_traffic or await_frame) else _RX_IDLE_SLEEP_S
            )
        self._logger.info("RX loop stopped (connection_id=%d)", connection_id)

    def _report_rx_failure(
        self,
        connection_id: int,
        stop_event: threading.Event,
        reason: str,
    ) -> None:
        """Report an RX fault only when it belongs to the current connection.

        A thread from a previous connection can still raise while it is being
        torn down (its transport is closed underneath it). If that report
        reached the worker it would cancel the *new* connection's waiters or
        tear the new connection down, so stale reports are dropped.
        """
        if stop_event.is_set() or connection_id != self._connection_id:
            self._logger.debug(
                "Ignoring RX failure from stale connection "
                "(connection_id=%d, current=%d)",
                connection_id,
                self._connection_id,
            )
            return

        # Release the worker thread *now*: it may be blocked in
        # `_wait_for_frame` and a queued slot cannot preempt it.
        self._cancel_waiters(RuntimeError("Disconnected"))
        self._connection_lost.emit(reason)

    def _port_still_present(self, transport: H9Transport) -> bool:
        """Check port presence at most every `_PORT_CHECK_INTERVAL_S`."""
        now = time.monotonic()
        if (now - self._last_port_check_at) < _PORT_CHECK_INTERVAL_S:
            return True
        self._last_port_check_at = now

        is_present = getattr(transport, "is_port_present", None)
        if is_present is None:
            return True
        try:
            return bool(is_present())
        except Exception:
            self._logger.debug("Port presence check failed", exc_info=True)
            return True

    def _try_deliver_to_waiters(self, frame: SysexFrame) -> bool:
        current_id = self._connection_id
        with self._waiters_lock:
            for waiter in list(self._waiters):
                if waiter.connection_id != current_id:
                    # Created for a previous connection: never satisfy it.
                    self._waiters.remove(waiter)
                    waiter.cancel(RuntimeError("Disconnected"))
                    continue
                if waiter.try_set(frame):
                    self._waiters.remove(waiter)
                    return True
        return False

    def _cancel_waiters(self, exc: BaseException) -> None:
        """Release every waiter. Safe from any thread; safe to call twice."""
        with self._waiters_lock:
            waiters = self._waiters
            self._waiters = []
        for waiter in waiters:
            waiter.cancel(exc)

    def _wait_for_frame(
        self, predicate: Callable[[SysexFrame], bool], *, timeout_s: float
    ) -> SysexFrame:
        waiter = _FrameWaiter(predicate, connection_id=self._connection_id)
        with self._waiters_lock:
            self._waiters.append(waiter)

        frame = waiter.wait(timeout_s)
        if frame is not None:
            return frame

        with self._waiters_lock:
            if waiter in self._waiters:
                self._waiters.remove(waiter)

        # A cancel means the connection died (or we are shutting down); a
        # plain timeout means the device did not answer.
        waiter.raise_if_cancelled()
        raise TimeoutError("Timed out waiting for matching SysEx response")

    def _send_eventide(self, command: int, payload: bytes = b"") -> None:
        transport = self._transport
        if transport is None:
            raise RuntimeError("Not connected")

        msg = build_eventide_sysex(self._connected_device_id, command, payload)
        try:
            transport.send_sysex(msg)
        except Exception as exc:
            self._logger.error(
                "Send failed (connection_id=%d, command=0x%02X): %s",
                self._connection_id,
                command,
                exc,
                exc_info=True,
            )
            # Already on the worker thread: report the loss immediately so the
            # caller's exception reaches an already-disconnected state.
            self._on_connection_lost(str(exc))
            raise

    def _request_current_program(self, *, timeout_s: float) -> PresetSnapshot:
        """Request the program dump, retrying the idempotent read once.

        Only this read is retried: relative commands (next/prev/knob deltas)
        and VALUE_PUT must never be replayed.
        """
        last_timeout: TimeoutError | None = None
        for attempt in range(1, _PROGRAM_DUMP_ATTEMPTS + 1):
            started = time.monotonic()
            self._logger.info(
                "Requesting current program (attempt=%d, connection_id=%d)",
                attempt,
                self._connection_id,
            )
            self._send_eventide(H9SysexCodes.SYSEXC_TJ_PROGRAM_WANT)

            try:
                frame = self._wait_for_frame(
                    lambda f: f.command == H9SysexCodes.SYSEXC_TJ_PROGRAM_DUMP,
                    timeout_s=timeout_s,
                )
            except TimeoutError as exc:
                last_timeout = exc
                self._logger.warning(
                    "Program dump timed out (attempt=%d, connection_id=%d, "
                    "elapsed=%.2fs)",
                    attempt,
                    self._connection_id,
                    time.monotonic() - started,
                )
                continue

            raw_text = frame.payload.decode("ascii", errors="replace")
            return parse_preset_dump_text(raw_text)

        if last_timeout is not None:
            raise last_timeout
        raise TimeoutError("Timed out waiting for matching SysEx response")

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
