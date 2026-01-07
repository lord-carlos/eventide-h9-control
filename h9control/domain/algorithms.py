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
    CATEGORIES: dict[int, AlgorithmCategory] = {
        1: AlgorithmCategory(
            name="TimeFactor", 
            keys=["DIGDLY", "VNTAGE", "TAPE", "MODDLY", "DUCKER", "BNDDLY", "FLTDLY", "MULTAP", "REVERS"]
        ),
        2: AlgorithmCategory(
            name="ModFactor", 
            keys=[
                "CHORUS", 
                "PHASER", 
                "Q-WAH", 
                "FLANGE", 
                "M-FLTR", 
                "ROTARY", 
                "TREMLO", 
                "VIBE", 
                "UNDLTR", 
                "RINGMD"
            ]
        ),
        3: AlgorithmCategory(
            name="PitchFactor", 
            keys=["DTONIC", "QUADVX", "HARMNY", "MICRO", "910.949", "PCHFLX", "OCTAVE", "CRYSTL", "HARPEG", "SYNTH"]
        ),
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
        5: AlgorithmCategory(
            name="H9 Exclusive", 
            keys=[
                "ULTRA.T", "RESNTR", "EQCOMP", "CRUSH", 
                "SPCTME", "SCULPT", "PTCFUZ", "HOTSAW",
                "HRMDLO", "TRICER"
            ]
        ),
    }

    # Algorithm key -> metadata.
    # `display_names` contains names we might see in dumps.
    ALGO_MAP: dict[str, AlgorithmMeta] = {
        # --- MODFACTOR (Category 2) ---
        "CHORUS": AlgorithmMeta(
            description="Simulates multiple instruments playing together by modulating delay lines.",
            display_names=["CHORUS"],
            knobs=["MODSRC", "RATE", "S-MOD", "D-MOD", "FILTER", "SHAPE", "SPEED", "DEPTH", "TYPE", "INTENS"],
        ),
        "PHASER": AlgorithmMeta(
            description="Created by a series of all pass filters mixed with the dry signal.",
            display_names=["PHASER"],
            knobs=["MODSRC", "RATE", "S-MOD", "D-MOD", "STAGES", "SHAPE", "SPEED", "DEPTH", "TYPE", "INTENS"],
        ),
        "Q-WAH": AlgorithmMeta(
            description="Simulates classic wah-wah pedal or auto-wah effects.",
            display_names=["Q-WAH"],
            knobs=["MODSRC", "RATE", "S-MOD", "D-MOD", "BOTTOM", "SHAPE", "SPEED", "DEPTH", "TYPE", "INTENS"],
        ),
        "FLANGE": AlgorithmMeta(
            description="Intense modulation effect with deep frequency notches.",
            display_names=["FLANGE", "FLANGER"],
            knobs=["MODSRC", "RATE", "S-MOD", "D-MOD", "MDO", "SHAPE", "SPEED", "DEPTH", "TYPE", "INTENS"],
        ),
        "M-FLTR": AlgorithmMeta(
            description="Modulated low pass, band pass, or high pass filters.",
            display_names=["M-FLTR", "MODFILTER"],
            knobs=["MODSRC", "RATE", "S-MOD", "D-MOD", "WIDTH", "SHAPE", "SPEED", "DEPTH", "TYPE", "INTENS"],
        ),
        "ROTARY": AlgorithmMeta(
            description="Rotating speaker (Leslie) simulation with rotor and horn control.",
            display_names=["ROTARY"],
            knobs=["MODSRC", "RATE", "S-MOD", "D-MOD", "TONE", "BALNCE", "HRNSPD", "RTRSPD", "SIZE", "INTENS"],
        ),
        "TREMLO": AlgorithmMeta(
            description="Modulates the level of the incoming audio with an LFO.",
            display_names=["TREMLO", "TREMOLOPAN"],
            knobs=["MODSRC", "RATE", "S-MOD", "D-MOD", "WIDTH", "SHAPE", "SPEED", "DEPTH", "TYPE", "INTENS"],
        ),
        "VIBE": AlgorithmMeta(
            description="Simulates pitch changes like a whammy bar or finger vibrato.",
            display_names=["VIBE", "VIBRATO"],
            knobs=["MODSRC", "RATE", "S-MOD", "D-MOD", "STAGES", "SHAPE", "SPEED", "DEPTH", "TYPE", "INTENS"],
        ),
        "UNDLTR": AlgorithmMeta(
            description="Combines two delays, two detuned voices, and an FM modulated tremolo.",
            display_names=["UNDLTR", "UNDULATOR"],
            knobs=["MODSRC", "RATE", "S-MOD", "D-MOD", "FEEDBK", "SHAPE", "SPEED", "DEPTH", "TYPE", "INTENS"],
        ),
        "RINGMD": AlgorithmMeta(
            description="Multiplies input signal by an audio frequency waveform for bell-like overtones.",
            display_names=["RINGMD", "RINGMOD"],
            knobs=["MODSRC", "RATE", "S-MOD", "D-MOD", "TONE", "SHAPE", "SPEED", "UNUSED", "TYPE", "INTENS"],
        ),
        # --- PITCHFACTOR (Category 3) ---
        "DTONIC": AlgorithmMeta(
            description="Diatonic pitch shifters track the notes that you're playing and shift the pitch by the selected harmonic interval based on the Key and Scale.",
            display_names=["DTONIC", "DIATONIC"],
            knobs=["FBK-B", "FBK-A", "SCALE", "KEY", "DLY-B", "DLY-A", "PICH-B", "PICH-A", "PICHMX", "MIX"],
        ),
        "QUADVX": AlgorithmMeta(
            description="Similar to Diatonic but delivers up to four pitch shifted voices (A, B, C, D) instead of two.",
            display_names=["QUADVX", "QUADRAVOX"],
            knobs=["PICH-D", "PICH-C", "SCALE", "KEY", "DLYGRP", "DLY-D", "PICH-B", "PICH-A", "PICHMX", "MIX"],
        ),
        "HARMNY": AlgorithmMeta(
            description="Combines twin chromatic pitch shifters with modulation to deliver an extremely wide range of effects.",
            display_names=["HARMNY", "HARMODULATOR"],
            knobs=["FEEDBK", "SHAPE", "M-RATE", "M-DPTH", "DLY-B", "DLY-A", "PICH-B", "PICH-A", "PICHMX", "MIX"],
        ),
        "MICRO": AlgorithmMeta(
            description="Fine-resolution pitch shifter for subtle tone-fattening plus delays for interesting slap back effects.",
            display_names=["MICRO", "MICROPITCH"],
            knobs=["TONE", "FEEDBK", "M-RATE", "M-DPTH", "DLY-B", "DLY-A", "PICH-B", "PICH-A", "PICHMX", "MIX"],
        ),
        "910.949": AlgorithmMeta(
            description="Emulates the sound and functionality of Eventide's legendary H910 and H949 Harmonizer units.",
            display_names=["910.949", "H910/H949"],
            knobs=["FDBK-B", "FDBK-A", "P-CNTL", "TYPE", "DLY-B", "DLY-A", "PICH-B", "PICH-A", "PICHMX", "MIX"],
        ),
        "PCHFLX": AlgorithmMeta(
            description="Designed to be used 'live' with an Expression Pedal, the on board HotKnob, or the FLEX switch.",
            display_names=["PCHFLX", "PITCHFLEX"],
            knobs=["TOE-B", "TOE-A", "SHAPE", "LPF", "THGLIS", "HTGLIS", "HEEL-B", "HEEL-A", "PICHMX", "MIX"],
        ),
        "OCTAVE": AlgorithmMeta(
            description="Creates a pair of sub-harmonics and adds an Octave FUZZ generator.",
            display_names=["OCTAVE", "OCTAVER"],
            knobs=["OCT-MX", "FUZZ", "SENSE", "ENVLOP", "RESN-B", "RESN-A", "CNTR-B", "CNTR-A", "SUB-MX", "MIX"],
        ),
        "CRYSTL": AlgorithmMeta(
            description="Twin reverse pitch changers with independently adjustable delays and feedback with added reverb.",
            display_names=["CRYSTL", "CRYSTALS"],
            knobs=["FBK-B", "FBK-A", "VRB-DC", "VRB-MX", "RDLY-B", "RDLY-A", "PICH-B", "PICH-A", "PICHMX", "MIX"],
        ),
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
        "DUCKER": AlgorithmMeta(
            description="The delay levels are dynamically lowered while you're playing and restored when you stop.",
            display_names=["DUCKER", "DUCKEDDELAY", "DUCKED DELAY"],
            knobs=["FILTER", "RELEAS", "THRSHD", "RATIO", "FBK-B", "FBK-A", "DLY-B", "DLY-A", "DLYMIX", "MIX"],
        ),
        "BNDDLY": AlgorithmMeta(
            description="Delays are followed by user selectable modulated filters.",
            display_names=["BNDDLY", "BANDDELAY", "BAND DELAY"],
            knobs=["FILTER", "SPEED", "DEPTH", "RESNCE", "FBK-B", "FBK-A", "DLY-B", "DLY-A", "DLYMIX", "MIX"],
        ),
        "FLTDLY": AlgorithmMeta(
            description="Dual delays ping pong between outputs with filter effects added.",
            display_names=["FLTDLY", "FILTERPONG", "FILTER PONG"],
            knobs=["FILTER", "SPEED", "DEPTH", "SHAPE", "SLUR", "FBK-A", "DLY-B", "DLY-A", "DLYMIX", "MIX"],
        ),
        "MULTAP": AlgorithmMeta(
            description="10 delay taps with controls for delay time, diffusion, tap levels and tap spacing.",
            display_names=["MULTAP", "MULTITAP", "MULTI TAP"],
            knobs=["FILTER", "SPREAD", "TAPER", "SLUR", "FBK-B", "FBK-A", "DLY-B", "DLY-A", "DLYMIX", "MIX"],
        ),
        "REVERS": AlgorithmMeta(
            description="Reverse audio effects. Audio is broken into segments, played backwards and spliced.",
            display_names=["REVERS", "REVERSE", "REVERSE DELAY"],
            knobs=["FILTER", "SPEED", "DEPTH", "XFADE", "FBK-B", "FBK-A", "DLY-B", "DLY-A", "DLYMIX", "MIX"],
        ),

        # --- H9 EXCLUSIVE (Category 5) ---
        "ULTRA.T": AlgorithmMeta(
            description="Versatile multi-tap delay for rhythmic patterns and volume swells.",
            display_names=["ULTRA.T", "ULTRATAP", "ULTRA TAP"],
            knobs=["RELEASE", "CHOP", "SLURM", "TONE", "TAPER", "SPREAD", "PREDLY", "TAPS", "LENGTH", "MIX"],
        ),
        "RESNTR": AlgorithmMeta(
            description="Staggers 4 resonant comb filters to create ambient, arpeggiated, or reverberant sounds.",
            display_names=["RESNTR", "RESONATOR"],
            knobs=["NOTE4", "NOTE3", "NOTE2", "NOTE1", "REVERB", "RESNCE", "FDBCK", "RHYTHM", "LENGTH", "MIX"],
        ),
        "EQCOMP": AlgorithmMeta(
            description="Multi-featured parametric equalizer coupled with a dynamic, intuitive compressor.",
            display_names=["EQCOMP", "EQ COMPRESSOR"],
            knobs=["TRIM", "COMP", "TREBLE", "BASS", "WIDTH2", "FREQ2", "GAIN2", "WIDTH1", "FREQ1", "GAIN1"],
        ),
        "CRUSH": AlgorithmMeta(
            description="Overdrive/distortion command center with Eventide Harmonizer octaves and Sag control.",
            display_names=["CRUSH", "CRUSHSTATION"],
            knobs=["TREBLE", "MIDFRQ", "MIDS", "BASS", "GRIT", "OCTAVE", "SAG", "SSTAIN", "DRIVE", "MIX"],
        ),
        "SPCTME": AlgorithmMeta(
            description="Multi-effects algorithm combining Modulation, two Delays, and Reverb.",
            display_names=["SPCTME", "SPACETIME"],
            knobs=["FDBK", "DLY-B", "DLY-A", "DLYLVL", "COLOR", "DECAY", "VERB", "RATE", "MODAMT", "MIX"],
        ),
        "SCULPT": AlgorithmMeta(
            description="Multi-band Distortion with Envelope Follower Control Filters.",
            display_names=["SCULPT"],
            knobs=["ENVFLT", "FLTPST", "FLTPRE", "LOWBST", "COMP", "HDRIVE", "LDRIVE", "XOVER", "BANDMX", "MIX"],
        ),
        "PTCFUZ": AlgorithmMeta(
            description="Combines Fuzz, three Pitch Shifters, and two Delays.",
            display_names=["PTCFUZ", "PITCHFUZZ", "PITCH FUZZ"],
            knobs=["FDBK", "DLY-B", "DLY-A", "DLYLVL", "PTCH-C", "PTCH-B", "PTCH-A", "PEACH", "FZTONE", "FUZZ"],
        ),
        "HOTSAW": AlgorithmMeta(
            description="Subtractive synthesis using saw waves with LFO, Envelope Follower, and Gate modulation.",
            display_names=["HOTSAW", "HOTSAWZ"],
            knobs=["RANGE", "DECAY", "ATTACK", "LFOAMT", "LFO", "RAMPUP", "RESNCE", "CUTOFF", "OSCDEP", "ALLMIX"],
        ),
        "HRMDLO": AlgorithmMeta(
            description="Harmadillo is a flexible tremolo algorithm with a wide range of sounds from classic lush tremolo to rhythmic, psychedelic, and even vocal-like sounds.",
            display_names=["HRMDLO", "HARMADILLO"],
            knobs=["DRIVE", "TONE", "SHAPE", "WIDTH", "XOVER", "LFOAMT", "RATE", "DEPTH", "SENS", "MIX"],
        ),
        "TRICER": AlgorithmMeta(
            description="TriceraChorus is a triple-voice bucket-brigade style chorus based on the classic choruses of the 1970s and 80s.",
            display_names=["TRICER", "TRICERACHORUS"],
            knobs=["FILTER", "PITCH", "DELAY", "ENVAMT", "ENVSNS", "MODAMT", "RATE", "DETUNE", "CHORUS", "MIX"],
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
