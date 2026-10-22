"""Stage 1 - fetch captions and video.

The only hard problem here is HTTP 403. YouTube rejects the download depending
on which "player client" yt-dlp pretends to be, and which clients work changes
over time. Measured against several videos:

    web_embedded   full 1080p stream, no 403          <- works today
    android_vr     resolves 1080p, then 403s mid-download
    tv_simply      only offers 360p
    mweb           only offers format 18 (360p muxed) <- the old note is stale
    default        403s

So the client is a fallback chain rather than a hardcoded name. When YouTube
changes again, the chain degrades instead of breaking.

A client can also succeed at low quality: on one Short, web_embedded delivered
a 360px stream while other clients offered 480px. So a download below 1080p is
kept as a fallback and the chain carries on looking for 1080p. The real file is
measured, not yt-dlp's claim - the delivered stream was smaller than the one
yt-dlp said it would pick.
"""

import json
import re
import subprocess
import time
from pathlib import Path

# Ordered best-first. Anything that resolves 1080p goes before the 360p-only
# clients, so we only fall back to low resolution as a last resort.
CLIENT_CHAIN = ["web_embedded", "android_vr", "tv_simply", "mweb", "default"]

# H.264 (avc1) is preferred over the smaller AV1 on purpose: stage 3 performs
# many independent seeks, and H.264 seeks far faster. The ~30MB extra is cheaper
# than the decode time.
VIDEO_FORMAT = (
    "bv[height<=1080][vcodec^=avc1]+ba/"
    "bv[height<=1080]+ba/"
    "b[height<=1080]"
)

# "1080p" measured on the short side, so a vertical 1080x1920 Short counts too.
TARGET_SHORT_SIDE = 1080


class FetchError(RuntimeError):
    """Raised when every client in the chain has failed."""


def _run(args, tee=False):
    """Run a command from an argument LIST - never a shell string.

    A shell string would be split by the shell, and zsh does not word-split
    unquoted variables, which silently mangles yt-dlp arguments. Passing a list
    to subprocess bypasses the shell entirely.

    Returns (returncode, combined_output). With tee=True the output is also
    printed as it arrives, so long downloads show progress.
    """
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        # On Windows text=True decodes with the legacy code page (cp1252), which
        # raises on a few byte values; a title in Hindi or Japanese must not
        # crash the run, so undecodable bytes become '?' instead.
        errors="replace",
        bufsize=1,
    )
    lines = []
    for line in proc.stdout:
        lines.append(line)
        if tee:
            print("    " + line.rstrip(), flush=True)
    proc.wait()
    return proc.returncode, "".join(lines)


def _client_args(client):
    if client == "default":
        return []
    return ["--extractor-args", f"youtube:player_client={client}"]


def fetch_captions(url, outdir, lang=None, translate=None):
    """Download the best caption track and the metadata. Works on the default client.

    Two steps, because asking for a track YouTube refuses aborts the whole
    yt-dlp run - captions AND metadata. YouTube refuses auto-translated tracks
    (HTTP 429, measured 2026-10-21), and a video whose metadata names the wrong
    language makes "en" an auto-translation. So:

      1. read the video's info: which human-written subtitles exist, which
         original-language automatic tracks ("xx-orig") exist
      2. choose ONE track and download only that

    Choice, best first:
      - human subtitles in the video's language (or the --lang override)
      - human subtitles in English (any variant: en, en-GB, en-US)
      - the original automatic track, preferring the video's language
    The metadata language is a hint, not a fact: Neso Academy (English) is
    tagged "hi", a Gate Smashers lecture (Hindi) is tagged "en", and on that
    one YouTube's English speech recognition produced nonsense while the
    uploader's English subtitles were good. Human tracks win for that reason.

    `translate` is fetched separately and may fail without harming the rest.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    code, out = _run([
        "yt-dlp", "--skip-download", "--write-info-json", "--no-playlist",
        "-o", str(outdir / "v"),
        "--", url,   # "--": what follows is a link, never an option
    ])
    info_path = outdir / "v.info.json"
    if not info_path.exists():
        raise FetchError(f"could not read the video's information:\n{out}")
    raw = json.loads(info_path.read_text(encoding="utf-8"))   # yt-dlp writes UTF-8
    human = list(raw.get("subtitles") or {})
    auto = list(raw.get("automatic_captions") or {})
    hint = lang or raw.get("language") or ""

    def same(code_, want):
        return bool(want) and code_.split("-")[0].lower() == want.split("-")[0].lower()

    choice = None   # (track code, is_human)
    for c in human:
        if same(c, hint):
            choice = (c, True)
            break
    if choice is None and lang:
        # Asked for a language: its own automatic track beats English subtitles.
        for c in auto:
            if c.endswith("-orig") and same(c, lang):
                choice = (c, False)
                break
    if choice is None:
        for c in human:
            if same(c, "en"):
                choice = (c, True)
                break
    if choice is None:
        origs = [c for c in auto if c.endswith("-orig")]
        origs.sort(key=lambda c: not same(c, hint))   # the hinted language first
        if origs:
            choice = (origs[0], False)

    vtt, language, source = None, hint or "unknown", None
    if choice is not None:
        track, is_human = choice
        got = _fetch_one_track(url, outdir, track, human=is_human)
        if got is True:
            vtt = outdir / f"v.{track}.vtt"
            language = _plain_code(track[:-len("-orig")] if track.endswith("-orig") else track)
            source = "captions (human)" if is_human else "captions (auto)"

    translated, translate_error = None, None
    if translate and not same(language, translate):
        tcode = next((c for c in human if same(c, translate)), None)
        is_human = tcode is not None
        tcode = tcode or translate
        got = _fetch_one_track(url, outdir, tcode, human=is_human)
        if got is True:
            translated = {"vtt": outdir / f"v.{tcode}.vtt", "lang": _plain_code(tcode),
                          "source": "human subtitles" if is_human else "auto-translated by YouTube"}
        else:
            translate_error = got

    # Chapters are the cheapest possible outline of a video - read them
    # before deciding anything else.
    info = {
        "title": raw.get("title"),
        "uploader": raw.get("uploader"),
        "duration": raw.get("duration"),
        "chapters": raw.get("chapters") or [],
    }
    return {"vtt": vtt, "info": info, "info_path": info_path,
            "language": language, "source": source, "translated": translated,
            "translate_error": translate_error}


def _plain_code(code):
    """'en-GB' and 'zh-Hans' stay; YouTube's internal 'en-ehkg1hFWq8A' becomes 'en'."""
    parts = code.split("-")
    if len(parts) > 1 and len(parts[1]) in (2, 4):
        return f"{parts[0]}-{parts[1]}"
    return parts[0]


def _fetch_one_track(url, outdir, lang, human=False, attempts=2):
    """One caption track on its own request, retrying once on HTTP 429.

    Measured 2026-10-21: YouTube answered 429 to EVERY auto-translated track
    (es, fr, de on two videos) while original and uploader-written tracks still
    downloaded in the same minute. So a 429 here usually means auto-translation
    is refused, not that we asked too fast; one retry is enough. Returns True if
    the track arrived, else the reason."""
    out = ""
    for i in range(attempts):
        if i:
            time.sleep(10)
        code, out = _run([
            "yt-dlp", "--skip-download",
            "--write-subs" if human else "--write-auto-subs",
            "--sub-langs", lang, "--sub-format", "vtt",
            "--no-playlist", "-o", str(outdir / "v"),
            "--", url,   # "--": what follows is a link, never an option
        ])
        if list(outdir.glob(f"v.{lang}.vtt")):
            return True
        if "429" not in out:
            break
    return "refused (HTTP 429)" if "429" in out else "not offered"


def fetch_audio(url, outdir, clients=None):
    """Download the audio track only, for speech-to-text. Same client chain as
    the video; quality is not an issue for speech, so the first success wins."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    failures = []
    for client in (clients or CLIENT_CHAIN):
        print(f"[fetch] audio via player client: {client}", flush=True)
        code, out = _run([
            "yt-dlp",
            *_client_args(client),
            "-f", "ba/b",
            "--no-playlist",
            "--newline",
            "-o", str(outdir / f"audio.{client}.%(ext)s"),
            "--", url,   # "--": what follows is a link, never an option
        ])
        got = sorted(outdir.glob(f"audio.{client}.*"))
        if code == 0 and got and got[0].stat().st_size > 0:
            return got[0]
        reason = _summarise_failure(out)
        failures.append(f"  {client}: {reason}")
        print(f"[fetch] {client} failed: {reason}", flush=True)
    raise FetchError("every client failed to download the audio:\n" + "\n".join(failures))


def _download(client, url, outdir):
    """One download attempt. Returns (path, short side, None) or (None, 0, reason)."""
    attempt = Path(outdir) / f"video.{client}.mp4"
    code, out = _run([
        "yt-dlp",
        *_client_args(client),
        "-f", VIDEO_FORMAT,
        "--merge-output-format", "mp4",
        # 10 MB requests: YouTube cut a whole-file 1080p stream off mid-way ten
        # times in a row on 2026-10-21; the same download in chunks completed.
        "--http-chunk-size", "10M",
        "--no-playlist",
        "--newline",
        "-o", str(attempt),
        "--", url,   # "--": what follows is a link, never an option
    ], tee=True)
    # Note: yt-dlp prints a "missing PO Token" WARNING and then downloads
    # anyway. The warning is not the error - only the exit code decides.
    if not (code == 0 and attempt.exists() and attempt.stat().st_size > 0):
        if attempt.exists():
            attempt.unlink()
        return None, 0, _summarise_failure(out)
    return attempt, _short_side(attempt), None


def _keep(attempt, target, best, client, side):
    attempt.replace(target)
    if best is not None and best[1].exists():
        best[1].unlink()
    print(f"[fetch] ok via {client} at {side}p "
          f"({target.stat().st_size / 1_000_000:.1f} MB)", flush=True)
    return target


def fetch_video(url, outdir, clients=None):
    """Download the video, trying each player client until one delivers 1080p.

    A download below 1080p is kept as a fallback while the remaining clients
    are tried; the best one is used if none reaches 1080p. Returns the path to
    the downloaded file. Raises FetchError with every client's failure if no
    client delivered anything.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / "video.mp4"

    failures = []
    best = None       # (short side in px, path, client) of the best download so far
    for client in (clients or CLIENT_CHAIN):
        # Once there is a fallback, ask before downloading again. A video that
        # is low quality everywhere would otherwise be downloaded five times.
        if best is not None:
            offered = _probe_short_side(client, url)
            if offered is not None and offered <= best[0]:
                print(f"[fetch] {client} offers only {offered}p - skipping", flush=True)
                continue

        print(f"[fetch] trying player client: {client}", flush=True)
        attempt, side, reason = _download(client, url, outdir)
        if attempt is None:
            failures.append(f"  {client}: {reason}")
            print(f"[fetch] {client} failed: {reason}", flush=True)
            continue

        if side >= TARGET_SHORT_SIDE:
            return _keep(attempt, target, best, client, side)

        print(f"[fetch] {client} delivered only {side}p, below "
              f"{TARGET_SHORT_SIDE}p - kept as a fallback", flush=True)
        if best is None or side > best[0]:
            if best is not None:
                best[1].unlink()
            best = (side, attempt, client)
        else:
            attempt.unlink()

    # YouTube varies between requests: on 2026-10-21 the same client gave one
    # video at 360p and, minutes later, at 1080p. So if a client still
    # advertises 1080p, give it exactly one more try before settling.
    if best is not None and best[0] < TARGET_SHORT_SIDE and not clients:
        for client in CLIENT_CHAIN:
            offered = _probe_short_side(client, url)
            if offered is None or offered < TARGET_SHORT_SIDE:
                continue
            print(f"[fetch] {client} advertises {offered}p - one more try", flush=True)
            time.sleep(5)
            attempt, side, reason = _download(client, url, outdir)
            if attempt is not None and side >= TARGET_SHORT_SIDE:
                return _keep(attempt, target, best, client, side)
            if attempt is not None:
                attempt.unlink()
            print(f"[fetch] retry gave {side or reason}", flush=True)
            break

    if best is not None:
        best[1].replace(target)
        print(f"[fetch] no client delivered {TARGET_SHORT_SIDE}p; using the best: "
              f"{best[0]}p via {best[2]} "
              f"({target.stat().st_size / 1_000_000:.1f} MB)", flush=True)
        return target

    raise FetchError(
        "every player client failed. Tried:\n" + "\n".join(failures)
        + "\n\nThe usual causes, most likely first:"
        + "\n  1. yt-dlp is out of date. YouTube changes often: `brew upgrade yt-dlp` (Mac) or `yt-dlp -U` (Windows)."
        + "\n  2. yt-dlp cannot solve YouTube's JavaScript challenge. It needs its"
        + "\n     challenge solver (yt-dlp-ejs) and a JavaScript runtime such as Deno."
        + "\n     Check: yt-dlp -v --simulate URL   (look for 'JS runtimes' and 'yt_dlp_ejs')"
        + "\n  3. This network blocks YouTube's video servers - common on school and"
        + "\n     work Wi-Fi. Try another network."
    )


def _short_side(path):
    """Measure the downloaded file's resolution - its shorter side, in pixels."""
    out = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path),
    ], capture_output=True, text=True).stdout
    m = re.search(r"(\d+),(\d+)", out)
    return min(int(m[1]), int(m[2])) if m else 0


def _probe_short_side(client, url):
    """Ask a client which resolution it would deliver, without downloading.

    Only used to skip clients that cannot beat the fallback already held. It
    can be optimistic, which is why the real download is measured afterwards.
    Returns None if the client cannot say.
    """
    code, out = _run([
        "yt-dlp",
        *_client_args(client),
        "-f", VIDEO_FORMAT,
        "--simulate",
        "--no-playlist",
        "--print", "%(width)sx%(height)s",
        "--", url,   # "--": what follows is a link, never an option
    ])
    sizes = re.findall(r"^(\d+)x(\d+)$", out, re.MULTILINE)
    if code != 0 or not sizes:
        return None
    w, h = sizes[-1]
    return min(int(w), int(h))


def _summarise_failure(out):
    """Pull the meaningful line out of yt-dlp's output."""
    for line in out.splitlines():
        if line.startswith("ERROR:"):
            return line[len("ERROR:"):].strip()
    return out.strip().splitlines()[-1] if out.strip() else "unknown error"
