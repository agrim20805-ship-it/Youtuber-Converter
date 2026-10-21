"""Stage 2 - turn a .vtt caption file into a clean transcript.

YouTube's auto-captions scroll: each phrase reappears in two or three
consecutive cues. Parsing naively roughly triples the length (one 6-hour
transcript came out at 1.35M characters and reduced to 456K once deduplicated).

The five steps below were measured and are proven. Step 4 is the
one that does the real work - steps 1-3 alone leave most duplication behind.
"""

import html
import re

CUE_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})\.\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}\.\d{3}[^\n]*\n"
    r"((?:(?!\n\n)[\s\S])*)"
)
TAG_RE = re.compile(r"<[^>]*>")


def _clean(text):
    """Step 2 - strip inline <...> timing tags, decode HTML entities (human
    subtitles carry &nbsp; and &amp;), and collapse whitespace."""
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub("", text))).strip()


def _stamp(seconds, long_form):
    if long_form:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _drop_overlap(prev_words, cur_words):
    """Step 4 - find the longest overlap between the end of the previous cue
    and the start of this one, and drop that overlapping prefix.

    Compared word-by-word rather than character-by-character, so a word split
    across two cues cannot produce a bogus partial match.
    """
    for k in range(min(len(prev_words), len(cur_words)), 0, -1):
        if prev_words[-k:] == cur_words[:k]:
            return cur_words[k:]
    return cur_words


def parse_vtt(path):
    """Return (lines, stats). Each line is '[MM:SS] text'."""
    raw = open(path, encoding="utf-8", errors="replace").read()

    # Step 1 - regex out the timestamp/text blocks.
    cues = []
    for hh, mm, ss, text in CUE_RE.findall(raw):
        cleaned = _clean(text)
        if cleaned:
            cues.append((int(hh) * 3600 + int(mm) * 60 + int(ss), cleaned))

    naive_chars = sum(len(t) for _, t in cues)
    long_form = bool(cues) and cues[-1][0] >= 3600

    lines = []
    prev_words = []
    for seconds, text in cues:
        words = text.split()

        # Step 3 - skip a cue identical to, or fully contained in, the previous.
        if prev_words and (
            words == prev_words
            or " " + " ".join(words) + " " in " " + " ".join(prev_words) + " "
        ):
            continue

        # Step 4 - drop the overlapping prefix.
        new_words = _drop_overlap(prev_words, words) if prev_words else words
        if not new_words:
            continue

        # Step 5 - emit.
        lines.append(f"[{_stamp(seconds, long_form)}] {' '.join(new_words)}")
        prev_words = words

    kept_chars = sum(len(line) for line in lines)
    stats = {
        "cues": len(cues),
        "lines": len(lines),
        "naive_chars": naive_chars,
        "chars": kept_chars,
        "ratio": (kept_chars / naive_chars) if naive_chars else 0.0,
        "last_timestamp": cues[-1][0] if cues else 0,
    }
    return lines, stats


def write_transcript(vtt_path, out_path, header=None):
    lines, stats = parse_vtt(vtt_path)
    body = "\n".join(lines)
    if header:
        body = header.rstrip() + "\n\n" + body
    open(out_path, "w", encoding="utf-8").write(body + "\n")
    return stats
