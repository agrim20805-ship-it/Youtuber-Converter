# Builds test.wav: 40s of real speech + synthetic SFX at hidden random times.
# The answer key goes to key.json, which is NOT read until after blind classification.
import random, subprocess, json
random.seed()  # unseeded on purpose
SFX = {
  "boom":   "aevalsrc='0.9*sin(2*PI*55*t)*exp(-5*t)':d=0.9:s=48000",
  "riser":  "aevalsrc='0.35*sin(2*PI*(150*t+450*t*t))*(t/2)':d=2:s=48000",
  "whoosh": "anoisesrc=d=0.7:c=pink:a=0.8:r=48000,highpass=f=1500,afade=t=in:d=0.35,afade=t=out:st=0.35:d=0.35",
  "pop":    "aevalsrc='0.9*sin(2*PI*1800*t)*exp(-70*t)':d=0.12:s=48000",
}
kinds = ["boom","riser","whoosh","pop","boom","whoosh","pop","riser"]
random.shuffle(kinds)
slots = sorted(random.sample(range(2, 37, 4), len(kinds)))   # >=4s apart
events = [{"t": s + round(random.random(), 2), "kind": k} for s, k in zip(slots, kinds)]
json.dump(events, open("key.json", "w"))

inputs = ["-t", "40", "-i", "audio.webm"]
filt = ["[0:a]aresample=48000,pan=mono|c0=c0,volume=0.6[sp]"]
labels = ["[sp]"]
for i, e in enumerate(events):
    inputs += ["-f", "lavfi", "-i", SFX[e["kind"]]]
    d = int(e["t"] * 1000)
    filt.append(f"[{i+1}:a]aformat=channel_layouts=mono,adelay={d}[s{i}]")
    labels.append(f"[s{i}]")
filt.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0[out]")
subprocess.run(["ffmpeg","-v","error",*inputs,"-filter_complex",";".join(filt),
                "-map","[out]","-t","40","-ac","1","test.wav","-y"], check=True)
print(f"test.wav built with {len(events)} hidden events (key sealed in key.json)")
