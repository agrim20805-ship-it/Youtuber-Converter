"""Stage 3 - keep only the frames that carry information.

This stage is the reason the tool is worth having. Ordinary tools sample evenly
and return mostly footage of the presenter's face.

Two earlier approaches were tried and measured, not guessed at:

1. ffmpeg scene-change detection. Mostly selects cuts to the talking head and
   fades to black. Scene changes are not the same thing as information.

2. Edge density on a 64x36 downscale (the original formula).
   Measured against 43 hand-labelled frames from a lecture it passed 11/11
   talking heads and only 16/32 screen frames - it ranked faces ABOVE code.
   The reason: at 64x36 text is averaged out of existence, so "edge density"
   stops measuring text and starts measuring room clutter - shelves, faces and
   furniture. The premise breaks at that resolution.

What is used now, and what it scored on the same labelled set:

- MOTION as the gate. Screen content holds still; a person never does.
  motion < 1.0 keeps 21/32 screen frames and 0/11 talking heads.
- TEXTURE as the score: edges detected by ffmpeg at the full 1920x1080, and only
  then downscaled. This measures real text, which the 64x36 version could not.

Saturation was dropped from the score. It sounds right (skin is colourful,
slides are not) but on a grey-walled room it did not separate the classes at all
(medians 10.4 vs 13.4, fully overlapping). It is still recorded for debugging.
"""

import csv
import subprocess
from pathlib import Path
from statistics import median

SCAN_W, SCAN_H = 64, 36
RGB_BYTES = SCAN_W * SCAN_H * 3
GRAY_BYTES = SCAN_W * SCAN_H

DEFAULT_THRESHOLD = 0.5   # texture relative to the video's own median
MOTION_MAX = 1.0          # above this the frame is moving footage, not a screen
DARK_FLOOR = 12.0         # fades to black (see dark_floor: relative on dark videos)
BRIGHT_CEILING = 243.0    # blank or washed-out slides
TEXTURE_FLOOR = 0.30      # essentially nothing on screen
DEDUP_DISTANCE = 6.0      # mean luminance difference below this = the same slide
MIN_GAP_SECONDS = 5
MIN_FRAMES = 35           # the frame budget on videos up to ~12 minutes
SECONDS_PER_FRAME = 20    # longer videos: one frame of budget per 20 s (see auto_max_frames)

# A slide beside a moving presenter bubble (picture-in-picture). The whole
# second counts as moving, so the motion gate dropped it - 4 of the 9 useful
# slide moments the light-slides lecture missed. Allowed through when the
# movement covers at most INSET_MOVING of the picture AND the still part is
# mostly plain light slide background. On that lecture's moving frames a person
# labelled, it let through 11 useful and 5 not useful (talking heads in front of
# shelves have no white background). Coverage 47% -> 71%, precision unchanged
# (89%); nearby settings (0.3-0.5, 0.5-0.7) gave the same. The score penalty
# lets a still frame of the same slide win its bucket; without it: 65%, 83%.
# Slides mode only - in board mode a whiteboard IS a light background.
# Not handled: dark slides with a bubble, text over moving video, scrolling.
INSET_MOVING = 0.4        # share of the picture (64x36 cells, grown by one) that moves
INSET_WHITE = 0.6         # share of the still part that is light, unsaturated background
INSET_PENALTY = 0.7       # score multiplier for such a frame
INSET_CELL_MOVES = 6.0    # a cell's brightness change that counts as moving

# Gameplay mode. Measured on a 13 s gameplay Short: a cut or a big on-screen
# change moved the thumbnail 12-85 between seconds; continuous footage 1-8.
GAMEPLAY_WINDOW = 3       # keep the sharpest second from each window this long
CHANGE_DISTANCE = 15.0    # a jump this big from the previous second = new scene


def scan(video):
    """Measure every second of the video. Two cheap ffmpeg passes."""
    appearance = _scan_appearance(video)   # luminance, saturation, motion
    texture = _scan_texture(video)         # text density, measured at full res

    frames = []
    for i, a in enumerate(appearance):
        a["texture"] = texture[i] if i < len(texture) else 0.0
        frames.append(a)
    if not frames:
        raise RuntimeError("scan produced no frames - is the video file valid?")
    return frames


def _scan_appearance(video):
    """Sample at 2fps so the two samples within a second give a motion figure."""
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(video),
         "-vf", f"fps=2,scale={SCAN_W}:{SCAN_H}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    samples = []
    while True:
        buf = proc.stdout.read(RGB_BYTES)
        if len(buf) < RGB_BYTES:
            break
        samples.append(_measure(buf))
    proc.stdout.close()
    err = proc.stderr.read().decode(errors="replace")
    proc.wait()
    if not samples:
        raise RuntimeError(f"scan produced no frames. ffmpeg said:\n{err}")

    out = []
    for second in range(len(samples) // 2):
        a, b = samples[2 * second], samples[2 * second + 1]
        row = {
            "second": second,
            "lum": a["lum"],
            "saturation": a["saturation"],
            "mean_lum": a["mean_lum"],
            # Stillness is the gate. A slide is identical half a second later;
            # a person talking is not.
            "motion": sum(abs(x - y) for x, y in zip(a["lum"], b["lum"])) / len(a["lum"]),
        }
        if row["motion"] >= MOTION_MAX:     # only needed where the gate would drop it
            row["moving"], row["still_white"] = _inset(a, b)
        out.append(row)
    return out


def _inset(a, b):
    """(share of the picture that moves, share of the still rest that is plain
    light background) - see INSET_MOVING. Rows are bit masks so growing the
    moving area by one cell is a few integer operations, not a loop per cell."""
    w, h = SCAN_W, SCAN_H
    full = (1 << w) - 1
    la, lb, sat = a["lum"], b["lum"], a["sat"]
    moved, white = [], []
    for y in range(h):
        m = wt = 0
        for x in range(w):
            i = y * w + x
            if abs(la[i] - lb[i]) > INSET_CELL_MOVES:
                m |= 1 << x
            if la[i] > 180 and sat[i] < 40:
                wt |= 1 << x
        moved.append((m | (m << 1) | (m >> 1)) & full)      # grow left and right
        white.append(wt)
    grown = [moved[y] | (moved[y - 1] if y else 0) | (moved[y + 1] if y + 1 < h else 0)
             for y in range(h)]                              # and up and down
    n_moving = sum(bin(g).count("1") for g in grown)
    n_still = w * h - n_moving
    n_white = sum(bin(white[y] & ~grown[y] & full).count("1") for y in range(h))
    return round(n_moving / (w * h), 3), round(n_white / max(1, n_still), 3)


def _measure(buf):
    lum = [0.0] * (SCAN_W * SCAN_H)
    sat = [0] * (SCAN_W * SCAN_H)
    for i in range(SCAN_W * SCAN_H):
        r, g, b = buf[i * 3], buf[i * 3 + 1], buf[i * 3 + 2]
        lum[i] = 0.299 * r + 0.587 * g + 0.114 * b
        sat[i] = max(r, g, b) - min(r, g, b)
    return {"lum": lum, "sat": sat,
            "saturation": sum(sat) / (SCAN_W * SCAN_H),
            "mean_lum": sum(lum) / len(lum)}


def _scan_texture(video):
    """Detect edges at the FULL source resolution, then downscale the edge map.

    Doing it in this order is the whole point: the mean of the resulting map is
    the fraction of the frame occupied by genuine text and line work. Downscaling
    first (as the original formula did) destroys the text before measuring it.
    ffmpeg does this in C, so it costs almost nothing.
    """
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(video),
         "-vf", f"fps=1,edgedetect=low=0.06:high=0.18,scale={SCAN_W}:{SCAN_H}",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    values = []
    while True:
        buf = proc.stdout.read(GRAY_BYTES)
        if len(buf) < GRAY_BYTES:
            break
        values.append(sum(buf) / GRAY_BYTES)
    proc.stdout.close()
    proc.wait()
    return values


def dark_floor(med_lum):
    """Below this a frame counts as a fade to black.

    A fixed 12 dropped every second of a Khan Academy tablet video: thin
    coloured writing on a black canvas has a median brightness of 3.7. A fade
    is dark compared with the rest of ITS video, so on dark videos the floor
    is half their median. Videos with a median of 24 or more keep 12.
    """
    return min(DARK_FLOOR, med_lum / 2)


def same_distance(med_lum):
    """Thumbnail distance below which two seconds show the same slide.

    6 suits normal videos (bright slides move ~69 in 30 s). On a black tablet
    canvas the whole picture averages 3.7, and separate stages of a drawing
    differ by only 0.2-1.1, so a fixed 6 merged the whole video into one
    frame. Dark videos (median under 24, where dark_floor drops below 12)
    shrink it in proportion.
    """
    return DEDUP_DISTANCE * min(1.0, med_lum / (2 * DARK_FLOOR))


def auto_max_frames(duration):
    """The frame budget when --max-frames is not given.

    A fixed 35 meant one frame per 40 s on a 24-minute lecture, and slides shown
    for less than that were lost: on the econ lecture, 3 of the 10 useful moments
    no kept frame showed were still slides that lost their 40 s slot. Measured
    2026-10-22 on the saved scans: one per 20 s raised coverage of useful moments
    from 41% to 53% (econ lecture) and 80% to 90% (screen recording); videos
    under ~12 minutes are unchanged."""
    return max(MIN_FRAMES, round(duration / SECONDS_PER_FRAME))


# --- Picking the mode automatically --------------------------------------
#
# The quietest tenth of a video's seconds tells slides apart from a presenter
# who never stops moving: a slide, screen or drawing is held perfectly still at
# times (10th-percentile motion 0.00-0.12 on the slides, screen and tablet test
# videos), a person at a board is not (0.80-2.92). Among moving videos, a game
# or sport clip jumps to a new picture far more often (58% of seconds vs 10-28%).
# Measured 2026-10-22 on 9 test scans; on the 6 with hand labels the rule picked
# the best mode every time: precision 60% -> 68%, coverage 56% -> 67% over
# default-everywhere. Only one video per style - treat the thresholds as a first
# guess. The 0.3 still-threshold sits in a wide gap; the 0.45 jump-threshold
# rests on a single 13 s clip, so short clips (under AUTO_SHORT s) count as
# fast footage too.

AUTO_STILL = 0.3     # 10th-percentile motion below this = slides / screen / drawing
AUTO_JUMPY = 0.45    # share of seconds with a big picture change = gameplay
AUTO_SHORT = 90      # a moving clip shorter than this is treated as fast footage
BOARD_MOTION_MAX = 4.5


def detect_mode(frames):
    """("default" | "board" | "gameplay", plain-words reason) from a scan."""
    motion = sorted(f["motion"] for f in frames)
    p10 = motion[min(len(motion) - 1, int(0.10 * len(motion)))]
    if p10 < AUTO_STILL:
        return "default", "the picture holds perfectly still at times (slides, screen or drawing)"
    jumps = [_distance(a["lum"], b["lum"]) for a, b in zip(frames, frames[1:])]
    jumpy = sum(1 for j in jumps if j >= CHANGE_DISTANCE) / max(1, len(jumps))
    if jumpy >= AUTO_JUMPY or len(frames) < AUTO_SHORT:
        return "gameplay", "the picture keeps moving and changes often (game, sport or a short clip)"
    return "board", "something is always moving, but the picture stays put (a presenter at a board)"


def is_inset(f):
    """A slide with a small moving picture-in-picture beside it (INSET_MOVING)."""
    return (f.get("moving") is not None and f["moving"] <= INSET_MOVING
            and f.get("still_white", 0) >= INSET_WHITE)


def select(frames, threshold=DEFAULT_THRESHOLD, max_frames=None,
           min_gap=MIN_GAP_SECONDS, motion_max=MOTION_MAX):
    """Gate on stillness, score on texture, then spread across the video."""
    med_tex = median(f["texture"] for f in frames) or 1e-6
    med_lum = median(f["mean_lum"] for f in frames)
    polarity = "light" if med_lum > 127 else "dark"

    floor = dark_floor(med_lum)
    debug = {}
    candidates = []
    for f in frames:
        f["score"] = f["texture"] / med_tex

        # Reject near EITHER extreme. The original rule dropped only dark frames,
        # which is right for dark-mode recordings and wrong for slides: a blank
        # white slide sailed straight through.
        if f["mean_lum"] < floor:
            debug[f["second"]] = "drop: near-black"
        elif f["mean_lum"] > BRIGHT_CEILING:
            debug[f["second"]] = "drop: near-white"
        elif f["texture"] < TEXTURE_FLOOR:
            debug[f["second"]] = "drop: blank"
        elif f["motion"] >= motion_max and not (motion_max <= MOTION_MAX and is_inset(f)):
            debug[f["second"]] = "drop: moving footage"
        elif f["score"] < threshold:
            debug[f["second"]] = "drop: below threshold"
        else:
            if f["motion"] >= motion_max:      # a slide beside a moving presenter bubble
                f["score"] *= INSET_PENALTY
            debug[f["second"]] = "candidate"
            candidates.append(f)

    chosen = []
    same = same_distance(med_lum)
    if candidates:
        duration = frames[-1]["second"] + 1
        bucket = max(1, duration / (max_frames or auto_max_frames(duration)))
        best = {}
        for f in candidates:
            key = int(f["second"] / bucket)
            if key not in best or f["score"] > best[key]["score"]:
                best[key] = f

        last_kept = None
        for f in sorted(best.values(), key=lambda x: x["second"]):
            if last_kept is not None:
                if f["second"] - last_kept["second"] < min_gap:
                    debug[f["second"]] = "drop: too close to previous"
                    continue
                # A slide held for 60 seconds should yield one frame, not twelve.
                if _distance(f["lum"], last_kept["lum"]) < same:
                    debug[f["second"]] = "drop: same slide as previous"
                    continue
            debug[f["second"]] = "KEEP"
            chosen.append(f)
            last_kept = f

    rows = _rows(frames, debug)
    meta = {"polarity": polarity, "median_lum": round(med_lum, 1),
            "median_texture": round(med_tex, 3), "candidates": len(candidates)}
    return chosen, rows, meta


def select_gameplay(frames, max_frames=35, window=GAMEPLAY_WINDOW):
    """For fast footage - games, sport, action - where the moving moments ARE
    the content. Stillness is ignored. Keeps the sharpest second in each window
    (texture doubles as sharpness: motion blur wipes out edges), plus the second
    after any big scene change that no kept frame already shows.
    """
    med_tex = median(f["texture"] for f in frames) or 1e-6
    med_lum = median(f["mean_lum"] for f in frames)
    floor = dark_floor(med_lum)
    debug = {}
    candidates = []
    for f in frames:
        f["score"] = f["texture"] / med_tex
        if f["mean_lum"] < floor:
            debug[f["second"]] = "drop: near-black"
        elif f["mean_lum"] > BRIGHT_CEILING:
            debug[f["second"]] = "drop: near-white"
        elif f["texture"] < TEXTURE_FLOOR:
            debug[f["second"]] = "drop: blank"
        else:
            debug[f["second"]] = "not sharpest in window"
            candidates.append(f)

    duration = frames[-1]["second"] + 1
    bucket = max(window, duration / max_frames)
    best = {}
    for f in candidates:
        key = int(f["second"] / bucket)
        if key not in best or f["score"] > best[key]["score"]:
            best[key] = f
    kept = {f["second"]: f for f in best.values()}
    for sec in kept:
        debug[sec] = "KEEP: sharpest in window"

    # Scene changes the window pick missed: a cut to something new between picks.
    by_second = {f["second"]: f for f in candidates}
    for prev, f in zip(frames, frames[1:]):
        if f["second"] not in by_second or _distance(f["lum"], prev["lum"]) < CHANGE_DISTANCE:
            continue
        nearest = min(kept.values(), key=lambda k: abs(k["second"] - f["second"]), default=None)
        if nearest and (abs(nearest["second"] - f["second"]) < 2
                        or _distance(f["lum"], nearest["lum"]) < CHANGE_DISTANCE):
            continue          # already shown by a kept frame
        kept[f["second"]] = f
        debug[f["second"]] = "KEEP: scene change"

    chosen = sorted(kept.values(), key=lambda x: x["second"])
    if len(chosen) > max_frames:
        top = sorted(chosen, key=lambda x: x["score"], reverse=True)[:max_frames]
        for f in chosen:
            if f not in top:
                debug[f["second"]] = "drop: over --max-frames"
        chosen = sorted(top, key=lambda x: x["second"])

    meta = {"polarity": "light" if med_lum > 127 else "dark",
            "median_lum": round(med_lum, 1), "median_texture": round(med_tex, 3),
            "candidates": len(candidates)}
    return chosen, _rows(frames, debug), meta


def select_interval(frames, every, max_frames=35):
    """One frame every `every` seconds, no filtering at all. Catches everything,
    including the presenter's face and near-identical repeats. If that is more
    than max_frames, the step widens so the frames still span the whole video."""
    med_tex = median(f["texture"] for f in frames) or 1e-6
    med_lum = median(f["mean_lum"] for f in frames)
    for f in frames:
        f["score"] = f["texture"] / med_tex
    duration = frames[-1]["second"] + 1
    step = max(every, duration / max_frames)
    wanted = {int(i * step) for i in range(max_frames) if i * step < duration}
    debug = {f["second"]: "KEEP: interval" if f["second"] in wanted else "between intervals"
             for f in frames}
    chosen = [f for f in frames if f["second"] in wanted]
    meta = {"polarity": "light" if med_lum > 127 else "dark",
            "median_lum": round(med_lum, 1), "median_texture": round(med_tex, 3),
            "candidates": len(frames), "step": round(step, 2)}
    return chosen, _rows(frames, debug), meta


def _rows(frames, debug):
    return [{
        "second": f["second"],
        "mean_lum": round(f["mean_lum"], 2),
        "texture": round(f["texture"], 3),
        "motion": round(f["motion"], 3),
        "saturation": round(f["saturation"], 3),
        "score": round(f["score"], 3),
        "decision": debug[f["second"]],
    } for f in frames]


def _distance(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def source_width(video):
    """Native width of the video, via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True).stdout.strip()
    try:
        return int(out.split(",")[0])
    except (ValueError, IndexError):
        return 0


def extract(video, seconds, outdir, width=None):
    """Pull the chosen seconds out at full resolution.

    -ss goes before -i so ffmpeg seeks instead of decoding from the start.
    No downscale by default: frames stay at the source 1920px, because 512px was
    too blurry to read on-screen text.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for sec in seconds:
        out = outdir / f"frame_{seconds_to_stamp(sec)}.jpg"
        args = ["ffmpeg", "-v", "error", "-ss", str(sec), "-i", str(video), "-frames:v", "1"]
        if width:
            # lanczos only matters when upscaling; harmless when downscaling.
            args += ["-vf", f"scale={width}:-2:flags=lanczos"]
        args += ["-q:v", "2", str(out), "-y"]
        if subprocess.run(args).returncode == 0 and out.exists():
            written.append(out)
    return written


def write_debug_csv(rows, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# --- The timestamp gap ---------------------------------------------------
#
# A concept's timestamp in the transcript is NOT when its visual appears. The
# presenter says the words, then draws the thing. In one lecture a graph is
# announced at 06:29 and only drawn at 08:04 - a gap of
# 95 seconds. Anything that extracts a frame AT the spoken timestamp gets the
# presenter mid-sentence, not the diagram.
#
# So: given a timestamp, search FORWARD for where the visual actually lands.

FORWARD_WINDOW = 120  # seconds to search ahead


def find_visual(scores, timestamp, window=FORWARD_WINDOW, motion_max=MOTION_MAX):
    """Given a transcript timestamp, return the second where its visual appears.

    `scores` is the list of per-second dicts from frame-scores.json. Searches
    [timestamp, timestamp + window] for the highest-scoring still second, and
    returns None if nothing in the window qualifies.
    """
    start = _to_seconds(timestamp)
    best = None
    for row in scores:
        sec = row["second"]
        if sec < start or sec > start + window:
            continue
        if row.get("motion", 0) >= motion_max:
            continue          # moving footage - the presenter, not the diagram
        if row.get("texture", 0) < TEXTURE_FLOOR:
            continue          # blank
        if best is None or row["score"] > best["score"]:
            best = row
    if best is None:
        return None
    return {"second": best["second"],
            "timestamp": seconds_to_stamp(best["second"]),
            "score": best["score"],
            "offset": best["second"] - start}


def _to_seconds(value):
    """Accept 484, '08:04' or '1:08:04'."""
    if isinstance(value, (int, float)):
        return int(value)
    parts = [int(p) for p in str(value).split(":")]
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def seconds_to_stamp(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600:02d}-{(seconds % 3600) // 60:02d}-{seconds % 60:02d}"
    return f"{seconds // 60:02d}-{seconds % 60:02d}"


# --- --clean-board: remove a presenter standing in front of the board ---------
#
# For each pixel, keep the value it has for most of the next CLEAN_WINDOW
# seconds (a temporal median, ffmpeg's tmedian). Writing stays put; a person
# who moves around vanishes. Tried 2026-10-21 on 4 + 4 frames a person had
# labelled "not useful" on two whiteboard lectures:
#   10 s centred on the frame   1 of 8 clearly readable (he had not moved yet)
#   40 s after the frame        8 of 8 showed the writing he was blocking
# Costs: a smudge where he stood longest, writing added in the next ~20 s can
# appear (sometimes half-written), anything erased in the window disappears.
# About 0.7 s per frame at 1080p. Copies go to clean/; originals untouched.

CLEAN_WINDOW = 40

# Is a clean copy actually clean? It is not when the presenter stays in one spot
# for much of the window (talking to camera): the median keeps a blurry ghost.
# Ghost share = share of a 160x90 thumbnail whose pixels differ from the window's
# median (by GHOST_DIFF) in at least GHOST_UNSETTLED of its seconds. Measured on
# 35 board frames a person labelled both ways (tests/score_clean.py): originals
# 28 useful, clean copies 27, "clean copy only when ghost share <= GHOST_MAX"
# 33 (whiteboard 11/11, Hindi lecture 22/24). 0.14-0.16 gave the same. Tuned on
# two videos only.
GHOST_DIFF = 25
GHOST_UNSETTLED = 0.4
GHOST_MAX = 0.14


def ghost_share(video, sec, duration, window=CLEAN_WINDOW):
    """Share of the picture where a clean copy of `sec` would keep a ghost."""
    w, h = 160, 90
    n = w * h
    window = max(3, min(window, int(duration)))
    start = max(0, min(sec, int(duration) - window - 1))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(window),
                          "-i", str(video), "-vf", f"fps=1,scale={w}:{h}",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True).stdout
    shots = [raw[i:i + n] for i in range(0, len(raw) - n + 1, n)]
    if len(shots) < 3:
        return 1.0
    need = max(1, int(GHOST_UNSETTLED * len(shots)))
    unsettled = 0
    for i in range(n):
        col = sorted(f[i] for f in shots)
        mid = col[len(col) // 2]
        if sum(1 for v in col if abs(v - mid) > GHOST_DIFF) >= need:
            unsettled += 1
    return unsettled / n


def clean_board(video, seconds, outdir, duration, window=CLEAN_WINDOW, width=None):
    """Write clean/frame_MM-SS.jpg for each second; returns the paths written."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    window = max(3, min(window, int(duration)))
    radius = (window - 1) // 2
    def one(sec):
        # near the end there are not 40 s left: slide the window back to fit
        start = max(0, min(sec, int(duration) - window - 1))
        vf = f"fps=1,tmedian=radius={radius}"
        if width:
            vf += f",scale={width}:-2:flags=lanczos"
        out = outdir / f"frame_{seconds_to_stamp(sec)}.jpg"
        # tmedian only outputs once it holds 2*radius+1 frames, so its FIRST
        # output frame is the median of the whole window
        proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(window + 2),
                               "-i", str(video), "-vf", vf, "-frames:v", "1", "-q:v", "2",
                               str(out), "-y"])
        return out if proc.returncode == 0 and out.exists() else None

    # each copy is its own ffmpeg run, so several at once (about 5 s each at 1080p)
    from concurrent.futures import ThreadPoolExecutor
    import os
    with ThreadPoolExecutor(max_workers=min(6, os.cpu_count() or 2)) as pool:
        return [p for p in pool.map(one, seconds) if p is not None]
