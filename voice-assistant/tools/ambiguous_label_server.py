#!/usr/bin/env python3
"""Review page for the wake-corpus clips a human has to call.

Serves the `ambiguous` bucket (stage-2 rejects whose transcript starts with
"okay") and the short-flagged positives from a build_real_sets.py output dir,
one clip at a time, and records wake / not / unsure. Stdlib only.

  python3 ambiguous_label_server.py /home/pi/wake-corpus/real_sets/2026-08-31-t7   # http://beelink:8797/

Writes <set>/ambiguous_labels.jsonl (append-only, last wins), which
build_real_sets.py consumes via --labels. Clips are ordered so the ones most
likely to be a real wake come first: a continuation that sounds like
"computer" (c/k/p/q/g onset), then a high stage-2 score, then bare "Okay.",
then long sentences last.

Keys: 1 = wake, 2 = not, 3 = unsure, space = replay, ← = back.
"""
from __future__ import annotations

import csv
import html
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = 8797
SET_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/pi/wake-corpus/real_sets/2026-08-31-t7")
LABELS = SET_DIR / "ambiguous_labels.jsonl"
CONT_RE = re.compile(r"^\W*(?:okay|ok)\W*(\w*)", re.I)


def priority(r: dict) -> tuple:
    text = r["transcript"]
    m = CONT_RE.match(text)
    cont = (m.group(1) if m else "").lower()
    like_computer = 0 if cont[:1] in ("c", "k", "p", "q", "g") and cont else 1
    try:
        score = -float(r["stage2_score"] or 0)
    except ValueError:
        score = 0.0
    bare = 0 if cont else 1
    return (0 if r["flag"] == "short" else 1, like_computer, bare, score, len(text))


def load_queue() -> list[dict]:
    rows = list(csv.DictReader((SET_DIR / "manifest.csv").open()))
    q = [r for r in rows if r["set"] == "ambiguous" or r["flag"] == "short"]
    for r in q:
        r["key"] = f'{r["sat"]}/{r["clip"]}'
    q.sort(key=priority)
    return q


def load_labels() -> dict[str, str]:
    out: dict[str, str] = {}
    if LABELS.exists():
        for line in LABELS.read_text().splitlines():
            try:
                d = json.loads(line); out[d["key"]] = d["label"]
            except (ValueError, KeyError):
                pass
    return out


PAGE = """<!doctype html><meta charset=utf-8><title>wake clip review</title>
<meta name=viewport content="width=device-width, initial-scale=1">
<style>body{font:16px system-ui;margin:1.2em auto;max-width:720px;padding:0 1em}
.card{border:1px solid #ccc;border-radius:10px;padding:1em;margin:1em 0}
.t{font-size:22px;margin:.4em 0}.m{color:#666;font-size:14px}
button{font-size:18px;padding:.6em 1.2em;margin:.3em;border-radius:8px;border:1px solid #888;background:#f4f4f4;cursor:pointer}
button.w{background:#d9f5d9}button.n{background:#f8d9d9}button.u{background:#eee}
.done{color:#080}.bar{height:8px;background:#eee;border-radius:4px}.bar>div{height:8px;background:#4a8;border-radius:4px}
ul{font-size:13px;color:#555;max-height:220px;overflow:auto}</style>
<h2>Wake clip review <span class=m id=prog></span></h2>
<div class=bar><div id=barfill style="width:0"></div></div>
<p class=m>Is "okay computer" actually said in this clip? <b>1</b> wake · <b>2</b> not · <b>3</b> unsure · <b>space</b> replay · <b>←</b> back.
Short-flagged positives come first: <i>wake</i> keeps them, <i>not</i> drops them.</p>
<div class=card id=card></div>
<ul id=recent></ul>
<script>
let q=[], labels={}, i=0;
async function load(){const r=await (await fetch('/api/state')).json(); q=r.queue; labels=r.labels;
  i=q.findIndex(c=>!labels[c.key]); if(i<0)i=q.length; render();}
function render(){const n=Object.keys(labels).filter(k=>q.some(c=>c.key===k)).length;
  document.getElementById('prog').textContent=`${n} / ${q.length} labelled`;
  document.getElementById('barfill').style.width=(100*n/q.length)+'%';
  const card=document.getElementById('card');
  if(i>=q.length){card.innerHTML='<p class=done>All done. Labels are in __LABELS__</p>';return;}
  const c=q[i];
  card.innerHTML=`<div class=m>${i+1} of ${q.length} · ${c.sat} · ${c.at} · ${c.flag==='short'?'SHORT POSITIVE':'ambiguous reject'} · s1 ${c.stage1||'—'} · s2 ${c.stage2_score||'—'} · ${c.speaker||''}</div>
  <div class=t>“${esc(c.transcript)||'(no transcript)'}”</div>
  <audio id=a controls autoplay src="/clip/${encodeURIComponent(c.out)}"></audio>
  <div>${labels[c.key]?'<span class=m>current: '+labels[c.key]+'</span>':''}</div>
  <div><button class=w onclick="mark('wake')">1 · wake</button><button class=n onclick="mark('not')">2 · not</button><button class=u onclick="mark('unsure')">3 · unsure</button></div>`;
  const rec=document.getElementById('recent'); rec.innerHTML=q.slice(Math.max(0,i-8),i).reverse().map(c=>`<li>${labels[c.key]||'—'} — ${esc(c.transcript)} (${c.sat} ${c.at})</li>`).join('');}
function esc(s){return (s||'').replace(/[&<>]/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[x]))}
async function mark(label){const c=q[i]; labels[c.key]=label;
  await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:c.key,label})});
  i++; render();}
document.addEventListener('keydown',e=>{if(e.key==='1')mark('wake');else if(e.key==='2')mark('not');else if(e.key==='3')mark('unsure');
  else if(e.key===' '){e.preventDefault();const a=document.getElementById('a');if(a){a.currentTime=0;a.play();}}
  else if(e.key==='ArrowLeft'&&i>0){i--;render();}});
load();
</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self._send(200, PAGE.replace("__LABELS__", html.escape(str(LABELS))).encode(),
                       "text/html; charset=utf-8")
        elif u.path == "/api/state":
            self._send(200, json.dumps({"queue": load_queue(), "labels": load_labels()}).encode(),
                       "application/json")
        elif u.path.startswith("/clip/"):
            rel = Path(u.path[len("/clip/"):].replace("%2F", "/"))
            target = (SET_DIR / rel).resolve()
            if not str(target).startswith(str(SET_DIR.resolve())) or target.suffix != ".wav" or not target.is_file():
                self._send(404, b"no", "text/plain"); return
            self._send(200, target.read_bytes(), "audio/wav")
        else:
            self._send(404, b"no", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/api/label":
            self._send(404, b"no", "text/plain"); return
        n = int(self.headers.get("Content-Length", 0))
        try:
            d = json.loads(self.rfile.read(n))
            assert d["label"] in ("wake", "not", "unsure") and "/" in d["key"]
        except Exception:  # noqa: BLE001
            self._send(400, b"bad", "text/plain"); return
        with LABELS.open("a") as fh:
            fh.write(json.dumps({"key": d["key"], "label": d["label"], "at": time.time()}) + "\n")
        self._send(200, b"ok", "text/plain")


if __name__ == "__main__":
    q = load_queue()
    print(f"[review] {len(q)} clips from {SET_DIR}, labels -> {LABELS}, http://0.0.0.0:{PORT}/", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
