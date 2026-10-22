"""Health check: has YouTube or yt-dlp broken ytconv?

Run it on your own Mac (not in the cloud - YouTube often blocks cloud servers,
so a cloud check would mostly report false failures):

    uv run python tests/healthcheck.py            # full: ~45 s, downloads ~9 MB
    uv run python tests/healthcheck.py --quick    # captions only: ~4 s

It runs the real tool (`python -m ytconv`) on two known videos and compares
the results with answers measured on 2026-10-22. Prints PASS/FAIL per check
and a summary line. Exit code 0 = everything passed, 1 = something failed.
All output goes to a temporary folder that the script deletes itself.

If a check fails, the usual first fix is:  brew upgrade yt-dlp
Standard library only.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---- Known answers (measured 2026-10-22; update here if a video changes) ----
# Check 1: Jacob Clifford, "Macro Unit 1 Summary" (23:40, human captions).
CAPTIONS_URL = "https://www.youtube.com/watch?v=bgqTkGKTsWE"
CAPTIONS_LINES = 288          # cue lines in transcript.txt
CAPTIONS_LINES_TOL = 0.10     # +-10 %
CAPTIONS_END_S = 23 * 60 + 34  # last timestamp, seconds
CAPTIONS_END_TOL = 15         # +-15 s
# Checks 2 and 3: Khan Academy, 7:16, small download. YouTube offers 720p at most.
VIDEO_URL = "https://www.youtube.com/watch?v=N2PpRnFqnqY"
VIDEO_MIN_HEIGHT = 720        # the most this video offers
VIDEO_SECONDS = 436           # 7:16 - frame-scores.json has one row per second
VIDEO_SECONDS_TOL = 5

TIMEOUT_CAPTIONS = 120
TIMEOUT_VIDEO = 420

results = []   # (name, ok, detail)


def report(name, ok, detail):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    for line in detail.splitlines():
        print(f"        {line}")
    sys.stdout.flush()


def run_tool(url, out, extra=(), timeout=300):
    """Run the real tool. Returns (exit code or None on timeout, combined output)."""
    cmd = ["uv", "run", "python", "-m", "ytconv", url, "-o", str(out), *extra]
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or b"")
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return None, partial
    except FileNotFoundError:
        return None, "`uv` was not found on PATH."
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def tail(text, n=6):
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return "\n".join(l[:200] for l in lines[-n:])


def version_line(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return (p.stdout or p.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "NOT FOUND"


def check_captions(tmp):
    name = "1. captions (transcript for a known video)"
    out = tmp / "captions"
    code, log = run_tool(CAPTIONS_URL, out, ["--no-frames"], TIMEOUT_CAPTIONS)
    path = out / "transcript.txt"
    if code != 0 or not path.exists():
        why = "timed out" if code is None else f"tool exited with code {code}"
        return report(name, False, f"{why}; no transcript written.\nlast output:\n{tail(log)}")
    cues = [l for l in path.read_text().splitlines() if re.match(r"\[\d+:\d{2}(:\d{2})?\] ", l)]
    if not cues:
        return report(name, False, "transcript.txt has no [MM:SS] lines.")
    m = re.match(r"\[(?:(\d+):)?(\d+):(\d{2})\]", cues[-1])
    h, mi, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    last = h * 3600 + mi * 60 + s
    n = len(cues)
    src = re.search(r"^Source: (.*)$", path.read_text(), re.M)
    detail = (f"{n} lines (expected {CAPTIONS_LINES} +-{CAPTIONS_LINES_TOL:.0%}), "
              f"ends at {last // 60}:{last % 60:02d} "
              f"(expected {CAPTIONS_END_S // 60}:{CAPTIONS_END_S % 60:02d} +-{CAPTIONS_END_TOL}s), "
              f"source: {src.group(1) if src else '?'}")
    ok = (abs(n - CAPTIONS_LINES) <= CAPTIONS_LINES * CAPTIONS_LINES_TOL
          and abs(last - CAPTIONS_END_S) <= CAPTIONS_END_TOL)
    report(name, ok, detail)


def check_video_and_frames(tmp):
    n2 = "2. video download + resolution"
    n3 = "3. frames kept + frame-scores.json"
    out, work = tmp / "video-out", tmp / "video-work"
    code, log = run_tool(VIDEO_URL, out, ["--keep-video", "--work-dir", str(work)], TIMEOUT_VIDEO)
    client = None
    m = re.search(r"\[fetch\] ok via (\S+) at \d+p", log)
    m2 = re.search(r"using the best: \d+p via (\S+)", log)
    client = m.group(1) if m else (m2.group(1) if m2 else None)
    tried = re.findall(r"\[fetch\] (\S+) failed:", log)
    clients_note = f"yt-dlp client that worked: {client or 'none'}"
    if tried:
        clients_note += f" (failed first: {', '.join(tried)})"

    videos = sorted(work.glob("video*.mp4")) if work.exists() else []
    if code != 0 or not videos:
        why = "timed out" if code is None else f"tool exited with code {code}"
        report(n2, False, f"{why}; no video file.\n{clients_note}\nlast output:\n{tail(log, 10)}\n"
                          "Likely cause: yt-dlp out of date (`brew upgrade yt-dlp`), or YouTube "
                          "blocking this network.")
        return report(n3, False, "skipped: no video to scan.")

    try:
        pr = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(videos[0])],
            capture_output=True, text=True, timeout=30)
        w, h = (int(x) for x in pr.stdout.strip().split(",")[:2])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        report(n2, False, "ffprobe could not read the downloaded file.")
        return report(n3, False, "skipped: unreadable video.")
    size = videos[0].stat().st_size / 1_000_000
    short = min(w, h)
    report(n2, short >= VIDEO_MIN_HEIGHT,
           f"downloaded {w}x{h}, {size:.1f} MB (expected {VIDEO_MIN_HEIGHT}p or better)\n{clients_note}"
           + ("" if short >= VIDEO_MIN_HEIGHT else
              "\nA lower resolution means the good clients are failing; see AGENTS.md gotchas."))

    frames = sorted((out / "frames").glob("*.jpg")) if (out / "frames").exists() else []
    scores = out / "frame-scores.json"
    rows = None
    if scores.exists():
        try:
            rows = len(json.loads(scores.read_text()))
        except ValueError:
            rows = None
    ok = bool(frames) and rows is not None and abs(rows - VIDEO_SECONDS) <= VIDEO_SECONDS_TOL
    report(n3, ok,
           f"{len(frames)} frames kept (expected > 0); frame-scores.json rows: "
           f"{rows if rows is not None else 'missing/unreadable'} (expected ~{VIDEO_SECONDS})")


def main():
    ap = argparse.ArgumentParser(description="Check that ytconv still works against YouTube.")
    ap.add_argument("--quick", action="store_true", help="captions check only (~4 s, no video download)")
    args = ap.parse_args()

    start = time.time()
    print("ytconv health check" + (" (quick)" if args.quick else ""))
    print(f"  yt-dlp  {version_line(['yt-dlp', '--version'])}")
    print(f"  ffmpeg  {version_line(['ffmpeg', '-version'])}")
    print()

    with tempfile.TemporaryDirectory(prefix="ytconv-health-") as d:
        tmp = Path(d)
        check_captions(tmp)
        if not args.quick:
            check_video_and_frames(tmp)

    failed = [r for r in results if not r[1]]
    print()
    print(f"{len(results) - len(failed)}/{len(results)} checks passed in {time.time() - start:.0f} s"
          + ("" if not failed else " - FAILED: " + "; ".join(r[0].split('.')[0] for r in failed)
             + ". First thing to try: brew upgrade yt-dlp"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
