"""Forced alignment of Arabic recitation against a known line-by-line text.

Uses Meta's MMS forced-alignment model (torchaudio.pipelines.MMS_FA), which is
a multilingual CTC model operating on romanized text. Because we supply the
transcript, the model never has to *recognise* Arabic -- it only decides *when*
each token occurs. That makes accuracy largely independent of Arabic ASR quality.

The corpus text is fully vocalised (tashkeel on ~100% of lines), so uroman's
Arabic->Latin mapping is close to deterministic, which is the ideal input here.
"""

import numpy as np
import torch
import torchaudio

import smlib as S

_BUNDLE = torchaudio.pipelines.MMS_FA
_cache = {}


def _resources():
    if "model" not in _cache:
        _cache["model"] = _BUNDLE.get_model().eval()
        _cache["tokenizer"] = _BUNDLE.get_tokenizer()
        _cache["aligner"] = _BUNDLE.get_aligner()
        _cache["vocab"] = set(_BUNDLE.get_dict())
        import uroman
        _cache["uroman"] = uroman.Uroman()
    return _cache


def romanize(lines):
    """Arabic lines -> list of word-lists, restricted to the model's vocabulary."""
    r = _resources()
    vocab = r["vocab"] - {"*", "-"}
    out = []
    for ln in lines:
        rom = r["uroman"].romanize_string(S.clean_line(ln), lcode="ara").lower()
        words = []
        for w in rom.split():
            w = "".join(c for c in w if c in vocab)
            if w:
                words.append(w)
        out.append(words)
    return out


@torch.inference_mode()
def emissions(wave, chunk_s=40.0, overlap_s=4.0, device="cpu"):
    """CTC log-probs for the whole file, computed in overlapping windows.

    A single forward pass over 8 minutes would need gigabytes of self-attention
    memory, so we window it. Each window keeps only its central region; the
    overlap exists purely to give the edges real acoustic context.
    """
    r = _resources()
    model = r["model"].to(device)
    sr = _BUNDLE.sample_rate
    n = len(wave)
    chunk, ov = int(chunk_s * sr), int(overlap_s * sr)

    if n <= chunk:
        e, _ = model(torch.from_numpy(wave)[None].to(device))
        return e[0].cpu(), n / e.shape[1]

    step = chunk - ov
    starts = list(range(0, max(1, n - ov), step))
    parts, ratio = [], None
    for i, s in enumerate(starts):
        e_ = min(n, s + chunk)
        seg = wave[s:e_]
        if len(seg) < 400:
            break
        em, _ = model(torch.from_numpy(seg)[None].to(device))
        em = em[0].cpu()
        if ratio is None:
            ratio = len(seg) / em.shape[0]
        # keep only the region this window owns
        lo = 0 if i == 0 else int(round((ov / 2) / ratio))
        hi = em.shape[0] if e_ >= n else em.shape[0] - int(round((ov / 2) / ratio))
        parts.append(em[lo:hi])
    full = torch.cat(parts, dim=0)
    return full, n / full.shape[0]


def align_words(emission, words, blank_star=True):
    """Align a flat word list to an emission. Returns one span-list per word."""
    r = _resources()
    tokens = r["tokenizer"](words)
    return r["aligner"](emission, tokens)


def silence_gaps(wave, sr, end, start, total, search=0.45, hop=0.005):
    """Find the real silent stretch between each line and the next, from audio
    energy rather than from token boundaries.

    CTC alignment stretches a line's final token into the following silence, so
    `end[i]`..`start[i+1]` understates the true pause -- by that measure ~32% of
    gaps look shorter than 0.2s. Measuring loudness directly recovers the actual
    downtime, which is what a marker should sit in the middle of.

    Returns (lo, hi): the silent interval for each of the first n-1 boundaries.
    Where no silence is detectable (the reciter runs straight on) lo == hi, and
    the midpoint degenerates to the token boundary, which is the right answer.
    """
    n = len(end)
    win = max(1, int(round(hop * sr)))
    # frame-wise RMS in dB
    usable = (len(wave) // win) * win
    frames = wave[:usable].reshape(-1, win)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms)

    def f(t):
        return int(np.clip(round(t / hop), 0, len(db) - 1))

    # speech level = loudness while a line is actually being recited
    voiced = np.zeros(len(db), bool)
    for i in range(n):
        voiced[f(start[i]):f(end[i]) + 1] = True
    if not voiced.any():
        return np.zeros(n - 1), np.zeros(n - 1)
    speech_db = np.percentile(db[voiced], 50)
    floor_db = np.percentile(db, 5)
    # 22 dB below conversational level, but never below the file's own noise
    thresh = max(speech_db - 22.0, floor_db + 6.0)

    lo_out, hi_out = np.zeros(n - 1), np.zeros(n - 1)
    for i in range(n - 1):
        a, b = f(end[i] - search), f(min(start[i + 1] + search, total))
        if b <= a:
            lo_out[i] = hi_out[i] = end[i]
            continue
        quiet = db[a:b + 1] < thresh
        # longest contiguous quiet run inside the window
        best_len = best_lo = 0
        cur = 0
        for j, q in enumerate(quiet):
            cur = cur + 1 if q else 0
            if cur > best_len:
                best_len, best_lo = cur, j - cur + 1
        if best_len < 2:                       # nothing convincingly quiet
            lo_out[i] = hi_out[i] = 0.5 * (end[i] + start[i + 1])
        else:
            lo_out[i] = (a + best_lo) * hop
            hi_out[i] = (a + best_lo + best_len) * hop
    return lo_out, hi_out


def line_spans(audio_path, lines, device="cpu"):
    """Richer variant of line_boundaries used for calibration experiments.

    Returns dict of arrays: first-word start, last-word end, mean token score,
    per line -- plus total duration. The gap between one line's `end` and the
    next line's `start` is the pause the reciter takes, which is where a human
    actually clicks.
    """
    wave = S.decode_16k(audio_path)
    sr = _BUNDLE.sample_rate
    total = len(wave) / sr
    em, ratio = emissions(wave, device=device)
    frame_s = ratio / sr

    per_line = romanize(lines)
    flat = [w for L in per_line for w in L]
    spans = align_words(em, flat)

    starts, ends, scores = [], [], []
    k = 0
    for words in per_line:
        if not words:
            starts.append(ends[-1] if ends else 0.0)
            ends.append(ends[-1] if ends else 0.0)
            scores.append(0.0)
            continue
        grp = spans[k:k + len(words)]
        starts.append(grp[0][0].start * frame_s)
        ends.append(grp[-1][-1].end * frame_s)
        sc = [t.score for g in grp for t in g]
        scores.append(float(np.mean(sc)) if sc else 0.0)
        k += len(words)

    starts, ends = np.array(starts), np.array(ends)
    gap_lo, gap_hi = silence_gaps(wave, sr, ends, starts, total)
    return {
        "start": starts, "end": ends, "conf": np.array(scores), "total": total,
        "gap_lo": gap_lo, "gap_hi": gap_hi,
    }


def place_markers(sp, offset=0.0, strategy="pause_mid"):
    """Turn per-line speech spans into marker times.

    `end[i]` is when line i's last word finishes; `start[i+1]` is when line i+1
    begins. The gap between them is the reciter's pause, and the strategy
    decides where in that gap the marker sits.

      pause_mid   exact centre of the real silence between the two lines,
                  measured from audio loudness. Equal slack on either side, so
                  a small timing error can't land the marker inside speech.
                  This is the default.
      token_mid   centre of the token-boundary gap. Cheaper but biased late,
                  because CTC stretches a line's last token into the silence.
      next_start  the next line's onset, minus `offset`. Reproduces where a
                  human clicks (median 0.066s against 320 reference markers),
                  because people react to hearing the next phrase begin.

    `offset` is subtracted from whatever the strategy produces, so it stays
    available as a nudge for any of them.
    """
    end, start, total = sp["end"], sp["start"], sp["total"]
    nxt = np.append(start[1:], total)
    nxt = np.maximum(nxt, end)   # guard against any span overlap

    if strategy == "pause_mid":
        if "gap_lo" not in sp:
            raise ValueError("pause_mid needs gap_lo/gap_hi from line_spans()")
        marker = np.append(0.5 * (sp["gap_lo"] + sp["gap_hi"]), total)
    elif strategy == "token_mid":
        marker = end + 0.5 * (nxt - end)
    elif strategy == "next_start":
        # Deliberately not clamped to `end`: reference markers show the reciter
        # is sometimes still trailing off when the click lands, and CTC token
        # ends run generous. Clamping hurt (median 0.091s vs 0.066s).
        marker = nxt
    else:
        raise ValueError(f"unknown strategy {strategy!r}")

    marker = np.maximum.accumulate(marker - offset)
    marker[-1] = total           # last marker is always end-of-file
    return marker


def line_boundaries(audio_path, lines, offset=0.0, strategy="pause_mid",
                    device="cpu"):
    """Return (markers, scores): the marker time in seconds for each line, plus
    a 0-1 confidence per line.

    The whole file is aligned in a single pass. The trellis is O(frames x
    tokens), which peaks around 130M cells (~130 MB) on the longest recitation
    here -- small enough that windowing the alignment would only risk drift for
    no benefit. Emissions are still computed in windows, because self-attention
    over eight minutes is what actually costs memory.
    """
    sp = line_spans(audio_path, lines, device=device)
    return place_markers(sp, offset, strategy), sp["conf"]
