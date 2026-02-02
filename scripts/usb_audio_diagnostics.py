#!/usr/bin/env python3
"""Diagnostic script for USB audio buffer overrun detection on Raspberry Pi."""

import argparse
import logging
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
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
# CONFIGURATION
# =============================================================================

SAMPLE_RATE = 48000
BUFFER_SIZE = 1024  # Match your app
BUFFER_DURATION = 8.0
UPDATE_INTERVAL = 2.0

# Stale audio detection thresholds
MAX_CONSECUTIVE_IDENTICAL_FRAMES = 10  # Consecutive identical frame hashes
SILENCE_THRESHOLD = 0.001  # RMS below this is considered silence


class AudioDiagnostics:
    """Audio capture diagnostics with stale data detection."""

    def __init__(
        self,
        device_index: int | None = None,
        channels: int = 2,
        sample_rate: int = SAMPLE_RATE,
        buffer_size: int = BUFFER_SIZE,
    ) -> None:
        self.device_index = device_index
        self.channels = channels
        self.sample_rate = sample_rate
        self.buffer_size = buffer_size

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

        # Stale detection state
        self.consecutive_silence_count = 0
        self.consecutive_identical_count = 0
        self.last_frame_hash: int | None = None
        self.last_rms = 0.0

        # Statistics
        self.frames_captured = 0
        self.start_time: float = 0.0
        self.silent_frames = 0
        self.identified_frames = 0

        # USB monitoring
        self.usb_errors_before = 0
        self._monitor_usb_errors()

    def _monitor_usb_errors(self) -> None:
        """Count USB buffer overrun errors in dmesg."""
        try:
            result = subprocess.run(
                ["dmesg"], capture_output=True, text=True, timeout=5
            )
            self.usb_errors_before = result.stdout.count("buffer overrun")
            logger.info(f"USB buffer overruns before start: {self.usb_errors_before}")
        except Exception as e:
            logger.warning(f"Could not check dmesg: {e}")

    def _check_usb_errors(self) -> int:
        """Check for new USB buffer overrun errors."""
        try:
            result = subprocess.run(
                ["dmesg"], capture_output=True, text=True, timeout=5
            )
            current_errors = result.stdout.count("buffer overrun")
            new_errors = current_errors - self.usb_errors_before
            return new_errors
        except Exception:
            return 0

    def start(self) -> None:
        """Start the diagnostic capture."""
        if self.running:
            return

        self.running = True
        self._stop_event.clear()
        self.start_time = time.time()

        # Start capture thread
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

        logger.info("=" * 60)
        logger.info("AUDIO DIAGNOSTICS STARTED")
        logger.info(f"Device: {self.device_index}")
        logger.info(f"Sample Rate: {self.sample_rate}Hz")
        logger.info(f"Buffer Size: {self.buffer_size} samples")
        logger.info(f"Channels: {self.channels}")
        logger.info("=" * 60)
        logger.info("Monitoring for:")
        logger.info("  - Consecutive identical audio frames (stale data)")
        logger.info("  - Consecutive silent frames")
        logger.info("  - USB buffer overrun errors")
        logger.info("  - Stream health issues")
        logger.info("=" * 60)

    def stop(self) -> None:
        """Stop the diagnostic capture."""
        if not self.running:
            return

        self.running = False
        self._stop_event.set()

        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)

        self._cleanup_stream()

        # Print summary
        self._print_summary()

    def _print_summary(self) -> None:
        """Print diagnostic summary."""
        duration = time.time() - self.start_time
        new_usb_errors = self._check_usb_errors()

        logger.info("=" * 60)
        logger.info("DIAGNOSTIC SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Duration: {duration:.1f} seconds")
        logger.info(f"Total frames captured: {self.frames_captured}")
        logger.info(f"Silent frames: {self.silent_frames}")
        logger.info(f"Identical/stale frames: {self.identified_frames}")
        logger.info(f"New USB buffer overruns: {new_usb_errors}")

        if self.consecutive_identical_count >= MAX_CONSECUTIVE_IDENTICAL_FRAMES:
            logger.warning(
                "STALE AUDIO DETECTED - Stream was returning identical frames"
            )

        if new_usb_errors > 0:
            logger.error(f"USB BUFFER OVERRUNS OCCURRED: {new_usb_errors}")
            logger.error("This is likely the cause of the stale audio issue!")

        logger.info("=" * 60)

    def _capture_loop(self) -> None:
        """Main capture loop with stale data detection."""
        input_device_index = self.device_index

        # Resolve device
        if input_device_index is None:
            try:
                default_info = self.p.get_default_input_device_info()
                input_device_index = int(default_info["index"])
                logger.info(
                    f"Using default device {input_device_index}: {default_info['name']}"
                )
            except OSError:
                logger.error("No default input device found.")
                self.running = False
                return

        # Open stream
        if not self._open_stream(input_device_index):
            self.running = False
            return

        logger.info(f"Stream opened successfully on device {input_device_index}")

        # Main capture loop
        assert self.stream is not None
        while self.running and not self._stop_event.is_set():
            try:
                # Read audio with OVERFLOW detection enabled
                audio_data = self.stream.read(
                    self.buffer_size,
                    exception_on_overflow=True,  # Enable to catch XRUNs
                )

                samples = np.frombuffer(audio_data, dtype=np.float32)

                # Analyze frame
                self._analyze_frame(samples)

                # Store in buffer
                with self.buffer_lock:
                    self.audio_buffer.extend(samples)

                self.frames_captured += 1

                # Periodic status logging
                if self.frames_captured % 100 == 0:
                    elapsed = time.time() - self.start_time
                    fps = self.frames_captured / elapsed if elapsed > 0 else 0
                    logger.debug(
                        f"Status: {self.frames_captured} frames, "
                        f"{fps:.1f} fps, RMS: {self.last_rms:.4f}, "
                        f"Identical streak: {self.consecutive_identical_count}"
                    )

            except OSError as e:
                # PortAudio overflow error
                if "Input overflowed" in str(e):
                    logger.error(f"PORTAUDIO INPUT OVERFLOW: {e}")
                    logger.error("USB buffer overrun detected in PortAudio!")
                    self._handle_stream_error("PortAudio overflow")
                else:
                    logger.error(f"OSError reading audio: {e}")
                    self._handle_stream_error(f"OSError: {e}")
                    time.sleep(0.1)

            except Exception as e:
                if self.running:
                    logger.error(f"Error reading audio: {e}")
                    self._handle_stream_error(f"Exception: {e}")
                    time.sleep(0.1)

        self._cleanup_stream()

    def _analyze_frame(self, samples: np.ndarray) -> None:
        """Analyze audio frame for stale data detection."""
        # Calculate frame hash (first 100 samples) for identity detection
        frame_hash = hash(samples[:100].tobytes())

        # Calculate RMS level
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

        # Check for identical frames (stale data)
        if frame_hash == self.last_frame_hash:
            self.consecutive_identical_count += 1
            self.identified_frames += 1

            if self.consecutive_identical_count == MAX_CONSECUTIVE_IDENTICAL_FRAMES:
                logger.warning(
                    f"STALE AUDIO DETECTED: {self.consecutive_identical_count} "
                    f"consecutive identical frames!"
                )
                logger.warning("This indicates USB buffer overrun or device lockup")

                # Check for USB errors
                new_usb_errors = self._check_usb_errors()
                if new_usb_errors > 0:
                    logger.error(f"USB buffer overruns detected: {new_usb_errors}")
        else:
            if self.consecutive_identical_count >= MAX_CONSECUTIVE_IDENTICAL_FRAMES:
                logger.info(
                    f"Audio stream recovered after {self.consecutive_identical_count} "
                    f"identical frames"
                )
            self.consecutive_identical_count = 0

        self.last_frame_hash = frame_hash

        # Log suspicious patterns
        if (
            self.consecutive_identical_count > 0
            and self.consecutive_identical_count % 50 == 0
        ):
            logger.warning(
                f"Still receiving identical frames: {self.consecutive_identical_count} total"
            )

    def _handle_stream_error(self, error_msg: str) -> None:
        """Handle stream error - log and optionally attempt recovery."""
        logger.error(f"Stream error: {error_msg}")

        # Check USB errors
        new_usb_errors = self._check_usb_errors()
        if new_usb_errors > 0:
            logger.error(f"USB errors detected: {new_usb_errors}")

        # Could implement auto-recovery here
        # For diagnostics, we just log and continue

    def _open_stream(self, device_index: int) -> bool:
        """Open the PyAudio stream."""
        try:
            dev_info = self.p.get_device_info_by_index(device_index)
            max_channels = int(dev_info.get("maxInputChannels", 1))
            actual_channels = min(self.channels, max_channels)

            logger.info(f"Opening stream on '{dev_info['name']}'")
            logger.info(f"  Sample rate: {self.sample_rate}Hz")
            logger.info(f"  Channels: {actual_channels}")
            logger.info(f"  Buffer size: {self.buffer_size}")

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
            logger.error(f"Failed to open audio stream: {e}")
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
            logger.info("Stream closed")

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
        description="USB Audio Diagnostics - Detect buffer overruns and stale data"
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="Audio device index to use. If not specified, uses default device.",
    )
    parser.add_argument(
        "--channels",
        type=int,
        default=2,
        help="Number of input channels to capture (default: 2).",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=600,
        help="Test duration in seconds (default: 600 = 10 minutes).",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available audio devices and exit.",
    )

    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        sys.exit(0)

    logger.info("Starting audio diagnostics...")
    logger.info(f"Test duration: {args.duration} seconds")

    diagnostics = AudioDiagnostics(
        device_index=args.device,
        channels=args.channels,
        sample_rate=SAMPLE_RATE,
        buffer_size=BUFFER_SIZE,
    )

    try:
        diagnostics.start()

        # Run for specified duration
        logger.info(f"Running for {args.duration} seconds...")
        time.sleep(args.duration)

        logger.info("Test duration completed")
        diagnostics.stop()

    except KeyboardInterrupt:
        logger.info("\nShutdown requested...")
        diagnostics.stop()
        logger.info("Done.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Error: {e}")
        diagnostics.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()
