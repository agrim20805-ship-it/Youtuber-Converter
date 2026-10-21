"""Thumbnail strips for labelling the --at pairs in tests/at_pairs.json.

For each spoken moment T: a thumbnail every `step` seconds from T to T+window,
plus what was said from T to T+15s (English: the human subtitles for the Hindi
lecture), so a person can click where the visual first appears.

    uv run python tests/build_at_pairs.py --media /some/temp/dir
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytconv import frames  # noqa: E402

STAMP = re.compile(r"^\[(\d+):(\d\d)(?::(\d\d))?\] (.*)$")


def said(key, start, seconds=15):
    text_dir = ROOT / "testdata" / key / "text"
    path = text_dir / "transcript.en.txt"
    if not path.exists():
        path = text_dir / "transcript.txt"
    words = []
    for line in path.read_text().splitlines():
        m = STAMP.match(line)
        if not m:
            continue
        a, b, c = m.group(1), m.group(2), m.group(3)
        t = int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)
        if start <= t <= start + seconds:
            words.append(m.group(4))
    return " ".join(words)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--media", required=True, help="where build_testset.py put the videos")
    args = p.parse_args()
    spec = json.loads((ROOT / "tests" / "at_pairs.json").read_text())
    window, step = spec["window"], spec["step"]

    built = []
    for pair in spec["pairs"]:
        key, at = pair["video"], pair["at"]
        t = frames._to_seconds(at)
        video = Path(args.media) / key / "video.mp4"
        duration = len(json.loads((ROOT / "testdata" / key / "scan.json").read_text()))
        end = min(t + window, duration - 1)
        out = ROOT / "testdata" / key / "at" / str(t)
        if not any(out.glob("s*.jpg")):   # built once; ffmpeg may give one fewer than `seconds`
            out.mkdir(parents=True, exist_ok=True)
            # one ffmpeg pass per pair: frame i of the output is second t + i*step
            subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(video),
                            "-t", str(end - t + 1), "-vf", f"fps=1/{step},scale=960:-2",
                            "-q:v", "4", str(out / "%d.jpg"), "-y"], check=True)
            numbered = [f for f in out.glob("*.jpg") if f.stem.isdigit()]
            for i, f in enumerate(sorted(numbered, key=lambda x: int(x.stem))):
                f.rename(out / f"s{t + i * step}.jpg")
        thumbs = sorted(int(f.stem[1:]) for f in out.glob("s*.jpg"))
        built.append({"video": key, "at": at, "t": t, "said": said(key, t), "seconds": thumbs})
        print(f"[{key} {at}] {len(thumbs)} thumbnails", flush=True)

    (ROOT / "testdata" / "at_pairs.json").write_text(json.dumps(built, indent=1))
    print(f"{len(built)} pairs ready")


if __name__ == "__main__":
    main()
