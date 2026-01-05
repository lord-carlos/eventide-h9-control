from __future__ import annotations

import logging
import threading
import time

import numpy as np
import pyaudio
from PySide6.QtCore import QObject, Signal

try:
    import aubio
except ImportError:
    aubio = None
    logging.warning("aubio not found. Beat detection will not work.")

from h9control.app.config import ConfigManager


class BeatDetector(QObject):
    bpm_detected = Signal(float)

    def __init__(self, config: ConfigManager) -> None:
        super().__init__()
        self.config = config
        self.running = False
        self.thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Audio parameters
        self.buffer_size = 64
        self.window_multiple = 32
        self.format = pyaudio.paFloat32
        # We might need to adjust channels based on device capabilities, but config has preference
        
        #Ugly hack
        self.BPM_CALIBRATION = 0.9974
        
        self.p = pyaudio.PyAudio()
        self.stream: pyaudio.Stream | None = None

        self.bpm_estimates: list[float] = []

    def start(self) -> None:
        if self.running:
            return

        if aubio is None:
            logging.error("Cannot start BeatDetector: aubio is missing.")
            return

        self.running = True
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logging.info("BeatDetector started.")

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self._stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        self.thread = None
        logging.info("BeatDetector stopped.")

    def _run_loop(self) -> None:
        input_device_index = self.config.audio_input_device_id
        channels = self.config.audio_input_channels

        # Resolve device if needed or use default
        if input_device_index is None:
            # Try to find a default input device
            try:
                default_info = self.p.get_default_input_device_info()
                input_device_index = int(default_info["index"])
                # Update config with detected default? Maybe not, keep it None to always use default.
            except OSError:
                logging.error("No default input device found.")
                self.running = False
                return

        # Get device info for sample rate
        try:
            dev_info = self.p.get_device_info_by_index(input_device_index)
            sample_rate = int(dev_info.get("defaultSampleRate", 44100))
            max_input_channels = int(dev_info.get("maxInputChannels", 1))
            if channels > max_input_channels:
                logging.warning(f"Requested {channels} channels but device only has {max_input_channels}. Using {max_input_channels}.")
                channels = max_input_channels
        except Exception as e:
            logging.error(f"Error getting device info: {e}")
            sample_rate = 44100

        win_size = self.buffer_size * self.window_multiple

        try:
            # Initialize aubio tempo
            # method='default', buf_size=win_size, hop_size=buffer_size, samplerate=sample_rate
            tempo_detect = aubio.tempo("default", win_size, self.buffer_size, sample_rate)

            self.stream = self.p.open(
                format=self.format,
                channels=channels,
                rate=sample_rate,
                input=True,
                frames_per_buffer=self.buffer_size,
                input_device_index=input_device_index,
            )
        except Exception as e:
            logging.error(f"Failed to open audio stream or init aubio: {e}")
            self.running = False
            return

        logging.info(f"Listening for beats on device {input_device_index} at {sample_rate}Hz")

        while self.running and not self._stop_event.is_set():
            try:
                data = self.stream.read(self.buffer_size, exception_on_overflow=False)
                samples = np.frombuffer(data, dtype=np.float32)

                if tempo_detect(samples):
                    bpm = tempo_detect.get_bpm()
                    if bpm:
                        self._process_bpm(bpm)
            except Exception as e:
                logging.error(f"Error in beat detection loop: {e}")
                time.sleep(0.1)  # Avoid tight loop on error

        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None

    def _process_bpm(self, raw_bpm: float) -> None:
        self.bpm_estimates.append(raw_bpm)
        
        # Keep last 10 estimates for median smoothing
        max_samples = 10
        if len(self.bpm_estimates) > max_samples:
            self.bpm_estimates.pop(0)

        if self.bpm_estimates:
            median_bpm = float(np.median(self.bpm_estimates))
            self.bpm_detected.emit(round(median_bpm * self.BPM_CALIBRATION, 1))

    def __del__(self) -> None:
        self.p.terminate()
