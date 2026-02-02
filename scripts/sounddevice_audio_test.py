#!/usr/bin/env python3
"""USB Audio Capture using sounddevice library with XRUN detection."""

import argparse
import logging
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# Suppress numba debug logging
logging.getLogger("numba").setLevel(logging.WARNING)

# =============================================================================
# CONFIGURATION - Match your app settings
# =============================================================================

SAMPLE_RATE = 48000
BUFFER_DURATION = 8.0
UPDATE_INTERVAL = 2.0
BLOCKSIZE = 1024  # Frames per callback

# Stale detection thresholds
MAX_CONSECUTIVE_IDENTICAL_CALLBACKS = 10
SILENCE_THRESHOLD = 0.001

# Recovery settings
ENABLE_AUTO_RECOVERY = True
MAX_RECOVERY_ATTEMPTS = 3
RECOVERY_BACKOFF_DELAYS = [1.0, 2.0, 4.0]
RECOVERY_COOLDOWN_SECONDS = 30


class SounddeviceAudioCapture:
    """Audio capture using sounddevice with XRUN detection and auto-recovery."""

    def __init__(
        self,
        device_index: int | None = None,
        channels: int = 2,
        sample_rate: int = SAMPLE_RATE,
        blocksize: int = BLOCKSIZE,
        enable_recovery: bool = ENABLE_AUTO_RECOVERY,
    ) -> None:
        self.device_index = device_index
        self.channels = channels
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self.enable_recovery = enable_recovery

        self.running = False
        self._stop_event = threading.Event()

        # Rolling buffer
        self.buffer_lock = threading.Lock()
        self.audio_buffer: deque = deque(
            maxlen=int(BUFFER_DURATION * sample_rate * channels)
        )

        # sounddevice stream
        self.stream: sd.InputStream | None = None
        self._stream_lock = threading.Lock()

        # Stale detection state
        self.consecutive_silence_count = 0
        self.consecutive_identical_count = 0
        self.last_callback_hash: int | None = None
        self.last_rms = 0.0

        # XRUN tracking
        self.xrun_count = 0
        self.input_overflow_count = 0
        self.input_underflow_count = 0

        # Recovery state
        self.recovery_attempts = 0
        self.last_recovery_time: float = 0.0
        self.total_recoveries = 0

        # Statistics
        self.callbacks_received = 0
        self.start_time: float = 0.0
        self.silent_callbacks = 0
        self.identical_callbacks = 0

    def _audio_callback(
        self, indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags
    ) -> None:
        """Audio callback - called by sounddevice for each audio block."""
        # Track XRUN conditions
        if status.input_overflow:
            self.input_overflow_count += 1
            logger.warning(
                f"INPUT OVERFLOW detected! Total: {self.input_overflow_count}"
            )

        if status.input_underflow:
            self.input_underflow_count += 1
            logger.warning(
                f"INPUT UNDERFLOW detected! Total: {self.input_underflow_count}"
            )

        # Flatten and store audio
        samples = indata.flatten()

        # Analyze for stale data
        is_stale = self._analyze_callback(samples)

        # Store in buffer
        with self.buffer_lock:
            self.audio_buffer.extend(samples)

        self.callbacks_received += 1

        # Check if we need recovery
        if is_stale and self._should_attempt_recovery():
            logger.warning(
                f"Stale data detected ({self.consecutive_identical_count} callbacks), "
                f"triggering recovery..."
            )
            # We can't recover from within the callback, so set a flag
            self._needs_recovery = True

    def _should_attempt_recovery(self) -> bool:
        """Check if we should attempt stream recovery."""
        if not self.enable_recovery:
            return False

        if self.recovery_attempts >= MAX_RECOVERY_ATTEMPTS:
            logger.error(f"Max recovery attempts ({MAX_RECOVERY_ATTEMPTS}) reached")
            return False

        # Cooldown period
        time_since_last = time.time() - self.last_recovery_time
        if time_since_last < RECOVERY_COOLDOWN_SECONDS:
            return False

        return True

    def _attempt_recovery(self) -> bool:
        """Attempt to recover the audio stream."""
        self.recovery_attempts += 1
        attempt_num = self.recovery_attempts

        logger.warning(f"RECOVERY ATTEMPT {attempt_num}/{MAX_RECOVERY_ATTEMPTS}")

        # Calculate backoff delay
        delay = RECOVERY_BACKOFF_DELAYS[
            min(attempt_num - 1, len(RECOVERY_BACKOFF_DELAYS) - 1)
        ]
        logger.info(f"Waiting {delay}s before recovery attempt...")
        time.sleep(delay)

        # Close existing stream
        self._cleanup_stream()

        # Wait for USB device to settle
        time.sleep(0.5)

        # Try to reopen stream
        if self._open_stream():
            logger.info(
                f"RECOVERY SUCCESSFUL - Stream reopened on attempt {attempt_num}"
            )
            self.recovery_attempts = 0
            self.last_recovery_time = time.time()
            self.total_recoveries += 1

            # Reset stale detection counters
            self.consecutive_identical_count = 0
            self.consecutive_silence_count = 0
            self.last_callback_hash = None
            self._needs_recovery = False

            return True
        else:
            logger.error(f"RECOVERY FAILED on attempt {attempt_num}")
            return False

    def _analyze_callback(self, samples: np.ndarray) -> bool:
        """Analyze callback data and return True if stale."""
        # Calculate callback hash (first 100 samples)
        callback_hash = hash(samples[:100].tobytes())

        # Calculate RMS
        rms = np.sqrt(np.mean(samples**2))
        self.last_rms = rms

        # Check for silence
        if rms < SILENCE_THRESHOLD:
            self.consecutive_silence_count += 1
            self.silent_callbacks += 1
        else:
            if self.consecutive_silence_count > 10:
                logger.info(
                    f"Audio resumed after {self.consecutive_silence_count} silent callbacks"
                )
            self.consecutive_silence_count = 0

        # Check for identical callbacks
        is_stale = False
        if callback_hash == self.last_callback_hash:
            self.consecutive_identical_count += 1
            self.identical_callbacks += 1

            if self.consecutive_identical_count >= MAX_CONSECUTIVE_IDENTICAL_CALLBACKS:
                is_stale = True

                if (
                    self.consecutive_identical_count
                    == MAX_CONSECUTIVE_IDENTICAL_CALLBACKS
                ):
                    logger.warning(
                        f"STALE DATA: {self.consecutive_identical_count} identical callbacks"
                    )
        else:
            if self.consecutive_identical_count >= MAX_CONSECUTIVE_IDENTICAL_CALLBACKS:
                logger.info(
                    f"Stream recovered after {self.consecutive_identical_count} identical callbacks"
                )
            self.consecutive_identical_count = 0

        self.last_callback_hash = callback_hash

        return is_stale

    def start(self) -> None:
        """Start the sounddevice audio capture."""
        if self.running:
            return

        self.running = True
        self._stop_event.clear()
        self.start_time = time.time()
        self._needs_recovery = False

        logger.info("=" * 60)
        logger.info("SOUNDDEVICE AUDIO CAPTURE STARTED")
        logger.info(f"Device: {self.device_index}")
        logger.info(f"Sample Rate: {self.sample_rate}Hz")
        logger.info(f"Blocksize: {self.blocksize} frames")
        logger.info(f"Channels: {self.channels}")
        logger.info(
            f"Auto-recovery: {'ENABLED' if self.enable_recovery else 'DISABLED'}"
        )
        logger.info("=" * 60)
        logger.info("Advantages over PyAudio:")
        logger.info("  - Explicit XRUN detection via CallbackFlags")
        logger.info("  - Better error handling and recovery")
        logger.info("  - Direct NumPy array support")
        logger.info("=" * 60)

        # Open initial stream
        if not self._open_stream():
            self.running = False
            return

        # Start recovery monitoring thread
        self._recovery_thread = threading.Thread(
            target=self._recovery_monitor, daemon=True
        )
        self._recovery_thread.start()

    def _recovery_monitor(self) -> None:
        """Monitor for recovery requests and handle them."""
        while self.running and not self._stop_event.is_set():
            if getattr(self, "_needs_recovery", False):
                if not self._attempt_recovery():
                    if self.recovery_attempts >= MAX_RECOVERY_ATTEMPTS:
                        logger.error("Max recovery attempts reached, stopping")
                        self.running = False
                        break
            time.sleep(0.1)

    def stop(self) -> None:
        """Stop the capture."""
        if not self.running:
            return

        self.running = False
        self._stop_event.set()

        if hasattr(self, "_recovery_thread") and self._recovery_thread.is_alive():
            self._recovery_thread.join(timeout=2.0)

        self._cleanup_stream()

        # Print summary
        self._print_summary()

    def _print_summary(self) -> None:
        """Print capture summary."""
        duration = time.time() - self.start_time

        logger.info("=" * 60)
        logger.info("CAPTURE SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Duration: {duration:.1f} seconds")
        logger.info(f"Total callbacks received: {self.callbacks_received}")
        logger.info(f"Silent callbacks: {self.silent_callbacks}")
        logger.info(f"Identical/stale callbacks: {self.identical_callbacks}")
        logger.info(f"Input overflows: {self.input_overflow_count}")
        logger.info(f"Input underflows: {self.input_underflow_count}")
        logger.info(f"Successful recoveries: {self.total_recoveries}")

        if self.consecutive_identical_count >= MAX_CONSECUTIVE_IDENTICAL_CALLBACKS:
            logger.warning(
                "STALE AUDIO DETECTED - Stream was returning identical callbacks"
            )

        logger.info("=" * 60)

    def _open_stream(self) -> bool:
        """Open the sounddevice InputStream."""
        try:
            # Query device info
            devices = sd.query_devices()

            if self.device_index is not None:
                if self.device_index >= len(devices):
                    logger.error(f"Invalid device index {self.device_index}")
                    return False
                device_info = devices[self.device_index]
            else:
                # Use default input device
                device_info = sd.query_devices(kind="input")
                self.device_index = device_info["index"]

            logger.info(
                f"Opening stream on '{device_info['name']}' (device {self.device_index})"
            )
            logger.info(f"  Sample rate: {self.sample_rate}Hz")
            logger.info(f"  Channels: {self.channels}")
            logger.info(f"  Blocksize: {self.blocksize}")
            logger.info(f"  Latency: 'high' (for stability)")

            with self._stream_lock:
                self.stream = sd.InputStream(
                    device=self.device_index,
                    channels=self.channels,
                    samplerate=self.sample_rate,
                    blocksize=self.blocksize,
                    dtype="float32",
                    latency="high",  # More robust against overruns
                    callback=self._audio_callback,
                )
                self.stream.start()

            logger.info("Stream opened and started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to open stream: {e}")
            return False

    def _cleanup_stream(self) -> None:
        """Clean up audio stream."""
        with self._stream_lock:
            if self.stream:
                try:
                    if self.stream.active:
                        self.stream.stop()
                    self.stream.close()
                except Exception as e:
                    logger.debug(f"Error closing stream: {e}")
                finally:
                    self.stream = None
                    logger.info("Stream closed")


def list_devices() -> None:
    """List audio devices."""
    print("\n=== Available Audio Input Devices (sounddevice) ===")

    devices = sd.query_devices()
    try:
        default_input = sd.query_devices(kind="input")
        default_index = default_input["index"]
    except Exception:
        default_index = -1

    for i, dev in enumerate(devices):
        max_input_channels = dev.get("max_input_channels", 0)
        if max_input_channels > 0:
            default_marker = " (DEFAULT)" if i == default_index else ""
            hostapi = sd.query_hostapis(dev["hostapi"])["name"]
            print(
                f"  [{i}] {dev['name']} "
                f"({max_input_channels} ch, {int(dev['default_samplerate'])}Hz) "
                f"[{hostapi}]{default_marker}"
            )
    print()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="USB Audio Capture using sounddevice with XRUN detection"
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="Audio device index to use.",
    )
    parser.add_argument(
        "--channels",
        type=int,
        default=2,
        help="Number of input channels (default: 2).",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=600,
        help="Test duration in seconds (default: 600).",
    )
    parser.add_argument(
        "--no-recovery",
        action="store_true",
        help="Disable auto-recovery (for testing).",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List devices and exit.",
    )

    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        sys.exit(0)

    logger.info("Starting sounddevice audio capture...")
    logger.info(f"Test duration: {args.duration} seconds")

    capture = SounddeviceAudioCapture(
        device_index=args.device,
        channels=args.channels,
        sample_rate=SAMPLE_RATE,
        blocksize=BLOCKSIZE,
        enable_recovery=not args.no_recovery,
    )

    try:
        capture.start()
        logger.info(f"Running for {args.duration} seconds (Ctrl+C to stop)...")
        time.sleep(args.duration)
        logger.info("Test duration completed")
        capture.stop()

    except KeyboardInterrupt:
        logger.info("\nShutdown requested...")
        capture.stop()
        logger.info("Done.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error: {e}")
        capture.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
