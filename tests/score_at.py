"""Score --at against the human answers in tests/labels/at_pairs.csv.

    uv run python tests/score_at.py --media /dir/with/test/videos [--write]

A pick is a hit if it lands within TOLERANCE seconds of the second a person
marked as "first appears clearly" (the thumbnails they chose from were 4 s
apart). For a moment marked "not shown", a method is right if it returns
nothing / says no text matched.

Methods compared:
    said-at      the spoken timestamp itself (a naive reference)
    said-at+8    a fixed delay only, no text reading
    old --at     frames.find_visual: best still frame in the next 2 minutes
    text match   match.locate_by_text: earliest matching slide text in 30 s, else
                 said-at + 8 s (rejected 2026-10-21; --at is now said-at + 8 s)

Results are split by set: "design" moments are the ones the new rule's two
numbers were chosen from (optimistic); "fresh" moments were labelled after the
rule was fixed, so they are the honest measure.
"""

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytconv import frames, match, ocr  # noqa: E402

TOLERANCE = 6
LANG = {v["key"]: v["language"] for v in json.loads((ROOT / "tests" / "testset.json").read_text())["videos"]}


def ocr_reader(key):
    """read_text(video, seconds) with an on-disk cache per video."""
    cache_file = ROOT / "testdata" / key / "ocr.json"
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}

    def read_text(video, seconds):
        todo = [s for s in seconds if str(s) not in cache]
        if todo:
            with tempfile.TemporaryDirectory() as tmp:
                paths = {}
                for s in todo:
                    p = Path(tmp) / f"{s}.jpg"
                    subprocess.run(["ffmpeg", "-v", "error", "-ss", str(s), "-i", str(video),
                                    "-frames:v", "1", "-q:v", "2", str(p), "-y"], check=True)
                    paths[str(p)] = s
                got = ocr.read(list(paths), languages=(LANG[key],))
                for p, s in paths.items():
                    cache[str(s)] = ocr.text_of(got.get(p, []))
            cache_file.write_text(json.dumps(cache, ensure_ascii=False))
        return {s: cache.get(str(s), "") for s in seconds}
    return read_text


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--media", required=True)
    p.add_argument("--write", action="store_true", help="append to tests/RESULTS.md")
    args = p.parse_args()

    with open(ROOT / "tests" / "labels" / "at_pairs.csv", newline="") as fh:
        answers = list(csv.DictReader(fh))

    methods = ["said-at", "said-at+8 (--at)", "old --at", "text match"]
    pair_set = {(p["video"], p["at"]): p.get("set", "design")
                for p in json.loads((ROOT / "tests" / "at_pairs.json").read_text())["pairs"]}
    hits = {}
    totals = {}
    lines = ["| set | video | said at | person | " + " | ".join(methods) + " | how |",
             "|---|---|---|---|" + "---|" * len(methods) + "---|"]
    cache = {}
    for a in answers:
        key, at, ans = a["video"], a["at"], a["answer"]
        if ans == "skip":       # the person could not tell: left out of the score
            continue
        if key not in cache:
            scan = json.loads((ROOT / "testdata" / key / "scan.json").read_text())
            rows = frames.select(scan)[1]
            text_dir = ROOT / "testdata" / key / "text"
            trs = [match.read_transcript(text_dir / "transcript.txt")]
            if (text_dir / "transcript.en.txt").exists():
                trs.append(match.read_transcript(text_dir / "transcript.en.txt"))
            cache[key] = (scan, rows, trs, ocr_reader(key))
        scan, rows, trs, reader = cache[key]
        t = frames._to_seconds(at)
        video = Path(args.media) / key / "video.mp4"

        old = frames.find_visual(rows, at)
        new = match.locate_by_text(scan, video, trs, at, reader)
        picks = {"said-at": t, "said-at+8 (--at)": t + match.LAG_SECONDS,
                 "old --at": old["second"] if old else None,
                 "text match": new["second"]}
        st = pair_set.get((key, at), "design")
        totals[st] = totals.get(st, 0) + 1
        cells = []
        for m in methods:
            pick = picks[m]
            if ans == "none":
                ok = m == "old --at" and old is None
            else:
                ok = pick is not None and abs(pick - int(ans)) <= TOLERANCE
            hits[(st, m)] = hits.get((st, m), 0) + ok
            cells.append(("✓ " if ok else "✗ ") + ("-" if pick is None else f"+{pick - t}s"))
        person = "not shown" if ans == "none" else f"+{int(ans) - t}s"
        how = ("text: " + ", ".join(new["words"][:4])) if new["score"] is not None else "+8 s"
        lines.append(f"| {st} | {key} | {at} | {person} | " + " | ".join(cells) + f" | {how} |")

    lines += ["", "| set | moments | " + " | ".join(methods) + " |",
              "|---|---|" + "---|" * len(methods)]
    for st, n in totals.items():
        lines.append(f"| {st} | {n} | " + " | ".join(
            f"{hits.get((st, m), 0)}/{n} ({hits.get((st, m), 0) / n:.0%})" for m in methods) + " |")
    text = "\n".join(lines)
    print(text)
    if args.write:
        res = ROOT / "tests" / "RESULTS.md"
        body = res.read_text() if res.exists() else "# Measured results\n"
        marker = "\n## --at against human answers\n"
        head, _, tail = body.partition(marker)
        rest = "\n## " + tail.split("\n## ", 1)[1] if "\n## " in tail else ""
        body = head + marker + (
            f"\nA hit is within {TOLERANCE} s of the second a person marked as the first clear "
            "appearance. \"design\" moments are the ones the rules were chosen from; "
            "\"fresh\" moments were picked from transcripts alone and labelled afterwards, "
            "so they are the honest measure. Made by `tests/score_at.py`.\n\n" + text + "\n") + rest
        res.write_text(body)
        print("\nupdated tests/RESULTS.md")


if __name__ == "__main__":
    main()
