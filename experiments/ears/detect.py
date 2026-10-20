import subprocess, sys, json, statistics as st
SRC, DUR = sys.argv[1], float(sys.argv[2])
COLS = int(DUR * 100)          # one column per 10ms
ROWS = 256                     # 0..8 kHz, linear, ~31 Hz per row
raw = subprocess.run(["ffmpeg","-v","error","-i",SRC,"-t",str(DUR),"-ac","1","-ar","16000",
    "-lavfi",f"showspectrumpic=s={COLS}x{ROWS}:legend=0:scale=log:fscale=lin:color=channel:gain=1",
    "-f","rawvideo","-pix_fmt","gray","-"], capture_output=True).stdout
assert len(raw) == COLS*ROWS, len(raw)
# row 0 = top = highest frequency; flip so index = frequency bin
col = lambda c: [raw[(ROWS-1-r)*COLS + c] for r in range(ROWS)]
hz = lambda r: r * 8000 / ROWS
LOW  = [r for r in range(ROWS) if hz(r) < 80]
MID  = [r for r in range(ROWS) if 300 <= hz(r) <= 3400]
HIGH = [r for r in range(ROWS) if hz(r) >= 5000]
bands = {"low":[], "mid":[], "high":[]}
for c in range(COLS):
    v = col(c)
    for name, rows in (("low",LOW),("mid",MID),("high",HIGH)):
        bands[name].append(sum(v[r] for r in rows)/len(rows))
def jumps(series, win=50):
    out=[]
    for i in range(len(series)):
        base = st.median(series[max(0,i-win):i] or [series[i]])
        out.append(series[i]-base)
    return out
J = {k: jumps(v) for k,v in bands.items()}
# candidate = low or high band jumps well above its recent baseline,
# more than the speech band jumps at the same instant
cands=[]
for i in range(COLS):
    lo, hi, md = J["low"][i], J["high"][i], J["mid"][i]
    s = max(lo, hi) - max(0, md)*0.5
    if s > 30: cands.append((i, s, "low" if lo>hi else "high"))
# peak-pick with 0.6s minimum spacing
events=[]
for i,s,b in sorted(cands, key=lambda x:-x[1]):
    if all(abs(i-e[0])>60 for e in events): events.append((i,s,b))
events.sort()
json.dump([{"t":round(i/100,2),"strength":round(s,1),"band":b} for i,s,b in events], open("detected.json","w"))
for i,s,b in events: print(f"  {i/100:6.2f}s  strength={s:5.1f}  band={b}")
print(f"{len(events)} events")
