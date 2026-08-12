from __future__ import annotations

import pytest

from h9control.app.config import ConfigManager
from h9control.app.state import DashboardState
from h9control.app.ui.qt_worker import H9DeviceWorker, _FrameWaiter, _PresetChangeDetector
from h9control.protocol.sysex import SysexFrame


def _make_worker(config: ConfigManager, **kwargs) -> H9DeviceWorker:
    worker = H9DeviceWorker(config=config, simulate_h9=True, **kwargs)
    return worker


def _frame(command: int, payload: bytes) -> SysexFrame:
    return SysexFrame(
        manufacturer_id=0x1C,
        model_id=0x70,
        device_id=1,
        command=command,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Connect / state population
# ---------------------------------------------------------------------------


def test_connect_populates_default_sim_preset(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()

    state = sim_worker._last_state
    assert state.connected is True
    assert state.preset_number == 1
    assert state.preset_name == "PRISTINE DIGITAL"
    assert state.algorithm_key == "DIGDLY"
    assert state.bpm == 125.0
    assert [k.name for k in state.knobs] == ["DLY-A", "DLY-B", "FBK-A", "FBK-B"]


# ---------------------------------------------------------------------------
# Knob adjustment
# ---------------------------------------------------------------------------


def test_adjust_knob_slot_steps_value_and_sets_override(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()
    before = sim_worker._last_state.knobs[0].raw_value

    sim_worker.adjust_knob_slot(0, 1)

    state = sim_worker._last_state
    assert state.knobs[0].raw_value > before
    assert state.knobs[0].pretty is not None
    assert sim_worker._knob_overrides["DLY-A"] == state.knobs[0].raw_value


def test_adjust_knob_slot_out_of_range_is_noop(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()
    before = sim_worker._last_state

    sim_worker.adjust_knob_slot(99, 1)
    sim_worker.adjust_knob_slot(-1, 1)

    assert sim_worker._last_state == before


def test_adjust_unknown_knob_is_noop(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()
    before = sim_worker._last_state

    sim_worker.adjust_knob("BOGUS", 1)

    assert sim_worker._last_state == before


def test_lock_delay_adjusts_both_delay_knobs(config: ConfigManager, qapp) -> None:
    config.lock_delay = True
    worker = _make_worker(config)
    try:
        worker.connect_or_refresh()
        worker.adjust_knob_slot(0, 1)

        assert "DLY-A" in worker._knob_overrides
        assert "DLY-B" in worker._knob_overrides
        state = worker._last_state
        assert state.lock_delay is True
        assert state.knobs[0].raw_value > 0
        assert state.knobs[1].raw_value > 0
    finally:
        worker.shutdown()


def test_lock_feedback_adjusts_both_feedback_knobs(config: ConfigManager, qapp) -> None:
    config.lock_feedback = True
    worker = _make_worker(config)
    try:
        worker.connect_or_refresh()
        worker.adjust_knob_slot(2, 1)

        assert "FBK-A" in worker._knob_overrides
        assert "FBK-B" in worker._knob_overrides
    finally:
        worker.shutdown()


def test_feedback_knob_uses_coarse_5_percent_step(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()
    sim_worker.adjust_knob_slot(2, 1)

    state = sim_worker._last_state
    expected = 0x4800 + int(round(32736 * 0.05))
    assert state.knobs[2].raw_value == expected


def test_knob_overrides_cleared_on_preset_change(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()
    sim_worker.adjust_knob_slot(0, 1)
    assert sim_worker._knob_overrides

    sim_worker.next_preset()

    assert not sim_worker._knob_overrides
    assert sim_worker._last_state.preset_number == 2


# ---------------------------------------------------------------------------
# Preset navigation
# ---------------------------------------------------------------------------


def test_next_and_prev_preset(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()
    sim_worker.next_preset()
    assert sim_worker._last_state.preset_name == "WARM ECHO"
    assert sim_worker._current_program == 1

    sim_worker.prev_preset()
    assert sim_worker._last_state.preset_name == "PRISTINE DIGITAL"
    assert sim_worker._current_program == 0


def test_prev_preset_wraps_through_program_127(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()
    sim_worker.prev_preset()
    assert sim_worker._last_state.preset_name == "MOTION DELAY"


def test_jump_to_preset(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()
    sim_worker.jump_to_preset(2)
    assert sim_worker._last_state.preset_name == "DARK TAPE"
    assert sim_worker._last_state.preset_number == 3


# ---------------------------------------------------------------------------
# BPM handling
# ---------------------------------------------------------------------------


def test_adjust_bpm_updates_device_and_state(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()
    sim_worker.adjust_bpm(5)
    assert sim_worker._last_state.bpm == 130.0


def test_adjust_bpm_clamps_to_range(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()
    sim_worker.adjust_bpm(1000)
    assert sim_worker._last_state.bpm == 300.0

    sim_worker.adjust_bpm(-1000)
    assert sim_worker._last_state.bpm == 20.0


def test_sync_live_bpm_writes_rounded_value(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()
    sim_worker.update_live_bpm(138.4)
    sim_worker.sync_live_bpm()
    assert sim_worker._last_state.bpm == 138.0


def test_sanitize_bpm_guardrails(sim_worker: H9DeviceWorker) -> None:
    assert sim_worker._sanitize_bpm(999.0) is None
    assert sim_worker._sanitize_bpm(19.0) is None
    assert sim_worker._sanitize_bpm(120.0) == 120.0
    assert sim_worker._sanitize_bpm(999.0) == 120.0
    assert sim_worker._sanitize_bpm(120.5) == 120.5


def test_auto_bpm_sync_only_sends_on_change(
    sim_worker: H9DeviceWorker, config: ConfigManager
) -> None:
    config.auto_bpm_mode = "continuous"
    sim_worker.connect_or_refresh()

    sim_worker.update_live_bpm(140.2)
    assert sim_worker._last_sent_auto_bpm == 140
    assert sim_worker._last_state.bpm == 140.0

    sim_worker.update_live_bpm(140.4)
    assert sim_worker._last_sent_auto_bpm == 140
    assert sim_worker._last_state.bpm == 140.0

    sim_worker.update_live_bpm(150.3)
    assert sim_worker._last_sent_auto_bpm == 150
    assert sim_worker._last_state.bpm == 150.0


def test_auto_bpm_sync_skipped_in_manual_mode(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()
    sim_worker.update_live_bpm(140.2)
    assert sim_worker._last_sent_auto_bpm is None
    assert sim_worker._last_state.bpm == 125.0
    assert sim_worker._last_state.live_bpm == 140.2


def test_live_bpm_persists_across_refresh(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()
    sim_worker.update_live_bpm(138.4)
    sim_worker.adjust_bpm(1)
    assert sim_worker._last_state.live_bpm == 138.4


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_refresh_after_shutdown_emits_error_state(sim_worker: H9DeviceWorker) -> None:
    sim_worker.connect_or_refresh()
    sim_worker.shutdown()

    sim_worker._refresh_state()

    state = sim_worker._last_state
    assert state.status_text.startswith("Refresh failed:")
    assert state.connected is True
    assert state.preset_name == "PRISTINE DIGITAL"


# ---------------------------------------------------------------------------
# Preset change detector
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t


def test_preset_detector_needs_burst_of_different_prefixes() -> None:
    clock = _Clock()
    detector = _PresetChangeDetector(now=clock.now)

    assert detector.observe(_frame(0x60, b"\x01\x02\x03")) is False
    clock.t += 0.01
    assert detector.observe(_frame(0x60, b"\x04\x05\x06")) is True


def test_preset_detector_ignores_button_noise() -> None:
    clock = _Clock()
    detector = _PresetChangeDetector(now=clock.now)

    assert detector.observe(_frame(0x60, b"\x07\x00\x5C\x11")) is False
    clock.t += 0.01
    assert detector.observe(_frame(0x60, b"\x07\x00\x5C\x22")) is False


def test_preset_detector_expires_old_prefixes() -> None:
    clock = _Clock()
    detector = _PresetChangeDetector(now=clock.now)

    assert detector.observe(_frame(0x60, b"\x01\x02\x03")) is False
    clock.t += 0.2
    assert detector.observe(_frame(0x60, b"\x04\x05\x06")) is False


def test_preset_detector_ignores_other_commands_and_short_payloads() -> None:
    clock = _Clock()
    detector = _PresetChangeDetector(now=clock.now)

    assert detector.observe(_frame(0x4F, b"\x01\x02\x03")) is False
    assert detector.observe(_frame(0x60, b"\x01\x02")) is False
    clock.t += 0.01
    assert detector.observe(_frame(0x60, b"\x01\x02\x03")) is False


# ---------------------------------------------------------------------------
# Frame waiter
# ---------------------------------------------------------------------------


def test_frame_waiter_matches_predicate() -> None:
    waiter = _FrameWaiter(lambda f: f.command == 0x4F)

    assert waiter.try_set(_frame(0x60, b"")) is False
    assert waiter.try_set(_frame(0x4F, b"")) is True
    assert waiter.try_set(_frame(0x4F, b"")) is False
    assert waiter.wait(timeout_s=0.01) == _frame(0x4F, b"")


def test_frame_waiter_times_out() -> None:
    waiter = _FrameWaiter(lambda f: False)
    assert waiter.wait(timeout_s=0.01) is None
