"""Labelling page for the test set. Standard library only, localhost only.

    uv run python tests/label_server.py          # then open http://127.0.0.1:8765

Shows one frame at a time. Y = useful, N = not useful, S = skip, B = back.
Every answer is written to tests/labels/frames.csv straight away, so closing
the page loses nothing and it resumes where it stopped.

/at is the second page: for each spoken moment, click the thumbnail where what
was said first appears clearly on screen. Saved to tests/labels/at_pairs.csv.
"""

import csv
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTDATA = ROOT / "testdata"
LABELS = ROOT / "tests" / "labels" / "frames.csv"
SPEC = json.loads((ROOT / "tests" / "testset.json").read_text())
KEYS = [v["key"] for v in SPEC["videos"]]
STYLE = {v["key"]: v["style"] for v in SPEC["videos"]}
PORT = 8765
AT_LABELS = ROOT / "tests" / "labels" / "at_pairs.csv"


def load_at_labels():
    if not AT_LABELS.exists():
        return {}
    with open(AT_LABELS, newline="") as fh:
        return {(r["video"], r["at"]): r["answer"] for r in csv.DictReader(fh)}


def save_at_labels(labels):
    tmp = AT_LABELS.with_suffix(".tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["video", "at", "answer", "labeller"])
        for (video, at), answer in labels.items():
            w.writerow([video, at, answer, "human"])
    tmp.replace(AT_LABELS)


def load_queue():
    items = []
    for key in KEYS:
        meta = TESTDATA / key / "testset.json"
        if not meta.exists():
            continue
        queue = json.loads(meta.read_text())["queue"]
        items += [{"video": key, "second": int(s)} for s in sorted(queue, key=int)]
    return items


def load_labels():
    if not LABELS.exists():
        return {}
    with open(LABELS, newline="") as fh:
        return {(r["video"], int(r["second"])): r["label"] for r in csv.DictReader(fh)}


def save_labels(labels):
    LABELS.parent.mkdir(parents=True, exist_ok=True)
    tmp = LABELS.with_suffix(".tmp")
    with open(tmp, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["video", "second", "label", "labeller"])
        for (video, second), label in sorted(labels.items(), key=lambda x: (KEYS.index(x[0][0]), x[0][1])):
            w.writerow([video, second, label, "human"])
    tmp.replace(LABELS)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Label frames</title>
<style>
 body{margin:0;font:16px system-ui;background:#111;color:#eee;display:flex;flex-direction:column;align-items:center}
 #top{padding:10px;text-align:center;max-width:960px}
 img{max-width:96vw;max-height:68vh;border:3px solid #333;margin:6px 0}
 .btns button{font:18px system-ui;padding:12px 22px;margin:6px;border:0;border-radius:8px;cursor:pointer}
 .y{background:#2e7d32;color:#fff}.n{background:#c62828;color:#fff}.s{background:#555;color:#fff}.b{background:#333;color:#ccc}
 #help{color:#aaa;font-size:14px;max-width:900px;text-align:left;margin:8px 16px}
 #prev{font-size:14px;color:#9c9}
</style></head><body>
<div id="top"><b id="pos"></b> &middot; <span id="what"></span> <span id="prev"></span></div>
<img id="img" alt="frame">
<div class="btns">
 <button class="y" onclick="answer('useful')">Y &nbsp;Useful</button>
 <button class="n" onclick="answer('not')">N &nbsp;Not useful</button>
 <button class="s" onclick="answer('skip')">S &nbsp;Skip / unsure</button>
 <button class="b" onclick="back()">B &nbsp;Back</button>
</div>
<div id="help"><b>Useful</b> = you would want this picture in your notes: a slide, diagram, code,
writing on a board, a table - or, for gameplay, a moment that shows what is happening.<br>
<b>Not useful</b> = only the presenter's face, a blank or black screen, a transition, a logo or intro card,
too blurry to read, or the same thing as an obviously better frame.<br>
Keys: Y / N / S, and B to go back. Answers save instantly.</div>
<script>
let items=[], labels={}, i=0;
const k=(it)=>it.video+':'+it.second;
async function load(){
  const r=await fetch('/api/state'); const d=await r.json();
  items=d.items; labels=d.labels;
  i=items.findIndex(it=>!(k(it) in labels)); if(i<0) i=items.length;
  show();
}
function show(){
  const done=Object.keys(labels).length;
  if(i>=items.length){document.getElementById('pos').textContent='All '+items.length+' done - thank you!';
    document.getElementById('img').style.display='none';document.getElementById('what').textContent='';return;}
  const it=items[i];
  document.getElementById('img').style.display='';
  document.getElementById('img').src='/img/'+it.video+'/'+it.second+'.jpg';
  document.getElementById('pos').textContent=(i+1)+' / '+items.length+'  ('+done+' labelled)';
  const m=Math.floor(it.second/60), s=String(it.second%60).padStart(2,'0');
  document.getElementById('what').textContent=d_style[it.video]+'  - '+m+':'+s;
  const p=labels[k(it)]; document.getElementById('prev').textContent=p?('(you said: '+p+')'):'';
}
async function answer(label){
  const it=items[i]; if(!it) return;
  await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({video:it.video,second:it.second,label})});
  labels[k(it)]=label;
  // jump to the next frame without a label (earlier batches stay labelled)
  let j=items.findIndex((x,n)=>n>i && !(k(x) in labels)); i=(j<0)?items.length:j; show();
}
function back(){ if(i>0){i--; show();} }
document.addEventListener('keydown',e=>{
  const c=e.key.toLowerCase();
  if(c==='y')answer('useful'); else if(c==='n')answer('not'); else if(c==='s')answer('skip'); else if(c==='b')back();
});
const d_style=STYLE_JSON;
load();
</script></body></html>"""


AT_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Label --at pairs</title>
<style>
 body{margin:0;font:16px system-ui;background:#111;color:#eee;padding:10px 16px}
 #said{font-size:19px;background:#1d2a1d;padding:12px;border-radius:8px;margin:8px 0;line-height:1.4}
 #grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:8px}
 .t{cursor:pointer;border:3px solid #333;border-radius:6px;overflow:hidden;background:#000}
 .t:hover{border-color:#ffb300}.t.picked{border-color:#2e7d32}
 .t img{width:100%;display:block}.t div{font-size:13px;padding:2px 6px;color:#ccc}
 button{font:16px system-ui;padding:10px 18px;margin:6px 6px 6px 0;border:0;border-radius:8px;cursor:pointer}
 .none{background:#c62828;color:#fff}.b{background:#333;color:#ccc}
 #help{color:#aaa;font-size:14px}
 #zoom{display:none;position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:9;flex-direction:column;align-items:center;justify-content:center}
 #zoom img{max-width:96vw;max-height:80vh;border:3px solid #555}
 #zoom .bar{margin-top:10px}.pick{background:#2e7d32;color:#fff}
</style></head><body>
<div id="zoom"><div id="ztime"></div><img id="zimg"><div class="bar">
 <button class="b" onclick="step(-1)">&larr; earlier</button>
 <button class="pick" onclick="pickZoom()">Pick this one</button>
 <button class="b" onclick="step(1)">later &rarr;</button>
 <button class="b" onclick="closeZoom()">Close (Esc)</button></div></div>
<div><b id="pos"></b> &middot; <span id="what"></span></div>
<div id="help">Read what was said, then click the FIRST thumbnail where it clearly appears on screen
(the slide, diagram, writing or code it is about). Click a thumbnail to see it full size, then "Pick this one"
(arrow keys step through). If it never appears in these two minutes, press "Not shown".</div>
<div id="said"></div>
<button class="none" onclick="answer('none')">Not shown in these 2 minutes</button>
<button class="b" onclick="answer('skip')">Can't tell (leave it out)</button>
<button class="b" onclick="back()">Back</button> <span id="prev"></span>
<div id="grid"></div>
<script>
let items=[], labels={}, i=0;
const k=(it)=>it.video+'|'+it.at;
async function load(){const r=await fetch('/api/at_state');const d=await r.json();items=d.items;labels=d.labels;
 i=items.findIndex(it=>!(k(it) in labels)); if(i<0)i=items.length; show();}
function mmss(s){return Math.floor(s/60)+':'+String(s%60).padStart(2,'0');}
function show(){
 const g=document.getElementById('grid'); g.innerHTML='';
 if(i>=items.length){document.getElementById('pos').textContent='All '+items.length+' done - thank you!';
  document.getElementById('said').textContent='';return;}
 const it=items[i];
 document.getElementById('pos').textContent=(i+1)+' / '+items.length;
 document.getElementById('what').textContent=it.style+' - said at '+it.at;
 document.getElementById('said').textContent='"'+it.said+'"';
 const p=labels[k(it)]; document.getElementById('prev').textContent=p?('(you said: '+(p==='none'?'not shown':p==='skip'?"can't tell":mmss(+p))+')'):'';
 for(const s of it.seconds){const d=document.createElement('div');d.className='t'+(p==String(s)?' picked':'');
  d.innerHTML='<img loading="lazy" src="/atimg/'+it.video+'/'+it.t+'/s'+s+'.jpg"><div>'+mmss(s)+'  (+'+(s-it.t)+'s)</div>';
  d.onclick=()=>openZoom(s); g.appendChild(d);}
 window.scrollTo(0,0);
}
async function answer(a){const it=items[i];if(!it)return;
 await fetch('/api/at_label',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({video:it.video,at:it.at,answer:a})});
 labels[k(it)]=a;i++;show();}
function back(){if(i>0){i--;show();}}
let zs=null;
function openZoom(s){zs=s;const it=items[i];document.getElementById('zimg').src='/atimg/'+it.video+'/'+it.t+'/s'+s+'.jpg';
 document.getElementById('ztime').textContent=mmss(s)+'  (+'+(s-it.t)+'s after it was said)';document.getElementById('zoom').style.display='flex';}
function closeZoom(){document.getElementById('zoom').style.display='none';zs=null;}
function step(d){const secs=items[i].seconds;const j=secs.indexOf(zs)+d;if(j>=0&&j<secs.length)openZoom(secs[j]);}
function pickZoom(){const s=zs;closeZoom();answer(String(s));}
document.addEventListener('keydown',e=>{if(zs===null)return;
 if(e.key==='Escape')closeZoom();else if(e.key==='ArrowLeft')step(-1);else if(e.key==='ArrowRight')step(1);else if(e.key==='Enter')pickZoom();});
load();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            return self._send(200, PAGE.replace("STYLE_JSON", json.dumps(STYLE)).encode(), "text/html; charset=utf-8")
        if self.path == "/at":
            return self._send(200, AT_PAGE.encode(), "text/html; charset=utf-8")
        if self.path == "/api/at_state":
            pairs = json.loads((TESTDATA / "at_pairs.json").read_text())
            for pr in pairs:
                pr["style"] = STYLE[pr["video"]]
            labels = {f"{v}|{a}": ans for (v, a), ans in load_at_labels().items()}
            return self._send(200, json.dumps({"items": pairs, "labels": labels}).encode(), "application/json")
        m = re.fullmatch(r"/atimg/([a-z]+)/(\d+)/s(\d+)\.jpg", self.path)
        if m and m.group(1) in KEYS:
            f = TESTDATA / m.group(1) / "at" / str(int(m.group(2))) / f"s{int(m.group(3))}.jpg"
            if f.exists():
                return self._send(200, f.read_bytes(), "image/jpeg")
        if self.path == "/api/state":
            labels = {f"{v}:{s}": l for (v, s), l in load_labels().items()}
            return self._send(200, json.dumps({"items": load_queue(), "labels": labels}).encode(), "application/json")
        m = re.fullmatch(r"/img/([a-z]+(?:-[a-z]+)?)/(\d+)\.jpg", self.path)
        if m and m.group(1) in KEYS:   # only known keys and digits: no path tricks
            f = TESTDATA / m.group(1) / "label" / f"{int(m.group(2))}.jpg"
            if f.exists():
                return self._send(200, f.read_bytes(), "image/jpeg")
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if self.path == "/api/at_label":
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0))
            answer = str(data.get("answer"))
            if (data.get("video") not in KEYS or not re.fullmatch(r"\d\d:\d\d", str(data.get("at")))
                    or not (answer in ("none", "skip") or answer.isdigit())):
                return self._send(400, b"bad label", "text/plain")
            labels = load_at_labels()
            labels[(data["video"], data["at"])] = answer
            save_at_labels(labels)
            return self._send(200, b"ok", "text/plain")
        if self.path != "/api/label":
            return self._send(404, b"not found", "text/plain")
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0))
        if data.get("video") not in KEYS or data.get("label") not in ("useful", "not", "skip"):
            return self._send(400, b"bad label", "text/plain")
        labels = load_labels()
        labels[(data["video"], int(data["second"]))] = data["label"]
        save_labels(labels)
        self._send(200, b"ok", "text/plain")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"Labelling page: http://127.0.0.1:{PORT}  (Ctrl-C to stop)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
