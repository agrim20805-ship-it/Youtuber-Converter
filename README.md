# ytconv

Turns a YouTube URL into a readable transcript and a folder of frames that are
almost all worth looking at.

## Requirements

The three tools it needs are free. On a Mac, install them with
[Homebrew](https://brew.sh):

```bash
brew install yt-dlp ffmpeg uv
```

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

**Captions.** The transcript comes from the video's own captions, which is why
it is fast. A video with no captions gets no transcript.

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

No Python packages are installed - the dependency list is empty. Everything
else is done by `yt-dlp` and `ffmpeg`.

## Use

```bash
uv run python -m ytconv "https://www.youtube.com/watch?v=VIDEO_ID"
```

Output lands in `output/<video_id>/`:

| File | What it is |
|---|---|
| `transcript.txt` | one `[MM:SS] text` line per cue, deduplicated |
| `metadata.json` | title, uploader, duration, chapters |
| `frames/` | `frame_MM-SS.jpg` - only frames showing real screen content |
| `frame-scores.json` | the score for every second, so later stages can search instead of re-scanning |

The video itself is downloaded to a temp directory and deleted afterwards, since
these files are large. `--keep-video` keeps it, `--work-dir DIR` chooses where.

## The timestamp gap

A concept is spoken before it is drawn. In one lecture a graph is announced
at 06:29 and only appears on screen at 08:04.
Extracting a frame at the spoken timestamp gets the presenter mid-sentence.

`--at` searches forward up to two minutes for the moment the visual actually
lands:

```bash
uv run python -m ytconv "URL" --at 06:29 --at 14:36
```

```
[at 06:29] visual at 0:08:04 (+95s, score 1.67)
[at 14:36] visual at 0:14:40 (+4s, score 2.10)
```

This is opt-in. A plain run never forward-searches.

## Useful flags

- `--no-frames` - transcript only, skips the video download entirely
- `--debug-scores` - also write `scores.csv` to see why each second was kept or dropped
- `--threshold 0.3` - loosen frame selection if too little is coming through
- `--max-frames 50` - cap on frames kept
- `--client mweb` - force one yt-dlp player client instead of the fallback chain

## Use it with Claude Code

The repo includes a [Claude Code](https://claude.com/claude-code) skill, so you
can say "convert this video" or type `/youtube-converter` instead of running the
commands yourself. The skill tells Claude to fetch the transcript first and
only pull frames when you want the visuals.

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
- **`experiments/` is experimental.** The audio experiments in
  `experiments/ears/` are unfinished research, not part of the tool.
- **Built with AI assistance.** This project, including the Claude Code skill,
  was made with the help of artificial intelligence. Review the code and
  instructions yourself, and use your own judgement before relying on it.
