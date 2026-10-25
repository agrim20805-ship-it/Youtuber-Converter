# Every command, tested

Every command in the [guide](GUIDE.md) (the full README) was run by hand on 2026-10-23, on a
fresh `git clone` of this repo, with real videos in place of `VIDEO_ID` and
`URL`. This page says what was run and what happened, including what failed
and what was not tested.

For a quick check on your own computer, use the
[health check](GUIDE.md#health-check) instead: it runs in about a minute.

## Setup

| | |
|---|---|
| Machine | Mac, Apple M4 Pro, macOS 27 |
| Tools | ffmpeg 8.1.2, yt-dlp 2026.07.04 (Homebrew, with Deno), uv 0.12.1 |
| Python | 3.9.6 (the Mac's built-in one), 3.12, 3.13, 3.14 |
| ytconv | 0.1.1. The ears fix and the Python 3.9 change were re-tested on 0.1.1; the rest ran on the commit just before it, whose tool code is the same. Research mode was added and tested in 0.1.2 (rows below say so). |

Videos used:

| Short name | Video | Why |
|---|---|---|
| Short | a 13-second gaming Short (the author's own) | fast to run, has captions |
| Tablet | [N2PpRnFqnqY](https://www.youtube.com/watch?v=N2PpRnFqnqY) - Khan Academy, 7 min, 720p at most | drawing on a dark tablet |
| Board | [nPp5jRGWz_A](https://www.youtube.com/watch?v=nPp5jRGWz_A) - Michel van Biezen, 5½ min | teacher in front of a whiteboard |
| Hindi | [3EJlovevfcA](https://www.youtube.com/watch?v=3EJlovevfcA) - Gate Smashers, 12 min | Hindi speech, English subtitles from the uploader |
| lecture.mp4 | the Tablet video, downloaded as a file | the "your own video files" section |

## Results

| Guide section | Command (placeholders filled in) | Result |
|---|---|---|
| Requirements | `brew install yt-dlp ffmpeg uv` | Not run (it installs). yt-dlp and ffmpeg were already installed with Homebrew; Homebrew has a `uv` package. |
| Requirements | `yt-dlp -v --simulate "…" \| grep …` | ✅ printed `yt_dlp_ejs-0.8.0` and `JS runtimes: deno-2.9.5` |
| Setup | `uv sync` | ✅ |
| Without uv (pip) | `python3 -m venv .venv` → `source .venv/bin/activate` → `pip install "yt-dlp[default,deno]"` → `python -m ytconv "Short"` | ✅ on Python 3.12. On the Mac's built-in 3.9, the venv and `python -m ytconv` steps ✅ (yt-dlp came from Homebrew; the `pip install yt-dlp` step was not run there). |
| Install it as a command | `pip install ytconv` → `ytconv "Short" --notes` | ✅ from PyPI on 3.13 (0.1.0). 0.1.1 wheel: ✅ with `pip3 install` on the Mac's built-in 3.9; the `ytconv` command landed outside the terminal's search path, `python3 -m ytconv` worked. |
| Install it as a command | `uvx ytconv "Short"` | ✅ |
| Install it as a command | Python: `from ytconv.cli import main; main([...])` | ✅ wrote transcript, frames and notes page |
| Use | `uv run python -m ytconv "Short"` | ✅ 27 s |
| Your own video files | `uv run python -m ytconv "lecture.mp4" --notes` | ✅ no captions, so speech-to-text ran (whisper.cpp). The first time it downloads a 466 MB model. |
| No captions | `uv sync --extra whisper` → `uv run python -m ytconv "Short" --stt` | ✅ faster-whisper, 7 lines |
| Languages | `--lang hi` on Hindi | ✅ Hindi transcript, 267 lines |
| Languages | `--lang hi --translate` on Hindi | ✅ plus `transcript.en.txt` from the uploader's English subtitles, 282 lines |
| The timestamp gap | `--at 00:52 --at 02:46` on Tablet | ✅ ran, gave a frame for each. Both said "text does NOT match - look a little later", as designed when the words aren't on screen yet. |
| Reading the screen | `--mark --clean-board` on Board | ✅ 16 of 16 presenter-removed copies judged clean, `marked/` written |
| How many frames | `--max-frames 60 --threshold 0.3` on Tablet | ✅ 720p (the best this video has). Run at the same time as other downloads of the same video, YouTube refused some with "403 Forbidden" and the tool fell back to 360p; run on its own, no problem. |
| How it picks frames | `--gameplay` on Short | ✅ 5 frames |
| Research mode (0.1.2) | `--search "how do black holes form" -o DIR` | ✅ 10 results in about 8 s, captions filled in for each, `search.json` written. Same on the Mac's built-in Python 3.9. |
| Research mode (0.1.2) | `--search` with no topic, with a URL as well, `--results 0`, `--results 99` | ✅ each stops with a clear message, exit code 2. A topic starting with `-` is searched as text. |
| Research mode (0.1.2) | the whole flow through the Claude Code skill: 3 videos converted, `research.md` written, then `--research-page DIR` | ✅ 50 of 50 citations passed the check; 5 of them also checked by hand against the transcript. Page opened in a browser: 3 frames load, every timestamp link opens the video at that second. |
| Research mode (0.1.2) | `--research-page` on a hand-made note with planted mistakes | ✅ flagged a wrong time, a time past the end, a video that was not converted and a claim with no matching words; a `javascript:` link was not made clickable; `<script>` was shown as text. |
| Health check | `uv run python tests/healthcheck.py` | ✅ 3 of 3 passed, 72 s (0.1.1). 0.1.2 adds a search check: ✅ 4 of 4 passed, 49 s. |
| Claude Code skill | `git clone …` → `cd` → `uv sync` → `mkdir -p ~/.claude/skills` → `ln -s …` | ✅ with a stand-in home folder: the link opened `SKILL.md`, and the skill's "find the project" step found the clone. Not tested: a real Claude Code session using it from that install. |
| Claude Desktop (MCP) | the `uvx --from ytconv …` config | ✅ ran the same messages Claude Desktop sends: Board converted with notes, 16 frames. Not tested inside the Claude Desktop app. |
| Claude Desktop (MCP) | the `uv --directory /path/to/clone …` config | ✅ same test, transcript only |
| MCP (0.1.3) | the server started with Claude Desktop's short PATH (`/usr/bin:/bin`), nothing set in the config | ✅ found yt-dlp and ffmpeg in Homebrew's folder by itself. On 0.1.2 the same start failed with "Missing program(s): yt-dlp". |
| MCP (0.1.3) | `convert_video` with every new option: `gameplay`, `every`, `max_same`, `threshold`, `debug_scores`, `frame_width`, `keep_video`, `work_dir`, `client`, `max_frames`, `stt`, `stt_engine`, `no_stt`, `no_clean_board`, plus `mark`, `notes`, `at`, `lang` | ✅ each reached the command (checked in the server's log of what it ran). Short with `gameplay`: 5 frames, the same as the command gives. |
| MCP (0.1.3) | 15 bad requests: conflicting options, numbers out of range, folders inside the project, a made-up client name, path tricks in `view_frames` | ✅ each refused with a plain message |
| MCP (0.1.3) | `view_frames` by name, by time, and from `marked/` | ✅ came back as pictures, at most 1280 px wide; a time with no frame gives the nearest one and says so |
| MCP (0.1.3) | research: `search_videos` → 3 × `convert_video` with `research_dir` → `build_research_page` | ✅ 50 of 50 citations ok; a citation moved past the end of its video was listed to fix |
| MCP (0.1.3) | the same checks on the Mac's built-in Python 3.9 | ✅ |
| Ears | `yt-dlp … -f ba -o audio.webm "Short"` → `python3 detect.py audio.webm 30` | ❌ then ✅. On 0.1.0 the times were about 2.2× too late, because the audio (13.8 s) was shorter than the 30 s asked for. Fixed in 0.1.1: it now analyses only the real length. The built-in test (8 hidden sounds) still finds 8 of 8, with output identical to before. |

## Python versions

ytconv has no Python packages to install and uses nothing newer than Python
3.9. Checked on 2026-10-23:

- **3.9** (the Mac's built-in): all 12 modules load, and full runs worked:
  - captions + `--notes --mark --at` on Short;
  - Board, with presenter-removed copies (16 of 16, as on 3.13);
  - Hindi with `--translate`;
  - `lecture.mp4` with speech-to-text;
  - the MCP server.
- **3.10, 3.11**: all modules load; no full run.
- **3.12, 3.13, 3.14**: full runs.

## Not tested

- Windows and Linux.
- Installing tools with Homebrew (`brew install`) from scratch.
- Using the skill or the MCP server from inside a real Claude app (only the
  exact messages those apps send were tested).
- Whiteboard lectures other than the one in the test set.

If something here no longer works for you, it is most likely YouTube changing
something: run the [health check](GUIDE.md#health-check) and update yt-dlp.
