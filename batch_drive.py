#!/usr/bin/env python
"""Batch-sync a Google Drive folder of recitations.

Runs in two phases so nothing unverified reaches Drive:

  build   align every recitation, write results to a local staging directory,
          and record a pass/fail verdict for each
  upload  copy ONLY the passing results into their Drive folders

A folder may hold one recitation (audio + Arabic text) or several side by side
("pages 132-144"); in the latter case files are paired by filename.
"""

import difflib
import json
import os
import re
import shutil
import sys
import time
import traceback

import numpy as np

import align as A
import smlib as S
import sync as SY

# Google Drive streams files on demand. Reading an mp3 straight off the mount
# can hand ffmpeg a PARTIAL stream, which decodes as a silently shorter audio
# and shifts every marker. Observed on 7 of 49 files, up to 3.6s short. So each
# file is copied to local disk and byte-verified before it is ever decoded.
CACHE = os.path.join(os.environ.get("TMPDIR", "/tmp"), "drive_audio_cache")

# Point this at the Drive folder to process, e.g.
#   export SYNC_DRIVE_ROOT="$HOME/Library/CloudStorage/GoogleDrive-<you>/My Drive/<folder>"
DRIVE = os.environ.get("SYNC_DRIVE_ROOT", "")
STAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".drive_stage")
MANIFEST = os.path.join(STAGE, "manifest.json")


def keyname(s):
    """Filename reduced to its distinguishing words, for pairing audio to text."""
    s = os.path.splitext(s)[0].lower()
    s = re.sub(r"\b(final|selected|edited|kar|en|translit|translation|page|pg)\b",
               " ", s)
    s = re.sub(r"\(\d+\)", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def survey():
    """Enumerate every (audio, arabic-text) recitation under DRIVE."""
    todo, skipped = [], []
    for folder in sorted(os.listdir(DRIVE)):
        p = os.path.join(DRIVE, folder)
        if not os.path.isdir(p):
            continue
        files = [f for f in sorted(os.listdir(p))
                 if os.path.isfile(os.path.join(p, f)) and not f.startswith(".")]
        audio = [f for f in files if f.lower().endswith(S.AUDIO_EXT)]
        txts = [f for f in files if f.lower().endswith(".txt")]
        syncs = [f for f in txts if S.is_sync_file(os.path.join(p, f))]
        arabic = [f for f in txts
                  if f not in syncs
                  and not S.is_label_file(os.path.join(p, f))
                  and not S.is_companion_name(f)
                  and S.arabic_ratio(os.path.join(p, f)) > 0.5]
        # One audio file means one recitation. If several Arabic-script texts
        # remain, keep only the best source rather than treating the folder as
        # a bundle -- otherwise a Lisan translation gets aligned to the audio.
        if len(audio) == 1 and len(arabic) > 1:
            arabic = [max(arabic, key=lambda f: S.source_rank(os.path.join(p, f)))]

        if syncs:
            skipped.append((folder, "already synced on Drive"))
            continue
        if not audio:
            skipped.append((folder, "no audio file"))
            continue
        if not arabic:
            skipped.append((folder, "no Arabic text"))
            continue

        multi = len(arabic) > 1
        used = set()
        for ar in arabic:
            ranked = sorted(
                ((difflib.SequenceMatcher(None, keyname(ar), keyname(a)).ratio(), a)
                 for a in audio), reverse=True)
            pick = next((a for _, a in ranked if a not in used), ranked[0][1])
            sim = dict((a, r) for r, a in ranked)[pick]
            if multi:
                used.add(pick)
            # Bundle folders hold several recitations, so the output is named
            # after the audio; single-recitation folders keep the folder name,
            # matching the ar-sync files already on Drive.
            stem = os.path.splitext(pick)[0] if multi else folder
            # folder/file names carry stray trailing spaces and backslashes
            # ("STS Shudaa Salaam pg 170\\", "aale imran page 156 ")
            stem = re.sub(r"[\\/\s]+$", "", stem)
            todo.append({
                "slug": re.sub(r"[^\w .+-]", "_",
                               f"{folder}__{os.path.splitext(pick)[0]}" if multi
                               else folder),
                "folder": folder, "audio": pick, "arabic": ar,
                "stem": stem, "pair_sim": round(sim, 3), "multi": multi,
            })
    return todo, skipped


def localise(src):
    """Copy a Drive file to local disk and verify it arrived whole."""
    os.makedirs(CACHE, exist_ok=True)
    want = os.path.getsize(src)
    dst = os.path.join(CACHE, f"{abs(hash(src)) % 10**12}_{os.path.basename(src)}")
    if not (os.path.exists(dst) and os.path.getsize(dst) == want):
        shutil.copy2(src, dst)
    got = os.path.getsize(dst)
    if got != want:
        raise IOError(f"incomplete copy from Drive: {got:,} of {want:,} bytes")
    return dst


def build(only=None):
    """Align everything ready, or -- when `only` is given -- just those folders.

    Pass folder names to restrict the run. Existing recitations are never
    reprocessed anyway, but an explicit list makes "sync exactly these" safe.
    """
    if os.path.exists(STAGE):
        shutil.rmtree(STAGE)
    os.makedirs(STAGE)
    todo, skipped = survey()
    if only:
        wanted = {o.lower() for o in only}
        missed = wanted - {r["folder"].lower() for r in todo}
        todo = [r for r in todo if r["folder"].lower() in wanted]
        for m in sorted(missed):
            why = next((w for f, w in skipped if f.lower() == m), "not found / not ready")
            print(f"  !! requested but not processed: {m}  ({why})")
    offset, strategy, _ = SY.load_calibration(None, None)
    import torch
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"{len(todo)} recitations to align | {strategy} offset {offset:+.3f} "
          f"| device {device}\n")

    results = []
    t0 = time.time()
    for i, r in enumerate(todo, 1):
        src = os.path.join(DRIVE, r["folder"])
        audio, artxt = os.path.join(src, r["audio"]), os.path.join(src, r["arabic"])
        rec = dict(r, status="ok", problems=[], note="")
        try:
            artxt = localise(artxt)
            audio = localise(audio)
            lines = S.read_lines(artxt)
            if not lines:
                raise ValueError("Arabic text file is empty")
            dur = S.duration(audio)
            # cross-check the container header against the real decoded length
            decoded = len(S.decode_16k(audio)) / 16000.0
            if abs(decoded - dur) > 0.05:
                raise IOError(f"audio truncated: header says {dur:.2f}s, "
                              f"decoded {decoded:.2f}s")
            sp = A.line_spans(audio, lines, device=device)
            marks = A.place_markers(sp, offset, strategy)
            probs = SY.diagnose(sp["conf"], marks)

            # --- validation gate -------------------------------------------
            fails = []
            if len(marks) != len(lines):
                fails.append(f"{len(marks)} markers for {len(lines)} lines")
            if not np.all(np.diff(marks) > 0) and len(marks) > 1:
                fails.append("markers not strictly increasing")
            if abs(marks[-1] - dur) > 0.05:
                fails.append("last marker is not the end of the audio")
            # A line the reciter never says still has to be placed somewhere,
            # so the aligner collapses it to near-zero duration. Catch that:
            # an implied speaking rate far above the file's own median means
            # the text contains a line that is not in the audio.
            span = np.diff(np.concatenate([[0.0], marks]))
            wt = np.array([max(1, S.weight(l)) for l in lines], float)
            rate = wt / np.maximum(span, 0.01)
            med_rate = float(np.median(rate))
            ghosts = [i + 1 for i, rt in enumerate(rate)
                      if rt > 4 * med_rate and span[i] < 1.0]
            if ghosts:
                fails.append("line(s) not present in the audio (collapsed to ~0s): "
                             + ", ".join(str(g) for g in ghosts))
            if any(p["mismatch"] for p in probs):
                bad = [p for p in probs if p["mismatch"]]
                fails.append("text/audio mismatch: " + "; ".join(
                    f"lines {p['lo']}-{p['hi']} at "
                    f"{int(p['t0'])//60}:{int(p['t0'])%60:02d}-"
                    f"{int(p['t1'])//60}:{int(p['t1'])%60:02d}" for p in bad))

            out = os.path.join(STAGE, r["slug"])
            os.makedirs(out, exist_ok=True)
            # No trailing newline: last line must be the last marker.
            with open(os.path.join(out, f"ar-sync {r['stem']}.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("\n".join(S.fmt_ts(m) for m in marks))
            with open(os.path.join(out, f"{r['stem']} labels.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write(SY.audacity_labels(marks, lines))

            rec.update(lines=len(lines), dur=round(dur, 1),
                       rate=round(sum(S.weight(l) for l in lines) / dur, 2),
                       conf=round(float(np.median(sp["conf"])), 3),
                       problems=probs,
                       status="ok" if not fails else "failed",
                       note="; ".join(fails))
        except Exception as e:
            rec.update(status="error", note=f"{type(e).__name__}: {e}")
            traceback.print_exc(limit=1)

        results.append(rec)
        mark = {"ok": "ok  ", "failed": "FAIL", "error": "ERR "}[rec["status"]]
        print(f"  [{i:>2}/{len(todo)}] {mark} {r['slug'][:52]:<54} "
              f"{rec.get('lines','?'):>4} lines  {rec.get('conf','?')}"
              + (f"  {rec['note'][:60]}" if rec["note"] else ""))

    json.dump({"results": results, "skipped": skipped},
              open(MANIFEST, "w"), indent=1)
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\naligned {len(results)} in {time.time()-t0:.0f}s -- "
          f"{ok} pass, {len(results)-ok} held back")
    print(f"staged in {STAGE}; nothing copied to Drive yet")


# Label tracks are only a checking aid, so they are collected in one place
# rather than cluttering every recitation folder.
LABELS_DIR = "Audacity labels"


def label_target(folder, fname, drive_root):
    """Where a label file goes, disambiguated if the name already exists."""
    dest = os.path.join(drive_root, LABELS_DIR)
    cand = os.path.join(dest, fname)
    if os.path.exists(cand):
        cand = os.path.join(dest, f"{folder} - {fname}")
    return cand


def upload(dry=True):
    m = json.load(open(MANIFEST))
    good = [r for r in m["results"] if r["status"] == "ok"]
    if not dry:
        os.makedirs(os.path.join(DRIVE, LABELS_DIR), exist_ok=True)
    print(f"{'DRY RUN -- ' if dry else ''}copying {len(good)} results to Drive\n")
    n = 0
    for r in good:
        out = os.path.join(STAGE, r["slug"])
        dest = os.path.join(DRIVE, r["folder"])
        for f in sorted(os.listdir(out)):
            s = os.path.join(out, f)
            if f.endswith(" labels.txt"):
                d = label_target(r["folder"], f, DRIVE)
                where = f"{LABELS_DIR}/{os.path.basename(d)}"
            else:
                d = os.path.join(dest, f)
                where = f"{r['folder']}/{f}"
            exists = " (OVERWRITES existing)" if os.path.exists(d) else ""
            print(f"  -> {where[:70]}{exists}")
            if not dry:
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
                n += 1
    print(f"\n{'would copy' if dry else 'copied'} "
          f"{sum(len(os.listdir(os.path.join(STAGE,r['slug']))) for r in good)} files")


if __name__ == "__main__":
    if not DRIVE or not os.path.isdir(DRIVE):
        sys.exit("set SYNC_DRIVE_ROOT to the Drive folder you want to process, e.g.\n"
                 '  export SYNC_DRIVE_ROOT="$HOME/Library/CloudStorage/'
                 'GoogleDrive-you@example.com/My Drive/Recitations"')
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build(only=sys.argv[2:] or None)
    elif cmd == "dry":
        upload(dry=True)
    elif cmd == "upload":
        upload(dry=False)
    else:
        sys.exit("usage: batch_drive.py [build|dry|upload]")
