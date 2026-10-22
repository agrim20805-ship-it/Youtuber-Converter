"""Add more frames to the labelling queue: the ones the current code keeps that
no label covers yet (they show up as "unlabelled" in score.py).

    uv run python tests/extend_queue.py --media /dir/with/test/videos --count 21

Order: frames the tool keeps with no flags ("auto") first, then frames more modes agree on,
taken in turn from each video so one lecture does not fill the batch. Then
open the labelling page (tests/label_server.py) - it starts at the first
frame without a label.
"""

import argparse
import json
import sys
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from ytconv import frames, repeats  # noqa: E402
from build_testset import LABEL_WIDTH, matching_label_second, modes  # noqa: E402
from score import load_labels  # noqa: E402


def unlabelled(key, labels):
    """{second: [modes that keep it]} for kept frames with no usable label."""
    d = ROOT / "testdata" / key
    scan = json.loads((d / "scan.json").read_text())
    meta = json.loads((d / "testset.json").read_text())
    by_sec = {f["second"]: f for f in scan}
    sample = [int(s) for s, why in meta["queue"].items() if why == "sample"]
    labelled = [s for s in sample if (key, s) in labels]
    same = frames.same_distance(median(f["mean_lum"] for f in scan))
    out = {}
    for mode, kept in modes(scan, key, repeats.MAX_SAME).items():
        for s in kept:
            if (key, s) in labels or str(s) in meta["queue"]:
                continue
            m = matching_label_second(s, by_sec, labelled, same)
            if m is None or labels.get((key, m)) == "skip":
                out.setdefault(s, []).append(mode)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--media", required=True, help="where build_testset.py put the videos")
    p.add_argument("--count", type=int, default=21)
    p.add_argument("--mode", default=None, help="only frames this mode keeps (e.g. auto)")
    args = p.parse_args()

    labels = load_labels()
    spec = json.loads((ROOT / "tests" / "testset.json").read_text())
    per_video = {}
    for v in spec["videos"]:
        if v.get("purpose") == "at" or v["source"] == "local":
            continue          # local clip: its file is not in --media
        todo = unlabelled(v["key"], labels)
        if args.mode:
            todo = {s: ms for s, ms in todo.items() if args.mode in ms}
        per_video[v["key"]] = sorted(todo.items(),
                                     key=lambda kv: ("auto" not in kv[1], -len(kv[1]), kv[0]))

    picked = []
    while len(picked) < args.count and any(per_video.values()):
        # each round: the best remaining frame of every video, best first
        heads = [(k, lst.pop(0)) for k, lst in per_video.items() if lst]
        heads.sort(key=lambda h: ("auto" not in h[1][1], -len(h[1][1])))
        picked += heads[:args.count - len(picked)]

    for key in sorted({k for k, _ in picked}):
        d = ROOT / "testdata" / key
        meta_path = d / "testset.json"
        meta = json.loads(meta_path.read_text())
        mine = [(s, ms) for k, (s, ms) in picked if k == key]
        for s, ms in mine:
            meta["queue"][str(s)] = "extra: kept by " + ", ".join(ms)
        meta_path.write_text(json.dumps(meta, indent=1))
        label_dir = d / "label"
        missing = [s for s, _ in mine if not (label_dir / f"{s}.jpg").exists()]
        written = frames.extract(Path(args.media) / key / "video.mp4", missing, label_dir,
                                 width=LABEL_WIDTH)
        for w in written:       # frame_MM-SS.jpg -> <second>.jpg, as the page expects
            sec = sum(int(p) * 60 ** i for i, p in enumerate(reversed(w.stem[6:].split("-"))))
            w.rename(label_dir / f"{sec}.jpg")
        print(f"[{key}] +{len(mine)}: " + ", ".join(f"{s}s ({'/'.join(ms)})" for s, ms in mine))
    print(f"{len(picked)} frames added to the queue")


if __name__ == "__main__":
    main()
