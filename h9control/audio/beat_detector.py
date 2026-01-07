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
            logging.error(f"Error getting device info for device {input_device_index}: {e}")
            
            # Fallback: find first available input device
            fallback_device = None
            fallback_info = None
            try:
                count = self.p.get_device_count()
                for i in range(count):
                    info = self.p.get_device_info_by_index(i)
                    if info.get("maxInputChannels", 0) > 0:
                        # Try to verify this device is actually usable by checking if we can get its info
                        try:
                            # Store both device index and info for later use
                            fallback_device = i
                            fallback_info = info
                            logging.warning(f"Falling back to device {i}: {info.get('name', 'Unknown')}")
                            break
                        except Exception:
                            continue
            except Exception as fallback_error:
                logging.error(f"Failed to find fallback device: {fallback_error}")
            
            if fallback_device is not None and fallback_info is not None:
                input_device_index = fallback_device
                try:
                    dev_info = fallback_info
                    sample_rate = int(dev_info.get("defaultSampleRate", 44100))
                    max_input_channels = int(dev_info.get("maxInputChannels", 1))
                    if channels > max_input_channels:
                        logging.warning(f"Fallback device only has {max_input_channels} channels (requested {channels}). Using {max_input_channels}.")
                        channels = max_input_channels
                except Exception as dev_error:
                    logging.error(f"Failed to get fallback device info: {dev_error}")
                    self.running = False
                    return
            else:
                logging.error("No audio input devices available. Beat detection disabled.")
                self.running = False
                return

        win_size = self.buffer_size * self.window_multiple

        # Try to open the audio stream, with fallback to other devices if it fails
        stream_opened = False
        devices_to_try = [input_device_index]
        
        # If the current device fails, try other available input devices
        if not stream_opened:
            try:
                count = self.p.get_device_count()
                for i in range(count):
                    if i != input_device_index:
                        info = self.p.get_device_info_by_index(i)
                        if info.get("maxInputChannels", 0) > 0:
                            devices_to_try.append(i)
            except Exception:
                pass
        
        for device_idx in devices_to_try:
            try:
                # Get fresh device info for each attempt
                dev_info = self.p.get_device_info_by_index(device_idx)
                device_sample_rate = int(dev_info.get("defaultSampleRate", 44100))
                device_channels = min(channels, int(dev_info.get("maxInputChannels", 1)))
                
                logging.debug(f"Attempting to open audio stream: device={device_idx} ({dev_info.get('name', 'Unknown')}), channels={device_channels}, rate={device_sample_rate}")
                
                # Initialize aubio tempo
                tempo_detect = aubio.tempo("default", win_size, self.buffer_size, device_sample_rate)

                self.stream = self.p.open(
                    format=self.format,
                    channels=device_channels,
                    rate=device_sample_rate,
                    input=True,
                    frames_per_buffer=self.buffer_size,
                    input_device_index=device_idx,
                )
                
                # Success!
                input_device_index = device_idx
                sample_rate = device_sample_rate
                channels = device_channels
                stream_opened = True
                if device_idx != devices_to_try[0]:
                    logging.warning(f"Successfully opened fallback device {device_idx}: {dev_info.get('name', 'Unknown')}")
                break
                
            except Exception as e:
                if device_idx == devices_to_try[-1]:  # Last device
                    logging.error(f"Failed to open audio stream on device {device_idx}: {e}")
                else:
                    logging.debug(f"Failed to open audio stream on device {device_idx}: {e}, trying next device...")
                continue
        
        if not stream_opened:
            logging.error("Could not open any audio input device. Beat detection disabled.")
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
