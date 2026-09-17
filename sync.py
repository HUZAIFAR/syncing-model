#!/usr/bin/env python
"""Generate Audacity-style line markers for Arabic recitations.

For each folder containing an audio file plus a line-per-marker Arabic text,
this writes an `ar-sync <name>.txt` in the project's HH.MM.SS.mmm format --
one timestamp per line, marking where that line finishes.

    python sync.py "path/to/folder"          # one recitation
    python sync.py "path/to/parent"          # every recitation beneath it
    python sync.py PATH --audacity           # also emit an Audacity label track
    python sync.py PATH --force              # overwrite existing sync files

Existing sync files are never overwritten without --force.
"""

import argparse
import json
import os
import sys
import time

import numpy as np

import align as A
import smlib as S

HERE = os.path.dirname(os.path.abspath(__file__))
CALIB = os.path.join(HERE, "calibration.json")

# Fallbacks if calibration.json is absent.
DEFAULT_STRATEGY = "pause_mid"   # exact centre of the real silence between lines
DEFAULT_OFFSET = 0.0             # the true midpoint needs no correction
# A line scoring below this share of the file's OWN median confidence is
# suspect. Relative rather than absolute, so it adapts to reciter and recording.
CONF_RATIO = 0.55
CONF_CEILING = 0.40        # never flag a line scoring at least this well
# A suspect run at least this long means the text for those lines is not in the
# audio at all, rather than one marker being slightly out.
MISMATCH_RUN = 3


def load_calibration(cli_offset, cli_strategy):
    offset, strategy, src = DEFAULT_OFFSET, DEFAULT_STRATEGY, "default"
    if os.path.exists(CALIB):
        with open(CALIB) as fh:
            c = json.load(fh)
        offset = c.get("offset", offset)
        strategy = c.get("strategy", strategy)
        src = f"calibration.json (n={c.get('n','?')})"
    if cli_offset is not None:
        offset, src = cli_offset, "command line"
    if cli_strategy is not None:
        strategy, src = cli_strategy, "command line"
    return offset, strategy, src


def diagnose(conf, markers):
    """Group low-confidence lines into runs.

    An isolated dip means one marker may be off. A long contiguous run means
    the text for those lines does not correspond to that stretch of audio --
    a wrong, truncated, or pasted-in section. Distinguishing the two is the
    difference between "check this marker" and "your text is wrong".
    """
    if len(conf) == 0:
        return []
    med = float(np.median(conf))
    # Relative to this file's own norm, but never flag a line whose absolute
    # score is healthy -- a crisp recording shouldn't get pickier about itself.
    thresh = min(CONF_RATIO * med, CONF_CEILING) if med > 0 else CONF_CEILING
    suspect = [i for i, c in enumerate(conf) if c < thresh]
    runs = []
    for i in suspect:
        if runs and i - runs[-1][-1] <= 2:      # bridge single-line recoveries
            runs[-1].append(i)
        else:
            runs.append([i])
    out = []
    for r in runs:
        lo, hi = r[0], r[-1]
        t0 = markers[lo - 1] if lo else 0.0
        out.append({"lo": lo + 1, "hi": hi + 1, "n": len(r),
                    "t0": t0, "t1": markers[hi],
                    "mismatch": len(r) >= MISMATCH_RUN,
                    "conf": float(np.mean([conf[i] for i in r]))})
    return out


def audacity_labels(ends, lines):
    """Label track: each line spans from the previous marker to its own."""
    out = []
    prev = 0.0
    for i, (e, ln) in enumerate(zip(ends, lines), 1):
        text = S.clean_line(ln)
        words = text.split()
        short = " ".join(words[:5]) + ("…" if len(words) > 5 else "")
        out.append(f"{prev:.6f}\t{e:.6f}\t{i}. {short}")
        prev = e
    # no trailing newline, matching the ar-sync convention
    return "\n".join(out)


def process(item, offset, args, root=None):
    # Show the path relative to what was scanned, so nested folders are
    # distinguishable ("Ramadan/Night 3" rather than just "Night 3").
    name = item["folder"].rstrip("/").split("/")[-1]
    if root:
        rel = os.path.relpath(item["folder"], os.path.abspath(root))
        if rel not in (".", ""):
            name = rel
    lines = S.read_lines(item["ar"])

    stem = S.output_stem(item)
    out_path = os.path.join(item["folder"], f"ar-sync {stem}.txt")

    # A folder that already has a sync file is left completely alone. Skipping
    # is the protection -- writing a second differently-named file would just
    # leave two candidate answers side by side.
    if item["sync"] and not args.force:
        print(f"  {name:<24} SKIP -- already synced "
              f"({os.path.basename(item['sync'])})")
        return None

    t = time.time()
    ends, conf = A.line_boundaries(item["audio"], lines, offset=offset,
                                   strategy=args.strategy, device=args.device)

    # No trailing newline: the file must end with the last timestamp, so the
    # last line is the last marker and nothing reads a blank line after it.
    # This also matches the hand-made reference files, which end on a digit.
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(S.fmt_ts(e) for e in ends))

    wrote = [os.path.basename(out_path)]
    if args.audacity:
        lab = os.path.join(item["folder"], f"{stem.strip()} labels.txt")
        with open(lab, "w", encoding="utf-8") as fh:
            fh.write(audacity_labels(ends, lines))
        wrote.append(os.path.basename(lab))

    probs = diagnose(conf, ends)
    bad = [p for p in probs if p["mismatch"]]
    flag = ""
    if bad:
        flag = f"  ⚠ TEXT DOESN'T MATCH AUDIO ({sum(p['n'] for p in bad)} lines)"
    elif probs:
        flag = f"  ⚠ check line(s) {[p['lo'] for p in probs]}"
    print(f"  {name:<22} {len(lines):>3} markers  {time.time()-t:>5.0f}s  "
          f"→ {', '.join(wrote)}{flag}")
    return {"folder": name, "n": len(lines), "problems": probs}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="a recitation folder, or a parent of several")
    ap.add_argument("--force", action="store_true", help="overwrite existing sync files")
    ap.add_argument("--audacity", action="store_true",
                    help="also write an importable Audacity label track")
    ap.add_argument("--offset", type=float, default=None,
                    help="seconds to subtract from every marker (default 0)")
    ap.add_argument("--strategy", default=None,
                    choices=["pause_mid", "token_mid", "next_start"],
                    help="where in the gap the marker sits; default pause_mid "
                         "= exact centre of the real silence between lines")
    ap.add_argument("--device", default=None, choices=["cpu", "mps"],
                    help="compute device; defaults to the Apple GPU when available "
                         "(~2x faster, verified bit-identical to CPU)")
    args = ap.parse_args()

    if not os.path.exists(args.path):
        sys.exit(f"no such path: {args.path}")

    items = S.discover(args.path)
    if not items:
        sys.exit(f"found no (audio + 'ar ...txt') pair under {args.path}")

    if args.device is None:
        # The GPU roughly halves runtime on this workload and was verified to
        # produce bit-identical markers, so prefer it when present.
        import torch
        args.device = "mps" if torch.backends.mps.is_available() else "cpu"

    offset, args.strategy, src = load_calibration(args.offset, args.strategy)
    print(f"placement: {args.strategy}  offset {offset:+.3f}s  [{src}]"
          f"   device: {args.device}")
    print(f"{len(items)} recitation(s) found\n")

    results = [r for r in (process(it, offset, args, root=args.path)
                           for it in items) if r]
    print(f"\ndone: {sum(r['n'] for r in results)} markers "
          f"across {len(results)} recitation(s)")

    def clock(x):
        return f"{int(x)//60}:{int(x)%60:02d}"

    mismatched = [r for r in results if any(p["mismatch"] for p in r["problems"])]
    minor = [r for r in results
             if r not in mismatched and r["problems"]]
    if mismatched:
        print("\n" + "=" * 62)
        print("TEXT / AUDIO MISMATCH -- these markers are NOT usable")
        print("=" * 62)
        for r in mismatched:
            print(f"\n  {r['folder']}")
            for p in r["problems"]:
                if not p["mismatch"]:
                    continue
                print(f"    lines {p['lo']}-{p['hi']} ({p['n']} lines), "
                      f"audio {clock(p['t0'])}-{clock(p['t1'])}")
            print("    The text for those lines does not appear in that part of")
            print("    the audio. Usually the text file has a wrong, truncated or")
            print("    pasted-in section. Listen at the times above and compare")
            print("    against the text; markers outside the range are fine.")
    if minor:
        print("\nworth a spot-check (single lines, probably just imprecise):")
        for r in minor:
            print(f"  {r['folder']}: line(s) {[p['lo'] for p in r['problems']]}")


if __name__ == "__main__":
    main()
