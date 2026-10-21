"""Speech to text, for videos with no captions. Free, runs on your own computer.

Two engines run the same open Whisper "small" model, so a problem in one does
not stop the other:

    faster-whisper  a Python extra:  uv sync --extra whisper  (or pip install faster-whisper)
    whisper.cpp     a separate program, called like ffmpeg and yt-dlp:
                    Mac: brew install whisper-cpp
                    Windows: the release zip from github.com/ggml-org/whisper.cpp
                    Linux: your package manager, or build it from that repo

Both keep their model in the project's models/ folder, downloaded once.

Audio is always decoded by ffmpeg, never by the PyAV package faster-whisper
normally uses: PyAV 19.0.2 crashed faster-whisper 1.2.1 on the very first test
(`open() got an unexpected keyword argument 'metadata_errors'`).
"""

import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL = "small"
GGML_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"
SAMPLE_RATE = 16000

INSTALL_HINT = (
    "No captions, and no free speech-to-text engine is set up. Either one works,\n"
    "both are free and run on your own computer:\n"
    "  uv sync --extra whisper        (or: pip install faster-whisper)\n"
    "  brew install whisper-cpp       (Windows/Linux: see the README)"
)


def engines():
    """The engines that can run here, best first."""
    found = []
    try:
        import faster_whisper  # noqa: F401
        found.append("faster-whisper")
    except ImportError:
        pass
    if _whisper_cli():
        found.append("whisper.cpp")
    return found


def _whisper_cli():
    return shutil.which("whisper-cli") or shutil.which("whisper-cpp")


def transcribe(media, engine=None, task="transcribe", language=None):
    """Transcribe (or, with task="translate", translate into English) an audio
    or video file. Returns {"engine", "language", "segments": [(start_s, text)]}.

    language=None lets the model detect it. Tries each available engine in turn
    unless one is named.
    """
    available = engines()
    order = [engine] if engine else available
    if not order:
        raise RuntimeError(INSTALL_HINT)
    errors = []
    for name in order:
        if name not in available:
            errors.append(f"{name}: not installed")
            continue
        try:
            run = _faster_whisper if name == "faster-whisper" else _whisper_cpp
            return run(Path(media), task, language)
        except Exception as exc:   # one engine failing must not stop the other
            errors.append(f"{name}: {exc}")
            print(f"[stt] {name} failed: {exc}", flush=True)
    raise RuntimeError("speech-to-text failed:\n  " + "\n  ".join(errors))


def _faster_whisper(media, task, language):
    import numpy as np
    from faster_whisper import WhisperModel

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(media), "-f", "f32le", "-ac", "1",
         "-ar", str(SAMPLE_RATE), "-"], capture_output=True, check=True).stdout
    audio = np.frombuffer(raw, np.float32)
    print(f"[stt] faster-whisper ({MODEL}) on {len(audio) / SAMPLE_RATE:.0f}s of audio", flush=True)
    model = WhisperModel(MODEL, device="cpu", compute_type="int8",
                         download_root=str(MODELS_DIR))
    segs, info = model.transcribe(audio, task=task, language=language, vad_filter=True)
    segments = [(s.start, s.text.strip()) for s in segs if s.text.strip()]
    return {"engine": "faster-whisper", "language": info.language, "segments": segments}


def _whisper_cpp(media, task, language):
    model = MODELS_DIR / "ggml-small.bin"
    if not model.exists():
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[stt] downloading the free whisper.cpp model once (~466 MB) to {model}", flush=True)
        part = model.with_suffix(".part")
        urllib.request.urlretrieve(GGML_URL, part)
        part.replace(model)

    with tempfile.TemporaryDirectory(prefix="ytconv-stt-") as tmp:
        wav = Path(tmp) / "audio.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(media), "-ar", str(SAMPLE_RATE),
                        "-ac", "1", "-c:a", "pcm_s16le", str(wav), "-y"], check=True)
        base = Path(tmp) / "out"
        args = [_whisper_cli(), "-m", str(model), "-f", str(wav),
                "-l", language or "auto", "-t", str(os.cpu_count() or 4),
                "-oj", "-of", str(base), "-np"]
        if task == "translate":
            args.append("-tr")
        print(f"[stt] whisper.cpp ({MODEL})", flush=True)
        proc = subprocess.run(args, capture_output=True, text=True)
        out = base.with_suffix(".json")
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError((proc.stderr or proc.stdout).strip()[-400:])
        data = json.loads(out.read_text(errors="replace"))
    segments = [(seg["offsets"]["from"] / 1000, seg["text"].strip())
                for seg in data.get("transcription", []) if seg.get("text", "").strip()]
    lang = data.get("result", {}).get("language")
    return {"engine": "whisper.cpp", "language": lang, "segments": segments}


def write_transcript(result, out_path, header=None):
    """Same `[MM:SS] text` format as the caption transcript."""
    segments = result["segments"]
    long_form = bool(segments) and segments[-1][0] >= 3600
    lines = []
    for start, text in segments:
        s = int(start)
        stamp = (f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}" if long_form
                 else f"{s // 60:02d}:{s % 60:02d}")
        lines.append(f"[{stamp}] {text}")
    body = "\n".join(lines) + "\n"
    if header:
        body = header + "\n\n" + body
    Path(out_path).write_text(body)
    return {"lines": len(lines), "chars": len(body),
            "last_timestamp": int(segments[-1][0]) if segments else 0}
