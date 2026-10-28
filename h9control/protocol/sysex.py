from __future__ import annotations

import re
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

    Every byte inside a SysEx message body must be 7-bit (0..0x7F).
    """

    if device_id < 0 or device_id > 0x7F:
        raise ValueError("device_id must be 0..0x7F (7-bit)")

    if command < 0 or command > 0x7F:
        raise ValueError("command must be 0..0x7F (7-bit)")

    payload = bytes(payload)
    for index, byte in enumerate(payload):
        if byte < 0 or byte > 0x7F:
            raise ValueError(
                f"payload byte at index {index} is 0x{byte:02X}; "
                "SysEx payload bytes must be 7-bit (0..0x7F)"
            )

    return [0xF0, EVENTIDE_MANUFACTURER_ID, EVENTIDE_MODEL_ID_H9, device_id, command, *payload, 0xF7]


# Value tokens in VALUE_DUMP payloads: optional sign + decimal digits, or
# unsigned hex digits. Anything else (e.g. "-A0", "0x30") is malformed.
_DECIMAL_VALUE_RE = re.compile(r"-?\d+")
_HEX_VALUE_RE = re.compile(r"[0-9A-Fa-f]+")


def decode_eventide_value(text: str) -> int:
    """Decode an ASCII value token from a VALUE_DUMP (0x2E) payload.

    Precedence rule (single source of truth for all call sites):

    1. Signed decimal (``-?\\d+``) wins: pure digit strings are parsed as
       decimal even though they are also valid hex ("12500" -> 12500, not
       0x12500). Negotiated integers (e.g. tempo = BPM * 100) arrive decimal.
    2. Otherwise a token of plain (unsigned) hex digits is parsed as hex
       ("30D4" -> 12500).
    3. Anything else raises ``ValueError``: a leading ``-`` is only accepted
       for decimal values, so "-A0" is malformed, not negative hex.
    """

    token = text.strip()
    if _DECIMAL_VALUE_RE.fullmatch(token):
        return int(token, 10)
    if _HEX_VALUE_RE.fullmatch(token):
        return int(token, 16)

    raise ValueError(
        f"Cannot parse Eventide value {text!r}: expected signed decimal "
        "(e.g. '12500') or unsigned hex digits (e.g. '30D4')"
    )
