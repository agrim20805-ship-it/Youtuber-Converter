"""Score every frame mode against the human labels.

    uv run python tests/score.py            # print the table
    uv run python tests/score.py --write    # also write tests/RESULTS.md

The modes are re-run on the saved scans with the CURRENT code, so a change to
ytconv/frames.py is scored the moment it is made - no re-download.

precision  of the frames a mode keeps, the share a person called useful
coverage   of the evenly sampled seconds a person called useful, the share whose
           picture some kept frame shows (same picture = thumbnail distance
           below frames.same_distance - 6, smaller on dark videos)
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from ytconv import frames  # noqa: E402
from build_testset import matching_label_second, modes  # noqa: E402

LABELS = ROOT / "tests" / "labels" / "frames.csv"


def load_labels():
    with open(LABELS, newline="") as fh:
        return {(r["video"], int(r["second"])): r["label"] for r in csv.DictReader(fh)}


def score_video(key, labels):
    d = ROOT / "testdata" / key
    scan = json.loads((d / "scan.json").read_text())
    meta = json.loads((d / "testset.json").read_text())
    by_sec = {f["second"]: f for f in scan}
    # the same rule the picker uses: smaller on dark videos (frames.same_distance)
    same = frames.same_distance(median(f["mean_lum"] for f in scan))
    sample = [int(s) for s, why in meta["queue"].items() if why == "sample"]
    labelled = [s for s in sample if (key, s) in labels]

    def label_of(sec):
        if (key, sec) in labels:
            return labels[(key, sec)]
        match = matching_label_second(sec, by_sec, labelled, same)
        return labels.get((key, match)) if match is not None else None

    useful_sample = [s for s in labelled if labels[(key, s)] == "useful"]
    rows = {}
    for mode, kept in modes(scan).items():
        got = [label_of(s) for s in kept]
        useful, not_useful = got.count("useful"), got.count("not")
        covered = sum(
            1 for s in useful_sample
            if any(frames._distance(by_sec[s]["lum"], by_sec[k]["lum"]) < same for k in kept))
        rows[mode] = {
            "kept": len(kept),
            "useful": useful,
            "not": not_useful,
            "unlabelled": sum(1 for g in got if g in (None, "skip")),
            "precision": useful / (useful + not_useful) if useful + not_useful else None,
            "coverage": covered / len(useful_sample) if useful_sample else None,
        }
    return rows, len(labelled), len(useful_sample)


def pct(x):
    return "  -  " if x is None else f"{x:5.0%}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true", help="also write tests/RESULTS.md")
    args = p.parse_args()

    labels = load_labels()
    spec = json.loads((ROOT / "tests" / "testset.json").read_text())
    lines = ["| video | style | mode | kept | useful | not | precision | coverage |",
             "|---|---|---|---|---|---|---|---|"]
    totals = {}
    for v in spec["videos"]:
        key = v["key"]
        if v.get("purpose") == "at" or not (ROOT / "testdata" / key / "scan.json").exists():
            continue
        rows, n_lab, n_useful = score_video(key, labels)
        for mode, r in rows.items():
            lines.append(f"| {key} | {v['style']} | {mode} | {r['kept']} | {r['useful']} | {r['not']} | "
                         f"{pct(r['precision'])} | {pct(r['coverage'])} |")
            t = totals.setdefault(mode, {"useful": 0, "not": 0, "cov_n": 0, "cov_d": 0})
            t["useful"] += r["useful"]
            t["not"] += r["not"]
            if r["coverage"] is not None:
                t["cov_n"] += round(r["coverage"] * n_useful)
                t["cov_d"] += n_useful
    lines.append("")
    lines.append("| all videos | mode | precision | coverage |")
    lines.append("|---|---|---|---|")
    for mode, t in totals.items():
        prec = t["useful"] / (t["useful"] + t["not"]) if t["useful"] + t["not"] else None
        cov = t["cov_n"] / t["cov_d"] if t["cov_d"] else None
        lines.append(f"| | {mode} | {pct(prec)} ({t['useful']}/{t['useful'] + t['not']}) | "
                     f"{pct(cov)} ({t['cov_n']}/{t['cov_d']}) |")
    text = "\n".join(lines)
    print(text)
    if args.write:
        res = ROOT / "tests" / "RESULTS.md"
        # keep the sections other scripts add (--at, speech-to-text)
        old = res.read_text() if res.exists() else ""
        others = [sec for sec in old.split("\n## ")[1:]
                  if not sec.startswith("Frame picking")]   # this script's own section is rewritten
        rest = "".join("\n## " + sec for sec in others)
        res.write_text(
            "# Measured results\n\n## Frame picking against human labels\n\n"
            "Made by `tests/score.py` from the labels in `tests/labels/frames.csv`: "
            f"{len(labels)} frames, each looked at by a person and marked useful in notes "
            "or not. Rule used: a whiteboard frame where the presenter's body or hand "
            "covers the writing counts as not useful, unless they are pointing at it.\n\n"
            "precision = of the frames a mode keeps, the share a person called useful. "
            "coverage = of the useful moments in an even sample, the share some kept "
            "frame shows.\n\n" + text + "\n" + rest)
        print("\nwrote tests/RESULTS.md")


if __name__ == "__main__":
    main()
