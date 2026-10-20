"""Loop-cancel: subtract each moment from the same moment one loop earlier.
What the song repeats cancels; what was added (or removed) is left over."""
import subprocess, sys, statistics as st
CPS, ROWS, BANDS = 50, 256, 64          # 50 columns/sec, 64 frequency bands
def grid(src, dur):
    cols = int(dur * CPS)
    raw = subprocess.run(["ffmpeg","-v","error","-i",src,"-ac","1","-ar","16000","-lavfi",
        f"showspectrumpic=s={cols}x{ROWS}:legend=0:scale=log:fscale=lin:color=channel",
        "-f","rawvideo","-pix_fmt","gray","-"], capture_output=True).stdout
    g = []
    step = ROWS // BANDS
    for c in range(cols):
        col = [raw[(ROWS-1-r)*cols + c] for r in range(ROWS)]
        g.append([sum(col[b*step:(b+1)*step])/step for b in range(BANDS)])
    return g
def diff(a, b): return sum(abs(x-y) for x,y in zip(a,b)) / len(a)
def find_loop(g):
    # coarse 16-band copy of the middle 60% of the track, where the beat is steady
    n=len(g); mid=g[int(n*.2):int(n*.8)]
    coarse=[[sum(c[i*4:(i+1)*4])/4 for i in range(16)] for c in mid]
    best=None
    for lag in range(int(1.0*CPS), int(4.5*CPS)):
        if lag >= len(coarse)//2: break
        d = st.mean(diff(coarse[t], coarse[t-lag]) for t in range(lag, len(coarse), 2))
        if best is None or d < best[1]: best=(lag,d)
    return best[0]
def residual(g, L):
    add, cut = [], []
    for t in range(len(g)):
        # compare with BOTH neighbouring loops, keep the better match:
        # a new section repeats next loop (cancels); a one-off SFX matches neither
        refs = [x for x in (t-L, t+L) if 0 <= x < len(g)]
        a=c=1e9
        for r in [ref+s for ref in refs for s in range(-2,3)]:
            if 0<=r<len(g):
                pa = sum(max(0,x-y) for x,y in zip(g[t],g[r]))/BANDS
                pc = sum(max(0,y-x) for x,y in zip(g[t],g[r]))/BANDS
                if pa+pc < a+c: a,c = pa,pc
        add.append(a); cut.append(c)
    return add, cut
def peaks(series, k=4.0, gap=0.4):
    med=st.median(series); mad=st.median(abs(x-med) for x in series) or 1
    th=med+k*mad
    out=[]
    for i,v in sorted(enumerate(series), key=lambda x:-x[1]):
        if v<th: break
        if all(abs(i-j)>gap*CPS for j,_ in out): out.append((i,v))
    return sorted(out), th
def write_pgm(rows, path):
    h=len(rows); w=len(rows[0])
    with open(path,"wb") as f:
        f.write(f"P5 {w} {h} 255\n".encode())
        for r in rows: f.write(bytes(max(0,min(255,int(v))) for v in r))
if __name__=="__main__":
    src, dur, tag = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    g=grid(src,dur); L=find_loop(g)
    add,cut=residual(g,L)
    pa,tha=peaks(add); pc,thc=peaks(cut)
    print(f"{tag}: loop length {L/CPS:.2f}s ({60/(L/CPS)*4:.0f} BPM if 4 beats/loop... or {60/(L/CPS)*8:.0f} if 8)")
    print("  ADDED  (new energy not in previous loop):", ", ".join(f"{i/CPS:.1f}s" for i,_ in pa) or "none")
    print("  CUT    (energy missing vs previous loop):", ", ".join(f"{i/CPS:.1f}s" for i,_ in pc) or "none")
    # residual picture: rows=frequency (high at top), brightness = added energy, x4 gain
    img=[[min(255, 4*max(0, g[t][b]-g[(t-L) if t>=L else min(len(g)-1,t+L)][b])) for t in range(len(g))] for b in reversed(range(BANDS))]
    write_pgm(img, f"resid_{tag}.pgm")
