"""Build the labelling queue for tests/score.py.

For every video in tests/testset.json:
  1. download it (once) into MEDIA_DIR - large files never go in the repo
  2. scan every second with frames.scan and save the scan to testdata/<key>/
  3. choose the seconds a person should label:
       - SAMPLE evenly spaced seconds (all seconds for very short videos)
       - plus up to KEPT_PER_MODE frames per mode, picked at random (fixed seed), from
         the kept frames that show a picture none of those already show
  4. extract those seconds as JPGs for the labelling page

Run from the project folder:
    uv run python tests/build_testset.py --media /some/temp/dir [--only KEY] [--local-gameplay FILE]
"""

import argparse
import json
import random
import sys
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytconv import fetch, frames, repeats  # noqa: E402

SAMPLE = 35          # evenly spaced seconds to label per video
KEPT_PER_MODE = 6    # extra kept frames per mode to label, chosen at random, when they
                     # show a picture no sampled second shows (keeps the total near 300)
SAME_PICTURE = frames.DEDUP_DISTANCE   # lum distance below this = the same picture
NEAR_SECONDS = 15    # a kept frame inherits a label only from a labelled second this close
LABEL_WIDTH = 960    # wide enough to read slide text on the labelling page


def modes(scan, key=None, max_same=0):
    """Every frame mode the scorer compares, run on one saved scan.

    With `key` and `max_same`, the two picker modes also get the tool's
    repeated-slide cap (ytconv/repeats.py), from the pictures and text cached
    by tests/build_repeats_cache.py. No cache: the cap is skipped, with a note."""
    out = {}
    out["default"] = frames.select(scan)[0]
    out["motion4.5"] = frames.select(scan, motion_max=4.5)[0]
    out["gameplay"] = frames.select_gameplay(scan)[0]
    out["every"] = frames.select_interval(scan, every=10)[0]
    out = {name: [f["second"] for f in chosen] for name, chosen in out.items()}
    if key and max_same:
        cache = repeats_cache(key)
        med_lum = median(f["mean_lum"] for f in scan)
        for name in ("default", "motion4.5"):
            if cache and all(s in cache["pics"] for s in out[name]):
                out[name] = repeats.cap(out[name], cache["pics"], cache["texts"],
                                        med_lum, keep=max_same)[0]
            elif len(out[name]) > max_same:
                print(f"[{key}] no repeats cache for {name}: run tests/build_repeats_cache.py",
                      file=sys.stderr)
    return out


def repeats_cache(key):
    """{"pics": {second: (board, frame)}, "texts": {second: text}} or None."""
    path = ROOT / "testdata" / key / "repeats-cache.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    return {"pics": {int(s): (bytes.fromhex(b), bytes.fromhex(f)) for s, (b, f) in raw["pics"].items()},
            "texts": {int(s): t for s, t in raw["texts"].items()}}


def matching_label_second(sec, scan_by_sec, labelled, same=SAME_PICTURE):
    """The labelled second that shows the same picture as `sec`, or None.
    `same`: the video's own same-picture distance (frames.same_distance)."""
    best = None
    for s in labelled:
        if abs(s - sec) > NEAR_SECONDS:
            continue
        d = frames._distance(scan_by_sec[sec]["lum"], scan_by_sec[s]["lum"])
        if d < same and (best is None or d < best[0]):
            best = (d, s)
    return best[1] if best else None


def build(video, media, local_gameplay):
    key = video["key"]
    out = ROOT / "testdata" / key
    out.mkdir(parents=True, exist_ok=True)

    if video["source"] == "local":
        if not local_gameplay:
            print(f"[{key}] skipped - pass --local-gameplay FILE", flush=True)
            return None
        path = Path(local_gameplay)
    else:
        work = Path(media) / key
        existing = sorted(work.glob("video*.mp4")) if work.exists() else []
        path = existing[0] if existing else fetch.fetch_video(video["source"], work)
    print(f"[{key}] video {path}", flush=True)

    scan_file = out / "scan.json"
    if scan_file.exists():
        scan = json.loads(scan_file.read_text())
    else:
        scan = frames.scan(path)
        for f in scan:
            f["lum"] = [round(x) for x in f["lum"]]
        scan_file.write_text(json.dumps(scan))
    by_sec = {f["second"]: f for f in scan}
    duration = len(scan)
    if video.get("purpose") == "at":     # used only for --at moments, no frame labels
        (out / "testset.json").write_text(json.dumps(
            {"key": key, "video": str(path), "duration": duration, "queue": {},
             "inherited": {}, "kept": {}}, indent=1))
        print(f"[{key}] {duration}s scanned (--at moments only)", flush=True)
        return 0

    if duration <= SAMPLE:
        sample = list(range(duration))
    else:
        step = duration / SAMPLE
        sample = sorted({int(step / 2 + i * step) for i in range(SAMPLE)})

    kept = modes(scan)
    queue = {s: "sample" for s in sample}
    inherited = {}
    rng = random.Random(f"{key}-testset")   # same picks on every run
    for mode, seconds in kept.items():
        unmatched = []
        for sec in seconds:
            if sec in queue:
                continue
            match = matching_label_second(sec, by_sec, sample)
            if match is None:
                unmatched.append(sec)
            else:
                inherited[sec] = match
        for sec in rng.sample(unmatched, min(KEPT_PER_MODE, len(unmatched))):
            queue.setdefault(sec, f"kept by {mode}")

    label_dir = out / "label"
    wanted = sorted(queue)
    missing = [s for s in wanted if not (label_dir / f"{s}.jpg").exists()]
    if missing:
        label_dir.mkdir(exist_ok=True)
        written = frames.extract(path, missing, label_dir, width=LABEL_WIDTH)
        for w in written:   # frame_MM-SS.jpg -> <second>.jpg, simpler for the page
            sec = sum(int(p) * 60 ** i for i, p in enumerate(reversed(w.stem[6:].split("-"))))
            w.rename(label_dir / f"{sec}.jpg")

    meta = {"key": key, "video": str(path), "duration": duration, "queue": queue,
            "inherited": inherited, "kept": kept}
    (out / "testset.json").write_text(json.dumps(meta, indent=1))
    print(f"[{key}] {duration}s, {len(sample)} sampled + "
          f"{len(queue) - len(sample)} kept-only = {len(queue)} to label "
          f"({len(inherited)} kept frames inherit a label)", flush=True)
    return len(queue)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--media", required=True, help="where the downloaded test videos live")
    p.add_argument("--only", default=None, help="build just this video key")
    p.add_argument("--local-gameplay", default=None, help="path to the local gameplay clip")
    args = p.parse_args()

    spec = json.loads((ROOT / "tests" / "testset.json").read_text())
    total = 0
    for video in spec["videos"]:
        if args.only and video["key"] != args.only:
            continue
        n = build(video, args.media, args.local_gameplay)
        total += n or 0
    print(f"\n{total} frames to label in total", flush=True)


if __name__ == "__main__":
    main()
