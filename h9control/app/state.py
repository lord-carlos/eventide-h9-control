from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnobBarState:
    name: str
    percent: int
    raw_value: int
    pretty: str | None = None


@dataclass(frozen=True)
class DashboardState:
    connected: bool
    status_text: str

    preset_number: int | None = None
    preset_name: str | None = None
    algorithm_name: str | None = None
    algorithm_key: str | None = None
    bpm: float | None = None
    live_bpm: float | None = None

    knobs: tuple[KnobBarState, ...] = ()


def ascii_bar(percent: int, *, width: int = 12) -> str:
    pct = max(0, min(100, percent))
    filled = int(round((pct / 100.0) * width))
    filled = max(0, min(width, filled))
    empty = width - filled
    return "[" + ("X" * filled) + ("-" * empty) + "]"
