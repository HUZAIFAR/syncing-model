"""Shared helpers: dataset discovery, text cleaning, timestamp I/O.

The corpus convention (see example_for_claude/):
    <folder>/ar <name>.txt        one recited line per line
    <folder>/ar-sync <name>.txt   one HH.MM.SS.mmm marker per line, marking the
                                  END of the corresponding ar line
    <folder>/<name>.mp3           the recitation
"""

import os
import re
import glob
import subprocess
import tempfile

# ---------------------------------------------------------------- text

BIDI = re.compile(r"[‎‏‪-‮⁦-⁩﻿]")
TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
TATWEEL = "ـ"
# Stage directions such as (زائِر يها پوتانو نام لے) -- "visitor says their name
# here". These are instructions to the reader and are NOT recited.
PARENTHETICAL = re.compile(r"\([^)]*\)")
# '=' separates the two hemistichs of a line of poetry and appears in 26 of the
# source texts (312x in one file alone). It must split words, not fuse them:
# without this, "تَوَكُّلِي=وَبِالْخَمْسَةِ" romanises to one token instead of two.
PUNCT = re.compile(r"[*٭•.,;:!?\[\]«»\"'،؛؟÷=\-]")


def clean_line(s, drop_parentheticals=True):
    """Strip everything the reciter does not voice. Keeps tashkeel, which the
    romanizer needs (and which this corpus has on ~100% of lines)."""
    s = BIDI.sub("", s)
    if drop_parentheticals:
        s = PARENTHETICAL.sub(" ", s)
    s = s.replace(TATWEEL, "")
    s = PUNCT.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def weight(s):
    """Rough spoken-length proxy: undiacritized, unspaced character count.
    Correlates r=+0.95 with real line duration across the reference corpus."""
    return len(TASHKEEL.sub("", clean_line(s)).replace(" ", ""))


# ------------------------------------------------------------ timestamps

def parse_ts(t):
    """'00.05.33.752' -> 333.752"""
    h, m, s, ms = t.strip().replace("\r", "").split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def fmt_ts(sec):
    """333.752 -> '00.05.33.752'  (the exact format Audacity round-trips here)"""
    if sec < 0:
        sec = 0.0
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}.{m:02d}.{s:02d}.{ms:03d}"


def read_lines(path):
    with open(path, encoding="utf-8") as fh:
        return [l for l in fh.read().splitlines() if l.strip()]


# -------------------------------------------------------------- dataset

ARABIC_CH = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
TS_LINE = re.compile(r"^\s*\d{1,2}[.:]\d{2}[.:]\d{2}[.:]\d{1,3}\s*$")
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".m4b", ".aac", ".flac", ".ogg", ".opus",
             ".aif", ".aiff", ".wma", ".mp4")


# Letters used in Lisan ud-Dawat / Urdu written in Arabic script but absent from
# classical Arabic. A Lisan translation scores ~8-13% on these and carries
# almost no tashkeel; the vocalised Arabic source scores <=0.1% with >=40%.
URDU_CH = re.compile(r"[ےۓںٹڈڑژھہۂۃۀگچپ]")
TASHKEEL_CH = re.compile(r"[ً-ْٰ]")
# Filenames that mark a companion file (translation, transliteration), never a
# recitation source -- even when written in Arabic script, like "dz_translation".
COMPANION_NAME = re.compile(r"(^|[\W_])(dz|translation|translit|lisan|urdu|guj)", re.I)


def script_profile(path):
    """(share of Urdu/Lisan-only letters, share of tashkeel) among Arabic chars."""
    try:
        t = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return 0.0, 0.0
    n = max(1, len(ARABIC_CH.findall(t)))
    return len(URDU_CH.findall(t)) / n, len(TASHKEEL_CH.findall(t)) / n


def is_lisan(path):
    urdu, tash = script_profile(path)
    return urdu > 0.02 and tash < 0.15


def is_companion_name(name):
    return bool(COMPANION_NAME.search(os.path.splitext(os.path.basename(name))[0]))


def source_rank(path):
    """Sort key for choosing the recited text: real Arabic before Lisan, an
    'ar'-named file before others, then most Arabic-script."""
    base = os.path.basename(path).lower()
    return (not is_lisan(path), base.startswith("ar"), arabic_ratio(path))


def arabic_ratio(path):
    """Fraction of non-space characters that are Arabic script."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            s = "".join(fh.read().split())
    except OSError:
        return 0.0
    return len(ARABIC_CH.findall(s)) / len(s) if s else 0.0


LABEL_LINE = re.compile(r"^-?\d+(\.\d+)?\t-?\d+(\.\d+)?\t")


def is_label_file(path):
    """True for an Audacity label track we generated (start<TAB>end<TAB>text).

    These must be excluded from Arabic-source detection: a label line carries
    the line's opening words, so the file can score high enough on the
    Arabic-script test to look like a source text on a later run.
    """
    try:
        lines = [l for l in open(path, encoding="utf-8", errors="ignore")
                 .read().splitlines() if l.strip()]
    except OSError:
        return False
    return bool(lines) and all(LABEL_LINE.match(l) for l in lines)


def is_sync_file(path):
    """A sync file is one whose every non-empty line is a timestamp. Detected by
    content rather than filename, so naming doesn't have to be consistent."""
    try:
        lines = [l for l in open(path, encoding="utf-8", errors="ignore")
                 .read().splitlines() if l.strip()]
    except OSError:
        return False
    return bool(lines) and all(TS_LINE.match(l) for l in lines)


def find_item(folder):
    """Locate the (audio, arabic, sync) triple in one folder. sync may be None.

    Identification is by content, not filename: the sync file is the one made
    entirely of timestamps, and the Arabic source is whichever remaining text
    file is actually Arabic script. Transliteration and translation files score
    near zero on that test, so they're excluded without needing a naming rule.
    """
    txts = glob.glob(os.path.join(folder, "*.txt"))
    audio = sorted(p for p in glob.glob(os.path.join(folder, "*"))
                   if p.lower().endswith(AUDIO_EXT))

    sync = next((t for t in txts if is_sync_file(t)), None)
    # Exclude our own outputs and named companions (a Lisan "dz_translation" is
    # Arabic script but is not what is recited), then prefer the real Arabic.
    candidates = [t for t in txts
                  if t != sync and not is_label_file(t)
                  and not is_companion_name(t) and arabic_ratio(t) > 0.5]
    ar = max(candidates, key=source_rank) if candidates else None

    if not audio or not ar:
        return None
    # Door Doa ships the same recitation twice (ar.mp3 == darwaza p3-5.mp3).
    # Prefer the descriptively-named one; bare "ar.mp3" is the duplicate.
    pick = next((m for m in audio
                 if os.path.basename(m).lower() != "ar.mp3"), audio[0])
    return {"folder": folder, "audio": pick, "ar": ar, "sync": sync}


def output_stem(item):
    """Name for the generated sync file.

    'ar salaam 3.txt' -> 'salaam 3', matching the existing corpus convention.
    Anything that isn't an 'ar'-prefixed name falls back to the folder name,
    so 'arabic (1).txt' in a 'testing' folder yields 'ar-sync testing.txt'
    rather than the mangled 'ar-sync abic (1).txt'.
    """
    base = os.path.splitext(os.path.basename(item["ar"]))[0]
    m = re.match(r"^ar[-_. ]+(.+)$", base, re.I)
    stem = m.group(1).strip() if m else ""
    if not stem:
        stem = os.path.basename(item["folder"].rstrip("/\\"))
    return re.sub(r"\s+", " ", stem).strip() or "output"


SKIP_DIRS = {"__pycache__", "node_modules"}


def discover(root):
    """Find every recitation at any depth under root, including root itself.

    Nesting is unrestricted -- inputs/Ramadan/Night 3/Doa can sit alongside
    inputs/Salaam 10 and both are found. A folder qualifies as a recitation as
    soon as it directly contains an audio file and an Arabic text file.
    """
    root = os.path.abspath(root)
    items = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and d not in SKIP_DIRS)
        it = find_item(dirpath)
        if it:
            items.append(it)
    return sorted(items, key=lambda i: i["folder"].lower())


# ---------------------------------------------------------------- audio

def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def decode_16k(path):
    """Decode to 16 kHz mono float32 numpy array via ffmpeg."""
    import numpy as np

    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tf:
        tmp = tf.name
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", path,
             "-ac", "1", "-ar", "16000", "-f", "f32le", tmp],
            check=True, capture_output=True,
        )
        return np.fromfile(tmp, dtype="<f4")
    finally:
        os.unlink(tmp)
