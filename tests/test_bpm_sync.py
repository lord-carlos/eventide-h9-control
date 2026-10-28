"""Tests for the extracted BPM helpers in h9control/app/bpm_sync.py."""

from __future__ import annotations

from h9control.app.bpm_sync import clamp_bpm, sanitize_live_bpm


def test_sanitize_live_bpm_rejects_stream_death_sentinel() -> None:
    # The beat detector emits -1.0 when the audio stream dies.
    assert sanitize_live_bpm(-1.0) is None
    assert sanitize_live_bpm(None) is None
    assert sanitize_live_bpm(120.5) == 120.5


def test_clamp_bpm_rounds_and_clamps_to_h9_range() -> None:
    assert clamp_bpm(140.4) == 140
    assert clamp_bpm(1000.0) == 300
    assert clamp_bpm(-5.0) == 20
    assert clamp_bpm(0.0) == 20
