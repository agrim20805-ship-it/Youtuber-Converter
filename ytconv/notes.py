"""--notes: one readable study pack (notes.html + notes.md) from a finished run.

Reads what the run already wrote - transcript.txt, metadata.json, frames.txt
and frames/*.jpg - and weaves them together. It contains no AI and calls no
API: the "Summary" line under each section is a placeholder that Claude fills
in later (the /youtube-converter skill does that).

Layout: one section per chapter (or per ~5 minutes when the video has none).
Inside a section the transcript is cut into paragraphs and every kept frame is
shown next to the paragraph it belongs to. Every timestamp links back to the
video at that second (YouTube sources only; a local file gets plain text).
"""

import html
import json
import re
from pathlib import Path

from . import frames as _frames
from . import match

PLACEHOLDER = "Summary: (not written - ask Claude to fill this in)"
SECTION_SECONDS = 300        # section length when the video has no chapters

# Paragraph rules (transcript lines are short cues, a few seconds each).
PARA_MIN_SECONDS = 45        # break at the next sentence end after this long...
PARA_MAX_SECONDS = 75        # ...or unconditionally after this long
PARA_MAX_CHARS = 900

# A frame at second S shows what was talked about slightly BEFORE it appeared
# (see AGENTS.md: visuals lag speech by ~8 s), so it belongs beside the
# transcript lines spoken from S-20 s to S+10 s.
FRAME_BEFORE = 20
FRAME_AFTER = 10

ID_RE = re.compile(r"[A-Za-z0-9_-]{6,20}")
FRAME_RE = re.compile(r"^frame_(?:(\d\d)-)?(\d\d)-(\d\d)\.jpg$")
STAMP_IN_LINE = re.compile(r"frame_(\S+?)\.jpg")


def write(out, source=None, is_local=False):
    """Write notes.html and notes.md into `out`. Returns the two paths."""
    out = Path(out)
    meta = _read_json(out / "metadata.json") or {}
    video_id = None if is_local else _video_id(meta, source)
    title = meta.get("title") or _title_from_transcript(out) or (
        Path(source).stem if source else out.name)
    lines = _read_lines(out / "transcript.txt")
    shots = _frame_list(out)

    end = max([meta.get("duration") or 0]
              + [s for s, _ in lines[-1:]] + [s for s in shots[-1:]]) + 1
    sections = _sections(meta.get("chapters") or [], end)
    for sec in sections:
        sec["paras"] = _paragraphs([l for l in lines if sec["start"] <= l[0] < sec["end"]])
        sec["frames"] = [s for s in shots if sec["start"] <= s < sec["end"]]
        _place_frames(sec)

    doc = {"title": title, "uploader": meta.get("uploader"), "video_id": video_id,
           "source": source, "is_local": is_local, "sections": sections,
           "has_transcript": bool(lines),
           # board videos: show the copy with the presenter removed where it came out clean
           "clean": _clean_seconds(out, shots)}
    (out / "notes.html").write_text(_html(doc), encoding="utf-8")
    (out / "notes.md").write_text(_markdown(doc), encoding="utf-8")
    placed = sum(len(p["frames"]) for s in sections for p in s["paras"]) \
        + sum(len(s["loose"]) for s in sections)
    print(f"[notes] {len(sections)} sections, {placed} frames placed "
          f"-> notes.html and notes.md", flush=True)
    return out / "notes.html", out / "notes.md"


# ---- reading what the run wrote -------------------------------------------

def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_lines(path):
    return match.read_transcript(path) if Path(path).exists() else []


def _title_from_transcript(out):
    try:
        first = (out / "transcript.txt").read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return None
    return first[2:].strip() if first.startswith("# ") else None


def _video_id(meta, source):
    vid = meta.get("id")
    if not vid and source:
        for key in ("v=", "youtu.be/", "/shorts/", "/live/", "/embed/"):
            if key in source:
                vid = re.split(r"[^A-Za-z0-9_-]", source.split(key)[1])[0]
                break
    return vid if vid and ID_RE.fullmatch(vid) else None


def _frame_list(out):
    """Seconds of the kept frames whose jpg exists. frames.txt is the list of
    frames the run chose; without it fall back to whatever is in frames/."""
    folder = out / "frames"
    names = []
    index = out / "frames.txt"
    if index.exists():
        names = [m.group(0) for m in
                 (STAMP_IN_LINE.search(l) for l in index.read_text(encoding="utf-8").splitlines()) if m]
    elif folder.is_dir():
        names = sorted(p.name for p in folder.glob("frame_*.jpg"))
    seconds = set()
    for name in names:
        m = FRAME_RE.match(name)
        if m and (folder / name).exists():
            h, mm, ss = m.groups()
            seconds.add(int(h or 0) * 3600 + int(mm) * 60 + int(ss))
    return sorted(seconds)


# ---- structure --------------------------------------------------------------

def _sections(chapters, end):
    starts = sorted((int(c["start"]), c.get("title") or "") for c in chapters
                    if c.get("start") is not None)
    if starts and starts[0][0] > 0:        # words before the first chapter
        starts.insert(0, (0, "Introduction"))
    if not starts:
        starts = [(t, "") for t in range(0, int(end), SECTION_SECONDS)] or [(0, "")]
    out = []
    for i, (start, title) in enumerate(starts):
        stop = starts[i + 1][0] if i + 1 < len(starts) else max(end, start + 1)
        out.append({"start": start, "end": stop,
                    "title": title or f"{_clock(start)} - {_clock(stop)}"})
    return out


def _paragraphs(lines):
    paras, cur = [], None
    for sec, text in lines:
        if cur:
            span = sec - cur["start"]
            ends = cur["text"].rstrip().endswith((".", "?", "!"))
            if (span >= PARA_MAX_SECONDS or len(cur["text"]) >= PARA_MAX_CHARS
                    or (span >= PARA_MIN_SECONDS and ends)):
                paras.append(cur)
                cur = None
        if cur is None:
            cur = {"start": sec, "end": sec, "text": "", "lines": [], "frames": []}
        cur["text"] = (cur["text"] + " " + text).strip()
        cur["lines"].append(sec)
        cur["end"] = sec
    if cur:
        paras.append(cur)
    return paras


def _place_frames(sec):
    """Attach each frame to the paragraph holding most of the transcript lines
    spoken from S-20 s to S+10 s (earlier paragraph wins a tie). If none of
    that window is in this section's transcript, use the paragraph nearest in
    time; with no transcript at all the frame goes in `loose`."""
    sec["loose"] = []
    for s in sec["frames"]:
        if not sec["paras"]:
            sec["loose"].append(s)
            continue
        lo, hi = s - FRAME_BEFORE, s + FRAME_AFTER
        counts = [sum(1 for t in p["lines"] if lo <= t <= hi) for p in sec["paras"]]
        if max(counts) > 0:
            best = counts.index(max(counts))
        else:
            best = min(range(len(sec["paras"])),
                       key=lambda i: abs(sec["paras"][i]["start"] - s))
        sec["paras"][best]["frames"].append(s)


# ---- shared helpers ---------------------------------------------------------

def _clock(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _watch_url(video_id, seconds=None):
    url = f"https://www.youtube.com/watch?v={video_id}"
    return url + (f"&t={int(seconds)}s" if seconds is not None else "")


def _clean_seconds(out, shots):
    """Seconds whose clean/ copy should be shown: clean/clean.json says which
    are free of a presenter ghost. No clean.json = no clean copies shown."""
    data = _read_json(out / "clean" / "clean.json") or {}
    ok = {r.get("second") for r in data.get("frames", []) if r.get("use") == "clean"}
    return {t for t in shots if t in ok and (out / _frame_path(t, clean=True)).exists()}


def _frame_path(seconds, clean=False):
    folder = "clean" if clean else "frames"
    return f"{folder}/frame_{_frames.seconds_to_stamp(seconds)}.jpg"


def _shown_path(doc, seconds):
    return _frame_path(seconds, clean=seconds in doc.get("clean", ()))


# ---- HTML -------------------------------------------------------------------

CSS = """
:root { color-scheme: light dark; --bg:#fff; --fg:#1d2330; --muted:#667085; --line:#e2e6ee; --accent:#2457d6; --card:#f6f8fc; }
@media (prefers-color-scheme: dark) { :root { --bg:#14171f; --fg:#e6e9f0; --muted:#98a2b3; --line:#2b3140; --accent:#7ea2ff; --card:#1b202b; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:16px/1.6 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 1.8rem; line-height: 1.25; margin: 0 0 4px; }
.byline { color: var(--muted); margin: 0 0 24px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
nav { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 12px 16px; margin-bottom: 32px; }
nav ol { margin: 0; padding-left: 20px; columns: 2 280px; }
section { border-top: 2px solid var(--line); padding-top: 12px; margin-top: 36px; }
h2 { font-size: 1.3rem; margin: 0 0 4px; }
.stamp { font-variant-numeric: tabular-nums; font-size: .85em; color: var(--muted); white-space: nowrap; }
.summary { color: var(--muted); font-style: italic; margin: 4px 0 16px; }
.block { display: flex; gap: 20px; align-items: flex-start; margin: 14px 0; }
.block .text { flex: 1 1 55%; min-width: 0; }
.block .text p { margin: 0; }
figure { margin: 0 0 12px; }
.shots { flex: 1 1 45%; min-width: 0; }
.shots figure:last-child { margin-bottom: 0; }
img { max-width: 100%; height: auto; border: 1px solid var(--line); border-radius: 6px; display: block; }
figcaption { font-size: .85em; color: var(--muted); margin-top: 4px; }
.block.textonly .text { flex-basis: 100%; }
.none { color: var(--muted); font-style: italic; }
@media (max-width: 720px) { .block { flex-direction: column; gap: 8px; } .shots, .block .text { flex-basis: auto; width: 100%; } }
"""


def _e(text):
    return html.escape(str(text), quote=True)


def _stamp_html(doc, seconds):
    label = _e(_clock(seconds))
    if doc["video_id"]:
        return (f'<a class="stamp" href="{_e(_watch_url(doc["video_id"], seconds))}" '
                f'target="_blank" rel="noopener">[{label}]</a>')
    return f'<span class="stamp">[{label}]</span>'


def _html(doc):
    p = ['<!doctype html>', '<html lang="en">', '<head>', '<meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         f'<title>{_e(doc["title"])} - notes</title>', f'<style>{CSS}</style>',
         '</head>', '<body>', '<main>', f'<h1>{_e(doc["title"])}</h1>']
    by = []
    if doc["uploader"]:
        by.append(_e(doc["uploader"]))
    if doc["video_id"]:
        by.append(f'<a href="{_e(_watch_url(doc["video_id"]))}" target="_blank" '
                  f'rel="noopener">Watch the video</a>')
    elif doc["is_local"] and doc["source"]:
        by.append(f'Local file: {_e(Path(doc["source"]).name)}')
    if by:
        p.append(f'<p class="byline">{" &middot; ".join(by)}</p>')
    if len(doc["sections"]) > 1:
        p.append('<nav><ol>')
        for i, s in enumerate(doc["sections"]):
            p.append(f'<li><a href="#s{i}">{_e(s["title"])}</a> '
                     f'<span class="stamp">{_e(_clock(s["start"]))}</span></li>')
        p.append('</ol></nav>')
    if not doc["has_transcript"]:
        p.append('<p class="none">No transcript was written for this video, '
                 'so only the frames are shown.</p>')
    for i, s in enumerate(doc["sections"]):
        p.append(f'<section id="s{i}">')
        p.append(f'<h2>{_e(s["title"])} {_stamp_html(doc, s["start"])}</h2>')
        p.append(f'<p class="summary">{_e(PLACEHOLDER)}</p>')
        for para in s["paras"]:
            p.append(_block_html(doc, _stamp_html(doc, para["start"]) + " " + _e(para["text"]),
                                 para["frames"]))
        if s["loose"]:
            p.append(_block_html(doc, "", s["loose"]))
        if not s["paras"] and not s["loose"] and doc["has_transcript"]:
            p.append('<p class="none">(nothing spoken in this section)</p>')
        p.append('</section>')
    p += ['</main>', '</body>', '</html>']
    return "\n".join(p) + "\n"


def _block_html(doc, text_html, shots):
    cls = "block" if shots else "block textonly"
    out = [f'<div class="{cls}">']
    if text_html:
        out.append(f'<div class="text"><p>{text_html}</p></div>')
    if shots:
        out.append('<div class="shots">')
        for s in shots:
            note = (f' · presenter removed (<a href="{_e(_frame_path(s))}">original</a>)'
                    if s in doc.get("clean", ()) else "")
            out.append(f'<figure><img src="{_e(_shown_path(doc, s))}" loading="lazy" '
                       f'alt="Frame at {_e(_clock(s))}">'
                       f'<figcaption>Frame at {_stamp_html(doc, s)}{note}</figcaption></figure>')
        out.append('</div>')
    out.append('</div>')
    return "\n".join(out)


# ---- Markdown ---------------------------------------------------------------

def _stamp_md(doc, seconds):
    label = _clock(seconds)       # no inner [] - "[[..]]" reads as a wikilink
    if doc["video_id"]:
        return f"[{label}]({_watch_url(doc['video_id'], seconds)})"
    return f"[{label}]"


def _markdown(doc):
    m = [f"# {doc['title']}", ""]
    if doc["uploader"]:
        m.append(f"Uploader: {doc['uploader']}  ")
    if doc["video_id"]:
        m.append(f"Video: {_watch_url(doc['video_id'])}")
    elif doc["is_local"] and doc["source"]:
        m.append(f"Local file: {Path(doc['source']).name}")
    m.append("")
    if not doc["has_transcript"]:
        m += ["_No transcript was written for this video, so only the frames are shown._", ""]
    for s in doc["sections"]:
        m += [f"## {s['title']} {_stamp_md(doc, s['start'])}", "", PLACEHOLDER, ""]
        for para in s["paras"]:
            m += _shots_md(doc, para["frames"])
            m += [f"{_stamp_md(doc, para['start'])} {para['text']}", ""]
        m += _shots_md(doc, s["loose"])
        if not s["paras"] and not s["loose"] and doc["has_transcript"]:
            m += ["_(nothing spoken in this section)_", ""]
    return "\n".join(m).rstrip() + "\n"


def _shots_md(doc, seconds):
    out = []
    for s in seconds:
        note = (f" - presenter removed ([original]({_frame_path(s)}))"
                if s in doc.get("clean", ()) else "")
        out += [f"![Frame at {_clock(s)}]({_shown_path(doc, s)})", "",
                f"_Frame at {_stamp_md(doc, s)}{note}_", ""]
    return out
