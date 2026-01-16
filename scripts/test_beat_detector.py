#!/usr/bin/env python3
"""Standalone beat detector for testing on Raspberry Pi without UI."""

import argparse
import logging
import sys
import threading
import time
from collections import deque
from pathlib import Path

import librosa
import numpy as np
import pyaudio

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress numba debug logging
logging.getLogger("numba").setLevel(logging.WARNING)

# =============================================================================
# CONFIGURABLE PARAMETERS - Tune these for CPU/accuracy tradeoff
# =============================================================================

# Audio capture settings
SAMPLE_RATE = 44100  # Lower = less CPU (22050 for Pi, 44100 for high accuracy)
BUFFER_SIZE = 1024  # PyAudio buffer size per read (samples)

# Rolling buffer settings
BUFFER_DURATION = 8.0  # Seconds of audio to keep in rolling buffer
UPDATE_INTERVAL = 2.0  # Seconds between BPM recalculations

# Librosa beat_track parameters
HOP_LENGTH = 256  # Hop length for onset detection (larger = faster, less accurate)
START_BPM = 120.0  # Starting tempo estimate for beat tracking

# Smoothing
ENABLE_SMOOTHING = True  # Enable smoothing of BPM over time (exponential moving average)
SMOOTHING_ALPHA = 0.6  # Weight for new detection (0.0-1.0). Higher = more responsive.

# Onset strength parameters
DETREND = False  # Detrend onset envelope (can help with some audio)
CENTER = True  # Center the onset envelope
FMAX = 8000.0  # Max frequency for mel spectrogram (lower = less CPU)
FMIN = 20.0  # Min frequency for mel spectrogram


class StandaloneBeatDetector:
    """
    Standalone beat detector using librosa with a rolling audio buffer.

    Captures audio continuously, maintains a rolling buffer of BUFFER_DURATION seconds,
    and recalculates BPM every UPDATE_INTERVAL seconds.
    """

    def __init__(self, device_index: int | None = None, channels: int = 1) -> None:
        self.device_index = device_index
        self.channels = channels
        self.running = False
        self.capture_thread: threading.Thread | None = None
        self.analysis_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Audio parameters (sample rate may be adjusted to device native rate)
        self.sample_rate = SAMPLE_RATE
        self.buffer_size = BUFFER_SIZE

        # Calculate buffer sizes (will be recalculated if sample rate changes)
        self.buffer_samples = int(BUFFER_DURATION * self.sample_rate)
        self.update_samples = int(UPDATE_INTERVAL * self.sample_rate)

        # Rolling audio buffer (circular buffer using deque)
        self.buffer_lock = threading.Lock()
        self.audio_buffer: deque = deque(maxlen=self.buffer_samples)

        # Current BPM value
        self.bpm: float = 0.0

        # PyAudio setup
        self.p = pyaudio.PyAudio()
        self.stream: pyaudio.Stream | None = None

    def start(self) -> None:
        """Start the beat detection threads."""
        if self.running:
            return

        self.running = True
        self._stop_event.clear()

        # Start capture thread
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

        # Start analysis thread
        self.analysis_thread = threading.Thread(target=self._analysis_loop, daemon=True)
        self.analysis_thread.start()

        logger.info("BeatDetector started.")

    def stop(self) -> None:
        """Stop the beat detection thread."""
        if not self.running:
            return
        self.running = False
        self._stop_event.set()

        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)
        self.capture_thread = None

        if self.analysis_thread and self.analysis_thread.is_alive():
            self.analysis_thread.join(timeout=2.0)
        self.analysis_thread = None

        logger.info("BeatDetector stopped.")

    def _capture_loop(self) -> None:
        """Thread 1: Audio Capture Loop - dedicated for real-time I/O."""
        input_device_index = self.device_index
        channels = self.channels

        # Resolve device if needed or use default
        if input_device_index is None:
            try:
                default_info = self.p.get_default_input_device_info()
                input_device_index = int(default_info["index"])
            except OSError:
                logger.error("No default input device found.")
                self.running = False
                return

        # Get device info and adjust sample rate to native rate
        try:
            dev_info = self.p.get_device_info_by_index(input_device_index)
            native_rate = int(dev_info.get("defaultSampleRate", 44100))
            max_input_channels = int(dev_info.get("maxInputChannels", 1))

            if channels > max_input_channels:
                logger.warning(
                    f"Requested {channels} channels but device only has "
                    f"{max_input_channels}. Using {max_input_channels}."
                )
                channels = max_input_channels

            # Update sample rate to match device native rate (avoid resampling artifacts)
            if native_rate != self.sample_rate:
                logger.debug(
                    f"Switching to native device rate: {native_rate} (was {self.sample_rate})"
                )
                self.sample_rate = native_rate
                self._recalculate_buffer_sizes()

        except Exception as e:
            logger.error(f"Error getting device info for device {input_device_index}: {e}")
            input_device_index = self._find_fallback_device(channels)
            if input_device_index is None:
                self.running = False
                return

        # Try to open audio stream
        if not self._open_stream(input_device_index, channels):
            self.running = False
            return

        logger.info(
            f"Listening for beats on device {input_device_index} at {self.sample_rate}Hz "
            f"(buffer: {BUFFER_DURATION}s, update: {UPDATE_INTERVAL}s)"
        )

        # Main capture loop
        assert self.stream is not None
        while self.running and not self._stop_event.is_set():
            try:
                audio_data = self.stream.read(self.buffer_size, exception_on_overflow=False)
                samples = np.frombuffer(audio_data, dtype=np.float32)

                # Append to deque (thread-safe extension)
                with self.buffer_lock:
                    self.audio_buffer.extend(samples)

            except Exception as e:
                if self.running:
                    logger.error(f"Error reading audio: {e}")
                    time.sleep(0.1)

        self._cleanup_stream()

    def _analysis_loop(self) -> None:
        """Thread 2: Analysis Loop - heavy processing without blocking capture."""
        while self.running and not self._stop_event.is_set():
            time.sleep(UPDATE_INTERVAL)

            snapshot = None
            with self.buffer_lock:
                # Check if we have enough data (at least a significant portion of buffer)
                if len(self.audio_buffer) >= self.update_samples:
                    snapshot = np.array(self.audio_buffer, dtype=np.float32)

            if snapshot is not None and len(snapshot) > 0:
                self._calculate_bpm(snapshot)

    def _recalculate_buffer_sizes(self) -> None:
        """Recalculate buffer sizes based on current sample rate."""
        self.buffer_samples = int(BUFFER_DURATION * self.sample_rate)
        self.update_samples = int(UPDATE_INTERVAL * self.sample_rate)
        with self.buffer_lock:
            self.audio_buffer = deque(maxlen=self.buffer_samples)

    def _find_fallback_device(self, channels: int) -> int | None:
        """Find a fallback input device if the configured one fails."""
        try:
            count = self.p.get_device_count()
            for i in range(count):
                info = self.p.get_device_info_by_index(i)
                if int(info.get("maxInputChannels", 0)) > 0:
                    logger.warning(f"Falling back to device {i}: {info.get('name', 'Unknown')}")
                    return i
        except Exception as e:
            logger.error(f"Failed to find fallback device: {e}")
        logger.error("No audio input devices available. Beat detection disabled.")
        return None

    def _open_stream(self, device_index: int, channels: int) -> bool:
        """Open the PyAudio stream. Returns True on success."""
        try:
            dev_info = self.p.get_device_info_by_index(device_index)
            max_channels = int(dev_info.get("maxInputChannels", 1))
            actual_channels = min(channels, max_channels)

            self.stream = self.p.open(
                format=pyaudio.paFloat32,
                channels=actual_channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.buffer_size,
                input_device_index=device_index,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to open audio stream on device {device_index}: {e}")
            return False

    def _cleanup_stream(self) -> None:
        """Clean up audio resources."""
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def _calculate_bpm(self, audio_data: np.ndarray) -> None:
        """Calculate BPM from the provided audio buffer using Inter-Beat Intervals (IBI)."""
        try:
            # Skip if buffer is mostly silence
            if np.max(np.abs(audio_data)) < 0.01:
                logger.debug("Buffer is silent, skipping BPM calculation")
                return

            # Calculate onset strength envelope
            onset_env = librosa.onset.onset_strength(
                y=audio_data,
                sr=self.sample_rate,
                hop_length=HOP_LENGTH,
                fmax=FMAX,
                center=CENTER,
                detrend=DETREND,
            )

            # Adaptive starting BPM: if we have a valid previous reading, use it
            # This prevents octave jumps (60 vs 120) and helps lock on
            current_start_bpm = self.bpm if self.bpm > 0 else START_BPM

            # Use beat_track to find beat locations
            # tightness=100 helps lock onto stable beats in electronic music
            tempo, beats = librosa.beat.beat_track(
                onset_envelope=onset_env,
                sr=self.sample_rate,
                hop_length=HOP_LENGTH,
                start_bpm=current_start_bpm,
                tightness=100,
            )

            if len(beats) < 2:
                logger.debug("Not enough beats detected")
                return

            # Refine beat locations using parabolic interpolation for sub-frame accuracy
            refined_beats = self._refine_beats(beats, onset_env)

            # Analyze beat timestamps for higher precision
            beat_times = refined_beats * HOP_LENGTH / self.sample_rate
            ibis = np.diff(beat_times)

            # Filter out unreasonable intervals (outside 40-220 BPM range)
            # 220 BPM ~= 0.27s, 40 BPM = 1.5s
            valid_ibis = ibis[(ibis > 0.27) & (ibis < 1.5)]

            if len(valid_ibis) == 0:
                logger.debug("No valid beat intervals found")
                return

            # Cluster Averaging for precision
            raw_bpm = self._calculate_bpm_from_ibis(valid_ibis)

            # Apply smoothing if enabled
            if ENABLE_SMOOTHING and self.bpm > 0:
                new_bpm = (self.bpm * (1 - SMOOTHING_ALPHA)) + (raw_bpm * SMOOTHING_ALPHA)
                self.bpm = round(new_bpm, 1)
            else:
                self.bpm = round(raw_bpm, 1)

            # Print to console
            print(f"BPM: {self.bpm} (raw: {raw_bpm:.1f})")

        except Exception as e:
            logger.error(f"Error calculating BPM: {e}")

    def _refine_beats(self, beats: np.ndarray, onset_env: np.ndarray) -> np.ndarray:
        """Refine beat locations using parabolic interpolation for sub-frame accuracy."""
        refined_beats = []
        for b in beats:
            if 0 < b < len(onset_env) - 1:
                alpha = onset_env[b - 1]
                beta = onset_env[b]
                gamma = onset_env[b + 1]

                # Only interpolate if distinct local peak
                denom = alpha - 2 * beta + gamma
                if beta >= alpha and beta >= gamma and denom != 0:
                    p = 0.5 * (alpha - gamma) / denom
                    refined_beats.append(b + p)
                else:
                    refined_beats.append(b)
            else:
                refined_beats.append(b)
        return np.array(refined_beats)

    def _calculate_bpm_from_ibis(self, valid_ibis: np.ndarray) -> float:
        """Calculate BPM from inter-beat intervals using cluster averaging."""
        # Get the median to find the "center" of the rhythm (rejects outliers)
        median_ibi = np.median(valid_ibis)

        # Select intervals within 5% of the median (rejects missed/double beats)
        tolerance = 0.05
        cluster_ibis = valid_ibis[np.abs(valid_ibis - median_ibi) <= (tolerance * median_ibi)]

        # Take the MEAN of this cluster for sub-sample precision
        if len(cluster_ibis) > 0:
            mean_ibi = float(np.mean(cluster_ibis))
            return 60.0 / mean_ibi
        return 60.0 / float(median_ibi)

    def __del__(self) -> None:
        """Cleanup PyAudio on deletion."""
        try:
            self.p.terminate()
        except Exception:
            pass


def list_devices() -> None:
    """List all available audio input devices."""
    p = pyaudio.PyAudio()
    print("\n=== Available Audio Input Devices ===")
    try:
        default_input = p.get_default_input_device_info()
        default_index = int(default_input["index"])
    except OSError:
        default_index = -1

    count = p.get_device_count()
    for i in range(count):
        info = p.get_device_info_by_index(i)
        max_input_channels = int(info.get("maxInputChannels", 0))
        if max_input_channels > 0:
            default_marker = " (DEFAULT)" if i == default_index else ""
            print(
                f"  [{i}] {info.get('name', 'Unknown')} "
                f"({max_input_channels} ch, {int(info.get('defaultSampleRate', 0))}Hz){default_marker}"
            )
    print()
    p.terminate()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Standalone beat detector for testing on Raspberry Pi"
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="Audio device index to use. If not specified, uses default device.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio devices and exit.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )

    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        sys.exit(0)

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info("Starting standalone beat detector...")
    if args.device is not None:
        logger.info(f"Using device index: {args.device}")

    detector = StandaloneBeatDetector(device_index=args.device, channels=1)

    try:
        detector.start()
        logger.info("Press Ctrl+C to stop...")
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
        detector.stop()
        logger.info("Done.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error: {e}")
        detector.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
