from __future__ import annotations

import numpy as np
import pytest

from h9control.app.config import ConfigManager
from h9control.audio.beat_detector import BeatDetector, READ_MARGIN_BLOCKS


class _FakeCallbackStatus:
    input_overflow = False
    input_underflow = False


@pytest.fixture()
def detector(config: ConfigManager) -> BeatDetector:
    return BeatDetector(config)


def _feed_ramp_mono(
    detector: BeatDetector, start: int, frames: int, blocksize: int = 1024
) -> None:
    """Feed a continuous sample ramp (value == interleaved index) via the callback."""
    written = 0
    while written < frames:
        n = min(blocksize, frames - written)
        indata = np.zeros((n, 1), dtype=np.float32)
        indata[:, 0] = np.arange(start + written, start + written + n, dtype=np.float32)
        detector._audio_callback(indata, n, None, _FakeCallbackStatus())
        written += n


def test_audio_path_is_lock_free(detector: BeatDetector) -> None:
    # SPSC design: the producer publishes one integer; no locks may appear
    # on the real-time callback path.
    assert not hasattr(detector, "_write_lock")


def test_extract_stereo_interleaves_selected_channels(detector: BeatDetector) -> None:
    detector.selected_channels = [1, 0]
    indata = np.zeros((3, 2), dtype=np.float32)
    indata[:, 0] = 10.0  # L
    indata[:, 1] = 20.0  # R

    out = detector._extract_stereo_channels_fast(indata)

    np.testing.assert_allclose(out, np.tile(np.array([20.0, 10.0], np.float32), 3))


def test_extract_stereo_clamps_out_of_range_channel_indices(
    detector: BeatDetector,
) -> None:
    detector.selected_channels = [5, 6]
    indata = np.zeros((4, 2), dtype=np.float32)
    indata[:, 0] = 1.0
    indata[:, 1] = 2.0

    out = detector._extract_stereo_channels_fast(indata)

    # Both indices clamp to the last channel of a 2-channel input
    np.testing.assert_allclose(out, np.full(8, 2.0, dtype=np.float32))


def test_extract_stereo_short_channel_list_falls_back_to_mono(
    detector: BeatDetector,
) -> None:
    # Config list corrupted/changed to a single entry mid-stream: must not
    # raise IndexError on the audio thread.
    detector.selected_channels = [0]
    indata = np.zeros((3, 2), dtype=np.float32)
    indata[:, 0] = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    indata[:, 1] = np.array([9.0, 9.0, 9.0], dtype=np.float32)

    out = detector._extract_stereo_channels_fast(indata)

    np.testing.assert_allclose(
        out, np.repeat(np.array([1.0, 2.0, 3.0], np.float32), 2)
    )


def test_extract_stereo_empty_channel_list_falls_back_to_mono(
    detector: BeatDetector,
) -> None:
    detector.selected_channels = []
    indata = np.zeros((2, 1), dtype=np.float32)
    indata[:, 0] = np.array([4.0, 5.0], dtype=np.float32)

    out = detector._extract_stereo_channels_fast(indata)

    np.testing.assert_allclose(out, np.array([4.0, 4.0, 5.0, 5.0], np.float32))


def test_read_ring_buffer_before_any_audio_returns_none(
    detector: BeatDetector,
) -> None:
    detector.sample_rate = 8000
    detector._recalculate_buffer_sizes()

    assert detector._read_ring_buffer(1024) is None


def test_spsc_consumer_never_reads_producer_workspace(
    detector: BeatDetector,
) -> None:
    # Shrink buffers so the test runs quickly, then force wrap-around.
    detector.sample_rate = 8000
    detector._recalculate_buffer_sizes()
    # Ring is mono now (default mode is stereo) -> rebuild in mono
    detector.mono_mode = True
    detector._recalculate_buffer_sizes()

    extra = 4096
    total_frames = detector.ring_size + extra
    _feed_ramp_mono(detector, 0, total_frames)

    published = detector.write_index
    assert published == total_frames

    # Ask for everything available: the consumer must clamp to the settled
    # window (published index minus the safety margin) and never read the
    # producer's in-flight workspace.
    snapshot = detector._read_ring_buffer(published)

    block = detector.buffer_size  # mono: 1 interleaved sample per frame
    margin = READ_MARGIN_BLOCKS * block
    expected_len = min(
        detector.ring_size - margin - block,  # never overlaps in-flight block
        published - margin,
    )
    assert snapshot is not None
    assert len(snapshot) == expected_len
    # Each interleaved position holds its own index as value: the newest
    # sample the consumer may read is (published - margin - 1).
    assert snapshot[-1] == np.float32(published - margin - 1)
    # Contiguous ramp ending exactly at the settled position.
    np.testing.assert_array_equal(
        snapshot,
        np.arange(published - margin - expected_len, published - margin, dtype=np.float32),
    )


def test_spsc_read_window_slides_with_published_index(
    detector: BeatDetector,
) -> None:
    detector.sample_rate = 8000
    detector.mono_mode = True
    detector._recalculate_buffer_sizes()

    # Three separated writes; read the last three samples each time.
    first_block = detector.buffer_size * 4
    for phase in range(3):
        start = phase * first_block
        _feed_ramp_mono(detector, start, first_block)
        window = detector._read_ring_buffer(3)

        assert window is not None
        # Newest settled sample = value at index write_index - margin - 1;
        # the window is the three samples ending there.
        np.testing.assert_array_equal(
            window,
            np.array(
                [
                    detector.write_index - READ_MARGIN_BLOCKS * detector.buffer_size - 3,
                    detector.write_index - READ_MARGIN_BLOCKS * detector.buffer_size - 2,
                    detector.write_index - READ_MARGIN_BLOCKS * detector.buffer_size - 1,
                ],
                dtype=np.float32,
            ),
        )
