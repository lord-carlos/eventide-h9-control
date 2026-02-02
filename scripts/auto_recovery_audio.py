#!/usr/bin/env python3
"""USB Audio Capture with Auto-Recovery - Detects and recovers from USB buffer overruns."""

import argparse
import logging
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import pyaudio

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
BUFFER_SIZE = 1024
BUFFER_DURATION = 8.0
UPDATE_INTERVAL = 2.0

# Stale detection thresholds
MAX_CONSECUTIVE_IDENTICAL_FRAMES = 10
SILENCE_THRESHOLD = 0.001

# Recovery settings
ENABLE_AUTO_RECOVERY = True
MAX_RECOVERY_ATTEMPTS = 3
RECOVERY_BACKOFF_DELAYS = [1.0, 2.0, 4.0]  # Exponential backoff
RECOVERY_COOLDOWN_SECONDS = 30  # Min time between recovery attempts


class AutoRecoveringAudioCapture:
    """Audio capture with automatic recovery from USB buffer overruns."""

    def __init__(
        self,
        device_index: int | None = None,
        channels: int = 2,
        sample_rate: int = SAMPLE_RATE,
        buffer_size: int = BUFFER_SIZE,
        enable_recovery: bool = ENABLE_AUTO_RECOVERY,
    ) -> None:
        self.device_index = device_index
        self.channels = channels
        self.sample_rate = sample_rate
        self.buffer_size = buffer_size
        self.enable_recovery = enable_recovery

        self.running = False
        self.capture_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Rolling buffer
        self.buffer_lock = threading.Lock()
        self.audio_buffer: deque = deque(
            maxlen=int(BUFFER_DURATION * sample_rate * channels)
        )

        # PyAudio
        self.p = pyaudio.PyAudio()
        self.stream: pyaudio.Stream | None = None
        self._stream_lock = threading.Lock()

        # Stale detection state
        self.consecutive_silence_count = 0
        self.consecutive_identical_count = 0
        self.last_frame_hash: int | None = None
        self.last_rms = 0.0

        # Recovery state
        self.recovery_attempts = 0
        self.last_recovery_time: float = 0.0
        self.total_recoveries = 0

        # Statistics
        self.frames_captured = 0
        self.start_time: float = 0.0
        self.silent_frames = 0
        self.identified_frames = 0

        # USB monitoring
        self.usb_errors_before = 0

    def _check_usb_errors(self) -> int:
        """Check for USB buffer overrun errors in dmesg."""
        try:
            result = subprocess.run(
                ["dmesg"], capture_output=True, text=True, timeout=2
            )
            return result.stdout.count("buffer overrun")
        except Exception:
            return 0

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
            logger.debug(
                f"Recovery cooldown: {time_since_last:.1f}s / {RECOVERY_COOLDOWN_SECONDS}s"
            )
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

        # Wait a bit more for USB device to settle
        time.sleep(0.5)

        # Try to reopen stream
        device_index = self.device_index
        if device_index is None:
            try:
                default_info = self.p.get_default_input_device_info()
                device_index = int(default_info["index"])
            except OSError:
                logger.error("No default input device for recovery")
                return False

        if self._open_stream(device_index):
            logger.info(
                f"RECOVERY SUCCESSFUL - Stream reopened on attempt {attempt_num}"
            )
            self.recovery_attempts = 0  # Reset counter on success
            self.last_recovery_time = time.time()
            self.total_recoveries += 1

            # Reset stale detection counters
            self.consecutive_identical_count = 0
            self.consecutive_silence_count = 0
            self.last_frame_hash = None

            return True
        else:
            logger.error(f"RECOVERY FAILED on attempt {attempt_num}")
            return False

    def start(self) -> None:
        """Start the auto-recovering audio capture."""
        if self.running:
            return

        self.running = True
        self._stop_event.clear()
        self.start_time = time.time()
        self.usb_errors_before = self._check_usb_errors()

        # Start capture thread
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

        logger.info("=" * 60)
        logger.info("AUTO-RECOVERING AUDIO CAPTURE STARTED")
        logger.info(f"Device: {self.device_index}")
        logger.info(f"Sample Rate: {self.sample_rate}Hz")
        logger.info(f"Buffer Size: {self.buffer_size} samples")
        logger.info(
            f"Auto-recovery: {'ENABLED' if self.enable_recovery else 'DISABLED'}"
        )
        logger.info(f"Max recovery attempts: {MAX_RECOVERY_ATTEMPTS}")
        logger.info("=" * 60)

    def stop(self) -> None:
        """Stop the capture."""
        if not self.running:
            return

        self.running = False
        self._stop_event.set()

        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=3.0)

        self._cleanup_stream()

        # Print summary
        self._print_summary()

    def _print_summary(self) -> None:
        """Print capture summary."""
        duration = time.time() - self.start_time
        new_usb_errors = self._check_usb_errors() - self.usb_errors_before

        logger.info("=" * 60)
        logger.info("CAPTURE SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Duration: {duration:.1f} seconds")
        logger.info(f"Total frames captured: {self.frames_captured}")
        logger.info(f"Silent frames: {self.silent_frames}")
        logger.info(f"Identical/stale frames: {self.identified_frames}")
        logger.info(f"Successful recoveries: {self.total_recoveries}")
        logger.info(f"USB buffer overruns: {new_usb_errors}")
        logger.info("=" * 60)

    def _capture_loop(self) -> None:
        """Main capture loop with auto-recovery."""
        device_index = self.device_index

        # Resolve device
        if device_index is None:
            try:
                default_info = self.p.get_default_input_device_info()
                device_index = int(default_info["index"])
            except OSError:
                logger.error("No default input device found.")
                self.running = False
                return

        # Open initial stream
        if not self._open_stream(device_index):
            self.running = False
            return

        logger.info("Capture loop started")

        # Main capture loop
        while self.running and not self._stop_event.is_set():
            with self._stream_lock:
                if self.stream is None or not self.stream.is_active():
                    logger.error("Stream is not active!")
                    if self._should_attempt_recovery():
                        if not self._attempt_recovery():
                            if self.recovery_attempts >= MAX_RECOVERY_ATTEMPTS:
                                logger.error("Max recovery attempts reached, stopping")
                                self.running = False
                                break
                    else:
                        time.sleep(0.1)
                    continue

                try:
                    # Read audio with overflow detection
                    audio_data = self.stream.read(
                        self.buffer_size, exception_on_overflow=True
                    )

                    samples = np.frombuffer(audio_data, dtype=np.float32)

                    # Analyze frame
                    is_stale = self._analyze_frame(samples)

                    # Store in buffer
                    with self.buffer_lock:
                        self.audio_buffer.extend(samples)

                    self.frames_captured += 1

                    # Check if we need recovery
                    if is_stale and self._should_attempt_recovery():
                        logger.warning(
                            f"Stale data detected ({self.consecutive_identical_count} frames), "
                            f"attempting recovery..."
                        )
                        if not self._attempt_recovery():
                            logger.error(
                                "Recovery failed, will retry on next detection"
                            )

                except OSError as e:
                    if "Input overflowed" in str(e):
                        logger.error(f"PORTAUDIO OVERFLOW: {e}")
                        if self._should_attempt_recovery():
                            self._attempt_recovery()
                    else:
                        logger.error(f"OSError: {e}")
                        time.sleep(0.1)

                except Exception as e:
                    if self.running:
                        logger.error(f"Error: {e}")
                        time.sleep(0.1)

    def _analyze_frame(self, samples: np.ndarray) -> bool:
        """Analyze frame and return True if stale data detected."""
        # Calculate frame hash
        frame_hash = hash(samples[:100].tobytes())

        # Calculate RMS
        rms = np.sqrt(np.mean(samples**2))
        self.last_rms = rms

        # Check for silence
        if rms < SILENCE_THRESHOLD:
            self.consecutive_silence_count += 1
            self.silent_frames += 1
        else:
            if self.consecutive_silence_count > 10:
                logger.info(
                    f"Audio resumed after {self.consecutive_silence_count} silent frames"
                )
            self.consecutive_silence_count = 0

        # Check for identical frames
        is_stale = False
        if frame_hash == self.last_frame_hash:
            self.consecutive_identical_count += 1
            self.identified_frames += 1

            if self.consecutive_identical_count >= MAX_CONSECUTIVE_IDENTICAL_FRAMES:
                is_stale = True

                if self.consecutive_identical_count == MAX_CONSECUTIVE_IDENTICAL_FRAMES:
                    logger.warning(
                        f"STALE DATA: {self.consecutive_identical_count} identical frames"
                    )
        else:
            if self.consecutive_identical_count >= MAX_CONSECUTIVE_IDENTICAL_FRAMES:
                logger.info(
                    f"Stream recovered after {self.consecutive_identical_count} identical frames"
                )
            self.consecutive_identical_count = 0

        self.last_frame_hash = frame_hash

        return is_stale

    def _open_stream(self, device_index: int) -> bool:
        """Open the PyAudio stream."""
        try:
            dev_info = self.p.get_device_info_by_index(device_index)
            max_channels = int(dev_info.get("maxInputChannels", 1))
            actual_channels = min(self.channels, max_channels)

            logger.info(
                f"Opening stream on '{dev_info['name']}' (device {device_index})"
            )

            with self._stream_lock:
                self.stream = self.p.open(
                    format=pyaudio.paFloat32,
                    channels=actual_channels,
                    rate=self.sample_rate,
                    input=True,
                    frames_per_buffer=self.buffer_size,
                    input_device_index=device_index,
                )

            logger.info(f"Stream opened: {actual_channels}ch @ {self.sample_rate}Hz")
            return True

        except Exception as e:
            logger.error(f"Failed to open stream: {e}")
            return False

    def _cleanup_stream(self) -> None:
        """Clean up audio stream."""
        with self._stream_lock:
            if self.stream:
                try:
                    if self.stream.is_active():
                        self.stream.stop_stream()
                    self.stream.close()
                except Exception as e:
                    logger.debug(f"Error closing stream: {e}")
                finally:
                    self.stream = None
                    logger.info("Stream closed")

    def __del__(self) -> None:
        """Cleanup."""
        try:
            self.p.terminate()
        except Exception:
            pass


def list_devices() -> None:
    """List audio devices."""
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
    parser = argparse.ArgumentParser(description="USB Audio Capture with Auto-Recovery")
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

    logger.info("Starting auto-recovering audio capture...")
    logger.info(f"Test duration: {args.duration} seconds")

    capture = AutoRecoveringAudioCapture(
        device_index=args.device,
        channels=args.channels,
        sample_rate=SAMPLE_RATE,
        buffer_size=BUFFER_SIZE,
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
