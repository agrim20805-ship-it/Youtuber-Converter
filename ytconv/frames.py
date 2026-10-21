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
DARK_FLOOR = 12.0         # fades to black
BRIGHT_CEILING = 243.0    # blank or washed-out slides
TEXTURE_FLOOR = 0.30      # essentially nothing on screen
DEDUP_DISTANCE = 6.0      # mean luminance difference below this = the same slide
MIN_GAP_SECONDS = 5

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
        out.append({
            "second": second,
            "lum": a["lum"],
            "saturation": a["saturation"],
            "mean_lum": a["mean_lum"],
            # Stillness is the gate. A slide is identical half a second later;
            # a person talking is not.
            "motion": sum(abs(x - y) for x, y in zip(a["lum"], b["lum"])) / len(a["lum"]),
        })
    return out


def _measure(buf):
    lum = [0.0] * (SCAN_W * SCAN_H)
    sat_total = 0
    for i in range(SCAN_W * SCAN_H):
        r, g, b = buf[i * 3], buf[i * 3 + 1], buf[i * 3 + 2]
        lum[i] = 0.299 * r + 0.587 * g + 0.114 * b
        sat_total += max(r, g, b) - min(r, g, b)
    return {"lum": lum,
            "saturation": sat_total / (SCAN_W * SCAN_H),
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


def select(frames, threshold=DEFAULT_THRESHOLD, max_frames=35,
           min_gap=MIN_GAP_SECONDS, motion_max=MOTION_MAX):
    """Gate on stillness, score on texture, then spread across the video."""
    med_tex = median(f["texture"] for f in frames) or 1e-6
    med_lum = median(f["mean_lum"] for f in frames)
    polarity = "light" if med_lum > 127 else "dark"

    debug = {}
    candidates = []
    for f in frames:
        f["score"] = f["texture"] / med_tex

        # Reject near EITHER extreme. The original rule dropped only dark frames,
        # which is right for dark-mode recordings and wrong for slides: a blank
        # white slide sailed straight through.
        if f["mean_lum"] < DARK_FLOOR:
            debug[f["second"]] = "drop: near-black"
        elif f["mean_lum"] > BRIGHT_CEILING:
            debug[f["second"]] = "drop: near-white"
        elif f["texture"] < TEXTURE_FLOOR:
            debug[f["second"]] = "drop: blank"
        elif f["motion"] >= motion_max:
            debug[f["second"]] = "drop: moving footage"
        elif f["score"] < threshold:
            debug[f["second"]] = "drop: below threshold"
        else:
            debug[f["second"]] = "candidate"
            candidates.append(f)

    chosen = []
    if candidates:
        duration = frames[-1]["second"] + 1
        bucket = max(1, duration / max_frames)
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
                if _distance(f["lum"], last_kept["lum"]) < DEDUP_DISTANCE:
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
    debug = {}
    candidates = []
    for f in frames:
        f["score"] = f["texture"] / med_tex
        if f["mean_lum"] < DARK_FLOOR:
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
