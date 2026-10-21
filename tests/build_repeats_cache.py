"""Cache what the repeated-slide check needs, so tests/score.py can score it
without the videos: for every second the picker modes keep, the 160x90
presenter-free board and frame pictures (ytconv/repeats.py) and the OCR text.

    uv run python tests/build_repeats_cache.py --media /dir/with/test/videos

Writes testdata/<key>/repeats-cache.json (testdata/ is never published).
Re-run it after a change to the picker - a new kept second has no cache entry.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from ytconv import frames, ocr, repeats  # noqa: E402
from build_testset import modes  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--media", required=True, help="where build_testset.py put the videos")
    args = p.parse_args()
    if not ocr.engine():
        sys.exit("needs OCR (built in on a Mac; tesseract elsewhere)")
    spec = json.loads((ROOT / "tests" / "testset.json").read_text())
    for v in spec["videos"]:
        key = v["key"]
        d = ROOT / "testdata" / key
        video = Path(args.media) / key / "video.mp4"
        if v.get("purpose") == "at" or not (d / "scan.json").exists() or not video.exists():
            continue
        scan = json.loads((d / "scan.json").read_text())
        kept = modes(scan)
        seconds = sorted(set(kept["default"]) | set(kept["motion4.5"]))
        pics = repeats.thumbs(video, seconds, len(scan))
        with tempfile.TemporaryDirectory() as tmp:
            frames.extract(video, seconds, tmp)
            paths = {s: str(Path(tmp) / f"frame_{frames.seconds_to_stamp(s)}.jpg") for s in seconds}
            lines = ocr.read(list(paths.values()))   # English, as a plain tool run
        (d / "repeats-cache.json").write_text(json.dumps({
            "pics": {str(s): [b.hex(), f.hex()] for s, (b, f) in pics.items()},
            "texts": {str(s): ocr.text_of(lines.get(paths[s], [])) for s in seconds}}))
        print(f"[{key}] cached {len(seconds)} seconds", flush=True)


if __name__ == "__main__":
    main()
