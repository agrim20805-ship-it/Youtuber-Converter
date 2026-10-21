"""Measure speech-to-text against human subtitles.

    uv run --extra whisper python tests/score_stt.py --media /dir/with/test/videos [--write]

Two checks, on the first MINUTES of each video:
  transcribe  the English econ lecture: word error rate against the uploader's
              own English subtitles, for each engine, plus speed
  translate   the Hindi DBMS lecture: Whisper's translate-to-English mode
              against the uploader's English subtitles. A translation can be
              right in different words, so this reports shared content words
              (how much of the reference's meaning-bearing words came back),
              not an error rate.

word error rate = (words substituted + deleted + inserted) / words in the
reference, after lowercasing and dropping punctuation. Lower is better.
"""

import argparse
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ytconv import match, stt  # noqa: E402

MINUTES = 5
WORD = re.compile(r"[a-z0-9']+")


def words(text):
    return WORD.findall(text.lower().replace("’", "'"))


def wer(ref, hyp):
    """Word-level edit distance / reference length (two-row dynamic programme)."""
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / max(1, len(ref))


def reference(key, seconds, english=False):
    d = ROOT / "testdata" / key / "text"
    path = d / ("transcript.en.txt" if english else "transcript.txt")
    # a subtitle line that starts in the last few seconds runs past the cut
    return " ".join(t for s, t in match.read_transcript(path) if s < seconds - 3)


def clip(video, seconds, tmp):
    out = Path(tmp) / f"{video.parent.name}.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-t", str(seconds),
                    "-vn", "-ac", "1", "-ar", "16000", str(out), "-y"], check=True)
    return out


def run(audio, engine, task="transcribe", language=None, seconds=0):
    t0 = time.time()
    res = stt.transcribe(audio, engine=engine, task=task, language=language)
    took = time.time() - t0
    text = " ".join(t for s, t in res["segments"] if s < seconds - 3)
    return text, took


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--media", required=True)
    p.add_argument("--write", action="store_true", help="add a section to tests/RESULTS.md")
    args = p.parse_args()
    seconds = MINUTES * 60
    engines = stt.engines()
    if not engines:
        sys.exit(stt.INSTALL_HINT)

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        audio = clip(Path(args.media) / "slides" / "video.mp4", seconds, tmp)
        ref = words(reference("slides", seconds))
        for eng in engines:
            hyp, took = run(audio, eng, language="en", seconds=seconds)
            rows.append(f"| transcribe | econ lecture (English) | {eng} | "
                        f"{wer(ref, words(hyp)):.1%} word error rate | "
                        f"{took:.0f} s for {seconds} s of audio ({seconds / took:.1f}x real time) |")
            print(rows[-1], flush=True)

        audio = clip(Path(args.media) / "hindi" / "video.mp4", seconds, tmp)
        ref_tokens = match.tokens(reference("hindi", seconds, english=True))
        for eng in engines:
            hyp, took = run(audio, eng, task="translate", language="hi", seconds=seconds)
            got = match.tokens(hyp)
            share = len(ref_tokens & got) / max(1, len(ref_tokens))
            rows.append(f"| translate | Hindi DBMS lecture -> English | {eng} | "
                        f"{share:.0%} of the reference's content words came back | "
                        f"{took:.0f} s for {seconds} s of audio ({seconds / took:.1f}x real time) |")
            print(rows[-1], flush=True)

    text = ("| check | video | engine | result | speed |\n|---|---|---|---|---|\n"
            + "\n".join(rows))
    if args.write:
        res = ROOT / "tests" / "RESULTS.md"
        body = res.read_text()
        marker = "\n## Speech-to-text against human subtitles\n"
        head, _, tail = body.partition(marker)
        rest = ""
        if tail and "\n## " in tail:
            rest = "\n## " + tail.split("\n## ", 1)[1]
        res.write_text(head + marker + (
            f"\nFirst {MINUTES} minutes of each video, Whisper `small` model, on a Mac "
            "(M4 Pro). faster-whisper runs on the CPU; Homebrew's whisper.cpp can use the "
            "Mac's GPU, which is the likely reason it is faster. The reference is the "
            "uploader's own subtitles, which are lightly edited, so part of the error rate "
            "is wording, not mistakes. Made by `tests/score_stt.py`.\n\n" + text + "\n") + rest)
        print("\nupdated tests/RESULTS.md")


if __name__ == "__main__":
    main()
