from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AlgorithmMeta:
    description: str
    knobs: list[str]
    display_names: list[str]


@dataclass(frozen=True)
class AlgorithmCategory:
    name: str
    keys: list[str]


class H9FullAlgorithmData:
    """Mapping of H9 algorithms, grouped into categories.

    The program dump header we observe is:
        [preset] effect_index dump_format category

    That means the most reliable mapping (even when the dump doesn't include an
    algorithm display-name line) is:
        (category, effect_index) -> algorithm key
    """

    # Category number -> ordered list of algorithm keys.
    # Intentionally partial: we only include keys we have knob mappings for.
    CATEGORIES: dict[int, AlgorithmCategory] = {
        1: AlgorithmCategory(name="TimeFactor", keys=["DIGDLY", "VNTAGE", "TAPE", "MODDLY"]),
        2: AlgorithmCategory(name="ModFactor", keys=[]),
        3: AlgorithmCategory(name="PitchFactor", keys=[]),
        4: AlgorithmCategory(
            name="Space",
            keys=[
                "HALL",
                "ROOM",
                "PLATE",
                "SPRING",
                "DUAL",
                "REVRVB",
                "MODEKO",
                "BKHOLE",
                "MANGLD",
                "TREMLO",
                "DYNAVB",
                "SHIMMR",
            ],
        ),
        5: AlgorithmCategory(name="H9 Exclusive", keys=["ULTRA.T", "PTCFUZ"]),
    }

    # Algorithm key -> metadata.
    # `display_names` contains names we might see in dumps.
    ALGO_MAP: dict[str, AlgorithmMeta] = {
        # --- SPACE (Category 4) ---
        "HALL": AlgorithmMeta(
            description="Simulates large enclosed spaces with a 3-band crossover reverb network.",
            display_names=["HALL"],
            knobs=["MIDLVL", "MODLVL", "HI-DCY", "LO-DCY", "HI-LVL", "LO-LVL", "PREDLY", "SIZE", "DECAY", "MIX"],
        ),
        "ROOM": AlgorithmMeta(
            description="Realistic room sounds from vocal booths to small halls.",
            display_names=["ROOM"],
            knobs=["HIFREQ", "MODLVL", "DFSION", "REFLEX", "HI-LVL", "LO-LVL", "PREDLY", "SIZE", "DECAY", "MIX"],
        ),
        "PLATE": AlgorithmMeta(
            description="Simulates early analog-mechanical reverbs with long, clean tails.",
            display_names=["PLATE"],
            knobs=["TONE", "MODLVL", "DFSION", "DSTNCE", "HI-DMP", "LO-DMP", "PREDLY", "SIZE", "DECAY", "MIX"],
        ),
        "SPRING": AlgorithmMeta(
            description="Models artificial reverbs found in guitar amplifiers.",
            display_names=["SPRING"],
            knobs=["RESNCE", "MODLVL", "TRM-RT", "TRMOLO", "HI-DMP", "LO-DMP", "NUMSPR", "TNSION", "DECAY", "MIX"],
        ),
        "DUAL": AlgorithmMeta(
            description="Combines two different studio reverbs (A and B) with independent controls.",
            display_names=["DUAL"],
            knobs=["RESNCE", "VRBMIX", "B-PDLY", "B-DCY", "B-TONE", "A-TONE", "A-PDLY", "SIZE", "A-DCY", "MIX"],
        ),
        "REVRVB": AlgorithmMeta(
            description="True reverse reverb followed by a forward reverb with delay and feedback.",
            display_names=["REVRVB", "REVERSE"],
            knobs=["CONTUR", "MODLVL", "DIFFUS", "LATE", "HI-LVL", "LO-LVL", "FEEDBK", "SIZE", "DECAY", "MIX"],
        ),
        "MODEKO": AlgorithmMeta(
            description="Feeds infinite reverb into infinite feedback delay with heavy modulation.",
            display_names=["MODEKO", "MODECHOVERB"],
            knobs=["E-TONE", "FX-MIX", "M-RATE", "E-FDBK", "HI-LVL", "LO-LVL", "ECHO", "SIZE", "DECAY", "MIX"],
        ),
        "BKHOLE": AlgorithmMeta(
            description="Massive cathedral-type spaces to out-of-this-world soundscapes.",
            display_names=["BKHOLE", "BLACKHOLE"],
            knobs=["RESNCE", "FEEDBK", "M-RATE", "M-DPTH", "HI-LVL", "LO-LVL", "PREDLY", "SIZE", "GRVITY", "MIX"],
        ),
        "MANGLD": AlgorithmMeta(
            description="Feeds a non-standard stereo reverb into distortion for chaotic sounds.",
            display_names=["MANGLD", "MANGLEDVERB"],
            knobs=["MIDLVL", "WOBBLE", "OUTPUT", "ODRIVE", "HI-LVL", "LO-LVL", "PREDLY", "SIZE", "DECAY", "MIX"],
        ),
        "TREMLO": AlgorithmMeta(
            description="Large reverb cut by an aggressive rhythmic tremolo.",
            display_names=["TREMLO", "TREMOLOVERB"],
            knobs=["HIFREQ", "STDPTH", "SPEED", "SHAPE", "HI-LVL", "LO-LVL", "PREDLY", "SIZE", "DECAY", "MIX"],
        ),
        "DYNAVB": AlgorithmMeta(
            description="Couples a reverb with an Omnipressor model for dynamic response.",
            display_names=["DYNAVB", "DYNAVERB"],
            knobs=["SCHAIN", "THRESH", "RELEAS", "ORATIO", "HI-LVL", "LO-LVL", "ATTACK", "SIZE", "DECAY", "MIX"],
        ),
        "SHIMMR": AlgorithmMeta(
            description="Pitch-shifted reverb tails for ethereal guitar sounds.",
            display_names=["SHIMMR", "SHIMMER"],
            knobs=["MIDDCY", "PITCH", "PICH-B", "PICH-A", "HI-DCY", "LO-DCY", "DELAY", "SIZE", "DECAY", "MIX"],
        ),

        # --- TIMEFACTOR (Category 1) ---
        "DIGDLY": AlgorithmMeta(
            description="Twin delays with independent time, feedback, and filters.",
            display_names=["DIGDLY", "DIGITALDELAY", "DIGITAL DELAY"],
            knobs=["FILTER", "SPEED", "DEPTH", "XFADE", "FBK-B", "FBK-A", "DLY-B", "DLY-A", "DLYMIX", "MIX"],
        ),
        "VNTAGE": AlgorithmMeta(
            description="Simulates analog and early digital delays with bit resolution control.",
            display_names=["VNTAGE", "VINTAGE", "VINTAGE DELAY"],
            knobs=["FILTER", "SPEED", "DEPTH", "BITS", "FBK-B", "FBK-A", "DLY-B", "DLY-A", "DLYMIX", "MIX"],
        ),
        "TAPE": AlgorithmMeta(
            description="Simulates hiss, wow, flutter, and saturation of analog tape delay.",
            display_names=["TAPE", "TAPE ECHO", "TAPEECHO"],
            knobs=["FILTER", "FLUTTR", "WOW", "SATUR", "FBK-B", "FBK-A", "DLY-B", "DLY-A", "DLYMIX", "MIX"],
        ),
        "MODDLY": AlgorithmMeta(
            description="Modulated delays optimized for chorus effects.",
            display_names=["MODDLY", "MOD DELAY", "MODDELAY"],
            knobs=["FILTER", "SPEED", "DEPTH", "WAVE", "FBK-B", "FBK-A", "DLY-B", "DLY-A", "DLYMIX", "MIX"],
        ),

        # --- H9 EXCLUSIVE (Category 5) ---
        "ULTRA.T": AlgorithmMeta(
            description="Versatile multi-tap delay for rhythmic patterns and volume swells.",
            display_names=["ULTRA.T", "ULTRATAP", "ULTRA TAP"],
            knobs=["RELEASE", "CHOP", "SLURM", "TONE", "TAPER", "SPREAD", "PREDLY", "TAPS", "LENGTH", "MIX"],
        ),
        "PTCFUZ": AlgorithmMeta(
            description="Combines Fuzz, three Pitch Shifters, and two Delays.",
            display_names=["PTCFUZ", "PITCHFUZZ", "PITCH FUZZ"],
            knobs=["DLYLVL", "PTCH-C", "PTCH-B", "PTCH-A", "PEACH", "FZTONE", "FUZZ", "DLY-B", "DLY-A", "MIX"],
        ),
    }

    @classmethod
    def get_info(cls, algo_key: str) -> dict:
        meta = cls.ALGO_MAP.get(algo_key.upper())
        if meta is None:
            return {"description": "Algorithm not found.", "knobs": [], "display_names": []}
        return {"description": meta.description, "knobs": list(meta.knobs), "display_names": list(meta.display_names)}

    @classmethod
    def knob_names(cls, algo_key: str) -> list[str]:
        meta = cls.ALGO_MAP.get(algo_key.upper())
        return list(meta.knobs) if meta is not None else []

    @classmethod
    def resolve_key_from_display_name(cls, display_name: str) -> str | None:
        """Best-effort mapping from dump algorithm display name -> algorithm key."""

        name = display_name.strip().upper()
        if not name:
            return None

        if name in cls.ALGO_MAP:
            return name

        wanted = re.sub(r"[^A-Z0-9]", "", name)
        for key, info in cls.ALGO_MAP.items():
            if re.sub(r"[^A-Z0-9]", "", key.upper()) == wanted:
                return key

            for disp in info.display_names:
                disp_norm = re.sub(r"[^A-Z0-9]", "", disp.upper())
                if disp_norm == wanted:
                    return key

        return None

    @classmethod
    def resolve_key_from_category_index(cls, category: int | None, effect_index: int | None) -> str | None:
        """Map (category, effect_index) -> algorithm key."""

        if category is None or effect_index is None:
            return None

        cat = cls.CATEGORIES.get(category)
        if not cat:
            return None

        if 0 <= effect_index < len(cat.keys):
            return cat.keys[effect_index]

        return None

    @classmethod
    def resolve_key_from_numbers(
        cls,
        algorithm_number: int | None,
        effect_index: int | None,
    ) -> str | None:
        """Back-compat: historically we called the category field `algorithm_number`."""

        return cls.resolve_key_from_category_index(algorithm_number, effect_index)
