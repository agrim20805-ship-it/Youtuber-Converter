"""Is a presenter-removed copy (clean/) more useful than the original frame?

    uv run python tests/score_clean.py --build --media /dir/with/test/videos
    uv run python tests/score_clean.py            # score, after labelling
    uv run python tests/score_clean.py --write    # also add the table to tests/RESULTS.md

--build makes clean copies (frames.clean_board, as `ytconv --clean-board` does)
of the frames auto mode keeps on the board videos, and queues them for the
labelling page as "<key>-clean". Scoring compares, second by second, the label
of the original frame with the label of its clean copy.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from ytconv import frames, repeats  # noqa: E402
from build_testset import modes  # noqa: E402
from score import load_labels  # noqa: E402

BOARD_VIDEOS = ["whiteboard", "hindi"]


def kept(key):
    scan = json.loads((ROOT / "testdata" / key / "scan.json").read_text())
    return sorted(modes(scan, key, repeats.MAX_SAME)["auto"]), len(scan)


def build(media):
    for key in BOARD_VIDEOS:
        seconds, duration = kept(key)
        d = ROOT / "testdata" / f"{key}-clean"
        (d / "label").mkdir(parents=True, exist_ok=True)
        tmp = d / "tmp"
        written = frames.clean_board(Path(media) / key / "video.mp4", seconds, tmp, duration,
                                     width=960)
        by_name = {f"frame_{frames.seconds_to_stamp(s)}.jpg": s for s in seconds}
        for p in written:
            p.replace(d / "label" / f"{by_name[p.name]}.jpg")
        tmp.rmdir()
        ghost = {str(s): round(frames.ghost_share(Path(media) / key / "video.mp4", s, duration), 4)
                 for s in seconds}
        (d / "testset.json").write_text(json.dumps(
            {"key": f"{key}-clean", "queue": {str(s): "clean" for s in seconds},
             "ghost": ghost}, indent=1))
        print(f"[{key}-clean] {len(written)} clean frames queued", flush=True)


def score():
    labels = load_labels()
    lines = ["| video | frames | original useful | presenter removed useful | better | worse "
             "| the tool's choice useful |", "|---|---|---|---|---|---|---|"]
    for key in BOARD_VIDEOS:
        seconds, _ = kept(key)
        meta = json.loads((ROOT / "testdata" / f"{key}-clean" / "testset.json").read_text())
        ghost = meta.get("ghost", {})
        trip = [(labels.get((key, s)), labels.get((f"{key}-clean", s)), ghost.get(str(s), 1.0))
                for s in seconds]
        trip = [t for t in trip if t[0] in ("useful", "not") and t[1] in ("useful", "not")]
        pairs = [(a, b) for a, b, _ in trip]
        # what the tool shows: the clean copy when its ghost share is low enough
        chosen = sum((b if g <= frames.GHOST_MAX else a) == "useful" for a, b, g in trip)
        if not pairs:
            continue
        orig = sum(a == "useful" for a, _ in pairs)
        clean = sum(b == "useful" for _, b in pairs)
        better = sum(a == "not" and b == "useful" for a, b in pairs)
        worse = sum(a == "useful" and b == "not" for a, b in pairs)
        n = len(pairs)
        lines.append(f"| {key} | {n} | {orig} ({orig / n:.0%}) | {clean} ({clean / n:.0%}) | "
                     f"{better} | {worse} | {chosen} ({chosen / n:.0%}) |")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--build", action="store_true")
    p.add_argument("--media", help="where build_testset.py put the videos")
    p.add_argument("--write", action="store_true")
    args = p.parse_args()
    if args.build:
        if not args.media:
            sys.exit("--build needs --media")
        build(args.media)
        return
    table = score()
    print(table)
    if args.write:
        res = ROOT / "tests" / "RESULTS.md"
        old = res.read_text()
        head = "\n## Presenter removed (--clean-board) against human labels\n"
        keep = [sec for sec in old.split("\n## ") if not sec.startswith("Presenter removed")]
        body = ("\nThe frames auto mode keeps on the board videos, each labelled twice: the "
                "original and its copy with the presenter removed (the median of the next 40 s). "
                "\"The tool's choice\" = what the notes page shows: the clean copy when its "
                f"ghost share is at most {frames.GHOST_MAX} (frames.ghost_share), else the "
                "original. That cut-off was tuned on these same frames. "
                "Made by `tests/score_clean.py`.\n\n" + table + "\n")
        res.write_text("\n## ".join(keep).rstrip() + "\n" + head + body)
        print("wrote tests/RESULTS.md")


if __name__ == "__main__":
    main()
