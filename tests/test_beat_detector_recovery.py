"""Recovery behaviour of the beat detector: late and unplugged audio devices.

Everything here runs against a fake ``sounddevice`` module, so no audio
hardware is touched and no PortAudio device list is required.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from h9control.app.config import ConfigManager
from h9control.audio import beat_detector
from h9control.audio.beat_detector import RECOVERY_BACKOFF_DELAYS, BeatDetector

FAST_INTERVAL = 0.01

USB_DEVICE = {
    "name": "USB Audio Input",
    "hostapi": 0,
    "max_input_channels": 2,
    "max_output_channels": 0,
    "default_samplerate": 48000.0,
}
OTHER_USB_DEVICE = {
    "name": "Other Audio Input",
    "hostapi": 0,
    "max_input_channels": 2,
    "max_output_channels": 0,
    "default_samplerate": 48000.0,
}
OUTPUT_ONLY_DEVICE = {
    "name": "Output Only",
    "hostapi": 0,
    "max_input_channels": 0,
    "max_output_channels": 2,
    "default_samplerate": 48000.0,
}


class FakeInputStream:
    """Minimal stand-in for ``sounddevice.InputStream``."""

    instances: list[FakeInputStream] = []
    fail_to_open = False

    def __init__(
        self,
        *,
        device=None,
        channels=1,
        samplerate=48000,
        blocksize=1024,
        dtype="float32",
        latency="high",
        callback=None,
    ) -> None:
        if FakeInputStream.fail_to_open:
            raise RuntimeError("device unavailable")
        self.device = device
        self.channels = channels
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.callback = callback
        self.started = False
        self.stopped = False
        self.closed = False
        FakeInputStream.instances.append(self)

    @property
    def active(self) -> bool:
        return self.started and not self.stopped

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


def install_fake_sd(
    monkeypatch: pytest.MonkeyPatch,
    devices: list[dict],
    *,
    fail_to_open: bool = False,
) -> dict:
    """Patch ``beat_detector.sd`` with a fake module; returns mutable state."""
    FakeInputStream.instances = []
    FakeInputStream.fail_to_open = fail_to_open
    state = {"devices": list(devices)}

    def query_devices(device=None, kind=None):
        items = state["devices"]
        if device is None:
            if kind == "input":
                for index, dev in enumerate(items):
                    if int(dev.get("max_input_channels", 0)) > 0:
                        return dict(dev, index=index)
                raise RuntimeError("no default input device")
            return items
        return items[device]

    monkeypatch.setattr(
        beat_detector,
        "sd",
        SimpleNamespace(query_devices=query_devices, InputStream=FakeInputStream),
    )
    return state


@pytest.fixture()
def fast_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the analysis loop pacing short so tests finish quickly."""
    monkeypatch.setattr(beat_detector, "UPDATE_INTERVAL", FAST_INTERVAL)


@pytest.fixture()
def detector(config: ConfigManager) -> BeatDetector:
    det = BeatDetector(config)
    yield det
    det.stop()


def test_happy_path_opens_stream_and_stop_joins_the_analysis_thread(
    monkeypatch: pytest.MonkeyPatch,
    detector: BeatDetector,
    fast_loop: None,
) -> None:
    install_fake_sd(monkeypatch, [USB_DEVICE])

    before = set(threading.enumerate())
    detector.start()

    assert detector.running is True
    assert detector.status_text == "Listening"
    new_threads = [t for t in threading.enumerate() if t not in before]
    assert len(new_threads) == 1
    assert new_threads[0] is detector.analysis_thread
    assert new_threads[0].is_alive()

    stream = FakeInputStream.instances[-1]
    assert len(FakeInputStream.instances) == 1
    assert stream.started is True
    assert stream.device == 0  # default input device

    detector.stop()

    assert detector.status_text == "Stopped"
    assert detector.analysis_thread is None
    assert not new_threads[0].is_alive()
    assert stream.closed is True


def test_start_without_a_device_keeps_running_and_retrying(
    monkeypatch: pytest.MonkeyPatch,
    detector: BeatDetector,
    fast_loop: None,
) -> None:
    install_fake_sd(monkeypatch, [], fail_to_open=True)

    detector.start()

    assert detector.running is True
    assert detector._needs_recovery is True
    assert detector.analysis_thread is not None
    assert detector.analysis_thread.is_alive()
    assert detector.status_text == "Audio disconnected (retrying)"

    detector.stop()

    assert detector.status_text == "Stopped"
    assert detector.analysis_thread is None


def test_recovery_backoff_wait_is_interruptible(
    monkeypatch: pytest.MonkeyPatch,
    detector: BeatDetector,
) -> None:
    install_fake_sd(monkeypatch, [USB_DEVICE])
    monkeypatch.setattr(detector, "_start_stream", lambda *a, **kw: False)
    detector._needs_recovery = True

    result: dict[str, bool] = {}
    worker = threading.Thread(
        target=lambda: result.update(recovered=detector._attempt_recovery()),
        daemon=True,
    )
    worker.start()
    time.sleep(0.1)  # let it enter the first backoff wait

    started = time.monotonic()
    detector._stop_event.set()
    worker.join(timeout=2.0)
    elapsed = time.monotonic() - started

    assert not worker.is_alive()
    assert result["recovered"] is False
    assert elapsed < 0.5


def test_recovery_never_gives_up_and_keeps_backing_off(
    monkeypatch: pytest.MonkeyPatch,
    detector: BeatDetector,
) -> None:
    install_fake_sd(monkeypatch, [USB_DEVICE])
    monkeypatch.setattr(detector, "_start_stream", lambda *a, **kw: False)

    requested: list[float] = []
    monkeypatch.setattr(
        detector._stop_event,
        "wait",
        lambda timeout=None: requested.append(timeout) or False,
    )

    for _ in range(7):
        assert detector._attempt_recovery() is False

    backoff = requested[0::2]
    settle = requested[1::2]
    assert backoff == [1, 2, 4, 8, 16, 30, 30]
    assert backoff == list(RECOVERY_BACKOFF_DELAYS) + [RECOVERY_BACKOFF_DELAYS[-1]]
    assert settle == [0.5] * 7
    assert detector.recovery_attempts == 7


def test_bpm_loss_is_signalled_once_per_outage(
    monkeypatch: pytest.MonkeyPatch,
    detector: BeatDetector,
) -> None:
    install_fake_sd(monkeypatch, [USB_DEVICE])
    emitted: list[float] = []
    detector.bpm_detected.connect(emitted.append)
    detector.bpm = 128.0

    open_stream = {"ok": False}
    monkeypatch.setattr(detector, "_start_stream", lambda *a, **kw: open_stream["ok"])
    monkeypatch.setattr(detector._stop_event, "wait", lambda timeout=None: False)

    assert detector._attempt_recovery() is False
    assert detector._attempt_recovery() is False
    assert emitted == [-1.0]
    assert detector.bpm == 128.0  # stabilizer keeps its last value
    assert detector.status_text == "Audio disconnected (retrying)"

    open_stream["ok"] = True
    assert detector._attempt_recovery() is True
    assert detector._bpm_loss_cleared is False
    assert detector._needs_recovery is False
    assert detector.recovery_attempts == 0
    assert detector.status_text == "Listening"
    assert emitted == [-1.0]

    open_stream["ok"] = False
    assert detector._attempt_recovery() is False
    assert emitted == [-1.0, -1.0]


def test_bpm_loss_is_signalled_before_the_backoff_wait(
    monkeypatch: pytest.MonkeyPatch,
    detector: BeatDetector,
) -> None:
    install_fake_sd(monkeypatch, [USB_DEVICE])
    events: list[tuple[str, object]] = []
    detector.bpm = 128.0
    detector.bpm_detected.connect(lambda value: events.append(("emit", value)))
    monkeypatch.setattr(detector, "_start_stream", lambda *a, **kw: False)
    monkeypatch.setattr(
        detector._stop_event,
        "wait",
        lambda timeout=None: events.append(("wait", timeout)) or True,
    )

    assert detector._attempt_recovery() is False
    assert events[0] == ("emit", -1.0)


def test_start_clears_stale_recovery_flag(
    monkeypatch: pytest.MonkeyPatch,
    detector: BeatDetector,
    fast_loop: None,
) -> None:
    install_fake_sd(monkeypatch, [USB_DEVICE])
    detector._needs_recovery = True

    detector.start()
    try:
        assert detector._needs_recovery is False
        assert detector.status_text == "Listening"
    finally:
        detector.stop()


def test_resolve_saved_identity_follows_reordered_devices(
    monkeypatch: pytest.MonkeyPatch,
    detector: BeatDetector,
) -> None:
    state = install_fake_sd(monkeypatch, [USB_DEVICE])
    detector._device_identity = ("USB Audio Input", 0)

    assert detector._resolve_saved_identity() == 0

    state["devices"] = [OUTPUT_ONLY_DEVICE, USB_DEVICE]

    assert detector._resolve_saved_identity() == 1


def test_resolve_saved_identity_returns_none_without_a_match(
    monkeypatch: pytest.MonkeyPatch,
    detector: BeatDetector,
) -> None:
    install_fake_sd(monkeypatch, [OUTPUT_ONLY_DEVICE])

    detector._device_identity = None
    assert detector._resolve_saved_identity() is None

    detector._device_identity = ("USB Audio Input", 0)
    assert detector._resolve_saved_identity() is None


def test_resolve_saved_identity_returns_none_when_query_fails(
    monkeypatch: pytest.MonkeyPatch,
    detector: BeatDetector,
) -> None:
    install_fake_sd(monkeypatch, [USB_DEVICE])
    detector._device_identity = ("USB Audio Input", 0)

    def failing_query_devices(*args, **kwargs):
        raise RuntimeError("PortAudio device query failed")

    monkeypatch.setattr(beat_detector.sd, "query_devices", failing_query_devices)

    assert detector._resolve_saved_identity() is None


def test_start_stream_device_index_overrides_config(
    monkeypatch: pytest.MonkeyPatch,
    config: ConfigManager,
    detector: BeatDetector,
) -> None:
    install_fake_sd(monkeypatch, [USB_DEVICE, OTHER_USB_DEVICE])
    config.audio_input_device_id = 0

    assert detector._start_stream(device_index=1) is True
    assert FakeInputStream.instances[-1].device == 1
    assert detector._device_identity == ("Other Audio Input", 0)
    detector._cleanup_stream()

    assert detector._start_stream() is True
    assert FakeInputStream.instances[-1].device == 0
    detector._cleanup_stream()


def test_input_overflow_still_requests_recovery(
    detector: BeatDetector,
) -> None:
    class _OverflowStatus:
        input_overflow = True
        input_underflow = False

    detector._needs_recovery = False
    indata = np.zeros((512, 2), dtype=np.float32)

    detector._audio_callback(indata, 512, None, _OverflowStatus())

    assert detector._needs_recovery is True
