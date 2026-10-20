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


def fetch_captions(url, outdir):
    """Download captions and metadata. Works on the default client.

    The `en.*` wildcard silently matches nothing, so the languages are named
    explicitly. Returns a dict with the chosen caption file and video metadata.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    code, out = _run([
        "yt-dlp",
        "--skip-download",
        "--write-auto-subs", "--write-subs",
        "--sub-langs", "en-orig,en",
        "--sub-format", "vtt",
        "--write-info-json",
        "--no-playlist",
        "-o", str(outdir / "v"),
        url,
    ])
    # en-orig is the original track; en may be a translation. Prefer the original.
    vtt = None
    for name in ("v.en-orig.vtt", "v.en.vtt"):
        if (outdir / name).exists():
            vtt = outdir / name
            break

    # Asking for two tracks is asking for twice the requests, and the second one
    # can 429 after the first has already landed a complete file. A non-zero exit
    # is therefore not fatal on its own -- only the absence of a usable VTT is.
    if code != 0 and vtt is None:
        raise FetchError(f"caption download failed:\n{out}")

    info = {}
    info_path = outdir / "v.info.json"
    if info_path.exists():
        raw = json.loads(info_path.read_text())
        # Chapters are the cheapest possible outline of a video - read them
        # before deciding anything else.
        info = {
            "title": raw.get("title"),
            "uploader": raw.get("uploader"),
            "duration": raw.get("duration"),
            "chapters": raw.get("chapters") or [],
        }

    return {"vtt": vtt, "info": info, "info_path": info_path if info_path.exists() else None}


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
        attempt = outdir / f"video.{client}.mp4"
        code, out = _run([
            "yt-dlp",
            *_client_args(client),
            "-f", VIDEO_FORMAT,
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--newline",
            "-o", str(attempt),
            url,
        ], tee=True)

        # Note: yt-dlp prints a "missing PO Token" WARNING and then downloads
        # anyway. The warning is not the error - only the exit code decides.
        if not (code == 0 and attempt.exists() and attempt.stat().st_size > 0):
            reason = _summarise_failure(out)
            failures.append(f"  {client}: {reason}")
            print(f"[fetch] {client} failed: {reason}", flush=True)
            if attempt.exists():
                attempt.unlink()
            continue

        side = _short_side(attempt)
        if side >= TARGET_SHORT_SIDE:
            attempt.replace(target)
            if best is not None:
                best[1].unlink()
            print(f"[fetch] ok via {client} at {side}p "
                  f"({target.stat().st_size / 1_000_000:.1f} MB)", flush=True)
            return target

        print(f"[fetch] {client} delivered only {side}p, below "
              f"{TARGET_SHORT_SIDE}p - kept as a fallback", flush=True)
        if best is None or side > best[0]:
            if best is not None:
                best[1].unlink()
            best = (side, attempt, client)
        else:
            attempt.unlink()

    if best is not None:
        best[1].replace(target)
        print(f"[fetch] no client delivered {TARGET_SHORT_SIDE}p; using the best: "
              f"{best[0]}p via {best[2]} "
              f"({target.stat().st_size / 1_000_000:.1f} MB)", flush=True)
        return target

    raise FetchError(
        "every player client failed. Tried:\n" + "\n".join(failures)
        + "\n\nThe usual causes, most likely first:"
        + "\n  1. yt-dlp is out of date. YouTube changes often: `brew upgrade yt-dlp`."
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
        url,
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
