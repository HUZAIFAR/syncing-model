"""Cache alignments once, then compare marker-placement strategies instantly.

The aligner tells us when each line's speech starts and ends. Where a human
puts the marker is a separate question -- they click somewhere in the pause
after the line. This finds which rule best reproduces the reference markers.

    python experiment.py            # build cache (slow) then report
    python experiment.py --report   # report only, from cache
"""

import os
import sys

import numpy as np

import align as A
import smlib as S

CACHE = "alignment_cache.npz"


def build(root="example_for_claude"):
    out = {}
    for it in S.discover(root):
        if not it["sync"]:
            continue
        name = it["folder"].rstrip("/").split("/")[-1]
        lines = S.read_lines(it["ar"])
        gt = np.array([S.parse_ts(t) for t in S.read_lines(it["sync"])])
        if len(lines) != len(gt):
            continue
        r = A.line_spans(it["audio"], lines)
        out[f"{name}|start"] = r["start"]
        out[f"{name}|end"] = r["end"]
        out[f"{name}|conf"] = r["conf"]
        out[f"{name}|gap_lo"] = r["gap_lo"]
        out[f"{name}|gap_hi"] = r["gap_hi"]
        out[f"{name}|gt"] = gt
        out[f"{name}|total"] = np.array([r["total"]])
        print(f"  cached {name} ({len(lines)} lines)")
    np.savez(CACHE, **out)
    print(f"wrote {CACHE}")


def load():
    z = np.load(CACHE)
    names = sorted({k.split("|")[0] for k in z.files})
    fields = ("start", "end", "conf", "gt", "total", "gap_lo", "gap_hi")
    return {n: {f: z[f"{n}|{f}"] for f in fields if f"{n}|{f}" in z.files}
            for n in names}


def strategies(d):
    """Candidate marker positions for each line."""
    end, start, total = d["end"], d["start"], float(d["total"][0])
    nxt = np.append(start[1:], total)          # next line's speech onset
    nxt = np.maximum(nxt, end)                 # guard against overlap
    out = {
        "word_end": end,
        "token_25": end + 0.25 * (nxt - end),
        "token_mid": end + 0.50 * (nxt - end),
        "token_75": end + 0.75 * (nxt - end),
        "next_start": nxt,
    }
    if "gap_lo" in d:                          # true silence, from audio energy
        out["pause_mid"] = np.append(0.5 * (d["gap_lo"] + d["gap_hi"]), total)
    return out


def report():
    data = load()
    keep = {}
    for n, d in data.items():
        bad = np.zeros(len(d["gt"]), bool)
        bad[np.where(np.diff(d["gt"]) <= 0)[0]] = True   # Salaam 8 duplicate
        keep[n] = ~bad

    names = list(data)
    print(f"{'strategy':<12}{'offset':>9}{'med|e|':>9}{'p90':>9}{'p99':>9}"
          f"{'max':>9}{'<0.25s':>9}{'<0.5s':>8}")
    best = None
    for s in strategies(data[names[0]]):
        resid = np.concatenate([(strategies(data[n])[s] - data[n]["gt"])[keep[n]]
                                for n in names])
        off = np.median(resid)
        a = np.abs(resid - off)
        row = (np.median(a), np.percentile(a, 90), np.percentile(a, 99), a.max(),
               100 * (a < .25).mean(), 100 * (a < .5).mean())
        print(f"{s:<12}{off:>+9.3f}{row[0]:>9.3f}{row[1]:>9.3f}{row[2]:>9.3f}"
              f"{row[3]:>9.3f}{row[4]:>8.1f}%{row[5]:>7.1f}%")
        if best is None or row[0] < best[1]:
            best = (s, row[0], off)
    print(f"\nbest = {best[0]}  (median |err| {best[1]:.3f}s, offset {best[2]:+.3f}s)")

    # leave-one-file-out: does the offset generalise across recitations?
    s = best[0]
    errs = []
    for n in names:
        others = np.concatenate([(strategies(data[m])[s] - data[m]["gt"])[keep[m]]
                                 for m in names if m != n])
        off = np.median(others)
        e = np.abs((strategies(data[n])[s] - data[n]["gt"])[keep[n]] - off)
        errs.append(e)
        print(f"  {n:<20} med|e|={np.median(e):.3f}s  p90={np.percentile(e,90):.3f}s  "
              f"max={e.max():.3f}s")
    a = np.concatenate(errs)
    print(f"\nLEAVE-ONE-OUT ({s}), n={len(a)}")
    print(f"  median {np.median(a):.3f}s   p90 {np.percentile(a,90):.3f}s   "
          f"p99 {np.percentile(a,99):.3f}s   max {a.max():.3f}s")
    for th in (0.1, 0.25, 0.5, 1.0, 2.0):
        print(f"  within {th:>4}s : {100*(a<th).mean():5.1f}%")
    return best


if __name__ == "__main__":
    if "--report" not in sys.argv:
        build(*[a for a in sys.argv[1:] if not a.startswith("-")])
    report()
