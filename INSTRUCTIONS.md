# How to use this

For how it works internally, see [README.md](README.md).

---

# The short version

1. Drop a folder for each recitation into **`inputs/`** (audio + Arabic text)
2. Double-click **`RUN ME.command`**
3. Done — timestamps appear inside each folder

That's it. No Terminal, no commands, no paths to type.

---

## Step 1 — Put your recitations in `inputs/`

One folder per recitation:

```
inputs/
  Salaam 10/
    salam 10.mp3
    ar salaam 10.txt
    en-translit salaam 10.txt     ← optional, ignored
  Wada Doa/
    wada.mp3
    arabic.txt
```

Each folder needs **two** things:

| | |
|---|---|
| **Audio** | `.mp3` `.wav` `.m4a` `.m4b` `.aac` `.flac` `.ogg` `.aiff` |
| **Arabic text** | `.txt`, one line per marker |

English translations and transliterations can stay in the folder — they're
ignored automatically.

**Filenames don't matter.** The tool reads the files to work out which is which,
so `ar.txt`, `arabic (1).txt`, and `ar salaam 3.txt` all work equally.

**Sub-folders are fine, nested as deep as you like:**

```
inputs/
  Ramadan/
    Night 3/
      Munajat/
        munajat.mp3
        ar munajat.txt
```

Everything gets found no matter how deep it sits.

## Step 2 — Check your line breaks

This is the one thing that can silently go wrong.

**One line of Arabic = one marker.** The line breaks *are* the markers. 20 lines
gives you 20 markers.

Make sure your editor hasn't turned wrapped long lines into real line breaks. In
TextEdit, turn off *Format ▸ Wrap to Page*. If unsure, count the lines before
running.

Text the reciter does **not** say — stage directions like
`(زائِر يها پوتانو نام لے)` — should be in parentheses so it gets skipped.

## Step 3 — Double-click `RUN ME.command`

A Terminal window opens, shows progress, and tells you when it's finished. Close
it with any keypress.

Roughly **20 seconds per 5 minutes of audio**, using your M4's GPU. Ten
recitations is a couple of minutes.

> **First time only:** macOS may say the file is from an unidentified developer.
> Right-click `RUN ME.command` → **Open** → **Open**. After that, double-clicking
> works normally.

## Step 4 — Collect your timestamps

Inside each recitation folder you'll now find:

| File | What it is |
|---|---|
| `ar-sync <name>.txt` | **your timestamps** — one per line, `HH.MM.SS.mmm`, each at the exact centre of the pause between lines |
| `<name> labels.txt` | for checking in Audacity (see below) |

## Re-running is always safe

Folders that already have a sync file are **skipped and left completely
untouched**. So you can add three new recitations to `inputs/`, click again, and
only the new ones get processed.

To redo one: delete its `ar-sync` file and click again.

---

## Checking a result

1. Open the audio in Audacity
2. **File ▸ Import ▸ Labels**
3. Pick the `<name> labels.txt` from that folder

You'll see a labelled block per line showing its opening words. Scrub a few
boundaries — especially the **first and last two** — and confirm they land where
the lines change.

**Do this the first time you use a new reciter or recording style.** After that,
occasional spot-checks are enough.

---

## If something goes wrong

**"Nothing to do — no recitations found"**
Your folders aren't inside `inputs/`, or a folder is missing its audio or its
Arabic text. Each recitation folder needs both, sitting directly inside it.

**macOS won't open the file**
Right-click `RUN ME.command` → **Open** → **Open**. One time only.

**"Setup problem: the Python environment is missing"**
The `~/.venvs/syncmarkers` folder was deleted. The window shows the command to
rebuild it.

**Wrong number of markers**
Your text file's line count isn't what you thought. Line breaks are markers —
check for soft-wrapping (Step 2).

**"TEXT / AUDIO MISMATCH -- these markers are NOT usable"**
The tool found a stretch of lines whose text isn't in that part of the audio. It
tells you the line range and the audio timestamps. Listen at those times and
compare against your text — usually the text file has a wrong, truncated, or
pasted-in section. Markers outside the flagged range are still fine, so once you
fix the text, delete the `ar-sync` file and run again.

**"worth a spot-check (single lines...)"**
One line scored oddly. Usually just an imprecise marker rather than a real
problem. Import the label track and look at that line.

**Markers drift further and further off as the recitation goes on**
The text doesn't match the audio — a skipped line, an ad-libbed addition, or the
wrong text file. Every line must be recited, in order.

**Markers are all consistently slightly early or late**
Markers sit at the exact centre of the silence between lines, so this shouldn't
happen. If you want to nudge them, use the Terminal:
`./syncmarkers inputs --force --offset 0.10`
(a positive number moves every marker earlier).

---

## Running from Terminal instead

The clickable file is just a wrapper. For more control:

```bash
./syncmarkers inputs --audacity
```

Optional one-time shortcut so you can type just `syncmarkers` from anywhere:

```bash
echo "alias syncmarkers=\"$(pwd)/syncmarkers\"" >> ~/.zshrc   # run from the repo folder
```

| Flag | What it does |
|---|---|
| `--audacity` | also write the label track for checking |
| `--force` | redo folders that already have a sync file |
| `--offset N` | shift all markers; default `0`. Positive = earlier |
| `--strategy S` | where in the gap the marker sits. Default `pause_mid` |
| `--device cpu` | force CPU. The GPU is automatic and ~2× faster |

You can also point it at any folder, not just `inputs/`.

---

## What it can't do

- **It won't warn you about its own small mistakes.** It *does* reliably catch
  a whole section where the text and audio disagree, and tells you the line and
  time range. But a single marker being half a second out is not detectable —
  use the label track when precision matters.
- **The text must match the recitation** exactly, line by line, in order.
- **The last marker is always the end of the audio file.**
