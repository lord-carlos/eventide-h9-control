from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import mido


EVENTIDE_MANUFACTURER_ID = 0x1C
EVENTIDE_MODEL_ID_H9 = 0x70


@dataclass(frozen=True)
class SysexFrame:
    manufacturer_id: int
    model_id: int
    device_id: int
    command: int
    payload: bytes


def decode_eventide_sysex(message: mido.Message) -> SysexFrame | None:
    """Decode a mido SysEx message into an Eventide `SysexFrame`.

    mido SysEx messages contain the data bytes WITHOUT 0xF0/0xF7.
    Eventide format is: 1C 70 <id> <cmd> <payload...>
    """

    msg = cast(Any, message)

    if msg.type != "sysex":
        return None

    data = bytes(msg.data)
    if len(data) < 4:
        return None

    if data[0] != EVENTIDE_MANUFACTURER_ID or data[1] != EVENTIDE_MODEL_ID_H9:
        return None

    device_id = data[2]
    command = data[3]
    payload = data[4:]

    return SysexFrame(
        manufacturer_id=data[0],
        model_id=data[1],
        device_id=device_id,
        command=command,
        payload=payload,
    )


def format_sysex_bytes(data: bytes | bytearray | list[int], *, max_len: int = 64) -> str:
    """Format SysEx bytes as hex, truncated for logs.

    Accepts framed (F0..F7) or unframed payloads.
    """

    if isinstance(data, list):
        raw = bytes(data)
    else:
        raw = bytes(data)

    truncated = raw[:max_len]
    hex_part = " ".join(f"{b:02X}" for b in truncated)
    if len(raw) > max_len:
        return f"{hex_part} …(+{len(raw) - max_len} bytes)"
    return hex_part


def build_eventide_sysex(device_id: int, command: int, payload: bytes | bytearray = b"") -> list[int]:
    """Build a *framed* Eventide SysEx message as a list of ints.

    Output is: F0 1C 70 <id> <cmd> <payload...> F7
    """

    if device_id < 0 or device_id > 127:
        raise ValueError("device_id must be 0..127")

    if command < 0 or command > 255:
        raise ValueError("command must be 0..255")

    return [0xF0, EVENTIDE_MANUFACTURER_ID, EVENTIDE_MODEL_ID_H9, device_id, command, *payload, 0xF7]
