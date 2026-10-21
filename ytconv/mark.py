"""Box the lines on a frame that the speaker is talking about (--mark).

For each frame: read its text lines with their positions (ocr.py), take what
was said from SAY_BEFORE seconds before the frame to SAY_AFTER after it (the
visual lags the speech), and draw a box around every line that shares words
with that speech. The original frame is left alone; the boxed copy goes in
marked/, and marked.json lists which lines matched and on which words, plus
"speech": on-slide (what is said names something on this screen), no match, or
no text. "no match" does NOT prove the speaker is off topic: handwritten
equations read back as symbols, and a whole video about one board has few
words that belong to one moment. Unmeasured - no labels exist for it yet.

Needs line positions, which the macOS reader gives. tesseract (Windows/Linux)
is read as plain text only, so there --mark says it cannot box anything.
"""

import json
import subprocess
from pathlib import Path

from . import match, ocr

SAY_BEFORE = 20     # speech this long before the frame can be about it
SAY_AFTER = 5       # ...and this long after
PAD = 0.006         # box margin, as a share of the frame width/height
EVERYWHERE_SHARE = 0.4  # said in this share of 30 s stretches: the lecture's topic, not this moment
COLOR = "0xFF2D55"  # a red that stands out on light and dark slides


def matching_lines(lines, spoken, common=frozenset()):
    """The OCR lines that share words with the speech, best first. Words in
    `common` (said all through the lecture) and lone digits are not evidence."""
    hits = []
    for line in lines:
        words = match.tokens(line["text"])
        shared = {w for w in words & spoken if w not in common and not (w.isdigit() and len(w) < 2)}
        if not shared:
            continue
        # one shared word is enough for a short label ("ARRAYS"); a long line
        # needs two, or half its words, so one stray word does not box it
        if len(shared) >= 2 or len(shared) / len(words) >= 0.5:
            hits.append({**line, "words": sorted(shared)})
    return sorted(hits, key=lambda h: -len(h["words"]))


def mark(frames_by_second, transcripts, out, languages=("en",)):
    """frames_by_second: {second: frame path}. Writes out/marked/ and
    out/marked.json. Returns the list written to marked.json."""
    paths = {str(p): s for s, p in frames_by_second.items()}
    read = ocr.read(list(paths), languages=languages)
    if read and not any("box" in l for lines in read.values() for l in lines):
        print("[mark] this OCR engine gives no line positions, so nothing can be boxed "
              "(the macOS reader does; tesseract here is text-only)", flush=True)
        return []
    (out / "marked").mkdir(parents=True, exist_ok=True)
    common = _said_everywhere(transcripts)
    results = []
    for path, sec in sorted(paths.items(), key=lambda x: x[1]):
        lines = [l for l in read.get(path, []) if l.get("box") and (l["confidence"] or 1) >= 0.3]
        spoken = set()
        for tr in transcripts:
            spoken |= match.tokens(" ".join(t for s, t in tr
                                            if sec - SAY_BEFORE <= s <= sec + SAY_AFTER))
        hits = matching_lines(lines, spoken, common)
        entry = {"frame": Path(path).name, "second": sec,
                 # is the speaker talking about what is on screen?
                 "speech": "on-slide" if hits else ("no match" if lines else "no text"),
                 "lines": [{"text": h["text"], "words": h["words"]} for h in hits]}
        if hits:
            target = out / "marked" / Path(path).name
            if _draw(Path(path), [h["box"] for h in hits], target):
                entry["marked"] = f"marked/{target.name}"
        results.append(entry)
    (out / "marked.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    return results


def _said_everywhere(transcripts, share=EVERYWHERE_SHARE, chunk=30):
    """Words spoken all through the lecture (its topic: "resistance",
    "database"). They match a title or banner on every frame and say nothing
    about this moment. Words said only around now ("DBMS" at 6:10) are kept."""
    chunks = {}
    for tr in transcripts:
        for sec, text in tr:
            chunks.setdefault(sec // chunk, set()).update(match.tokens(text))
    if len(chunks) < 4:
        return frozenset()
    seen = {}
    for words in chunks.values():
        for w in words:
            seen[w] = seen.get(w, 0) + 1
    return frozenset(w for w, n in seen.items() if n / len(chunks) >= share)


def _draw(src, boxes, target):
    """One ffmpeg call: a drawbox per matching line, boxes given as 0-1 fractions."""
    w, h = _size(src)
    if not w:
        return False
    thick = max(3, w // 400)
    filters = []
    for x, y, bw, bh in boxes:
        x0 = max(0, int((x - PAD) * w))
        y0 = max(0, int((y - PAD) * h))
        x1 = min(w - 1, int((x + bw + PAD) * w))
        y1 = min(h - 1, int((y + bh + PAD) * h))
        filters.append(f"drawbox=x={x0}:y={y0}:w={x1 - x0}:h={y1 - y0}:color={COLOR}:t={thick}")
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vf", ",".join(filters),
                           "-q:v", "2", str(target), "-y"])
    return proc.returncode == 0 and target.exists()


def _size(path):
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                           "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True)
    try:
        w, h = proc.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except ValueError:
        return 0, 0
