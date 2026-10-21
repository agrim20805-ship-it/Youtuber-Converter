# ytconv

Turns a YouTube URL into a readable transcript and a folder of frames that are
almost all worth looking at.

Built for lectures, but it works on any video - gaming clips, tutorials,
references for Claude. **Videos without captions work too:** a free
speech-to-text model runs on your own computer and writes the transcript
([details](#any-video-even-without-captions---free)). Videos in other languages
work, and can be translated to English.

By default, frames are picked where the screen holds still (slides, code,
diagrams). For gameplay and other fast footage, use
[`--gameplay`](#gameplay-and-fast-footage) instead.

<a href="https://www.youtube.com/shorts/72ZZiSuqWXU"><img src="https://img.youtube.com/vi/72ZZiSuqWXU/maxresdefault.jpg" alt="Watch the 30-second demo on YouTube" width="480"></a>

**[▶ Watch the 30-second demo](https://www.youtube.com/shorts/72ZZiSuqWXU)**

**Contents:** [Requirements](#requirements) · [Setup](#setup) ·
[Without uv (pip)](#without-uv-pip) · [Use](#use) ·
[No captions - free speech-to-text](#any-video-even-without-captions---free) ·
[Languages and translation](#languages-and-translation) ·
[The timestamp gap](#the-timestamp-gap) ·
[Reading the screen](#reading-the-screen-mark-and-clean-board) ·
[Useful flags](#useful-flags) · [How many frames](#choosing-how-many-frames) ·
[Gameplay](#gameplay-and-fast-footage) · [How well it works](#how-well-it-works) ·
[Claude Code skill](#use-it-with-claude-code) · [Ears](#experimental-ears) ·
[Just ask Claude](#or-just-ask-claude) · [Disclaimer](#disclaimer)

## Requirements

The three tools it needs are free. On a Mac, install them with
[Homebrew](https://brew.sh):

```bash
brew install yt-dlp ffmpeg uv
```

`uv` is optional - it only sets up Python for you. To use pip instead, see
[Without uv (pip)](#without-uv-pip).

**Why Homebrew's yt-dlp.** YouTube now sends a small JavaScript puzzle before
it serves a video. Homebrew's yt-dlp comes with the solver for
that puzzle (`yt-dlp-ejs`) and a JavaScript runtime to run it (Deno). If you
install yt-dlp another way - `pip install yt-dlp`, for example - you also need
that solver and one JavaScript runtime (Deno, Node, Bun or QuickJS), or
downloads can fail. To check what yours has:

```bash
yt-dlp -v --simulate "https://www.youtube.com/watch?v=VIDEO_ID" 2>&1 | grep -oE "JS runtimes: [^ ]+|yt_dlp_ejs-[0-9.]+"
```

You want to see both a `yt_dlp_ejs-...` line and a `JS runtimes: ...` line
naming a runtime.

**Networks.** Some networks - many school and work Wi-Fi networks among them -
block the servers YouTube streams video from. On those, the video download fails
even with everything installed. Untested whether transcripts still work there.

**Captions.** The transcript comes from the video's own captions when it has
them, which is fast. The uploader's own subtitles are used first, then
YouTube's automatic ones. A video with no captions at all needs the free
speech-to-text add-on - see
[Any video, even without captions](#any-video-even-without-captions---free).

**Video quality.** The tool aims for 1080p. If a download comes back lower,
it keeps it as a fallback and tries the other yt-dlp clients, checking first
whether each can do better so it doesn't download the same thing five times.
Some videos are simply not available in 1080p - in testing, a YouTube Short
topped out at 480p. Then it uses the best it found, says so, and upscales the
frames, which adds no real detail.

## Setup

Once, to create the private Python folder this project runs in:

```bash
uv sync
```

The basic tool installs no Python packages - everything is done by `yt-dlp`
and `ffmpeg`. Speech-to-text is an optional add-on, below.

### Without uv (pip)

The tool itself is plain Python with nothing to install, so uv is only a
convenience. With pip instead, you need **Python 3.10 or newer** (check with
`python3 --version`; get it from [python.org](https://www.python.org/downloads/)
if yours is older) and `ffmpeg` installed separately (`brew install ffmpeg` on
a Mac, or [ffmpeg.org](https://ffmpeg.org/download.html)).

From the project folder, once:

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install "yt-dlp[default,deno]"
```

The first command makes a private Python folder, the second switches it on (on
Windows: `.venv\Scripts\activate`), and the third installs yt-dlp into it. The
`[default,deno]` part matters: it also installs the solver for YouTube's
JavaScript puzzle and Deno to run it (see Requirements).

Then, in every command in this README, replace `uv run python` with `python`:

```bash
python -m ytconv "https://www.youtube.com/watch?v=VIDEO_ID"
```

In a new terminal window, run `source .venv/bin/activate` again first - that
is what lets the tool find the yt-dlp you installed.

## Use

```bash
uv run python -m ytconv "https://www.youtube.com/watch?v=VIDEO_ID"
```

(With the pip setup: `python -m ytconv "URL"`, after activating `.venv`.)

Output lands in `output/<video_id>/`:

| File | What it is |
|---|---|
| `transcript.txt` | one `[MM:SS] text` line per cue, deduplicated. The first lines say the language and where it came from |
| `transcript.en.txt` | with `--translate`: the same in English (or another language) |
| `metadata.json` | title, uploader, duration, chapters |
| `frames/` | `frame_MM-SS.jpg` - only frames showing real screen content |
| `frame-scores.json` | the score for every second, so later stages can search instead of re-scanning |
| `at.json` | with `--at`: the frame picked for each moment and the text read off it |
| `marked/`, `marked.json` | with `--mark`: frames with boxes around what is being talked about |
| `clean/` | with `--clean-board`: frames with the presenter removed from the whiteboard |

The video itself is downloaded to a temp directory and deleted afterwards, since
these files are large. `--keep-video` keeps it, `--work-dir DIR` chooses where.

## Any video, even without captions - free

**It's free, and it runs on your own computer.** No account, no API key, nothing
is sent anywhere. When a video has no captions, ytconv downloads just its audio
and transcribes it with [Whisper](https://github.com/openai/whisper), an
open-source speech-to-text model, using its `small` size. Install **one** of
these two engines - both are free:

**Option 1 - faster-whisper (Python).** From the project folder:

```bash
uv sync --extra whisper
```

or, with the pip setup: `pip install faster-whisper`. Then add `--extra whisper`
to your runs: `uv run --extra whisper python -m ytconv "URL"`.

**Option 2 - whisper.cpp (a separate program, no Python packages).**

- Mac: `brew install whisper-cpp`
- Windows: download the release zip from
  [github.com/ggml-org/whisper.cpp/releases](https://github.com/ggml-org/whisper.cpp/releases)
  and put the folder containing `whisper-cli.exe` on your PATH
- Linux: install it from your package manager if it has it, or build it from
  [source](https://github.com/ggml-org/whisper.cpp)

Only the Mac route has been tested. The first run downloads the model once
(about 465 MB, into this project's `models/` folder).

It then happens on its own: a video without captions gets a transcript, and the
first lines of `transcript.txt` say it came from speech-to-text. To use it even
when captions exist - or on a video file on your computer - add `--stt`:

```bash
uv run python -m ytconv "lecture.mp4" --stt
```

`--stt-engine faster-whisper` or `--stt-engine whisper.cpp` picks one if you
installed both. Measured on 5 minutes of an English lecture against the
uploader's own subtitles:

| Engine | Word error rate | Speed on an M4 Mac |
|---|---|---|
| faster-whisper | 5.3% | 4.3x faster than real time |
| whisper.cpp | 5.2% | 7.9x faster than real time |

## Languages and translation

The transcript comes out in the language the video is spoken in. YouTube's
idea of that language is sometimes wrong, so you can say it yourself:

```bash
uv run python -m ytconv "URL" --lang hi
```

`--translate` also writes an English copy, `transcript.en.txt`, and
`--translate es` (or any language code) writes another language:

```bash
uv run python -m ytconv "URL" --lang hi --translate
```

Where the translation comes from, in order:

1. **The uploader's own subtitles in that language** - the best source, and
   the one that works most often.
2. **YouTube's automatic translation.** In testing (October 2026) YouTube
   refused almost every request for these with "too many requests". The tool
   then says so clearly.
3. **No captions at all, translating to English:** Whisper's translate mode.
   On 5 minutes of a Hindi lecture, about 80% of the meaning-bearing words of
   the uploader's English subtitles came back.

When none of those work, the [Claude Code skill](#use-it-with-claude-code) has
Claude translate `transcript.txt` itself, keeping every timestamp.

## The timestamp gap

A concept is spoken before it is drawn. Extracting a frame at the exact
second something is said usually gets the presenter mid-sentence, before the
slide changes or the diagram is drawn.

`--at` gives you the frame for a moment in the transcript:

```bash
uv run python -m ytconv "URL" --at 00:52 --at 02:46
```

```
[at 00:52] frame at 0:01:00 (+8s); on-screen text matches: array, two, memory, list, ways
```

It takes the frame **8 seconds after** the moment, because that was the best of
four rules tested on 45 moments a person marked by hand
([results](#how-well-it-works)). Then it reads the text on that frame and
checks it against what was said. If the words don't match, it says
`does NOT match what was said - look a little later`, so you (or Claude) know
to look further on. Reading the text needs the text reader described below;
without one, `--at` still picks the frame.

## Reading the screen: --mark and --clean-board

ytconv can read the text on a frame (OCR). On a Mac this uses the text reader
built into macOS - nothing to install (it needs Apple's command line tools,
which Homebrew already installed). On Windows and Linux it uses
[tesseract](https://github.com/tesseract-ocr/tesseract) if you install it
(free); that route is untested.

**`--mark`** saves a copy of each frame in `marked/` with a red box around the
lines on screen that the speaker is talking about at that moment. Words said
all through the lecture (its topic) don't count, so the title isn't boxed on
every frame. `marked.json` lists the matched words and gives each frame
`on-slide`, `no match` or `no text`. It works with Hindi speech over English
slides by using the English transcript. Limits: boxes need the macOS reader
(tesseract gives text without positions); handwritten equations read back as
symbols, so a maths board often shows `no match` even when the speaker is
talking about it - `no match` does not prove they're off topic.

**`--clean-board`** saves a copy of each frame in `clean/` with a presenter who
stands in front of a whiteboard removed. For every pixel it keeps the colour it
has for most of the next 40 seconds: the writing stays put, a person who moves
around disappears. On 8 frames where a teacher was blocking the board (two
lectures), all 8 had the hidden writing readable afterwards. Limits: a smudge
stays where the presenter stood longest; writing added in the next ~20 seconds
can appear, sometimes half-written; anything erased in that time vanishes.
About 0.7 seconds per frame. The original frames are never changed.

```bash
uv run python -m ytconv "URL" --motion-max 4.5 --mark --clean-board
```

## Useful flags

- `--no-frames` - transcript only, skips the video download entirely
- `--at MM:SS` - the frame for a moment in the transcript (repeatable)
- `--mark` - copies of the frames with boxes around what is being talked about
- `--clean-board` - copies of the frames with the presenter removed from the whiteboard
- `--lang hi` - the language the video is spoken in, if YouTube has it wrong
- `--translate [LANG]` - also write the transcript in English (or LANG)
- `--stt` - use speech-to-text even if captions exist, or on a local file
- `--debug-scores` - also write `scores.csv` to see why each second was kept or dropped
- `--threshold 0.3` - loosen frame selection if too little is coming through
- `--max-frames 50` - cap on frames kept
- `--gameplay` - for games, sport and action: keep the sharpest moments instead of the still ones
- `--every 5` - no filtering, one frame every 5 seconds
- `--motion-max 4.5` - let more movement through the stillness check (default 1.0)
- `--client mweb` - force one yt-dlp player client instead of the fallback chain

### Choosing how many frames

You decide how many frames to keep. The more frames you give Claude, the more
of the video it can actually see - more slides, more steps, more detail to work
from. The trade-off: every frame takes up part of Claude's context, so a huge
batch leaves less room for everything else.

```bash
uv run python -m ytconv "URL" --max-frames 60 --threshold 0.3
```

`--max-frames` is a limit, not a target (the default is 35). By default the
tool only keeps frames where the screen holds still, and skips repeats of the same slide,
so to get more frames, raise the limit **and** loosen `--threshold` (default
0.5, lower keeps more). For fewer, cheaper frames, lower the limit:
`--max-frames 10`.

### Gameplay and fast footage

The default mode throws away any second where the picture is moving, because in
a lecture, movement is almost always the presenter. In a game clip, the moving
moments are the point, so the default can miss exactly what you want. Three
ways round it, from smallest change to biggest:

| Flag | What it keeps | Good for |
|---|---|---|
| `--motion-max 4.5` | Same rules, but lets more movement through (default 1.0) | Shaky camera, a presenter beside the slides or in front of a whiteboard, Shorts |
| `--gameplay` | The sharpest second from every 3 seconds, plus any big scene change in between. Ignores stillness | Gaming clips, sport, action |
| `--every 5` | One frame every 5 seconds, nothing filtered | When you want everything, repeats included |

```bash
uv run python -m ytconv "URL" --gameplay
```

"Sharpest" means the most edges on screen. Motion blur smears edges away, so
the sharpest second in a window is usually the clearest one. `--max-frames`
still applies to all three. With `--every`, if the interval would need more
frames than the limit, it is widened so the frames still cover the whole video,
and the tool says so.

## How well it works

Measured on real lectures in different styles - light slides, a dark screen
recording, a whiteboard, tablet drawing, Hindi speech over English writing,
voice-over slides, a code screencast - plus a gameplay Short. A person looked at
**279 frames** and marked each useful in notes or not, and marked **45 moments**
where a visual first appears after it is spoken. Full tables:
[tests/RESULTS.md](tests/RESULTS.md). Re-run them yourself with the scripts in
`tests/` (they download the test videos; nothing is stored in the repo but the
labels).

**Frame picking** (precision = share of kept frames a person called useful):

| Video style | Default mode | Best mode for it |
|---|---|---|
| Light slides | 82% useful | default |
| Dark screen recording | 81% useful, shows 80% of the useful moments | default |
| Whiteboard with presenter | 0 of 16 useful | `--motion-max 4.5` or `--gameplay` (32-35%), plus `--clean-board` |
| Tablet drawing (Khan Academy style) | 5 frames, 3 of the 4 a person judged useful | default |
| Hindi lecture, presenter at a board | keeps 1 frame | `--gameplay` (50%) or `--motion-max 4.5` (30%), plus `--clean-board` |
| Gameplay Short | keeps 1 frame | `--gameplay` (40%) |

**`--at`** - hits within 6 seconds of where a person said the visual appears:

| Rule | 30 moments the rules were designed on | 15 fresh moments | All 45 |
|---|---|---|---|
| The exact second it was said | 3 | 6 | 9 (20%) |
| **8 seconds later (what `--at` does)** | **20** | **9** | **29 (64%)** |
| Old rule: stillest frame in the next 2 minutes | 1 | 3 | 4 (9%) |
| Earliest slide whose text matches the speech | 15 | 5 | 20 (44%) |

Matching slide text sounds smarter but lost: a slide's title usually appears
first and the diagram is built under it seconds later, and short words like
"tree" match the wrong board. So `--at` uses the delay and only *reports* the
text.

**Speech-to-text and translation** - see the tables above.

**Known weak spots** (measured, not fixed yet):

- A drawing that builds up slowly (tablet videos): the tool keeps the stage it
  sees first, and later additions to the same picture can be dropped as
  repeats. On the test video, notation added after 4:39 was missed. Add
  `--every 30` for the later stages. (Until 2026-10-21 these videos kept no
  frames at all; the dark-background check is now relative to the video.)
- A presenter standing in front of the board: the default mode drops most
  frames as "moving footage". `--motion-max 4.5` and `--clean-board` help.
- A teacher who writes slowly: the visual can land 12-60 seconds after it is
  said, and no `--at` rule tested catches that.
- `--mark`'s `on-slide` / `no match` verdict has not been measured against
  human labels.

## Use it with Claude Code

The repo includes a [Claude Code](https://claude.com/claude-code) skill, so you
can say "convert this video" or type `/youtube-converter` instead of running the
commands yourself. The skill tells Claude to fetch the transcript first and
only pull frames when you want the visuals, to transcribe videos without
captions, and to translate when YouTube won't.

To install it, from a terminal:

```bash
git clone https://github.com/agrim20805-ship-it/Youtuber-Converter.git
```

```bash
cd Youtuber-Converter
```

```bash
uv sync
```

(No uv? Do the three [pip setup](#without-uv-pip) commands here instead.)

```bash
mkdir -p ~/.claude/skills
```

```bash
ln -s "$PWD/skills/youtube-converter" ~/.claude/skills/youtube-converter
```

The last command makes a link from Claude Code's skills folder back to this
repo, rather than a copy, so a later `git pull` updates the skill too. Keep the
cloned folder where it is - moving it breaks the link. Then start a new Claude
Code session and type `/youtube-converter`.

Read `skills/youtube-converter/SKILL.md` before installing. A skill is a set of
instructions Claude follows, including running commands on your computer.

## Experimental: ears

`experiments/ears/` lets Claude (or you) "hear" a video by turning its sound
into pictures. ffmpeg draws the audio as a spectrogram: time left to right,
pitch bottom to top, brightness = loudness. A boom shows up as a bright block
at the bottom, a whoosh as a grainy cloud up high, a riser as a climbing line.
Free, and nothing extra to install - only `ffmpeg` and Python.

It is an experiment, not part of `ytconv`:

- **Works:** sound effects over speech or quiet audio - 8 of 8 found and named
  in a blind test.
- **Partly works:** music-heavy edits. Drops, breakdowns and endings are found;
  added sound effects cannot be told apart from the song.

The quick version, from the `experiments/ears` folder:

```bash
yt-dlp --extractor-args "youtube:player_client=web_embedded" -f ba -o audio.webm "URL"
```

```bash
python3 detect.py audio.webm 30
```

The second command lists moments in the first 30 seconds where a sudden low or
high sound appears. To draw the timeline picture and for the other tools, see
[experiments/ears/README.md](experiments/ears/README.md).

## Or just ask Claude

All the commands above are here for reference. With the
[Claude Code skill](#use-it-with-claude-code) installed, you don't need to type
any of them - tell Claude what you want in plain English and it runs the right
commands for you. For example:

- "Convert this video: https://www.youtube.com/watch?v=..."
- "Just get me the transcript of this one."
- "This video has no captions - transcribe it."
- "Convert this Hindi lecture into English."
- "Convert this video into Spanish."
- "What's on screen at 06:29?"
- "Circle what he's talking about on each slide."
- "The teacher keeps standing in front of the board - get me clean frames."
- "Get me more frames - around 60."
- "This is a gaming clip - get me the frames that matter."
- "What sound effects can you hear in this Short?" (experimental)

## Disclaimer

- **Not affiliated with YouTube or Google.** This is an independent project.
- **Respect copyright and YouTube's Terms of Service.** Downloading videos may
  be against YouTube's terms. Use this only on videos you have the right to
  download, for your own study. Don't redistribute what it downloads.
- **No warranty.** It is provided as-is. YouTube changes often, and downloads
  can stop working without notice.
- **No liability.** The author is not liable for any damage or loss that comes
  from using this software, including: network problems, blocked connections
  or data charges; YouTube or Google restricting, rate-limiting or banning an
  account or IP address; hacking, malware, security breaches or unauthorised
  access to any computer, network or account; misuse of the software by anyone;
  and any copyright or terms-of-service claim over what is downloaded. You use
  it entirely at your own risk.
- **Speech-to-text models are downloaded from Hugging Face** on first use
  (Whisper `small`, MIT licence). Transcripts and translations can contain
  mistakes - check anything important against the video.
- **`experiments/` is experimental.** The audio experiments in
  `experiments/ears/` are unfinished research, not part of the tool.
- **Built with AI assistance.** This project, including the Claude Code skill,
  was made with the help of artificial intelligence. Review the code and
  instructions yourself, and use your own judgement before relying on it.
