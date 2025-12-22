from __future__ import annotations

from dataclasses import dataclass
import re

from h9control.domain.algorithms import H9FullAlgorithmData


@dataclass(frozen=True)
class PresetSnapshot:
    """A minimal, tolerant representation of a preset dump.

    For MVP we keep this small and keep the raw text around so we can improve
    parsing later without losing information.
    """

    raw_text: str
    preset_number: int | None = None
    # Header fields: [preset] effect_index dump_format category
    effect_index: int | None = None
    dump_format: int | None = None
    category: int | None = None

    # The leading token on the knob line (usually same as effect_index, sometimes hex).
    effect_number: int | None = None
    algorithm_name: str | None = None
    preset_name: str | None = None

    # Parsed numeric values (best-effort)
    effect_number_raw: int | None = None
    knob_values: list[int] | None = None  # 10 values, typically 0..0x7FE0
    pedal_value: int | None = None
    checksum: str | None = None

    # Derived
    algorithm_key: str | None = None
    knobs_by_name: dict[str, int] | None = None


def parse_preset_dump_text(raw_text: str) -> PresetSnapshot:
    """Parse the ASCII-ish preset dump text into a `PresetSnapshot`.

    The H9 docs indicate dump fields are space/newline separated.
    This parser is intentionally tolerant: it extracts what it can.
    """

    # The real program dump we observed starts with something like:
    #   "[<preset>] <algo> <...>\r\n <effect> <knob...> ...\r\nC_<checksum>\r\n<ALGO NAME>\r\n<PRESET NAME>\r\n\x00"
    lines = [line.replace("\x00", "").strip() for line in raw_text.replace("\r", "").split("\n")]
    lines = [line for line in lines if line]

    preset_number: int | None = None
    effect_index: int | None = None
    dump_format: int | None = None
    category: int | None = None

    effect_number: int | None = None
    algorithm_name: str | None = None
    preset_name: str | None = None

    effect_number_raw: int | None = None
    knob_values: list[int] | None = None
    pedal_value: int | None = None
    checksum: str | None = None

    algorithm_key: str | None = None
    knobs_by_name: dict[str, int] | None = None

    def extract_decimal_int_tokens(s: str) -> list[int]:
        # Only treat full tokens as integers; this avoids parsing hex like "450d" as 450.
        ints: list[int] = []
        for tok in s.split():
            if tok.lstrip("-").isdigit():
                ints.append(int(tok))
        return ints

    if lines:
        header_line = lines[0]

        # If the line begins with a bracketed number, that's the preset number.
        m = re.match(r"^\s*\[(\d+)\]", header_line)
        if m:
            preset_number = int(m.group(1))
            tail = header_line[m.end() :].strip()
            tail_ints = extract_decimal_int_tokens(tail)
            if len(tail_ints) >= 1:
                effect_index = tail_ints[0]
            if len(tail_ints) >= 2:
                dump_format = tail_ints[1]
            if len(tail_ints) >= 3:
                category = tail_ints[2]
        else:
            header_ints = extract_decimal_int_tokens(header_line)
            if len(header_ints) >= 1:
                preset_number = header_ints[0]
            if len(header_ints) >= 2:
                effect_index = header_ints[1]
            if len(header_ints) >= 3:
                dump_format = header_ints[2]
            if len(header_ints) >= 4:
                category = header_ints[3]

    # Effect number lives at the start of the knob/value line (next non-empty line).
    if len(lines) >= 2:
        tokens = lines[1].split()
        if tokens:
            first_token = tokens[0]
            # Docs say 0-9, but real H9 dumps also show hex indices like "b".
            # Treat this as an index; parse as hex when possible.
            if re.fullmatch(r"[0-9A-Fa-f]+", first_token):
                effect_number_raw = int(first_token, 16)
                effect_number = effect_number_raw

            # Following tokens are typically hex values: 10 knob values + pedal value.
            hex_tokens = tokens[1:]
            hex_values: list[int] = []
            for tok in hex_tokens:
                try:
                    hex_values.append(int(tok, 16))
                except ValueError:
                    break

            if len(hex_values) >= 10:
                knob_values = hex_values[:10]
                if len(hex_values) >= 11:
                    pedal_value = hex_values[10]

    # Names usually appear after the checksum line.
    checksum_idx = next((i for i, line in enumerate(lines) if line.startswith("C_")), None)
    if checksum_idx is not None:
        checksum = lines[checksum_idx]
        trailing = [ln.strip() for ln in lines[checksum_idx + 1 :] if ln.strip()]
        # Common: <ALGO NAME> then <PRESET NAME>
        if len(trailing) >= 2:
            algorithm_name = trailing[0]
            preset_name = trailing[1]
        elif len(trailing) == 1:
            # Sometimes dumps include only the algorithm name (no user preset name).
            # Example: "BLACKHOLE" where our internal key is "BKHOLE".
            only_line = trailing[0]
            if H9FullAlgorithmData.resolve_key_from_display_name(only_line) is not None:
                algorithm_name = only_line
            else:
                preset_name = only_line

    if algorithm_name is not None:
        algorithm_key = H9FullAlgorithmData.resolve_key_from_display_name(algorithm_name)

    # Fallback: if the dump omitted the algorithm display name, try numeric mapping.
    if algorithm_key is None:
        # Prefer header mapping (category + effect_index). If that's missing, fall back
        # to the knob-line leading token.
        algorithm_key = H9FullAlgorithmData.resolve_key_from_category_index(category, effect_index)
        if algorithm_key is None:
            algorithm_key = H9FullAlgorithmData.resolve_key_from_category_index(category, effect_number_raw)
        if algorithm_key is not None and algorithm_name is None:
            algorithm_name = algorithm_key

    if algorithm_key is not None and knob_values is not None:
        knob_names = H9FullAlgorithmData.knob_names(algorithm_key)
        if len(knob_names) == 10:
            knobs_by_name = {name: value for name, value in zip(knob_names, knob_values, strict=True)}

    return PresetSnapshot(
        raw_text=raw_text,
        preset_number=preset_number,
        effect_index=effect_index,
        dump_format=dump_format,
        category=category,
        effect_number=effect_number,
        algorithm_name=algorithm_name,
        preset_name=preset_name,

        effect_number_raw=effect_number_raw,
        knob_values=knob_values,
        pedal_value=pedal_value,
        checksum=checksum,

        algorithm_key=algorithm_key,
        knobs_by_name=knobs_by_name,
    )
