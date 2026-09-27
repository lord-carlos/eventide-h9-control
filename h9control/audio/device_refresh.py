from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import sounddevice as sd


class AudioDetector(Protocol):
    @property
    def running(self) -> bool: ...

    def stop(self) -> None: ...

    def start(self) -> None: ...


def refresh_portaudio_device_list() -> None:
    """Reinitialize PortAudio so its cached device list reflects hot-plugging."""
    sd._terminate()
    sd._initialize()


def refresh_audio_devices(
    detector: AudioDetector | None,
    update_device_list: Callable[[], int],
) -> int:
    """Refresh PortAudio and the settings list while safely restarting capture."""
    restart_detector = detector is not None and detector.running
    if detector is not None:
        detector.stop()

    try:
        refresh_portaudio_device_list()
        return update_device_list()
    finally:
        if restart_detector and detector is not None:
            detector.start()
