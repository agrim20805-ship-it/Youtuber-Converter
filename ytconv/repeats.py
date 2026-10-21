"""At most a few frames per slide or board: drop the rest of a repeated run.

The picker already drops a frame that looks like the one before it. That misses
two cases, both seen on the test lectures:
  - a presenter walking in front of a whiteboard makes every frame look new,
    though the writing behind him has not changed (7 frames of one board,
    3:17-4:09, on the physics lecture)
  - a slide being annotated stroke by stroke (4 frames of one table, 11:28-13:13,
    on the econ lecture)

Two frames show the same thing when either test says so:
  board  the presenter removed (median of the next CLEAN_WINDOW seconds, as
         --clean-board does, on a 160x90 grey thumbnail) leaves nearly the same
         picture: mean difference below SAME_BOARD
  text   both frames carry a lot of text (RICH_TEXT words) and the newer one adds
         fewer than NEW_WORDS
...unless the newer frame adds VETO_WORDS or more words of text: then it is a new
slide whatever the picture says. Without that veto the board test grouped
different screens of a dark screen recording (a 40 s median of a screen that
keeps changing is mush). The veto needs OCR, so without OCR nothing is dropped.

From each run the MAX_SAME clearest frames are kept: those closest to their own
presenter-free board, i.e. the ones he blocks least.

Measured 2026-10-21 on the 300 hand labels (tests/score.py, --max-same 0 vs 2):
default          precision 58% -> 60%, coverage 56% -> 56%
--motion-max 4.5 precision 61% -> 64%, coverage 62% -> 60% (the lost moment,
                 whiteboard 3:25, is the same board as the kept 3:49 and 4:09)
Whiteboard 24 -> 16 frames, Hindi lecture 32 -> 28. Tried and lost: comparing
with the group's last frame, or any frame, and a threshold of 5 - each dropped
more frames but lost more coverage.
"""

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import match
from .frames import CLEAN_WINDOW

THUMB_W, THUMB_H = 160, 90
SAME_BOARD = 4.0     # mean grey difference on a 160x90 thumbnail (scaled down on dark videos)
RICH_TEXT = 15       # words on screen for the text test to apply
NEW_WORDS = 3        # fewer new words than this on a text-rich frame = same slide
VETO_WORDS = 10      # this many new words = a new slide, whatever the picture says
COMMON_SHARE = 0.5   # words on half the frames (title bar, logo, URL) do not count
MAX_SAME = 2


def _grey(args):
    return subprocess.run(["ffmpeg", "-v", "error", *args, "-f", "rawvideo", "-"],
                          capture_output=True).stdout


def board_thumb(video, sec, duration, window=CLEAN_WINDOW):
    """160x90 grey picture of the board with the presenter removed."""
    window = max(3, min(window, int(duration)))
    start = max(0, min(sec, int(duration) - window - 1))
    vf = f"fps=1,scale={THUMB_W}:{THUMB_H},format=gray,tmedian=radius={(window - 1) // 2}"
    return _grey(["-ss", str(start), "-t", str(window + 2), "-i", str(video),
                  "-vf", vf, "-frames:v", "1"])


def frame_thumb(video, sec):
    """160x90 grey picture of the frame itself."""
    return _grey(["-ss", str(sec), "-i", str(video),
                  "-vf", f"scale={THUMB_W}:{THUMB_H},format=gray", "-frames:v", "1"])


def thumbs(video, seconds, duration):
    """{second: (board, frame)}, made in parallel - ffmpeg does the work."""
    def one(sec):
        return sec, (board_thumb(video, sec, duration), frame_thumb(video, sec))
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        return dict(pool.map(one, seconds))


def _distance(a, b):
    if not a or not b or len(a) != len(b):
        return float("inf")       # a failed thumbnail never counts as "same"
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def group(seconds, pics, texts, med_lum):
    """Split the kept seconds (in order) into runs that show the same thing.

    pics  {second: (board, frame)} from thumbs()
    texts {second: OCR text}
    """
    same_board = SAME_BOARD * min(1.0, med_lum / 24)   # dark video: smaller differences
    toks = {s: match.tokens(texts.get(s, "")) for s in seconds}
    seen = {}
    for s in seconds:
        for w in toks[s]:
            seen[w] = seen.get(w, 0) + 1
    common = ({w for w, n in seen.items() if n / len(seconds) >= COMMON_SHARE}
              if len(seconds) >= 4 else set())
    runs, union = [], set()
    for s in seconds:
        words = toks[s] - common
        if runs:
            new = len(words - union)
            rich = len(words) >= RICH_TEXT and len(union) >= RICH_TEXT
            board = _distance(pics[s][0], pics[runs[-1][0]][0]) < same_board
            if (rich and new < NEW_WORDS) or (board and new < VETO_WORDS):
                runs[-1].append(s)
                union |= words
                continue
        runs.append([s])
        union = set(words)
    return runs


def clearest(run, pics, keep):
    """The `keep` frames of a run the presenter blocks least, in time order."""
    return sorted(sorted(run, key=lambda s: _distance(pics[s][1], pics[s][0]))[:keep])


def cap(seconds, pics, texts, med_lum, keep=MAX_SAME):
    """(kept seconds, runs that lost frames as [{"group", "kept"}])."""
    out, trimmed = [], []
    for run in group(seconds, pics, texts, med_lum):
        if len(run) <= keep:
            out += run
        else:
            mine = clearest(run, pics, keep)
            out += mine
            trimmed.append({"group": run, "kept": mine})
    return sorted(out), trimmed
