"""Match what was said to the text on screen.

No language model, nothing to install: words are compared after normalising
them, and words that appear on every frame (a channel name, a slide header)
count for little, because they say nothing about which slide is meant.

CJK text has no spaces between words, so Chinese/Japanese/Korean characters
are compared as overlapping character pairs instead.
"""

import math
import re
import unicodedata

STOPWORDS = set("""
a an and are as at be been but by can could did do does doing for from had has have
he her here him his how i if in into is it its just let lets like me more most my no
not now of on one or our out so some than that the their them then there these they
this those to up us very was we were what when where which who why will with would
you your yeah okay ok gonna going get got go really thing things also well right
""".split())

WORD = re.compile(r"\w+", re.UNICODE)


def _is_cjk(ch):
    name = unicodedata.name(ch, "")
    return name.startswith(("CJK", "HIRAGANA", "KATAKANA", "HANGUL"))


def tokens(text):
    """Normalised content words (and CJK character pairs) of a text."""
    out = set()
    for w in WORD.findall(unicodedata.normalize("NFKC", text).lower()):
        cjk = "".join(ch for ch in w if _is_cjk(ch))
        if cjk:
            out.update(cjk[i:i + 2] for i in range(max(1, len(cjk) - 1)))
            w = "".join(ch for ch in w if not _is_cjk(ch))
            if not w:
                continue
        if w in STOPWORDS or (len(w) < 3 and not w.isdigit()):
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]      # curves -> curve, slides -> slide
        out.add(w)
    return out


def idf(documents):
    """Weight per word: rare across the frames = informative."""
    n = len(documents)
    df = {}
    for doc in documents:
        for w in doc:
            df[w] = df.get(w, 0) + 1
    return {w: math.log((n + 1) / (c + 0.5)) for w, c in df.items()}


def score(spoken, slide, weights):
    """How well a slide's words match what was said: 0 (nothing) to 1.

    The harmonic mean of two shares, weighted by rarity: how much of the
    slide's text was spoken, and how much of the speech is on the slide.
    """
    if not spoken or not slide:
        return 0.0, []
    w = lambda t: weights.get(t, 1.0)
    common = spoken & slide
    if not common:
        return 0.0, []
    hit = sum(w(t) for t in common)
    p = hit / sum(w(t) for t in slide)
    r = hit / sum(w(t) for t in spoken)
    return 2 * p * r / (p + r), sorted(common, key=lambda t: -w(t))


# --- --at: where does the visual for a spoken moment appear? -----------------
#
# Measured 2026-10-21 against 45 moments a person labelled on 8 lectures
# (30 used to design the rules, then 15 fresh ones chosen from transcripts only):
#
#   frame at the spoken second                  9/45
#   said-at + 8 s                              29/45   <- the rule
#   old --at: stillest frame in next 2 min      4/45   (skips to a "nicer" frame)
#   earliest slide text matching the speech    20/45   (locate_by_text, below)
#
# Text matching loses because a slide's title appears first and the diagram
# builds under it seconds later, and short words ("tree", "data") match the
# wrong board. So --at picks said-at + 8 s, then READS that frame and reports
# whether its text matches the speech - a check for Claude, not a chooser.
# Weak spot: a teacher writing slowly (12-60 s late); nothing above catches it.

STAMP = re.compile(r"^\[(\d+):(\d\d)(?::(\d\d))?\] (.*)$")
SPOKEN_SECONDS = 20     # what was said from T to T+20s is what the slide must match
TEXT_WINDOW = 30        # look for matching text this far after T
LAG_SECONDS = 8         # with no matching text: the typical delay to the visual
MAX_PICTURES = 15       # distinct pictures OCR'd per search
MIN_SCORE = 0.08        # below this no slide text matches the speech


def read_transcript(path):
    """[(second, text)] from a transcript.txt written by ytconv."""
    out = []
    for line in open(path, encoding="utf-8"):
        m = STAMP.match(line.rstrip("\n"))
        if m:
            a, b, c = m.group(1), m.group(2), m.group(3)
            sec = int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)
            out.append((sec, m.group(4)))
    return out


def spoken_at(transcripts, start, seconds=SPOKEN_SECONDS):
    """One token set per transcript (e.g. original + English translation)."""
    return [tokens(" ".join(t for s, t in tr if start <= s <= start + seconds))
            for tr in transcripts]


def pictures(scan, start, end, same=6.0):
    """Split [start, end] into runs showing the same picture; for each run keep
    its first second and its stillest second (the sharpest to read)."""
    runs = []
    for f in scan:
        if not start <= f["second"] <= end:
            continue
        if runs and _distance(f["lum"], runs[-1]["anchor"]["lum"]) < same:
            run = runs[-1]
            if f["motion"] < run["best"]["motion"]:
                run["best"] = f
        else:
            runs.append({"first": f["second"], "anchor": f, "best": f})
    return runs


def _distance(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def locate(timestamp, last_second, transcripts=(), read_text=None, video=None):
    """The --at pick: said-at + LAG_SECONDS, plus a check of its text.

    `read_text(video, [second])` returns {second: text on screen}; with it the
    result says whether that text matches what was said ("matches": True/False),
    else "matches" is None. Returns {"second", "offset", "method", "matches",
    "score", "words", "text"}.
    """
    from .frames import _to_seconds
    t = _to_seconds(timestamp)
    second = max(t, min(t + LAG_SECONDS, last_second))
    out = {"second": second, "offset": second - t, "method": f"said-at + {LAG_SECONDS}s",
           "matches": None, "score": None, "words": [], "text": ""}
    if read_text is None:
        return out
    text = read_text(video, [second]).get(second, "")
    out["text"] = text
    spoken = spoken_at(transcripts, t) if transcripts else []
    if any(spoken):
        sc, words = max((score(sp, tokens(text), {}) for sp in spoken), key=lambda x: x[0])
        out.update(score=round(sc, 3), words=words[:8], matches=sc >= MIN_SCORE)
    return out


def locate_by_text(scan, video, transcripts, timestamp, read_text=None):
    """REJECTED rule, kept so tests/score_at.py can keep measuring it: the
    earliest picture in the next TEXT_WINDOW s whose text matches the speech.

    `read_text(video, seconds)` returns {second: text on screen}; pass None
    when no OCR engine is available. Returns {"second", "offset", "method",
    "score", "words", "text"}.
    """
    from .frames import _to_seconds
    t = _to_seconds(timestamp)
    last = scan[-1]["second"] if scan else t
    fallback = {"second": min(t + LAG_SECONDS, last), "offset": min(LAG_SECONDS, last - t),
                "method": f"said-at + {LAG_SECONDS}s (no matching text on screen)",
                "score": None, "words": [], "text": ""}
    spoken = spoken_at(transcripts, t) if transcripts else []
    if read_text is None or not any(spoken):
        return fallback
    runs = pictures(scan, t, t + TEXT_WINDOW)
    if len(runs) > MAX_PICTURES:
        step = len(runs) / MAX_PICTURES
        runs = [runs[int(i * step)] for i in range(MAX_PICTURES)]
    texts = read_text(video, [r["best"]["second"] for r in runs])
    docs = [tokens(texts.get(r["best"]["second"], "")) for r in runs]
    weights = idf(docs)
    for run, doc in zip(runs, docs):          # earliest picture that matches
        sc, words = max((score(sp, doc, weights) for sp in spoken), key=lambda x: x[0])
        if sc >= MIN_SCORE:
            return {"second": run["first"], "offset": run["first"] - t,
                    "method": "matching text on screen", "score": round(sc, 3),
                    "words": words[:8], "text": texts.get(run["best"]["second"], "")}
    return fallback
