"""Command line entry point. Orchestrates the three stages."""

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import fetch, frames, transcript


def build_parser():
    p = argparse.ArgumentParser(
        prog="ytconv",
        description="Turn a video into a clean transcript and the frames that carry information.",
    )
    p.add_argument("source", help="YouTube URL, or a path to a local video file")
    p.add_argument("-o", "--out", default=None,
                   help="output directory for the transcript and frames (default: ./output/<id>)")
    p.add_argument("--work-dir", default=None,
                   help="where the large video file lives (default: a temp dir, deleted afterwards)")
    p.add_argument("--keep-video", action="store_true",
                   help="do not delete the downloaded video when finished")
    p.add_argument("--no-frames", action="store_true",
                   help="transcript only - skips the video download entirely")
    p.add_argument("--threshold", type=float, default=frames.DEFAULT_THRESHOLD,
                   help=f"frame score threshold (default {frames.DEFAULT_THRESHOLD})")
    p.add_argument("--max-frames", type=int, default=35,
                   help="maximum frames to keep (default 35)")
    p.add_argument("--frame-width", type=int, default=None,
                   help="downscale frames to this width (default: keep source 1080p)")
    p.add_argument("--debug-scores", action="store_true",
                   help="write scores.csv with every second's metrics and keep/drop reason")
    p.add_argument("--at", action="append", default=[], metavar="MM:SS",
                   help="find the visual for a transcript timestamp, searching "
                        "forward up to 2 minutes. Repeatable.")
    p.add_argument("--client", default=None,
                   help="force a single yt-dlp player client instead of the fallback chain")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.source.startswith("-"):
        # yt-dlp would read it as one of its own options (--exec runs commands).
        print("The source must be a URL or a file path, not something starting "
              "with '-'.", file=sys.stderr)
        return 2
    local = Path(args.source)
    is_local = local.exists() and local.is_file()

    out = Path(args.out) if args.out else Path("output") / _slug(args.source, is_local)
    out.mkdir(parents=True, exist_ok=True)

    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="ytconv-"))
    work.mkdir(parents=True, exist_ok=True)
    temp_work = args.work_dir is None and not args.keep_video

    info = {}
    try:
        # ---- Stage 1/2: captions and transcript -------------------------
        if is_local:
            print("[fetch] local file - skipping captions", flush=True)
            video = local
        else:
            print("[fetch] captions and metadata", flush=True)
            caps = fetch.fetch_captions(args.source, work)
            info = caps["info"]
            if info.get("title"):
                print(f"        {info['title']} ({_hms(info.get('duration') or 0)})", flush=True)

            _write_metadata(info, out)

            if caps["vtt"]:
                header = _header(info)
                stats = transcript.write_transcript(caps["vtt"], out / "transcript.txt", header)
                print(f"[transcript] {stats['lines']} lines, {stats['chars']:,} chars "
                      f"({stats['ratio']:.0%} of naive parse), "
                      f"ends at {_hms(stats['last_timestamp'])}", flush=True)
            else:
                print("[transcript] no English captions found for this video", flush=True)

            if args.no_frames:
                _report(out)
                return 0

            clients = [args.client] if args.client else None
            video = fetch.fetch_video(args.source, work, clients=clients)

        if args.no_frames:
            _report(out)
            return 0

        # ---- Stage 3: frames --------------------------------------------
        native = frames.source_width(video)
        if native and native < 1280:
            print(f"[frames] NOTE: source is only {native}px wide. Frames will be "
                  f"upscaled to {args.frame_width or 1280}px for legibility, but "
                  f"upscaling adds no real detail.", flush=True)
            if not args.frame_width:
                args.frame_width = 1280
        print("[frames] scanning one frame per second", flush=True)
        scanned = frames.scan(video)
        chosen, rows, meta = frames.select(
            scanned, threshold=args.threshold, max_frames=args.max_frames)
        print(f"[frames] {len(scanned)}s scanned, {meta['polarity']}-background video "
              f"(median luminance {meta['median_lum']}), "
              f"{meta['candidates']} candidates, {len(chosen)} kept", flush=True)

        # Always written: later stages search this instead of re-scanning.
        (out / "frame-scores.json").write_text(json.dumps(rows, indent=1))
        if args.debug_scores:
            frames.write_debug_csv(rows, out / "scores.csv")
            print(f"[frames] wrote {out / 'scores.csv'}", flush=True)

        # A concept is SPOKEN before it is DRAWN, so extract the visual for any
        # requested timestamp by searching forward, not at the timestamp itself.
        for stamp in args.at:
            hit = frames.find_visual(rows, stamp)
            if hit:
                print(f"[at {stamp}] visual at {_hms(hit['second'])} "
                      f"(+{hit['offset']}s, score {hit['score']:.2f})", flush=True)
                frames.extract(video, [hit["second"]], out / "frames",
                               width=args.frame_width)
            else:
                print(f"[at {stamp}] no still frame found within 2 minutes", flush=True)

        if chosen:
            written = frames.extract(video, [f["second"] for f in chosen],
                                     out / "frames", width=args.frame_width)
            print(f"[frames] extracted {len(written)} frames", flush=True)
            _write_frame_index(chosen, out)
        else:
            print("[frames] nothing scored above the threshold. "
                  "Re-run with --debug-scores --threshold 0.15 to see why.", flush=True)

        _report(out)
        return 0

    except fetch.FetchError as exc:
        # A clean message beats a traceback - none of this is a bug in the tool.
        print(f"\nCould not download the video.\n{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

    finally:
        if temp_work and work.exists():
            # shutil, not `rm -rf` - the shell form is sometimes blocked.
            shutil.rmtree(work, ignore_errors=True)


def _slug(source, is_local):
    """Folder name for the output. Only letters, digits, '-' and '_' survive, so
    a crafted URL such as youtu.be/../../x cannot write outside ./output."""
    if is_local:
        raw = Path(source).stem
    else:
        raw = "video"
        for key in ("v=", "youtu.be/", "/shorts/"):
            if key in source:
                raw = source.split(key)[1][:11]
                break
    return re.sub(r"[^A-Za-z0-9_-]", "", raw) or "video"


def _hms(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def _header(info):
    bits = [f"# {info.get('title') or 'Untitled'}"]
    if info.get("uploader"):
        bits.append(f"Uploader: {info['uploader']}")
    if info.get("duration"):
        bits.append(f"Duration: {_hms(info['duration'])}")
    return "\n".join(bits)


def _write_metadata(info, out):
    """title, uploader, duration, chapters - chapters are the cheapest outline
    of a video available, so they are kept even though nothing uses them yet."""
    (out / "metadata.json").write_text(json.dumps({
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "chapters": [{"start": c.get("start_time"), "title": c.get("title")}
                     for c in (info.get("chapters") or [])],
    }, indent=1))
    if info.get("chapters"):
        print(f"[transcript] {len(info['chapters'])} chapters", flush=True)


def _write_frame_index(chosen, out):
    lines = [f"frame_{frames.seconds_to_stamp(f['second'])}.jpg  "
             f"[{_hms(f['second'])}]  score={f['score']:.2f}" for f in chosen]
    (out / "frames.txt").write_text("\n".join(lines) + "\n")


def _report(out):
    print(f"\nDone. Output in {out.resolve()}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
