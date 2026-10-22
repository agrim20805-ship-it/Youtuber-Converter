"""Read the text on frames (OCR). Free, nothing to install on a Mac.

    macOS          Apple's Vision framework, through a small Swift helper
                   (ocr_vision.swift) compiled once into .cache/ by swiftc
    Windows/Linux  tesseract, if it is on PATH (free: github.com/tesseract-ocr)

If neither is available, OCR is skipped with a message - nothing else breaks.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths

ROOT = Path(__file__).resolve().parent.parent
SWIFT_SRC = Path(__file__).resolve().parent / "ocr_vision.swift"
# .cache/ in the project folder, or a per-user cache folder when installed (paths.py)
VISION_BIN = paths.cache_dir() / "ocr-vision"
BATCH = 40   # images per helper call

# Vision's own codes for the languages it reads (measured: 34 on macOS 28).
VISION_LANGS = {
    "en": "en-US", "fr": "fr-FR", "it": "it-IT", "de": "de-DE", "es": "es-ES", "pt": "pt-BR",
    "zh": "zh-Hans", "yue": "yue-Hans", "ko": "ko-KR", "ja": "ja-JP", "ru": "ru-RU",
    "uk": "uk-UA", "th": "th-TH", "vi": "vi-VT", "ar": "ar-SA", "tr": "tr-TR", "id": "id-ID",
    "cs": "cs-CZ", "da": "da-DK", "nl": "nl-NL", "no": "nb-NO", "nb": "nb-NO", "nn": "nn-NO",
    "ms": "ms-MY", "pl": "pl-PL", "ro": "ro-RO", "sv": "sv-SE", "fi": "fi-FI", "hi": "hi-IN",
    "mr": "mr-IN",
}
# tesseract's three-letter names for the same languages (traineddata must be installed)
TESS_LANGS = {
    "en": "eng", "fr": "fra", "it": "ita", "de": "deu", "es": "spa", "pt": "por", "zh": "chi_sim",
    "ko": "kor", "ja": "jpn", "ru": "rus", "uk": "ukr", "th": "tha", "vi": "vie", "ar": "ara",
    "tr": "tur", "id": "ind", "cs": "ces", "da": "dan", "nl": "nld", "no": "nor", "ms": "msa",
    "pl": "pol", "ro": "ron", "sv": "swe", "fi": "fin", "hi": "hin", "mr": "mar",
}


def engine():
    """'vision', 'tesseract' or None."""
    if sys.platform == "darwin" and (VISION_BIN.exists() or shutil.which("swiftc")):
        return "vision"
    if _tesseract():
        return "tesseract"
    return None


def _tesseract():
    """Path of tesseract, or None. The Windows installer does not add itself to
    PATH unless asked, so its usual install folders are checked as well."""
    found = shutil.which("tesseract")
    if found:
        return found
    if sys.platform == "win32":
        import os
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                exe = Path(base) / "Tesseract-OCR" / "tesseract.exe"
                if exe.exists():
                    return str(exe)
    return None


def _vision_bin():
    if VISION_BIN.exists() and VISION_BIN.stat().st_mtime >= SWIFT_SRC.stat().st_mtime:
        return VISION_BIN
    VISION_BIN.parent.mkdir(parents=True, exist_ok=True)
    print("[ocr] compiling the macOS text-recognition helper (once)", flush=True)
    proc = subprocess.run(["swiftc", "-O", str(SWIFT_SRC), "-o", str(VISION_BIN)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("could not compile the OCR helper:\n" + proc.stderr[-400:])
    return VISION_BIN


def _base(code):
    return (code or "en").split("-")[0].lower()


def read(images, languages=("en",)):
    """OCR a list of image paths. Returns {path: [{"text", "confidence"}, ...]};
    the macOS reader also gives each line a "box" (tesseract does not).

    `languages` are plain codes like "en" or "hi"; English is always included,
    because slides are so often in English whatever the spoken language.
    """
    images = [str(p) for p in images]
    codes = list(dict.fromkeys([_base(c) for c in languages] + ["en"]))
    which = engine()
    if which == "vision":
        return _read_vision(images, codes)
    if which == "tesseract":
        return _read_tesseract(images, codes)
    raise RuntimeError("no OCR engine: on Windows/Linux install tesseract (free); "
                       "on a Mac, the Xcode command line tools provide swiftc")


def _read_vision(images, codes):
    binary = _vision_bin()
    langs = ",".join(VISION_LANGS[c] for c in codes if c in VISION_LANGS) or "en-US"
    out = {}
    for i in range(0, len(images), BATCH):
        proc = subprocess.run([str(binary), langs, *images[i:i + BATCH]],
                              capture_output=True, text=True)
        for line in proc.stdout.splitlines():
            obj = json.loads(line)
            # box: [x, y, w, h] as 0-1 fractions, origin top-left (used by --mark)
            out[obj["image"]] = [{"text": l["text"], "confidence": l["confidence"],
                                  "box": l.get("box")} for l in obj["lines"]]
    return out


def _read_tesseract(images, codes):
    langs = "+".join(TESS_LANGS[c] for c in codes if c in TESS_LANGS) or "eng"
    out = {}
    exe = _tesseract()
    for img in images:
        # tesseract writes UTF-8; Windows would decode it as cp1252 (Hindi, CJK garbled)
        run = lambda lang: subprocess.run([exe, img, "stdout", "-l", lang], capture_output=True,
                                          text=True, encoding="utf-8", errors="replace")
        proc = run(langs)
        if proc.returncode != 0 and langs != "eng":
            proc = run("eng")   # a language pack that is not installed fails the whole call
        out[img] = [{"text": t.strip(), "confidence": None}
                    for t in proc.stdout.splitlines() if t.strip()]
    return out


def text_of(lines, min_confidence=0.3):
    """One string from the OCR lines, dropping low-confidence guesses."""
    return " ".join(l["text"] for l in lines
                    if l["confidence"] is None or l["confidence"] >= min_confidence)
