"""Command line entry point. Orchestrates the three stages."""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import fetch, frames, mark, match, ocr, repeats, research, stt, transcript

LANG_RE = re.compile(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?")


def build_parser():
    p = argparse.ArgumentParser(
        prog="ytconv",
        description="Turn a video into a clean transcript and the frames that carry information.",
    )
    p.add_argument("source", nargs="?", default=None,
                   help="YouTube URL, or a path to a local video file "
                        "(not needed with --search or --research-page)")
    p.add_argument("--search", default=None, metavar="TOPIC",
                   help="research mode: search YouTube for TOPIC and list the results with "
                        "channel, length, views and captions (writes search.json)")
    p.add_argument("--results", type=int, default=None, metavar="N",
                   help="with --search: how many results to list "
                        "(default 10, at most 25)")
    p.add_argument("--research-page", default=None, metavar="DIR",
                   help="research mode: turn DIR/research.md into research.html and check "
                        "its timestamp citations against DIR/videos/<id>/")
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
    p.add_argument("--max-frames", type=int, default=None,
                   help=f"maximum frames to keep (default {frames.MIN_FRAMES}, or one per "
                        f"{frames.SECONDS_PER_FRAME} s on longer videos; 35 for --gameplay/--every)")
    p.add_argument("--motion-max", type=float, default=None,
                   help=f"seconds moving more than this are skipped as footage "
                        f"({frames.MOTION_MAX} for slides, {frames.BOARD_MOTION_MAX} for a "
                        f"presenter at a board). Giving it turns off the automatic mode choice")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--gameplay", action="store_true",
                      help="for games, sport and action: ignore stillness, keep the sharpest "
                           f"frame every {frames.GAMEPLAY_WINDOW}s plus big scene changes")
    mode.add_argument("--every", type=float, default=None, metavar="SECONDS",
                      help="no filtering: one frame every SECONDS seconds")
    p.add_argument("--max-same", type=int, default=repeats.MAX_SAME, metavar="N",
                   help=f"at most N frames of one slide or board (default {repeats.MAX_SAME}; "
                        "0 keeps every repeat). Needs OCR to check")
    p.add_argument("--frame-width", type=int, default=None,
                   help="downscale frames to this width (default: keep source 1080p)")
    p.add_argument("--debug-scores", action="store_true",
                   help="write scores.csv with every second's metrics and keep/drop reason")
    p.add_argument("--at", action="append", default=[], metavar="MM:SS",
                   help="the frame for a transcript timestamp: 8 s after it was "
                        "said (visuals lag speech), with its on-screen text checked "
                        "against the words. Repeatable.")
    p.add_argument("--mark", action="store_true",
                   help="also save copies of the frames in marked/ with a box around "
                        "the on-screen lines the speaker is talking about")
    p.add_argument("--clean-board", action="store_true",
                   help="also save copies of the frames in clean/ with a presenter who "
                        "stands in front of a whiteboard removed (uses the next 40 s). "
                        "On automatically when the tool picks board mode")
    p.add_argument("--no-clean-board", action="store_true",
                   help="board mode: do not make the presenter-removed copies")
    p.add_argument("--client", default=None,
                   help="force a single yt-dlp player client instead of the fallback chain")
    p.add_argument("--lang", default=None, metavar="CODE",
                   help="the video's spoken language, e.g. hi or es (default: detected)")
    p.add_argument("--translate", nargs="?", const="en", default=None, metavar="LANG",
                   help="also write transcript.LANG.txt in another language (default en). "
                        "Uses the uploader's subtitles, else YouTube's auto-translation")
    p.add_argument("--stt", action="store_true",
                   help="transcribe the audio with free speech-to-text even if captions "
                        "exist (and for local files)")
    p.add_argument("--stt-engine", choices=["faster-whisper", "whisper.cpp"], default=None,
                   help="force one speech-to-text engine (default: whichever is installed)")
    p.add_argument("--no-stt", action="store_true",
                   help="local files: do not transcribe the audio (a subtitle file next "
                        "to the video is still used)")
    p.add_argument("--notes", action="store_true",
                   help="also write notes.html and notes.md: the transcript by chapter with "
                        "each frame beside what was said and every timestamp linked to the video")
    return p


def _console_safe():
    """Never crash on printing. A Windows console is often cp1252, which cannot
    show Hindi, Japanese or emoji in a video title: print '?' instead of raising."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass   # not a normal text stream (e.g. captured by a test)


def _missing_tools(is_local, no_frames):
    """Programs the run needs that are not on PATH (shutil.which also finds .exe)."""
    need = []
    if not is_local:
        need.append("yt-dlp")
    if is_local or not no_frames:
        need += ["ffmpeg", "ffprobe"]
    return [t for t in need if not shutil.which(t)]


def main(argv=None):
    _console_safe()
    args = build_parser().parse_args(argv)
    modes = [m for m, v in (("a video", args.source), ("--search", args.search),
                            ("--research-page", args.research_page)) if v is not None]
    if len(modes) != 1:
        print("Give exactly one of: a video URL or file, --search \"topic\", or "
              "--research-page DIR" + (f" (got {' and '.join(modes)})." if modes else "."),
              file=sys.stderr)
        return 2
    if args.results is not None and args.search is None:
        print("--results only works with --search.", file=sys.stderr)
        return 2
    if args.research_page is not None:
        return _research_page(args.research_page)
    if args.search is not None:
        return _search(args)
    if args.source.startswith("-"):
        # yt-dlp would read it as one of its own options (--exec runs commands).
        print("The source must be a URL or a file path, not something starting "
              "with '-'.", file=sys.stderr)
        return 2
    if args.every is not None and args.every <= 0:
        print("--every needs a number of seconds above 0.", file=sys.stderr)
        return 2
    if args.max_frames is not None and args.max_frames < 1:
        print("--max-frames needs to be at least 1.", file=sys.stderr)
        return 2
    for name in ("lang", "translate"):
        value = getattr(args, name)
        if value is not None and not LANG_RE.fullmatch(value):
            print(f"--{name} needs a language code such as en, hi, es or zh-Hans.",
                  file=sys.stderr)
            return 2
    local = Path(args.source)
    try:
        is_local = local.exists() and local.is_file()
    except (OSError, ValueError):   # a URL is not a valid file name on Windows
        is_local = False

    missing = _missing_tools(is_local, args.no_frames)
    if missing:
        print("Missing program(s): " + ", ".join(missing) + ". Install them and make "
              "sure they are on your PATH (open a NEW terminal after installing); "
              "see the README, Requirements.", file=sys.stderr)
        return 2

    out = Path(args.out) if args.out else Path("output") / _slug(args.source, is_local)
    out.mkdir(parents=True, exist_ok=True)

    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="ytconv-"))
    work.mkdir(parents=True, exist_ok=True)
    temp_work = args.work_dir is None and not args.keep_video

    info = {}
    try:
        # ---- Stage 1/2: captions and transcript -------------------------
        if is_local:
            video = local
            _local_transcript(local, args, out)
        else:
            print("[fetch] captions and metadata", flush=True)
            caps = fetch.fetch_captions(args.source, work, lang=args.lang,
                                        translate=args.translate)
            info = caps["info"]
            if info.get("title"):
                print(f"        {info['title']} ({_hms(info.get('duration') or 0)})", flush=True)

            _write_metadata(info, out)

            if caps["vtt"] and not args.stt:
                header = _header(info, caps["language"], caps["source"])
                stats = transcript.write_transcript(caps["vtt"], out / "transcript.txt", header)
                dedup = (f" ({stats['ratio']:.0%} of naive parse)"
                         if caps["source"] == "captions (auto)" else "")
                print(f"[transcript] {caps['language']}, {caps['source']}: "
                      f"{stats['lines']} lines, {stats['chars']:,} chars{dedup}, "
                      f"ends at {_hms(stats['last_timestamp'])}", flush=True)
                if args.translate:
                    _write_caption_translation(caps, args.translate, info, out)
            else:
                if not caps["vtt"]:
                    print("[transcript] this video has no captions - using speech-to-text",
                          flush=True)
                if stt.engines():
                    clients = [args.client] if args.client else None
                    audio = fetch.fetch_audio(args.source, work, clients=clients)
                    _speech_to_text(audio, args, info, out)
                else:   # say so before downloading audio nothing can use
                    print("[stt] " + stt.INSTALL_HINT.replace("\n", "\n      "), flush=True)

            if args.no_frames:
                _notes(args, out, is_local)
                _report(out)
                return 0

            clients = [args.client] if args.client else None
            video = fetch.fetch_video(args.source, work, clients=clients)

        if args.no_frames:
            _notes(args, out, is_local)
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
        if not args.gameplay and args.every is None and args.motion_max is None:
            picked, why = frames.detect_mode(scanned)   # no mode given: choose one
            print(f"[frames] mode: {picked} - {why}. Choose yourself with --gameplay "
                  f"or --motion-max N", flush=True)
            args.gameplay = picked == "gameplay"
            args.motion_max = (frames.BOARD_MOTION_MAX if picked == "board"
                               else frames.MOTION_MAX)
            if picked == "board" and not args.clean_board and not args.no_clean_board:
                # the presenter covers the writing in most board frames
                args.clean_board = True
                print("[frames] board mode: also saving copies with the presenter "
                      "removed in clean/ (--no-clean-board skips this)", flush=True)
        elif args.motion_max is None:
            args.motion_max = frames.MOTION_MAX
        if args.max_frames is None:   # picker: grows with length; other modes: 35
            args.max_frames = (frames.MIN_FRAMES if args.gameplay or args.every is not None
                               else frames.auto_max_frames(len(scanned)))
        if args.gameplay:
            chosen, rows, meta = frames.select_gameplay(scanned, max_frames=args.max_frames)
            label = "gameplay mode"
        elif args.every is not None:
            chosen, rows, meta = frames.select_interval(
                scanned, args.every, max_frames=args.max_frames)
            label = f"one frame every {meta['step']:g}s"
            if meta["step"] > args.every:
                print(f"[frames] every {args.every:g}s would be more than --max-frames "
                      f"{args.max_frames}; widened to every {meta['step']:g}s", flush=True)
        else:
            chosen, rows, meta = frames.select(
                scanned, threshold=args.threshold, max_frames=args.max_frames,
                motion_max=args.motion_max)
            label = f"{meta['polarity']}-background video (median luminance {meta['median_lum']})"
        print(f"[frames] {len(scanned)}s scanned, {label}, "
              f"{meta['candidates']} candidates, {len(chosen)} kept", flush=True)
        if (not args.gameplay and args.every is None
                and 0 < args.max_same < len(chosen)):
            chosen = _drop_repeats(args, video, scanned, chosen, rows, meta, out)

        # Always written: later stages search this instead of re-scanning.
        (out / "frame-scores.json").write_text(json.dumps(rows, indent=1))
        if args.debug_scores:
            frames.write_debug_csv(rows, out / "scores.csv")
            print(f"[frames] wrote {out / 'scores.csv'}", flush=True)

        at_seconds = _at(args, video, scanned, out) if args.at else []

        if chosen:
            written = frames.extract(video, [f["second"] for f in chosen],
                                     out / "frames", width=args.frame_width)
            print(f"[frames] extracted {len(written)} frames", flush=True)
            _write_frame_index(chosen, out)
        else:
            print("[frames] nothing scored above the threshold. "
                  "Re-run with --debug-scores --threshold 0.15 to see why.", flush=True)

        if args.clean_board:
            wanted = sorted(set([f["second"] for f in chosen] + at_seconds))
            print(f"[clean] removing the presenter from {len(wanted)} frames "
                  f"(median of the next {frames.CLEAN_WINDOW} s)", flush=True)
            cleaned = frames.clean_board(video, wanted, out / "clean", len(scanned),
                                         width=args.frame_width)
            print(f"[clean] wrote {len(cleaned)} frames to clean/ - a smudge can remain where "
                  f"the presenter stood longest", flush=True)
            _judge_clean(video, wanted, len(scanned), out)

        if args.mark:
            _mark(args, [f["second"] for f in chosen] + at_seconds, out)

        _notes(args, out, is_local)
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


def _judge_clean(video, seconds, duration, out):
    """clean/clean.json: which clean copies are clean, and which keep a ghost of a
    presenter who hardly moved. The notes page shows only the clean ones."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as pool:
        ghosts = list(pool.map(lambda sec: frames.ghost_share(video, sec, duration), seconds))
    rows = []
    for sec, g in zip(seconds, ghosts):
        rows.append({"second": sec, "frame": f"frame_{frames.seconds_to_stamp(sec)}.jpg",
                     "ghost_share": round(g, 3),
                     "use": "clean" if g <= frames.GHOST_MAX else "original"})
    (out / "clean" / "clean.json").write_text(json.dumps(
        {"rule": f"use the clean copy when ghost_share <= {frames.GHOST_MAX}", "frames": rows},
        indent=1))
    good = sum(r["use"] == "clean" for r in rows)
    print(f"[clean] {good} of {len(rows)} copies look clean; the rest keep a ghost of the "
          f"presenter, so the notes page uses the original there (clean/clean.json)", flush=True)


def _search(args):
    results = research.DEFAULT_RESULTS if args.results is None else args.results
    if not 1 <= results <= research.MAX_RESULTS:
        print(f"--results needs a number from 1 to {research.MAX_RESULTS}.", file=sys.stderr)
        return 2
    if not args.search.strip():
        print("--search needs a topic to search for.", file=sys.stderr)
        return 2
    if not shutil.which("yt-dlp"):
        print("Missing program: yt-dlp. Install it and make sure it is on your PATH; "
              "see the README, Requirements.", file=sys.stderr)
        return 2
    slug = re.sub(r"[^a-z0-9]+", "-", args.search.lower()).strip("-")[:40] or "topic"
    out = Path(args.out) if args.out else Path("output") / f"research-{slug}"
    try:
        rows = research.search(args.search, results, out)
    except fetch.FetchError as e:
        print(f"[search] failed: {e}", file=sys.stderr)
        return 1
    if not rows:
        print("[search] no results.", file=sys.stderr)
        return 1
    print(f"\nNext: convert the videos you pick, each into {out / 'videos' / '<id>'}, e.g.\n"
          f"  ytconv \"{rows[0]['url']}\" --no-frames -o \"{out / 'videos' / rows[0]['id']}\"",
          flush=True)
    return 0


def _research_page(folder):
    folder = Path(folder)
    if not (folder / "research.md").is_file():
        print(f"No research.md in {folder}. Write the note first (the /youtube-converter "
              "skill does this in research mode).", file=sys.stderr)
        return 2
    research.build_page(folder)
    return 0


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


def _header(info, language=None, source=None):
    bits = [f"# {info.get('title') or 'Untitled'}"]
    if info.get("uploader"):
        bits.append(f"Uploader: {info['uploader']}")
    if info.get("duration"):
        bits.append(f"Duration: {_hms(info['duration'])}")
    if language:
        bits.append(f"Language: {language}")
    if source:
        bits.append(f"Source: {source}")
    return "\n".join(bits)


def _same_language(a, b):
    return bool(a and b) and a.split("-")[0].lower() == b.split("-")[0].lower()


def _write_caption_translation(caps, target, info, out):
    t = caps["translated"]
    if _same_language(caps["language"], target):
        print(f"[translate] the video is already in {target}", flush=True)
    elif t:
        path = out / f"transcript.{t['lang']}.txt"
        stats = transcript.write_transcript(t["vtt"], path, _header(info, t["lang"], t["source"]))
        print(f"[translate] {path.name}: {t['source']}, {stats['lines']} lines", flush=True)
    else:
        why = caps.get("translate_error") or "not offered"
        print(f"[translate] YouTube's {target} track: {why}. YouTube currently refuses most "
              f"auto-translated captions; uploader-written subtitles still work. Ask Claude to "
              f"translate transcript.txt - the /youtube-converter skill does this.", flush=True)


def _speech_to_text(media, args, info, out):
    """Transcript from the audio itself, with a free local engine."""
    if not stt.engines():
        print("[stt] " + stt.INSTALL_HINT.replace("\n", "\n      "), flush=True)
        return
    try:
        result = stt.transcribe(media, engine=args.stt_engine, language=args.lang)
    except RuntimeError as exc:
        print(f"[stt] {exc}", flush=True)
        return
    source = f"speech-to-text ({result['engine']}, Whisper {stt.MODEL})"
    stats = stt.write_transcript(result, out / "transcript.txt",
                                 _header(info, result["language"], source))
    print(f"[transcript] {result['language']}, {source}: {stats['lines']} lines, "
          f"ends at {_hms(stats['last_timestamp'])}", flush=True)

    target = args.translate
    if not target:
        return
    if _same_language(result["language"], target):
        print(f"[translate] the video is already in {target}", flush=True)
    elif target.split("-")[0].lower() == "en":
        print("[translate] translating the speech into English", flush=True)
        tr = stt.transcribe(media, engine=result["engine"], task="translate",
                            language=result["language"])
        stt.write_transcript(tr, out / "transcript.en.txt",
                             _header(info, "en", f"translated by {tr['engine']}"))
        print("[translate] transcript.en.txt written", flush=True)
    else:
        print(f"[translate] speech-to-text can only translate into English. Ask Claude to "
              f"translate transcript.txt into {target} - the /youtube-converter skill does "
              f"this.", flush=True)


def _local_transcript(local, args, out):
    """Transcript for a local video: a subtitle file next to it (video.srt,
    video.vtt, video.en.srt ...), else free speech-to-text if an engine is
    installed. --stt forces speech-to-text; --no-stt forbids it."""
    info = {"title": local.name}
    side = None if args.stt else transcript.find_sidecar(local, args.lang)
    if side:
        path, lang = side
        print(f"[fetch] local file - using the subtitle file {path.name}", flush=True)
        header = _header(info, lang or args.lang, f"subtitle file ({path.name})")
        stats = transcript.write_transcript(path, out / "transcript.txt", header)
        print(f"[transcript] subtitle file {path.name}: {stats['lines']} lines, "
              f"{stats['chars']:,} chars, ends at {_hms(stats['last_timestamp'])}", flush=True)
        if args.translate:
            print("[translate] not available for a subtitle file; ask Claude to translate "
                  "transcript.txt - the /youtube-converter skill does this.", flush=True)
        return
    print("[fetch] local file - " + ("--stt given, so any subtitle file is ignored" if args.stt
          else "no captions to fetch, no subtitle file next to it"), flush=True)
    if args.no_stt and not args.stt:
        print("[transcript] --no-stt: not transcribing; frames only", flush=True)
        return
    found = stt.engines()
    if not found:
        print("[stt] " + stt.INSTALL_HINT.replace("\n", "\n      "), flush=True)
        print("[transcript] none made; carrying on with frames only", flush=True)
        return
    engine = args.stt_engine if args.stt_engine in found else found[0]
    seconds = stt.media_duration(local)
    info["duration"] = seconds or None
    if seconds:
        est = seconds / stt.SPEED[engine]
        how_long = f"about {round(est / 5) * 5 or 5} s" if est < 90 else f"about {round(est / 60)} min"
        eta = f"{how_long} (audio is {_hms(seconds)}, ~{stt.SPEED[engine]:g}x real time)"
    else:
        eta = "this can take a while"
    print(f"[transcript] transcribing the audio with {engine}: {eta}. "
          f"--no-stt skips this.", flush=True)
    _speech_to_text(local, args, info, out)


def _write_metadata(info, out):
    """title, uploader, duration, chapters - chapters are the cheapest outline
    of a video available, so they are kept even though nothing uses them yet."""
    (out / "metadata.json").write_text(json.dumps({
        "id": info.get("id"),
        "url": info.get("webpage_url"),
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "chapters": [{"start": c.get("start_time"), "title": c.get("title")}
                     for c in (info.get("chapters") or [])],
    }, indent=1))
    if info.get("chapters"):
        print(f"[transcript] {len(info['chapters'])} chapters", flush=True)


def _at(args, video, scanned, out):
    """A concept is SPOKEN before it is DRAWN: take the frame 8 s later (the
    best of four rules on 45 hand-labelled moments - see match.py), then read
    its text and say whether it matches what was said."""
    transcripts = [match.read_transcript(p) for p in sorted(out.glob("transcript*.txt"))]
    reader = None
    if ocr.engine():
        langs = (args.lang or "en",)

        def reader(_video, seconds):
            got = {}
            for sec in seconds:
                path = out / "frames" / f"frame_{frames.seconds_to_stamp(sec)}.jpg"
                lines = ocr.read([path], languages=langs).get(str(path), [])
                got[sec] = ocr.text_of(lines)
            return got
    else:
        print("[at] no OCR engine, so the frame's text is not checked "
              "(free: tesseract on Windows/Linux)", flush=True)
    results = []
    for stamp in args.at:
        t = frames._to_seconds(stamp)
        pick = match.locate(stamp, scanned[-1]["second"])
        frames.extract(video, [pick["second"]], out / "frames", width=args.frame_width)
        hit = match.locate(stamp, scanned[-1]["second"], transcripts, reader, video)
        if hit["matches"] is True:
            check = "on-screen text matches: " + ", ".join(hit["words"][:5])
        elif hit["matches"] is False:
            check = "on-screen text does NOT match what was said - look a little later"
        else:
            check = "text not checked"
        print(f"[at {stamp}] frame at {_hms(hit['second'])} (+{hit['offset']}s); {check}",
              flush=True)
        results.append({"said_at": _hms(t), "frame": f"frame_{frames.seconds_to_stamp(hit['second'])}.jpg",
                        **{k: hit[k] for k in ("second", "offset", "method", "matches",
                                               "score", "words", "text")}})
    (out / "at.json").write_text(json.dumps(results, indent=1, ensure_ascii=False),
                                 encoding="utf-8")   # Windows default is cp1252
    return [r["second"] for r in results]


def _mark(args, seconds, out):
    if not ocr.engine():
        print("[mark] no OCR engine, so nothing can be boxed "
              "(free: tesseract on Windows/Linux, built in on a Mac)", flush=True)
        return
    transcripts = [match.read_transcript(p) for p in sorted(out.glob("transcript*.txt"))]
    if not transcripts:
        print("[mark] no transcript to match against (add --stt for a local file)", flush=True)
        return
    paths = {}
    for sec in sorted(set(seconds)):
        path = out / "frames" / f"frame_{frames.seconds_to_stamp(sec)}.jpg"
        if path.exists():
            paths[sec] = path
    results = mark.mark(paths, transcripts, out, languages=(args.lang or "en",))
    boxed = sum(1 for r in results if r.get("marked"))
    counts = {k: sum(1 for r in results if r.get("speech") == k)
              for k in ("on-slide", "no match", "no text")}
    print(f"[mark] {boxed} of {len(paths)} frames have lines matching the speech "
          f"-> marked/ (details in marked.json)", flush=True)
    print(f"[mark] speech vs screen: {counts['on-slide']} on-slide, "
          f"{counts['no match']} no match, {counts['no text']} no text", flush=True)


def _drop_repeats(args, video, scanned, chosen, rows, meta, out):
    """Keep at most --max-same frames of one slide or board (see repeats.py)."""
    if not ocr.engine():
        print("[frames] repeated slides not checked: that needs OCR "
              "(built in on a Mac; free tesseract on Windows/Linux)", flush=True)
        return chosen
    seconds = [f["second"] for f in chosen]
    with tempfile.TemporaryDirectory() as tmp:
        frames.extract(video, seconds, tmp, width=args.frame_width)
        paths = {s: str(Path(tmp) / f"frame_{frames.seconds_to_stamp(s)}.jpg") for s in seconds}
        lines = ocr.read(list(paths.values()), languages=(args.lang or "en",))
    texts = {s: ocr.text_of(lines.get(p, [])) for s, p in paths.items()}
    pics = repeats.thumbs(video, seconds, len(scanned))
    kept, trimmed = repeats.cap(seconds, pics, texts, meta["median_lum"], keep=args.max_same)
    if not trimmed:
        return chosen
    for t in trimmed:
        for r in rows:
            if r["second"] in t["group"] and r["second"] not in t["kept"]:
                r["decision"] = f"drop: same slide/board as {_hms(t['kept'][0])}"
    (out / "repeats.json").write_text(json.dumps(
        [{"same_slide": [_hms(s) for s in t["group"]], "kept": [_hms(s) for s in t["kept"]]}
         for t in trimmed], indent=1))
    print(f"[frames] {len(seconds) - len(kept)} repeats dropped: {len(trimmed)} slides or "
          f"boards were in more than {args.max_same} frames; kept the {args.max_same} "
          f"clearest of each (list in repeats.json, --max-same 0 keeps all)", flush=True)
    return [f for f in chosen if f["second"] in kept]


def _write_frame_index(chosen, out):
    lines = [f"frame_{frames.seconds_to_stamp(f['second'])}.jpg  "
             f"[{_hms(f['second'])}]  score={f['score']:.2f}" for f in chosen]
    (out / "frames.txt").write_text("\n".join(lines) + "\n")


def _notes(args, out, is_local):
    if args.notes:
        from . import notes
        notes.write(out, source=args.source, is_local=is_local)


def _report(out):
    print(f"\nDone. Output in {out.resolve()}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
