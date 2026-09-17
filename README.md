# Recitation marker sync

Generates the `ar-sync` line markers automatically, so you don't have to sit
through each recitation clicking in Audacity.

Give it a folder with an audio file and a line-per-marker Arabic text; it writes
one `HH.MM.SS.mmm` timestamp per line, placed at the exact centre of the silence
between that line and the next.

Runs entirely on your machine. No API, no per-file cost, no internet after the
one-time model download.

## Use it

**One click:** put a folder per recitation in `inputs/`, then double-click
`RUN ME.command`. Outputs land in each recitation's own folder. Nesting inside
`inputs/` is unrestricted. See [INSTRUCTIONS.md](INSTRUCTIONS.md).

**From the terminal**, for more control:

```bash
./syncmarkers inputs --audacity
```

`syncmarkers` works from any directory and accepts any path, not just `inputs/`.
An optional shortcut:

```bash
echo "alias syncmarkers=\"$(pwd)/syncmarkers\"" >> ~/.zshrc   # run from the repo folder
```

Folders that already contain a sync file are skipped and left untouched, so
re-running over a growing `inputs/` only processes what's new. `--force`
overrides that.

### File naming doesn't matter

Files are identified by **content**, not filename: the Arabic source is
whichever text file is actually Arabic script (>50 % Arabic characters), and an
existing sync file is recognised by being nothing but timestamps.
`ar salaam 3.txt`, `arabic (1).txt`, and `ar.txt` all work. Transliteration and
translation files are excluded automatically because they score near zero on
the Arabic-script test.

The generated file is named from the Arabic file's `ar `-prefix when it has one
(`ar salaam 3.txt` → `ar-sync salaam 3.txt`), otherwise from the folder name.

Useful flags:

| Flag | Effect |
|---|---|
| `--audacity` | also write an importable Audacity label track for spot-checking |
| `--force` | redo folders that already have a sync file |
| `--offset N` | subtract N seconds from every marker; default 0 |
| `--strategy S` | `pause_mid` (default), `next_start`, or `token_mid` |
| `--device cpu` | force CPU; the Apple GPU is used automatically (~2× faster, bit-identical) |

**Spot-checking:** run with `--audacity`, then in Audacity use
*File → Import → Labels* and pick the generated `… labels.txt`. Each label spans
one line and shows its opening words, so you can see and hear whether the
boundaries land right.

## What to expect

Validated against 320 hand-placed markers from a reference corpus of 14
recitations (78 minutes). Nothing is fitted to them — the default applies no
offset — so every number below is held out by construction:

| | error vs. hand-placed markers |
|---|---|
| median | **0.046 s** |
| p90 | 0.155 s |
| p99 | 0.637 s |
| worst of 319 | 2.33 s |
| within 0.25 s | **96.9 %** |
| within 1 s | 99.7 % |

Separately — and more relevant to what the markers are for — **0 of 306 markers
land inside speech**, with a median 175 ms of silence on either side (p5 =
85 ms, worst case 5 ms).

For scale, a naive "spread the lines out by character count" model gets a median
of 3.27 s. The acoustic alignment is ~50× better than that.

Runtime is about 30x realtime on an M4's GPU (15x on CPU). The full 78-minute
reference corpus processes in roughly 2.5 minutes.

Almost all of that is the neural network: profiling one 125 s file gave 98 %
neural net, 1.4 % audio decode, 0.3 % alignment, 0.2 % romanization. The GPU
halves the neural-net stage; the alignment step is a sequential dynamic program
that only runs on CPU, but it is fast enough not to matter.

## How it works

The key idea is **forced alignment**, not transcription. The model is given both
the audio *and* your text, so it never has to work out *what* was said, only
*when*. That's why Arabic ASR quality — normally the weak point — barely matters
here.

1. `ffmpeg` decodes the audio to 16 kHz mono.
2. Text is cleaned: bidi control characters, `*` separators, and the
   parenthetical stage directions like `(زائِر يها پوتانو نام لے)` are removed,
   since the reciter doesn't voice them.
3. `uroman` romanizes the Arabic. The source text is fully vocalized (tashkeel
   on 319 of 320 reference lines), which makes this mapping near-deterministic —
   normally the hardest part of Arabic alignment.
4. Meta's MMS forced-alignment model (`torchaudio.pipelines.MMS_FA`) produces a
   time for every token. Emissions are computed in overlapping 40 s windows,
   because self-attention over a full 8-minute file is what actually costs
   memory; the alignment itself then runs in a single pass (~130 MB worst case).
5. Markers are placed and the timestamps written.

### Where the marker goes

This matters more than anything else in the pipeline. Six placement rules were
measured against the reference markers:

| rule | median error |
|---|---|
| end of the line's last word | 0.261 s |
| 25 % into the token gap | 0.191 s |
| midpoint of the token gap | 0.129 s |
| 75 % into the token gap | 0.087 s |
| next line's onset, minus 0.21 s | 0.066 s |
| **centre of the real silence** (default) | **0.046 s** |

The default, `pause_mid`, puts the marker at the exact centre of the silence
between two lines, measured from **audio loudness** rather than from token
boundaries. That distinction matters: CTC alignment stretches a line's final
token into the following silence, so by token boundaries 31.7 % of gaps look
shorter than 0.2 s, while measuring loudness directly shows only 13.4 % are —
and just 1 of 306 boundaries has no silence at all.

Centring in real silence gives equal slack on both sides, so a small timing
error cannot push the marker into speech. Measured silence is 0.34 s at the
median, i.e. ~170 ms of margin each way.

It also happens to be the most accurate rule, and its fitted offset came out at
+0.017 s — statistically nothing. So the default applies **no correction at
all**: the true midpoint is simply where the markers belong, and it is where the
hand-made markers were already landing.

`--strategy next_start` restores the previous behaviour (reproducing human click
timing, which lands late in the pause); `--strategy token_mid` uses the cheaper
token-gap midpoint.

## Files

| | |
|---|---|
| `RUN ME.command` | double-click launcher — processes everything in `inputs/` |
| `inputs/` | drop recitation folders here (any nesting depth; git-ignored) |
| `syncmarkers` | terminal launcher, runnable from any directory |
| `sync.py` | the CLI itself |
| `align.py` | forced alignment and marker placement |
| `smlib.py` | dataset discovery, text cleaning, timestamp format |
| `evaluate.py` | scores predictions against known `ar-sync` files |
| `experiment.py` | caches alignments, compares placement rules |
| `calibration.json` | placement strategy and offset (`pause_mid`, 0.0 s) |
| `batch_drive.py` | batch-process a Google Drive folder (set `SYNC_DRIVE_ROOT`) |

Recitation audio and texts are **not** included — the repository is code only.

## Known limits

- **Section-level mismatch is caught; per-marker wobble is not.** A contiguous
  run of low-confidence lines reliably means the text for those lines is absent
  from that stretch of audio, and `diagnose()` reports the line and time range
  (0 false positives across the 14 reference recitations, which have median
  confidence 0.52-0.58 against ~0.13 in a genuine mismatch). But confidence
  does *not* predict sub-second error on an individual marker — correlation is
  only -0.20, and pause length and speaking-rate deviation were no better. A
  clean run means "no section is wrong", not "every marker is exact". Use
  `--audacity` to eyeball anything important.
- **The residual may not all be model error.** At a 46 ms median we're near the
  precision of hand-clicking itself. The single worst case (Salaam 2 line 7,
  2.33 s early) *is* a genuine model error: the reciter takes a mid-line breath
  and the aligner treated it as the line boundary. Comparing implied speaking
  rate across the two lines favours the hand-made marker there.
- **Assumes line count matches marker count**, one marker per line, last marker
  at end of file. That held for all 14 reference recitations.
- **Requires the text to match what's recited.** Skipped lines, ad-lib
  additions, or a wrong text file will misalign.

## Data issues found in the reference corpus

- `Salaam 8` lines 25 and 26 share the timestamp `00.05.38.928` — a double-click
  or a missed marker. That boundary is excluded from scoring; the aligner scores
  it 0.01 confidence, which independently suggests something is off there.
- `Door Doa` contains the same recitation twice — `ar.mp3` and
  `darwaza p3-5.mp3` are both 131.6 s. The code prefers the descriptively-named
  file.

## Setup

Already installed at `~/.venvs/syncmarkers`. To rebuild from scratch:

```bash
python3 -m venv ~/.venvs/syncmarkers && ~/.venvs/syncmarkers/bin/pip install torch torchaudio uroman soundfile numpy
```

Needs `ffmpeg` on PATH. The MMS model (~1.2 GB) downloads once to
`~/.cache/torch/hub/checkpoints/`.

## Re-tuning later

If the reciter or recording style changes and markers start drifting, add the
new folders (with hand-made `ar-sync` files) to the reference set and refit:

```bash
~/.venvs/syncmarkers/bin/python experiment.py    # rebuild cache, compare rules
~/.venvs/syncmarkers/bin/python evaluate.py      # score end-to-end
```
