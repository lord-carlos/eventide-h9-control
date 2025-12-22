from __future__ import annotations

"""Eventide H9 / Factor SysEx constants.

These values are derived from the Eventide SysEx documentation.
Keep protocol constants here so the rest of the codebase doesn't duplicate them.
"""


class H9SysexCodes:
    # Response codes
    SYSEXC_OK = 0x00
    SYSEXC_ERROR = 0x0D

    # Value/key operations
    SYSEXC_VALUE_PUT = 0x2D
    SYSEXC_VALUE_DUMP = 0x2E
    SYSEXC_OBJECTINFO_WANT = 0x31
    SYSEXC_VALUE_WANT = 0x3B

    # Preset/program dump
    SYSEXC_PROGRAM_DUMP = 0x15
    SYSEXC_TJ_PRESETS_WANT = 0x48
    SYSEXC_TJ_PRESETS_DUMP = 0x49
    SYSEXC_TJ_SYSVARS_WANT = 0x4C
    SYSEXC_TJ_SYSVARS_DUMP = 0x4D
    SYSEXC_TJ_PROGRAM_WANT = 0x4E
    SYSEXC_TJ_PROGRAM_DUMP = 0x4F
    SYSEXC_TJ_ALL_WANT = 0x50
    SYSEXC_TJ_ALL_DUMP = 0x51


class H9SystemKeys:
    """System variable keys (Appendix B style)."""

    # Boolean keys (base 0x100)
    KEY_SP_BYPASS = 0x102
    KEY_SP_TAP_SYN = 0x107

    # Word keys (base 0x300)
    # Tempo is represented as BPM * 100.
    KEY_SP_TEMPO = 0x302


MAX_KNOB_VALUE_14BIT = 0x7FE0
