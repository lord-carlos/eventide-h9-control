"""Librosa-based beat detector with rolling buffer and Qt signal integration."""

from __future__ import annotations

import logging
import threading
import time

import librosa
import numpy as np
import pyaudio
from PySide6.QtCore import QObject, Signal

from h9control.app.config import ConfigManager


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


class BeatDetector(QObject):
    """
    Beat detector using librosa with a rolling audio buffer.

    Captures audio continuously, maintains a rolling buffer of BUFFER_DURATION seconds,
    and recalculates BPM every UPDATE_INTERVAL seconds.
    """

    bpm_detected = Signal(float)

    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self.config = config
        self.running = False
        self.thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Audio parameters (sample rate may be adjusted to device native rate)
        self.sample_rate = SAMPLE_RATE
        self.buffer_size = BUFFER_SIZE

        # Calculate buffer sizes (will be recalculated if sample rate changes)
        self.buffer_samples = int(BUFFER_DURATION * self.sample_rate)
        self.update_samples = int(UPDATE_INTERVAL * self.sample_rate)

        # Rolling audio buffer (circular buffer using numpy)
        self.audio_buffer = np.zeros(self.buffer_samples, dtype=np.float32)
        self.samples_since_update = 0

        # Current BPM value
        self.bpm: float = 0.0

        # PyAudio setup
        self.p = pyaudio.PyAudio()
        self.stream: pyaudio.Stream | None = None

    def start(self) -> None:
        """Start the beat detection thread."""
        if self.running:
            return

        self.running = True
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logging.info("BeatDetector started.")

    def stop(self) -> None:
        """Stop the beat detection thread."""
        if not self.running:
            return
        self.running = False
        self._stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        self.thread = None
        logging.info("BeatDetector stopped.")

    def _run_loop(self) -> None:
        """Main thread loop - capture audio and periodically calculate BPM."""
        input_device_index = self.config.audio_input_device_id
        channels = self.config.audio_input_channels

        # Resolve device if needed or use default
        if input_device_index is None:
            try:
                default_info = self.p.get_default_input_device_info()
                input_device_index = int(default_info["index"])
            except OSError:
                logging.error("No default input device found.")
                self.running = False
                return

        # Get device info and adjust sample rate to native rate
        try:
            dev_info = self.p.get_device_info_by_index(input_device_index)
            native_rate = int(dev_info.get("defaultSampleRate", 44100))
            max_input_channels = int(dev_info.get("maxInputChannels", 1))

            if channels > max_input_channels:
                logging.warning(
                    f"Requested {channels} channels but device only has "
                    f"{max_input_channels}. Using {max_input_channels}."
                )
                channels = max_input_channels

            # Update sample rate to match device native rate (avoid resampling artifacts)
            if native_rate != self.sample_rate:
                logging.debug(
                    f"Switching to native device rate: {native_rate} (was {self.sample_rate})"
                )
                self.sample_rate = native_rate
                self._recalculate_buffer_sizes()

        except Exception as e:
            logging.error(f"Error getting device info for device {input_device_index}: {e}")
            input_device_index = self._find_fallback_device(channels)
            if input_device_index is None:
                self.running = False
                return

        # Try to open audio stream
        if not self._open_stream(input_device_index, channels):
            self.running = False
            return

        logging.info(
            f"Listening for beats on device {input_device_index} at {self.sample_rate}Hz "
            f"(buffer: {BUFFER_DURATION}s, update: {UPDATE_INTERVAL}s)"
        )

        # Main capture loop
        assert self.stream is not None
        while self.running and not self._stop_event.is_set():
            try:
                audio_data = self.stream.read(self.buffer_size, exception_on_overflow=False)
                samples = np.frombuffer(audio_data, dtype=np.float32)

                # Roll buffer and add new samples
                self.audio_buffer = np.roll(self.audio_buffer, -len(samples))
                self.audio_buffer[-len(samples) :] = samples

                self.samples_since_update += len(samples)

                # Recalculate BPM at update interval
                if self.samples_since_update >= self.update_samples:
                    self._calculate_bpm()
                    self.samples_since_update = 0

            except Exception as e:
                if self.running:
                    logging.error(f"Error reading audio: {e}")
                    time.sleep(0.1)

        self._cleanup_stream()

    def _recalculate_buffer_sizes(self) -> None:
        """Recalculate buffer sizes based on current sample rate."""
        self.buffer_samples = int(BUFFER_DURATION * self.sample_rate)
        self.update_samples = int(UPDATE_INTERVAL * self.sample_rate)
        self.audio_buffer = np.zeros(self.buffer_samples, dtype=np.float32)

    def _find_fallback_device(self, channels: int) -> int | None:
        """Find a fallback input device if the configured one fails."""
        try:
            count = self.p.get_device_count()
            for i in range(count):
                info = self.p.get_device_info_by_index(i)
                if int(info.get("maxInputChannels", 0)) > 0:
                    logging.warning(f"Falling back to device {i}: {info.get('name', 'Unknown')}")
                    return i
        except Exception as e:
            logging.error(f"Failed to find fallback device: {e}")
        logging.error("No audio input devices available. Beat detection disabled.")
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
            logging.error(f"Failed to open audio stream on device {device_index}: {e}")
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

    def _calculate_bpm(self) -> None:
        """Calculate BPM from the current audio buffer using Inter-Beat Intervals (IBI)."""
        try:
            # Skip if buffer is mostly silence
            if np.max(np.abs(self.audio_buffer)) < 0.01:
                logging.debug("Buffer is silent, skipping BPM calculation")
                return

            # Calculate onset strength envelope
            onset_env = librosa.onset.onset_strength(
                y=self.audio_buffer,
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
                logging.debug("Not enough beats detected")
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
                logging.debug("No valid beat intervals found")
                return

            # Cluster Averaging for precision
            raw_bpm = self._calculate_bpm_from_ibis(valid_ibis)

            # Apply smoothing if enabled
            if ENABLE_SMOOTHING and self.bpm > 0:
                new_bpm = (self.bpm * (1 - SMOOTHING_ALPHA)) + (raw_bpm * SMOOTHING_ALPHA)
                self.bpm = round(new_bpm, 1)
            else:
                self.bpm = round(raw_bpm, 1)

            logging.debug(f"BPM detected: {self.bpm} (raw: {raw_bpm:.2f})")

            # Emit signal for UI
            self.bpm_detected.emit(self.bpm)

        except Exception as e:
            logging.error(f"Error calculating BPM: {e}")

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
