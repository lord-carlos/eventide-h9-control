"""Librosa-based beat detector with a single-producer/single-consumer ring
buffer for Raspberry Pi."""

from __future__ import annotations

import logging
import os
import threading
import time

import librosa
import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, Signal

from h9control.app.config import ConfigManager


# =============================================================================
# CONFIGURABLE PARAMETERS - Tune these for CPU/accuracy tradeoff
# =============================================================================

# Audio capture settings
SAMPLE_RATE = 48000  # Capture rate; analysis runs at ANALYSIS_SAMPLE_RATE
BUFFER_SIZE = 1024  # Buffer size per callback (samples)

# Rolling buffer / analysis settings
BUFFER_DURATION = 10.0  # Seconds of audio to keep in rolling buffer
ANALYSIS_WINDOW = 8.0  # Seconds of audio analyzed per pass (~16 beats at 120 BPM)
UPDATE_INTERVAL = 2.0  # Seconds between BPM recalculations
ANALYSIS_SAMPLE_RATE = 22050  # librosa's native rate; no tempo information lost

# Librosa beat_track parameters
HOP_LENGTH = 256  # Hop length for onset detection (larger = faster, less accurate)
START_BPM = 120.0  # Initial tempo estimate for beat tracking
TIGHTNESS = 100  # Beat tracker tightness (100=strict, 50=adaptive)

# Onset strength parameters
DETREND = False  # Detrend onset envelope (can help with some audio)
CENTER = True  # Center the onset envelope
FMAX = 8000.0  # Max frequency for mel spectrogram (lower = less CPU)
FMIN = 20.0  # Min frequency for mel spectrogram

# Performance settings
MONO_MODE = False  # Set to True to use only first channel (halves CPU/USB bandwidth)

# Silence handling
SILENCE_THRESHOLD = 0.05  # Skip BPM calc when audio below this level
HOLD_BPM_ON_SILENCE = True  # Keep last BPM during silence/breakdowns

# Stream recovery backoff (seconds per attempt; the last value repeats). The
# stream is retried indefinitely, so a late or unplugged USB device is picked
# up whenever it appears.
RECOVERY_BACKOFF_DELAYS = (1, 2, 4, 8, 16, 30)

# SPSC safety margin: the analysis thread never reads closer to the published
# write index than this many callback blocks (in interleaved samples). One
# in-flight block is the provable minimum; two give headroom against
# back-to-back callbacks. Beat detection tolerates this much staleness.
READ_MARGIN_BLOCKS = 2

# Harmonic (octave) correction: raw readings are mapped into the configured
# BPM range by scoring these tempo multiples against the onset autocorrelation,
# weighted by a log-normal prior around the perceptual tempo center.
HARMONIC_FACTORS = (0.5, 2 / 3, 0.75, 1.0, 4 / 3, 1.5, 2.0)
PRIOR_CENTER_BPM = 120.0  # Perceptual tempo prior center
PRIOR_STD_OCTAVES = 1.0  # Prior width in octaves
# Comb scoring: autocorrelation weights at 1x-4x each candidate's beat period,
# so a true 4/4 tempo also collects energy at the 2-beat and bar level, while
# spurious fractional periodicities (e.g. 3-eighth-note accents) only hit some.
COMB_WEIGHTS = (1.0, 0.5, 1 / 3, 0.25)
# Plain octave corrections are trusted fully; triplet-based factors (2/3, 3/4,
# 4/3, 3/2) must win with slightly stronger evidence (most music is 4/4).
# Keep this light: the penalty also applies when ESCAPING a wrong triplet
# lock (e.g. tracker stuck at 92 BPM on a true 138 BPM song), so a strong
# penalty defeats itself. The comb sum is the primary 4/4 discriminator.
OCTAVE_FACTORS = (0.5, 1.0, 2.0)
TRIPLET_FACTOR_PENALTY = 0.95

# Stabilization
SMOOTHING_ALPHA = 0.6  # EMA weight for new readings during fine tracking
FINE_TRACK_TOLERANCE = 0.03  # <=3% deviation: fine tracking, else hold/switch
SWITCH_CONFIRM_READINGS = 3  # Consecutive readings needed to confirm a tempo change


class BeatDetector(QObject):
    """
    Beat detector using librosa with a single-producer/single-consumer ring
    buffer for real-time audio.

    The audio callback (producer) writes each block into the ring buffer and
    then publishes the write index with a single integer assignment -- the
    callback never acquires a lock. The analysis thread (consumer) takes one
    snapshot of the published index per read and only touches fully-written
    samples behind it, minus a small safety margin (READ_MARGIN_BLOCKS), so
    it never reads the producer's in-flight block. Index resets (stream
    recovery, buffer resize) happen only while the stream is stopped, so no
    producer or reader can race them.

    Also provides fault detection for silent failures (callback stalls) and
    keeps retrying while the audio device is missing.
    """

    bpm_detected = Signal(float)

    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self.config = config
        self.running = False
        self.analysis_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Audio parameters (sample rate may be adjusted to device native rate)
        self.sample_rate = SAMPLE_RATE
        self.buffer_size = BUFFER_SIZE
        self.mono_mode = MONO_MODE

        # Selected channels for beat detection
        self.selected_channels = self.config.audio_selected_channels

        # Detectable BPM range (read from config on start)
        self.min_bpm: float = 90.0
        self.max_bpm: float = 150.0

        # Calculate buffer sizes
        self.buffer_samples = int(BUFFER_DURATION * self.sample_rate)
        self.update_samples = int(UPDATE_INTERVAL * self.sample_rate)
        self.analysis_window_samples = int(ANALYSIS_WINDOW * self.sample_rate)

        # Single-producer/single-consumer ring buffer. Stores interleaved
        # stereo samples or mono. `write_index` is the only datum shared
        # between producer and consumer: it counts interleaved samples
        # written since the last reset (monotonic; it never wraps) and is
        # published by the audio callback with a single integer assignment,
        # which readers treat as atomic on CPython. No locks on the audio
        # path.
        channels = 1 if self.mono_mode else 2
        self.ring_buffer = np.zeros(self.buffer_samples * channels, dtype=np.float32)
        self.ring_size = len(self.ring_buffer)
        self.write_index = 0

        # Analysis tracking
        self.last_read_total = 0  # Published index observed at the last read

        # Current BPM value (stabilized)
        self.bpm: float = 0.0
        self.last_raw_bpm: float = 0.0  # Last pre-stabilization reading (stale detection)
        self._last_calculated_bpm: float = 0.0  # Track previous BPM for stale detection
        self.same_bpm_count: int = 0  # Count of identical BPM readings

        # Tempo change confirmation state (hysteresis)
        self._pending_bpm: float | None = None  # Candidate tempo awaiting confirmation
        self._pending_count: int = 0  # Consecutive readings agreeing with candidate

        # sounddevice setup
        self.stream: sd.InputStream | None = None
        self._stream_lock = threading.Lock()

        # Stream monitoring and recovery
        self._needs_recovery = False
        self.recovery_attempts = 0
        self.last_recovery_time: float = 0.0
        self.total_recoveries = 0

        # Identity (name, hostapi) of the last successfully opened device, used
        # to re-find it when a replug shifts its index
        self._device_identity: tuple[str, int | None] | None = None
        self._bpm_loss_cleared = False  # True once -1.0 was emitted for this loss
        self.status_text = "Stopped"

        # Buffer health tracking
        self.last_callback_time: float = 0.0
        self.callback_stall_count: int = 0

    def _audio_callback(
        self, indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags
    ) -> None:
        """
        Audio callback - called by sounddevice for each audio block.

        Producer side of the SPSC ring: writes the block without taking any
        lock, then publishes the new write index with a single integer
        assignment. The consumer only touches fully-written regions behind
        the published index, so this needs no synchronization.
        """
        # Track XRUN conditions - overflow means hardware dropped frames
        if status.input_overflow:
            logging.error(f"Audio input overflow! Total: {status.input_overflow}")
            self._needs_recovery = True
        if status.input_underflow:
            logging.warning(f"Audio input underflow! Total: {status.input_underflow}")

        # Extract samples based on mono/stereo mode
        if self.mono_mode:
            # Take first channel only
            samples = indata[:, 0].astype(np.float32)
        else:
            # Extract selected stereo channels
            samples = self._extract_stereo_channels_fast(indata)

        # Write to ring buffer, then publish. Only this thread (the audio
        # callback) writes `write_index`, so the read-modify-write cannot
        # race; the single assignment is the publish step consumers observe.
        sample_count = len(samples)
        write_pos = self.write_index % self.ring_size

        # Handle wrap-around within this callback
        if write_pos + sample_count <= self.ring_size:
            # No wrap - single copy
            self.ring_buffer[write_pos : write_pos + sample_count] = samples
        else:
            # Wrap around - two copies
            first_part = self.ring_size - write_pos
            self.ring_buffer[write_pos:] = samples[:first_part]
            self.ring_buffer[: sample_count - first_part] = samples[first_part:]

        self.write_index += sample_count  # publish (single assignment)
        self.last_callback_time = time.time()

    def _extract_stereo_channels_fast(self, indata: np.ndarray) -> np.ndarray:
        """
        Fast stereo extraction from multi-channel input.

        Args:
            indata: Array of shape [frames, channels]

        Returns:
            Interleaved stereo array [L, R, L, R, ...]
        """
        total_channels = indata.shape[1]
        num_frames = indata.shape[0]

        if total_channels == 1 or len(self.selected_channels) < 2:
            # Mono input, or the configured channel list became shorter than
            # two entries (config can change mid-stream): duplicate the first
            # channel to stereo instead of indexing and risking an
            # IndexError on the audio thread.
            return np.repeat(indata[:, 0], 2)

        # Extract selected channels
        left_idx = min(self.selected_channels[0], total_channels - 1)
        right_idx = min(self.selected_channels[1], total_channels - 1)

        left = indata[:, left_idx]
        right = indata[:, right_idx]

        # Interleave: [L0, R0, L1, R1, ...]
        stereo = np.empty(num_frames * 2, dtype=np.float32)
        stereo[0::2] = left
        stereo[1::2] = right

        return stereo

    def start(self) -> None:
        """Start the beat detection."""
        if self.running:
            return

        # Read detectable BPM range from config (validated defensively)
        self.min_bpm = float(self.config.audio_min_bpm)
        self.max_bpm = float(self.config.audio_max_bpm)
        if not 30.0 <= self.min_bpm < self.max_bpm <= 300.0:
            logging.warning(
                f"Invalid BPM range [{self.min_bpm}, {self.max_bpm}], using [90, 150]"
            )
            self.min_bpm, self.max_bpm = 90.0, 150.0

        self.running = True
        self._stop_event.clear()
        self.recovery_attempts = 0
        self.callback_stall_count = 0
        self._bpm_loss_cleared = False
        self.status_text = "Starting"

        # Open stream directly (no capture thread needed). A missing device is
        # not fatal: the analysis loop keeps retrying until one appears.
        if not self._start_stream():
            self._needs_recovery = True
            self.status_text = "Audio disconnected (retrying)"

        # Start analysis thread with lower priority
        self.analysis_thread = threading.Thread(target=self._analysis_loop, daemon=True)
        self.analysis_thread.start()

        logging.info("BeatDetector started with ring buffer.")

    def _start_stream(self, device_index: int | None = None) -> bool:
        """Open and start the audio stream."""
        input_device_index = (
            device_index
            if device_index is not None
            else self.config.audio_input_device_id
        )

        # Read selected channels from config
        self.selected_channels = self.config.audio_selected_channels
        if len(self.selected_channels) < 2 and not self.mono_mode:
            logging.warning(
                f"Invalid selected_channels {self.selected_channels}, using [0, 1]"
            )
            self.selected_channels = [0, 1]

        # We need to open stream with enough channels to cover the highest selected index
        max_selected_channel = max(self.selected_channels)
        channels = max_selected_channel + 1

        # Resolve device if needed or use default
        if input_device_index is None:
            try:
                default_info = sd.query_devices(kind="input")
                input_device_index = int(default_info["index"])
            except Exception as e:
                logging.error(f"No default input device found: {e}")
                return False

        # Get device info and adjust sample rate to native rate
        try:
            dev_info = sd.query_devices(input_device_index)
            native_rate = int(dev_info.get("default_samplerate", 44100))
            max_input_channels = int(dev_info.get("max_input_channels", 1))

            # Validate selected channels against device capabilities
            if max_selected_channel >= max_input_channels:
                logging.warning(
                    f"Selected channels {self.selected_channels} exceed device max {max_input_channels}. "
                    f"Falling back to [0, 1]."
                )
                self.selected_channels = [0, 1]
                max_selected_channel = 1
                channels = 2

            if channels > max_input_channels:
                logging.warning(
                    f"Requested {channels} channels but device only has "
                    f"{max_input_channels}. Using {max_input_channels}."
                )
                channels = max_input_channels

            # Check if SAMPLE_RATE is explicitly set (non-zero, non-empty)
            if SAMPLE_RATE and SAMPLE_RATE > 0:
                # Force configured sample rate
                logging.warning(
                    f"Forcing sample rate to {SAMPLE_RATE}Hz (device native: {native_rate}Hz)"
                )
                self.sample_rate = SAMPLE_RATE
            else:
                # Use device native rate (avoid resampling artifacts)
                if native_rate != self.sample_rate:
                    logging.debug(
                        f"Using native device rate: {native_rate}Hz (was {self.sample_rate}Hz)"
                    )
                    self.sample_rate = native_rate

            self._recalculate_buffer_sizes()

        except Exception as e:
            logging.error(
                f"Error getting device info for device {input_device_index}: {e}"
            )
            input_device_index = self._find_fallback_device(channels)
            if input_device_index is None:
                return False

        # Open stream with sounddevice
        if not self._open_stream(input_device_index, channels):
            return False

        logging.info(
            f"Listening for beats on device {input_device_index} at {self.sample_rate}Hz "
            f"(buffer: {BUFFER_DURATION}s, update: {UPDATE_INTERVAL}s, "
            f"mode: {'mono' if self.mono_mode else 'stereo'})"
        )
        self.status_text = "Listening"

        return True

    def stop(self) -> None:
        """Stop the beat detection."""
        if not self.running:
            return
        self.running = False
        self._stop_event.set()

        if self.analysis_thread and self.analysis_thread.is_alive():
            self.analysis_thread.join(timeout=2.0)
        self.analysis_thread = None

        self._cleanup_stream()
        self.status_text = "Stopped"
        logging.info("BeatDetector stopped.")

    def _recalculate_buffer_sizes(self) -> None:
        """Recalculate buffer sizes based on current sample rate."""
        self.buffer_samples = int(BUFFER_DURATION * self.sample_rate)
        self.update_samples = int(UPDATE_INTERVAL * self.sample_rate)
        self.analysis_window_samples = int(ANALYSIS_WINDOW * self.sample_rate)

        # Recreate ring buffer
        channels = 1 if self.mono_mode else 2
        self.ring_buffer = np.zeros(self.buffer_samples * channels, dtype=np.float32)
        self.ring_size = len(self.ring_buffer)

        # Reset indices
        self.write_index = 0
        self.last_read_total = 0

    def _analysis_loop(self) -> None:
        """Analysis Loop - heavy processing at lower priority."""
        # Lower thread priority to reduce callback jitter
        try:
            os.nice(10)
            logging.debug("Analysis thread priority lowered")
        except Exception:
            pass

        while self.running and not self._stop_event.is_set():
            time.sleep(UPDATE_INTERVAL)

            # Check for callback stall (>2 seconds without callback)
            time_since_callback = time.time() - self.last_callback_time
            if self.last_callback_time > 0 and time_since_callback > 2.0:
                logging.error(
                    f"Callback stall! No audio for {time_since_callback:.1f}s"
                )
                self.callback_stall_count += 1
                self._needs_recovery = True

            # Check if we need recovery. Retry forever: a missing (or
            # unplugged) device is expected to come back at some point.
            if self._needs_recovery:
                if self._attempt_recovery():
                    logging.info("Stream recovered successfully")
                else:
                    logging.error("Stream recovery failed, will retry")
                continue

            # Check for new audio data. The unlocked read of write_index
            # here is only a scheduling hint; _read_ring_buffer takes its
            # own single consistent snapshot of the published index.
            channels = 1 if self.mono_mode else 2
            samples_available = self.write_index - self.last_read_total
            samples_needed = self.update_samples * channels

            if samples_available < samples_needed:
                logging.debug(f"Not enough audio: {samples_available}/{samples_needed}")
                continue

            # Read the full analysis window (newest settled audio), capped
            # by the amount actually published so far
            window_samples = min(self.analysis_window_samples * channels, self.write_index)
            snapshot = self._read_ring_buffer(window_samples)
            self.last_read_total = self.write_index

            if snapshot is not None and len(snapshot) > 0:
                self._calculate_bpm(snapshot)

                # Check for stale raw BPM (same value repeated)
                if self.last_raw_bpm == self._last_calculated_bpm:
                    self.same_bpm_count += 1
                    if self.same_bpm_count >= 15:  # ~30 seconds
                        logging.warning(
                            f"BPM appears stuck at {self.last_raw_bpm} for {self.same_bpm_count * 2}s"
                        )
                else:
                    self.same_bpm_count = 0
                    self._last_calculated_bpm = self.last_raw_bpm

    def _read_ring_buffer(self, sample_count: int) -> np.ndarray | None:
        """
        Read the newest `sample_count` fully-written samples from the ring
        buffer, ending READ_MARGIN_BLOCKS callback blocks behind the
        producer's published write index.

        Consumer side of the SPSC ring: the producer's in-flight block starts
        at the published index, so regions behind the index minus the margin
        are never touched by the producer and need no lock. The data is
        therefore up to ~READ_MARGIN_BLOCKS * buffer_size / sample_rate
        seconds staler than the newest sample -- acceptable for beat
        detection.

        Takes exactly one snapshot of the published index; all arithmetic
        below uses that single consistent value. The read length is clamped
        to what the ring actually retains. Handles wrap-around by copying
        two segments. Returns None when there is no settled audio yet.
        """
        published = self.write_index
        channels = 1 if self.mono_mode else 2
        block = self.buffer_size * channels
        margin = READ_MARGIN_BLOCKS * block

        usable = published - margin  # newest fully-settled sample count
        if sample_count <= 0 or usable <= 0:
            return None

        # Clamp to what the ring actually retains, and keep the read arc
        # away from the producer's in-flight block even under wrap-around:
        # read region + margin + one block must fit inside the ring for the
        # two regions to never overlap modulo ring_size.
        read_len = min(sample_count, usable, self.ring_size - margin - block)
        read_pos = (usable - read_len) % self.ring_size

        # Check if we need to handle wrap-around
        if read_pos + read_len <= self.ring_size:
            # No wrap - single read
            return self.ring_buffer[read_pos : read_pos + read_len].copy()
        else:
            # Wrap around - read two segments and concatenate
            first_part = self.ring_size - read_pos
            segment1 = self.ring_buffer[read_pos:].copy()
            segment2 = self.ring_buffer[: read_len - first_part].copy()
            return np.concatenate([segment1, segment2])

    def _attempt_recovery(self) -> bool:
        """Attempt to recover the audio stream, retrying forever via backoff."""
        self.recovery_attempts += 1
        attempt_num = self.recovery_attempts

        # Calculate backoff delay (last value repeats, so retries never stop)
        delay = RECOVERY_BACKOFF_DELAYS[
            min(attempt_num - 1, len(RECOVERY_BACKOFF_DELAYS) - 1)
        ]
        logging.error(
            f"Audio stream recovery attempt {attempt_num} (waiting {delay}s)"
        )
        if self._stop_event.wait(delay):
            return False

        # Tell the worker the live BPM is gone - once per loss, not per retry.
        # The stabilizer keeps its last value so a tempo is confirmed normally
        # after recovery.
        if self.bpm > 0 and not self._bpm_loss_cleared:
            self.bpm_detected.emit(-1.0)
            self._bpm_loss_cleared = True

        # Close existing stream
        self._cleanup_stream()

        # Clear ring buffer and reset indices. Safe without a lock: the
        # stream was closed above, so the producer (audio callback) is gone,
        # and this runs on the analysis (consumer) thread itself.
        self.ring_buffer.fill(0)
        self.write_index = 0
        self.last_callback_time = 0.0
        logging.info("Cleared ring buffer for fresh start")

        # Wait for USB device to settle
        if self._stop_event.wait(0.5):
            return False

        # Reopen stream, preferring the device we last used if it moved index
        if self._start_stream(device_index=self._resolve_saved_identity()):
            logging.info(f"Recovery successful (attempt {attempt_num})")
            self.recovery_attempts = 0
            self.last_recovery_time = time.time()
            self.total_recoveries += 1
            self._needs_recovery = False
            self.callback_stall_count = 0
            self._bpm_loss_cleared = False
            self.status_text = "Listening"
            return True

        logging.error(f"Recovery failed on attempt {attempt_num}")
        self.status_text = "Audio disconnected (retrying)"
        return False

    def _resolve_saved_identity(self) -> int | None:
        """Return the index of the last-used input device, or None.

        Device indices shift when USB devices are plugged in or removed, so
        recovery re-finds the device by its (name, hostapi) identity. None
        falls back to the configured/default device path.
        """
        if self._device_identity is None:
            return None

        try:
            for index, dev in enumerate(sd.query_devices()):
                identity = (dev.get("name"), dev.get("hostapi"))
                if (
                    identity == self._device_identity
                    and int(dev.get("max_input_channels", 0)) > 0
                ):
                    return index
        except Exception as e:
            logging.debug(f"Failed to resolve saved audio device identity: {e}")
            return None

        logging.debug(f"Saved audio device identity {self._device_identity} gone")
        return None

    def _find_fallback_device(self, channels: int) -> int | None:
        """Find a fallback input device if configured one fails."""
        try:
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                max_input_channels = int(dev.get("max_input_channels", 0))
                if max_input_channels > 0:
                    logging.warning(
                        f"Falling back to device {i}: {dev.get('name', 'Unknown')}"
                    )
                    return i
        except Exception as e:
            logging.error(f"Failed to find fallback device: {e}")
        logging.error("No audio input devices available. Beat detection disabled.")
        return None

    def _open_stream(self, device_index: int, channels: int) -> bool:
        """Open the sounddevice InputStream. Returns True on success."""
        try:
            dev_info = sd.query_devices(device_index)
            max_channels = int(dev_info.get("max_input_channels", 1))
            actual_channels = min(channels, max_channels)

            logging.info(f"Opening sounddevice stream on '{dev_info['name']}'")
            logging.info(f"  Sample rate: {self.sample_rate}Hz")
            logging.info(f"  Channels: {actual_channels}")
            logging.info(f"  Blocksize: {self.buffer_size}")
            logging.info(f"  Latency: 'high' (for stability)")

            with self._stream_lock:
                self.stream = sd.InputStream(
                    device=device_index,
                    channels=actual_channels,
                    samplerate=self.sample_rate,
                    blocksize=self.buffer_size,
                    dtype="float32",
                    latency="high",
                    callback=self._audio_callback,
                )
                self.stream.start()

            logging.info("Stream opened and started successfully")
            # Remember which device this is so a replug that shifts indices
            # can still be recovered onto the same hardware
            self._device_identity = (dev_info.get("name"), dev_info.get("hostapi"))
            return True
        except Exception as e:
            logging.error(f"Failed to open audio stream on device {device_index}: {e}")
            return False

    def _cleanup_stream(self) -> None:
        """Clean up audio resources."""
        with self._stream_lock:
            if self.stream:
                try:
                    if self.stream.active:
                        self.stream.stop()
                    self.stream.close()
                except Exception as e:
                    logging.debug(f"Error closing stream: {e}")
                finally:
                    self.stream = None
                    logging.info("Stream closed")

    def _calculate_bpm(self, audio_data: np.ndarray) -> None:
        """Calculate BPM from the provided audio buffer using Inter-Beat Intervals (IBI)."""
        try:
            # Convert stereo to mono by averaging channels
            if self.mono_mode:
                mono_audio = audio_data
            else:
                # audio_data is interleaved stereo: [L, R, L, R, ...]
                mono_audio = (audio_data[0::2] + audio_data[1::2]) / 2.0

            # Skip or hold BPM during silence/breakdowns
            if np.max(np.abs(mono_audio)) < SILENCE_THRESHOLD:
                if HOLD_BPM_ON_SILENCE and self.bpm > 0:
                    logging.debug("Low audio level, holding previous BPM")
                    return
                else:
                    logging.debug("Buffer too quiet, skipping BPM calc")
                    return

            # Resample to the analysis rate. Tempo is a ~2-3 Hz amplitude
            # modulation and the onset analysis discards everything above
            # FMAX anyway, so downsampling loses no tempo information.
            if self.sample_rate != ANALYSIS_SAMPLE_RATE:
                mono_audio = librosa.resample(
                    mono_audio,
                    orig_sr=self.sample_rate,
                    target_sr=ANALYSIS_SAMPLE_RATE,
                )
            sr = ANALYSIS_SAMPLE_RATE

            # Calculate onset strength envelope
            onset_env = librosa.onset.onset_strength(
                y=mono_audio,
                sr=sr,
                hop_length=HOP_LENGTH,
                fmax=FMAX,
                center=CENTER,
                detrend=DETREND,
            )

            # Anchor the tracker to the stabilized tempo, or to the pending
            # candidate while a tempo change is being confirmed
            if self._pending_bpm is not None:
                current_start_bpm = self._pending_bpm
            elif self.bpm > 0:
                current_start_bpm = self.bpm
            else:
                current_start_bpm = START_BPM

            # Use beat_track to find beat locations
            tempo, beats = librosa.beat.beat_track(
                onset_envelope=onset_env,
                sr=sr,
                hop_length=HOP_LENGTH,
                start_bpm=current_start_bpm,
                tightness=TIGHTNESS,
            )

            if len(beats) < 2:
                logging.debug("Not enough beats detected")
                return

            # Refine beat locations using parabolic interpolation for sub-frame accuracy
            refined_beats = self._refine_beats(beats, onset_env)

            # Analyze beat timestamps for higher precision
            beat_times = refined_beats * HOP_LENGTH / sr
            ibis = np.diff(beat_times)

            # Wide interval gate (half/double the configured range): harmonic
            # errors are corrected below instead of being discarded here
            max_ibi = 60.0 / (self.min_bpm / 2)
            min_ibi = 60.0 / (self.max_bpm * 2)
            valid_ibis = ibis[(ibis > min_ibi) & (ibis < max_ibi)]

            if len(valid_ibis) == 0:
                logging.debug("No usable beat intervals, holding previous BPM")
                return

            # Cluster Averaging for precision
            raw_bpm = self._calculate_bpm_from_ibis(valid_ibis)

            # Correct octave/harmonic errors into the configured BPM range
            corrected_bpm = self._correct_harmonics(raw_bpm, onset_env, sr)
            self.last_raw_bpm = corrected_bpm

            # Stabilize (EMA fine tracking + confirmed switching)
            self._update_stabilized_bpm(corrected_bpm)

        except Exception as e:
            logging.error(f"Error calculating BPM: {e}")

    def _correct_harmonics(
        self, raw_bpm: float, onset_env: np.ndarray, sr: int
    ) -> float:
        """
        Map a raw tempo reading into the configured BPM range by scoring
        harmonic candidates (half, 2/3, 3/4, same, 4/3, 3/2, double) against
        the onset envelope autocorrelation.

        Each candidate is scored with a comb sum (autocorrelation at 1x-4x its
        beat period, so a true 4/4 tempo also collects energy at the 2-beat and
        bar level), a log-normal perceptual prior, and a penalty for
        triplet-based factors.

        Fixes classic octave errors (e.g. 163 BPM detected for a 122 BPM song,
        or 92 BPM for a 138 BPM song with a 3-eighth-note accent pattern).
        """
        candidates: list[tuple[float, float]] = []  # (bpm, factor)
        for factor in HARMONIC_FACTORS:
            cand = raw_bpm * factor
            if self.min_bpm <= cand <= self.max_bpm and all(
                abs(cand - c) > 0.01 for c, _ in candidates
            ):
                candidates.append((cand, factor))

        if not candidates:
            return float(np.clip(raw_bpm, self.min_bpm, self.max_bpm))
        if len(candidates) == 1:
            return candidates[0][0]

        # Normalized autocorrelation of the onset envelope
        env = onset_env - np.mean(onset_env)
        ac = np.correlate(env, env, mode="full")[len(env) - 1 :]
        if len(ac) == 0 or ac[0] <= 0:
            return float(np.clip(raw_bpm, self.min_bpm, self.max_bpm))
        ac = ac / ac[0]

        fps = sr / HOP_LENGTH

        def ac_at_lag(lag: float) -> float:
            """Autocorrelation at a fractional lag (linear interpolation)."""
            lo = int(np.floor(lag))
            if lo >= len(ac) - 1:
                return 0.0
            frac = lag - lo
            return float(ac[lo] * (1.0 - frac) + ac[lo + 1] * frac)

        def comb_pulse(bpm: float) -> float:
            """Weighted autocorrelation sum at 1x-4x the beat period."""
            lag = fps * 60.0 / bpm
            return float(
                sum(w * ac_at_lag(k * lag) for k, w in enumerate(COMB_WEIGHTS, 1))
            )

        best_bpm = candidates[0][0]
        best_score = -1.0
        for cand, factor in candidates:
            pulse = comb_pulse(cand)
            prior = float(
                np.exp(
                    -0.5
                    * (np.log2(cand / PRIOR_CENTER_BPM) / PRIOR_STD_OCTAVES) ** 2
                )
            )
            factor_weight = 1.0 if factor in OCTAVE_FACTORS else TRIPLET_FACTOR_PENALTY
            score = pulse * prior * factor_weight
            logging.debug(
                f"  harmonic candidate {cand:6.1f} BPM (x{factor:.2f}): "
                f"pulse={pulse:.3f} prior={prior:.3f} fw={factor_weight:.2f} "
                f"score={score:.3f}"
            )
            if score > best_score:
                best_score = score
                best_bpm = cand

        if abs(best_bpm - raw_bpm) > 0.5:
            logging.debug(f"Harmonic correction: {raw_bpm:.1f} -> {best_bpm:.1f} BPM")

        return best_bpm

    def _update_stabilized_bpm(self, new_bpm: float) -> None:
        """
        Update the reported BPM with hysteresis.

        Small deviations (<=3%) are fine-tracked with an EMA. Larger deviations
        must repeat for SWITCH_CONFIRM_READINGS consecutive passes (~4-6s)
        before the tempo switches, preventing jumps from single bad readings.
        """
        # First lock
        if self.bpm <= 0:
            self.bpm = round(new_bpm, 1)
            self._pending_bpm = None
            self._pending_count = 0
            logging.debug(f"BPM locked: {self.bpm}")
            self.bpm_detected.emit(self.bpm)
            return

        deviation = abs(new_bpm - self.bpm) / self.bpm

        if deviation <= FINE_TRACK_TOLERANCE:
            # Fine tracking
            self.bpm = round(
                self.bpm * (1 - SMOOTHING_ALPHA) + new_bpm * SMOOTHING_ALPHA, 1
            )
            self._pending_bpm = None
            self._pending_count = 0
            logging.debug(f"BPM fine-tracked: {self.bpm}")
            self.bpm_detected.emit(self.bpm)
            return

        # Large deviation: count consecutive agreeing readings before switching
        if self._pending_bpm is not None and (
            abs(new_bpm - self._pending_bpm) / self._pending_bpm
            <= FINE_TRACK_TOLERANCE
        ):
            self._pending_count += 1
        else:
            self._pending_bpm = new_bpm
            self._pending_count = 1

        if self._pending_count >= SWITCH_CONFIRM_READINGS:
            logging.info(f"BPM change confirmed: {self.bpm} -> {new_bpm:.1f}")
            self.bpm = round(new_bpm, 1)
            self._pending_bpm = None
            self._pending_count = 0
            self.bpm_detected.emit(self.bpm)
        else:
            logging.debug(
                f"Holding {self.bpm} BPM (candidate {new_bpm:.1f}, "
                f"{self._pending_count}/{SWITCH_CONFIRM_READINGS})"
            )

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
        cluster_ibis = valid_ibis[
            np.abs(valid_ibis - median_ibi) <= (tolerance * median_ibi)
        ]

        # Take the MEAN of this cluster for sub-sample precision
        if len(cluster_ibis) > 0:
            mean_ibi = float(np.mean(cluster_ibis))
            return 60.0 / mean_ibi
        return 60.0 / float(median_ibi)

    def __del__(self) -> None:
        """Cleanup on deletion."""
        pass
