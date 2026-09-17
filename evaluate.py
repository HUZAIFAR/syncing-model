"""Validate the aligner against the 320 hand-placed markers in the reference
corpus, and derive the click-offset calibration.

Usage:  python evaluate.py [corpus_root]
Writes eval_results.json with per-line residuals.
"""

import json
import sys
import time

import numpy as np

import align as A
import smlib as S


def main(root="example_for_claude", device="cpu"):
    items = [it for it in S.discover(root) if it["sync"]]
    rows, per_folder = [], []
    t0 = time.time()

    for it in items:
        name = it["folder"].rstrip("/").split("/")[-1]
        lines = S.read_lines(it["ar"])
        gt = np.array([S.parse_ts(t) for t in S.read_lines(it["sync"])])
        if len(lines) != len(gt):
            print(f"  !! {name}: {len(lines)} lines vs {len(gt)} markers -- skipped")
            continue

        t = time.time()
        ends, conf = A.line_boundaries(it["audio"], lines, device=device)
        dt = time.time() - t
        d = ends - gt

        # Salaam 8 has a duplicated marker (lines 25/26 share a timestamp);
        # that boundary is not recoverable ground truth, so exclude it.
        bad = np.zeros(len(gt), bool)
        dup = np.where(np.diff(gt) <= 0)[0]
        bad[dup] = True

        for i in range(len(gt)):
            rows.append({
                "folder": name, "line": i + 1,
                "pred": float(ends[i]), "truth": float(gt[i]),
                "resid": float(d[i]), "conf": float(conf[i]),
                "excluded": bool(bad[i]),
            })
        keep = d[~bad]
        per_folder.append((name, len(gt), np.median(keep), np.median(np.abs(keep)),
                           np.percentile(np.abs(keep), 90), np.abs(keep).max(), dt))
        print(f"  {name:<20} n={len(gt):<3} med_signed={np.median(keep):+.3f}s  "
              f"med|e|={np.median(np.abs(keep)):.3f}s  p90={np.percentile(np.abs(keep),90):.3f}s  "
              f"max={np.abs(keep).max():.3f}s  ({dt:.0f}s)")

    resid = np.array([r["resid"] for r in rows if not r["excluded"]])
    offset = float(np.median(resid))

    print(f"\n{'='*78}\nRAW (no calibration)   n={len(resid)}")
    _report(resid)
    print(f"\nCLICK OFFSET (median residual) = {offset:+.3f}s")
    print("  -> you click this much AFTER the last word's audio ends"
          if offset < 0 else
          "  -> model runs this much late vs your clicks")
    print(f"\nCALIBRATED (shift predictions by {-offset:+.3f}s)")
    _report(resid - offset)
    print(f"\ntotal wall time {time.time()-t0:.0f}s")

    json.dump({"offset": offset, "rows": rows}, open("eval_results.json", "w"), indent=1)
    print("wrote eval_results.json")


def _report(e):
    a = np.abs(e)
    print(f"  median |err| = {np.median(a):.3f}s    mean = {a.mean():.3f}s")
    print(f"  p90 = {np.percentile(a,90):.3f}s   p99 = {np.percentile(a,99):.3f}s   max = {a.max():.3f}s")
    for th in (0.1, 0.25, 0.5, 1.0, 2.0):
        print(f"  within {th:>4}s : {100*(a<th).mean():5.1f}%")


if __name__ == "__main__":
    main(*sys.argv[1:])
