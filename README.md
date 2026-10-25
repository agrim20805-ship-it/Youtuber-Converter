# ytconv

Turns a YouTube video into study material: a clean transcript and only the
frames worth seeing - slides, diagrams, code - not the presenter's face.

<a href="https://www.youtube.com/shorts/72ZZiSuqWXU"><img src="https://img.youtube.com/vi/72ZZiSuqWXU/maxresdefault.jpg" alt="Watch the 30-second demo on YouTube" width="480"></a>

## What it does

- Transcripts from captions, or free speech-to-text on your own computer
- Frames picked to suit slides, whiteboards or gameplay
- Translation, a notes page, and video files from your computer
- Research mode: compares several videos on a topic, each claim linked to the second it was said

## Requirements

Python 3.9 or newer, yt-dlp and ffmpeg. On a Mac:

```bash
brew install yt-dlp ffmpeg
pip install ytconv
```

On a Mac's built-in Python, use `pip3` and `python3 -m ytconv`.

## Use

```bash
ytconv "URL"               # transcript + frames
ytconv "URL" --no-frames   # transcript only
ytconv "URL" --notes       # plus a notes page
ytconv --search "topic"    # research mode
```

`URL` is a YouTube link or a video file.

**With Claude:** a Claude Code skill and an MCP server are included
([setup](https://github.com/agrim20805-ship-it/Youtuber-Converter/blob/main/GUIDE.md#use-it-with-claude-code)).

**Tested** on six kinds of video: 73% of the frames it kept were useful in notes
([results](https://github.com/agrim20805-ship-it/Youtuber-Converter/blob/main/tests/RESULTS.md),
[every command checked](https://github.com/agrim20805-ship-it/Youtuber-Converter/blob/main/TESTED.md)).

**Disclaimer:** not affiliated with YouTube or Google. Use it only on videos
you have the right to download, for your own study. Provided as-is, with no
warranty. Built with AI assistance.

---

For more detailed instructions, check [this](https://github.com/agrim20805-ship-it/Youtuber-Converter/blob/main/GUIDE.md).
