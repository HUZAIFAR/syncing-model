#!/bin/bash
# Double-click this in Finder to sync everything in the inputs/ folder.
cd "$(dirname "$0")" || exit 1

PY="$HOME/.venvs/syncmarkers/bin/python"
INPUTS="inputs"

printf '\033[2J\033[H'
echo "════════════════════════════════════════════════════════"
echo "   RECITATION MARKER SYNC"
echo "════════════════════════════════════════════════════════"
echo

finish() {
    echo
    echo "════════════════════════════════════════════════════════"
    echo "Press any key to close this window."
    read -r -n 1 -s
    exit "${1:-0}"
}

if [ ! -x "$PY" ]; then
    echo "✗ Setup problem: the Python environment is missing."
    echo "  Expected it at: $PY"
    echo
    echo "  Rebuild it by pasting this into Terminal:"
    echo "    python3 -m venv ~/.venvs/syncmarkers && \\"
    echo "      ~/.venvs/syncmarkers/bin/pip install torch torchaudio uroman soundfile numpy"
    finish 1
fi

if [ ! -d "$INPUTS" ]; then
    mkdir -p "$INPUTS"
    echo "Created the inputs folder for you."
    echo
fi

# Count candidate folders so we can give a useful message before the slow part.
COUNT=$("$PY" - <<'PYEOF'
import smlib as S
try:
    print(len(S.discover("inputs")))
except Exception:
    print(0)
PYEOF
)

if [ "$COUNT" -eq 0 ]; then
    echo "Nothing to do — no recitations found in the inputs folder."
    echo
    echo "Put one folder per recitation inside:"
    echo "   $(pwd)/inputs"
    echo
    echo "Each folder needs:"
    echo "   • an audio file   (.mp3 / .wav / .m4a / .m4b / .aac / .flac)"
    echo "   • the Arabic text (.txt, one line per marker)"
    echo "   • optionally the English / transliteration — these are ignored"
    echo
    echo "Sub-folders are fine, nested as deep as you like. For example:"
    echo "   inputs/Salaam 10/"
    echo "   inputs/Ramadan/Night 3/Doa/"
    finish 0
fi

echo "Found $COUNT recitation(s) in inputs/. Working…"
echo "(roughly 20 seconds per 5 minutes of audio)"
echo

"$PY" sync.py "$INPUTS" --audacity
STATUS=$?

echo
if [ $STATUS -eq 0 ]; then
    echo "✓ Finished."
    echo
    echo "  Your timestamps:  ar-sync <name>.txt   (in each recitation folder)"
    echo "  To double-check:  <name> labels.txt"
    echo "                    → Audacity: File ▸ Import ▸ Labels"
    echo
    echo "Folders that already had a sync file were left untouched, so"
    echo "re-running this is always safe. To redo one, delete its"
    echo "ar-sync file and click again."
else
    echo "✗ Something went wrong (exit code $STATUS). The error is above."
fi
finish $STATUS
