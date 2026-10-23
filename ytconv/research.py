"""Research mode: find videos on a topic, and check the note written from them.

Two jobs, neither of which uses AI:

- search(): `ytconv --search "topic"` searches YouTube, looks up each result's
  details (captions, date, chapters) and prints a table plus search.json. It
  ranks nothing - Claude (the /youtube-converter skill) picks the videos and
  says why.
- build_page(): `ytconv --research-page DIR` turns the research.md that Claude
  wrote into research.html, and checks every timestamp link in it against the
  transcripts in DIR/videos/<id>/: is the video there, is the time inside the
  video, and was something like the claim actually said near that time.
"""

import datetime
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import fetch, match, notes

DEFAULT_RESULTS = 10
MAX_RESULTS = 25
DETAIL_WORKERS = 6          # parallel detail look-ups (about 2 s each)
DETAIL_CLIENT = "web_embedded"
SHORT_SECONDS = 60          # this long or less counts as a Short
CITE_WINDOW = 30            # a citation at T is checked against what was said T-30..T+30 s
NOT_FROM_VIDEOS = "(not from the videos)"


# ---- search -----------------------------------------------------------------

def _yt_json(args):
    """Run yt-dlp and parse its JSON. Warnings go to stderr, so stdout stays JSON."""
    proc = subprocess.run(["yt-dlp", "--no-warnings"] + args, capture_output=True,
                          text=True, errors="replace")
    if proc.returncode != 0 or not proc.stdout.strip():
        lines = (proc.stderr or "").strip().splitlines()
        raise fetch.FetchError(lines[-1] if lines else f"yt-dlp exited {proc.returncode}")
    return json.loads(proc.stdout)


def _captions(info):
    """Short label: 'human en', 'auto en', 'auto hi', or 'none'."""
    human = [k for k in (info.get("subtitles") or {}) if k != "live_chat"]
    auto = info.get("automatic_captions") or {}
    original = [k[:-5] for k in auto if k.endswith("-orig")]
    if any(k == "en" or k.startswith("en-") for k in human):
        return "human en"
    if human:
        return "human " + human[0]
    if original:
        # A video with dubbed audio has one "-orig" track per dub: prefer the
        # video's own language, then English, then whatever comes first.
        spoken = (info.get("language") or "").split("-")[0].lower()
        bases = [k.split("-")[0].lower() for k in original]
        for want in (spoken, "en"):
            if want and want in bases:
                return "auto " + want
        return "auto " + original[0]
    if auto:
        return "auto"
    return "none"


def _details(entry):
    """Everything the flat search does not give: captions, date, chapters."""
    row = {"id": entry.get("id"), "url": f"https://www.youtube.com/watch?v={entry.get('id')}",
           "title": entry.get("title") or "", "channel": entry.get("channel") or entry.get("uploader") or "",
           "verified": bool(entry.get("channel_is_verified")),
           "duration": entry.get("duration"), "views": entry.get("view_count")}
    row["short"] = bool("/shorts/" in (entry.get("url") or "")
                        or (row["duration"] is not None and row["duration"] <= SHORT_SECONDS))
    try:
        info = _yt_json(["--skip-download", "-J", "--no-playlist"]
                        + fetch._client_args(DETAIL_CLIENT) + [row["url"]])
    except (fetch.FetchError, ValueError) as e:
        row.update(captions="?", language=None, uploaded=None, chapters=None,
                   live=False, error=str(e)[:200])
        return row
    row.update(captions=_captions(info), language=info.get("language"),
               uploaded=info.get("upload_date"), chapters=len(info.get("chapters") or []),
               live=info.get("live_status") in ("is_live", "is_upcoming"), error=None)
    row["duration"] = info.get("duration") or row["duration"]
    row["views"] = info.get("view_count") or row["views"]
    return row


def search(query, results=DEFAULT_RESULTS, out=None):
    """Search YouTube for `query`; write search.json into `out`; return the rows."""
    # The query goes after the ytsearch prefix as one list item, so yt-dlp can
    # never read it as an option - not even a query that starts with '-'.
    flat = _yt_json(["--flat-playlist", "-J", f"ytsearch{int(results)}:{query}"])
    entries = [e for e in flat.get("entries") or [] if e and e.get("id")]
    print(f"[search] {len(entries)} results for \"{query}\" - checking captions and details",
          flush=True)
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
        rows = list(pool.map(_details, entries))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    print(_table(rows), flush=True)
    if out is not None:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        data = {"query": query, "searched": datetime.date.today().isoformat(), "results": rows}
        (out / "search.json").write_text(json.dumps(data, indent=1, ensure_ascii=False),
                                         encoding="utf-8")
        print(f"[search] wrote {out / 'search.json'}", flush=True)
    return rows


def _length(seconds):
    if seconds is None:
        return "?"
    return notes._clock(seconds)


def _views(n):
    if n is None:
        return "?"
    for size, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return str(n)


def _table(rows):
    lines = [f"{'#':>2}  {'channel':<26} {'length':>8} {'views':>7}  {'captions':<9} "
             f"{'uploaded':<10} title"]
    for r in rows:
        channel = (r["channel"][:24] + (" ✓" if r["verified"] else ""))[:26]
        date = r.get("uploaded") or "?"
        if len(date) == 8:
            date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        flags = []
        if r.get("short"):
            flags.append("Short")
        if r.get("live"):
            flags.append("live")
        if r.get("error"):
            flags.append("details failed")
        title = r["title"][:60] + (f"  [{', '.join(flags)}]" if flags else "")
        lines.append(f"{r['rank']:>2}  {channel:<26} {_length(r['duration']):>8} "
                     f"{_views(r['views']):>7}  {r.get('captions') or '?':<9} {date:<10} {title}")
    return "\n".join(lines)


# ---- research.md -> research.html -------------------------------------------

INLINE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)\)"
    r"|\[(?P<text>[^\]]+)\]\((?P<href>[^)\s]+)\)"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|`(?P<code>[^`]+)`")
YOUTUBE = re.compile(r"^https://(?:www\.|m\.)?(?:youtube\.com/watch\?(?:[^#]*&)?v=|youtu\.be/)"
                     r"(?P<id>[A-Za-z0-9_-]{11})(?P<rest>[^#]*)")
TIME_PARAM = re.compile(r"[?&]t=(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s?)?(?:&|$)")


def _safe_href(url):
    """Only https links and plain relative paths are clickable. Anything else
    (javascript:, data:, file:, //host) is shown as text."""
    if url.startswith("https://"):
        return True
    return ":" not in url.split("/")[0] and not url.startswith("//")


def _inline(text):
    out, pos = [], 0
    for m in INLINE.finditer(text):
        out.append(notes._e(text[pos:m.start()]))
        pos = m.end()
        if m.group("src") is not None:
            src = m.group("src")
            if _safe_href(src):
                out.append(f'<img src="{notes._e(src)}" alt="{notes._e(m.group("alt"))}">')
            else:
                out.append(notes._e(m.group(0)))
        elif m.group("href") is not None:
            href = m.group("href")
            label = _inline(m.group("text"))
            if _safe_href(href):
                extra = ' target="_blank" rel="noopener"' if href.startswith("https://") else ""
                out.append(f'<a href="{notes._e(href)}"{extra}>{label}</a>')
            else:
                out.append(notes._e(m.group(0)))
        elif m.group("bold") is not None:
            out.append(f"<strong>{_inline(m.group('bold'))}</strong>")
        else:
            out.append(f"<code>{notes._e(m.group('code'))}</code>")
    out.append(notes._e(text[pos:]))
    return "".join(out)


HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
IMAGE_ONLY = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)$")
TABLE_RULE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse(md):
    """Split research.md into blocks: (kind, data, heading it sits under)."""
    blocks, para, heading = [], [], ""
    lines = md.splitlines()

    def flush():
        if para:
            blocks.append(("p", " ".join(para), heading))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            flush()
            i += 1
            continue
        m = HEADING.match(line)
        if m:
            flush()
            heading = m.group(2).strip()
            blocks.append(("h" + str(len(m.group(1))), heading, heading))
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) and TABLE_RULE.match(lines[i + 1].strip()):
            flush()
            rows = [_cells(line)]
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            blocks.append(("table", rows, heading))
            continue
        for kind, rx in (("ul", BULLET), ("ol", NUMBERED)):
            if rx.match(line):
                flush()
                items = []
                while i < len(lines) and rx.match(lines[i]):
                    items.append(rx.match(lines[i]).group(1).strip())
                    i += 1
                    # an indented line continues the item
                    while (i < len(lines) and lines[i].startswith(("  ", "\t"))
                           and lines[i].strip() and not BULLET.match(lines[i])
                           and not NUMBERED.match(lines[i])):
                        items[-1] += " " + lines[i].strip()
                        i += 1
                blocks.append((kind, items, heading))
                break
        else:
            if line.startswith(">"):
                flush()
                quote = []
                while i < len(lines) and lines[i].startswith(">"):
                    quote.append(lines[i][1:].strip())
                    i += 1
                blocks.append(("quote", " ".join(quote), heading))
                continue
            m = IMAGE_ONLY.match(line.strip())
            if m:
                flush()
                blocks.append(("figure", (m.group(1), m.group(2)), heading))
                i += 1
                continue
            para.append(line.strip())
            i += 1
            continue
        continue
    flush()
    return blocks


def render(blocks, title):
    p = ['<!doctype html>', '<html lang="en">', '<head>', '<meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width, initial-scale=1">',
         f'<title>{notes._e(title)} - research</title>',
         f'<style>{notes.CSS}{EXTRA_CSS}</style>', '</head>', '<body>', '<main>']
    for kind, data, _ in blocks:
        if kind[0] == "h":
            level = min(int(kind[1]), 3)
            p.append(f"<h{level}>{_inline(data)}</h{level}>")
        elif kind == "p":
            p.append(f"<p>{_inline(data)}</p>")
        elif kind in ("ul", "ol"):
            p.append(f"<{kind}>" + "".join(f"<li>{_inline(x)}</li>" for x in data) + f"</{kind}>")
        elif kind == "quote":
            p.append(f"<blockquote>{_inline(data)}</blockquote>")
        elif kind == "figure":
            alt, src = data
            if _safe_href(src):
                p.append(f'<figure><img src="{notes._e(src)}" alt="{notes._e(alt)}">'
                         f'<figcaption>{_inline(alt)}</figcaption></figure>')
            else:
                p.append(f"<p>{notes._e(f'![{alt}]({src})')}</p>")
        elif kind == "table":
            head, *body = data
            p.append('<div class="tablewrap"><table><thead><tr>'
                     + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                     + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>"
                               for row in body)
                     + "</tbody></table></div>")
    p += ['</main>', '</body>', '</html>']
    return "\n".join(p) + "\n"


EXTRA_CSS = """
h1 { margin-bottom: 16px; }
h2 { border-top: 2px solid var(--line); padding-top: 12px; margin-top: 32px; }
h3 { font-size: 1.1rem; margin: 20px 0 4px; }
blockquote { margin: 12px 0; padding: 8px 14px; border-left: 3px solid var(--accent); background: var(--card); border-radius: 0 6px 6px 0; }
.tablewrap { overflow-x: auto; margin: 12px 0; }
table { border-collapse: collapse; width: 100%; font-size: .92em; }
th, td { border: 1px solid var(--line); padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: var(--card); }
figure { margin: 16px 0; max-width: 760px; }
code { font-size: .9em; background: var(--card); padding: 1px 4px; border-radius: 4px; }
"""


# ---- citation check ---------------------------------------------------------

def _cited_seconds(rest):
    m = TIME_PARAM.search(rest)
    if not m or not any(m.groupdict().values()):
        return None
    return sum(int(m.group(k) or 0) * f for k, f in (("h", 3600), ("m", 60), ("s", 1)))


def _load_videos(folder):
    videos = {}
    for d in sorted((folder / "videos").glob("*")) if (folder / "videos").is_dir() else []:
        meta = notes._read_json(d / "metadata.json") or {}
        vid = meta.get("id") or d.name
        lines = notes._read_lines(d / "transcript.txt")
        duration = meta.get("duration") or (lines[-1][0] + 5 if lines else None)
        videos[vid] = {"duration": duration, "lines": lines, "title": meta.get("title"),
                       "folder": d.name}
    return videos


def _claim_text(text):
    """The words of a claim, without its links' URLs (link text is kept)."""
    return INLINE.sub(lambda m: m.group("text") or m.group("alt") or m.group("bold")
                      or m.group("code") or "", text)


# Words too common to show that a claim and a transcript are about the same thing.
FILLER = set("""
about after again against all along already although always among another any
anything around away back because before being below between both called came
come comes each even every first from give given goes good great just know
large last least less made make makes many might much must need never next
only other over part same see seem show shows since small something still such
take takes tell than through time times today together under until upon used
uses using want way ways well while whole without work works world year years
claim claims video videos explain explains explained says said say talk talks
""".split())
SHARED_NEEDED = 2           # content words a claim must share with what was said...
SHORT_CLAIM = 3             # ...unless the claim itself has this few content words


def _claim_words(text):
    """Content words of a claim: link names ("Kurzgesagt 05:00") and filler removed."""
    bare = INLINE.sub(lambda m: "" if m.group("href") else
                      (m.group("alt") or m.group("bold") or m.group("code") or ""), text)
    return {w for w in match.tokens(bare) if w not in FILLER and not w.isdigit()}


SKIP_HEADINGS = re.compile(r"question|source|none of them|not covered|frame|picture", re.I)


def check(blocks, videos):
    """Every YouTube timestamp link, checked. Returns a dict for research-check.json."""
    cites, uncited = [], []
    units = []
    for kind, data, heading in blocks:
        if kind in ("p", "quote"):
            units.append((data, heading))
        elif kind in ("ul", "ol"):
            units.extend((x, heading) for x in data)
        elif kind == "table":
            units.extend((" | ".join(row), heading) for row in data[1:])
    for text, heading in units:
        found = 0
        claim_words = _claim_words(text)
        for m in INLINE.finditer(text):
            href = m.group("href")
            y = YOUTUBE.match(href or "")
            if not y:
                continue
            sec = _cited_seconds(y.group("rest"))
            if sec is None:
                continue        # a plain link to a video (e.g. in the sources table)
            found += 1
            vid = y.group("id")
            row = {"video": vid, "second": sec, "label": m.group("text"),
                   "claim": _claim_text(text)[:160], "problem": None}
            v = videos.get(vid)
            if v is None:
                row["problem"] = "video not converted: no videos/<id>/ folder for it"
            elif v["duration"] is not None and sec > v["duration"] + 1:
                row["problem"] = (f"past the end: the video is only "
                                  f"{notes._clock(v['duration'])} long")
            elif not v["lines"]:
                row["problem"] = "no transcript to check against"
            else:
                said = " ".join(t for s, t in v["lines"] if sec - CITE_WINDOW <= s <= sec + CITE_WINDOW)
                shared = claim_words & match.tokens(said)
                row["shared_words"] = sorted(shared)[:12]
                needed = min(SHARED_NEEDED, max(1, len(claim_words) - SHORT_CLAIM + 1))
                if len(shared) < needed:
                    row["problem"] = (f"check by hand: what was said within {CITE_WINDOW} s "
                                      f"of this time shares {len(shared)} of the claim's "
                                      f"words ({', '.join(sorted(shared)) or 'none'})")
            cites.append(row)
        if (not found and NOT_FROM_VIDEOS not in text and not SKIP_HEADINGS.search(heading or "")
                and len(claim_words) >= SHORT_CLAIM + 1):
            uncited.append({"heading": heading, "text": _claim_text(text)[:160]})
    return {"citations": len(cites), "problems": [c for c in cites if c["problem"]],
            "ok": [c for c in cites if not c["problem"]], "uncited": uncited,
            "videos_found": sorted(videos)}


def build_page(folder):
    """research.md -> research.html + research-check.json. Returns the check dict."""
    folder = Path(folder)
    md_path = folder / "research.md"
    md = md_path.read_text(encoding="utf-8")
    blocks = parse(md)
    title = next((d for k, d, _ in blocks if k == "h1"), "Research")
    (folder / "research.html").write_text(render(blocks, _claim_text(title)), encoding="utf-8")
    result = check(blocks, _load_videos(folder))
    (folder / "research-check.json").write_text(json.dumps(result, indent=1, ensure_ascii=False),
                                                encoding="utf-8")
    print(f"[research] wrote {folder / 'research.html'}", flush=True)
    print(f"[research] {result['citations']} timestamp citations across "
          f"{len(result['videos_found'])} converted videos: "
          f"{len(result['ok'])} ok, {len(result['problems'])} to look at", flush=True)
    for c in result["problems"]:
        print(f"  - [{c['label']}] {c['video']} at {notes._clock(c['second'])}: {c['problem']}",
              flush=True)
    if result["uncited"]:
        print(f"[research] {len(result['uncited'])} paragraphs or bullets with no timestamp "
              f"citation (fine if they are not claims; mark own knowledge "
              f"\"{NOT_FROM_VIDEOS}\"):", flush=True)
        for u in result["uncited"][:10]:
            print(f"  - under \"{u['heading']}\": {u['text'][:90]}", flush=True)
    print("[research] details in research-check.json", flush=True)
    return result
