# Ears (experiment)

**Status: experiment, not part of `ytconv`.** It lets Claude "hear" a video by
turning sound into pictures and numbers it can read. Free, zero-install - only
`ffmpeg` and the Python standard library.

## What works and what doesn't (measured)

| Audio type | Result |
|---|---|
| Synthetic SFX over speech | **8/8 found, 8/8 named correctly, 0 false alarms**, mean timing error 0.09s (blind test, answer key sealed until after guessing) |
| Sparse real audio (a game clip) | Readable: ambience, voice, drone, the silence before a jump scare, the hit, a scream. Not yet scored against a real answer key. |
| Wall-to-wall music (music edits) | **Structure only.** Drops, breakdowns, stutters and endings are found reliably. Added SFX can NOT be reliably told apart from the song's own sounds. |

## How it works

1. **Spectrogram tiles.** ffmpeg draws sound as a picture: time left to right,
   pitch bottom to top, brightness = loudness. Different sounds leave different
   shapes - a boom is a fading block at the bottom, a whoosh a grainy cloud up
   high, a riser a climbing diagonal line, a pop a tiny bright dot. Claude reads
   2-second tiles laid out on one contact sheet (~1,500 tokens per sheet).
2. **`detect.py`** proposes candidate moments: sudden jumps in very low (<80 Hz)
   or very high (>5 kHz) energy. Deliberately sloppy - it over-proposes and the
   tiles do the filtering. For shorts under ~60s, skip it and tile the whole
   track instead.
3. **`loop.py`** - the loop-cancel trick. Subtracts each moment from the same
   moment one loop earlier and later; what the song repeats cancels, one-off
   sounds remain. Confirms drops and section changes, but still flags about one
   moment per second on real edits, so it does not produce a usable SFX list.
   Likely reason: editors chop the song at their cut points, so it rarely
   repeats cleanly.
4. **`make_test.py`** builds the known-answer test: synthetic SFX at hidden
   random times over real speech, key written to `key.json`.

## Commands

Write paths as `"$HOME/..."`, never `"~/..."` - a quoted tilde does not expand.

Run these from this folder (`experiments/ears`), or call the scripts by their
full path. Set `DUR` to how many seconds to analyse - the whole short, or the
part of a longer video you care about.

```bash
# 1. audio only, through the 403-safe client
yt-dlp --extractor-args "youtube:player_client=web_embedded" -f ba -o audio.webm "URL"

# 2. how many seconds to analyse (an even number)
DUR=30

# 3. timeline sheet: consecutive 2-second tiles, 5 per row
mkdir -p tl
for i in $(seq 0 $((DUR/2 - 1))); do
  ffmpeg -v error -ss $((i*2)) -t 2 -i audio.webm \
    -lavfi "apad=whole_dur=2,showspectrumpic=s=300x150:legend=0:scale=log:fscale=log:color=intensity" \
    "tl/$(printf %03d $i).png" -y
done
ffmpeg -v error -framerate 1 -pattern_type glob -i "tl/*.png" \
  -vf "pad=iw+6:ih+6:3:3:color=white,tile=5x$(( (DUR/2 + 4) / 5 ))" -frames:v 1 sheet.png -y

# 4. candidate moments (args: file, seconds)
python3 detect.py audio.webm $DUR

# 5. music only - the loop-cancel trick (args: file, seconds, a label)
python3 loop.py audio.webm $DUR mytag
```

Then open `sheet.png`. Tile 1 is 0-2s, tile 2 is 2-4s, and so on, left to
right, top to bottom. Every tile is a separate ffmpeg run, so analyse a section
rather than a whole hour-long lecture.

To test it on a known answer: put any speech recording at `audio.webm`, run
`python3 make_test.py` (builds `test.wav` with hidden sound effects), make the
sheet from `test.wav` with `DUR=40`, write down your guesses, and only then
open `key.json`.

## Next steps

- **Score against a real answer key.** The creator of an edit knows where every
  SFX is. That is the only honest way to improve the music case.
- If music stays unreadable, consider a free offline sound-classifier model.
  That needs installing packages, so it needs a yes first.
- Line sound events up with the video's cut times - "does the whoosh land on
  the cut?" is the core question for studying edits. Not built.
