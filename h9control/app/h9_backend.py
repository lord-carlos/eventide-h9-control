from __future__ import annotations

from collections.abc import Callable

from h9control.protocol.codes import H9SysexCodes, H9SystemKeys
from h9control.protocol.sysex import SysexFrame


class H9Backend:
    """Backend helpers for common device operations.

    This intentionally hides protocol keys (e.g. H9SystemKeys) from UI-facing code.
    It relies on the caller to provide a transport-safe send + wait mechanism.
    """

    def __init__(
        self,
        *,
        send_eventide: Callable[[int, bytes], None],
        wait_for_frame: Callable[[Callable[[SysexFrame], bool], float], SysexFrame],
    ) -> None:
        self._send_eventide = send_eventide
        self._wait_for_frame = wait_for_frame

    def get_bpm(self, *, timeout_s: float) -> float:
        tempo_x100 = self.get_value(H9SystemKeys.KEY_SP_TEMPO, timeout_s=timeout_s)
        return tempo_x100 / 100.0

    def set_bpm(self, bpm: int) -> None:
        self.set_value(H9SystemKeys.KEY_SP_TEMPO, bpm * 100)

    def get_value(self, key: int, *, timeout_s: float) -> int:
        key_str = f"{key:X}".encode("ascii")
        self._send_eventide(H9SysexCodes.SYSEXC_VALUE_WANT, key_str)

        requested_key_hex = f"{key:X}".upper()

        def _matches_value_dump(frame: SysexFrame) -> bool:
            if frame.command != H9SysexCodes.SYSEXC_VALUE_DUMP:
                return False
            parts = (
                frame.payload.decode("ascii", errors="replace")
                .strip("\x00\r\n ")
                .split()
            )
            return len(parts) >= 1 and parts[0].upper() == requested_key_hex

        frame = self._wait_for_frame(
            _matches_value_dump,
            timeout_s,
        )

        text = frame.payload.decode("ascii", errors="replace").strip("\x00\r\n ")
        parts = text.split()
        if len(parts) < 2:
            raise ValueError(f"Unexpected VALUE_DUMP payload: {text!r}")

        value_part = parts[1]
        if value_part.lstrip("-").isdigit():
            return int(value_part, 10)
        return int(value_part, 16)

    def set_value(self, key: int, value: int | str) -> None:
        # key:X formats the key as hex (e.g., 770 -> '302')
        key_str = f"{key:X}"

        if isinstance(value, int):
            # value:X formats the value as hex (e.g., 12000 -> '2EE0')
            val_str = f"{value:X}"
        else:
            val_str = value

        payload = bytearray()
        payload.extend(key_str.encode("ascii"))
        payload.append(0x20)
        payload.extend(val_str.encode("ascii"))

        self._send_eventide(H9SysexCodes.SYSEXC_VALUE_PUT, bytes(payload))
