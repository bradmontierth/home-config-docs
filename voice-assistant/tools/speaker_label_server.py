#!/usr/bin/env python3
"""Speaker-labeling review page for the speaker-ID enrollment set (item 9).

Serves the kitchen-satellite clips synced to the beelink and records who is
speaking in each one. Stdlib only (beelink host python has no httpx/fastapi).

  python3 speaker_label_server.py            # http://beelink:8791/

Reads  ~/voice-pipeline/data/speaker_clips/           (cmd-*.wav, verify-ok-*.wav)
       ~/voice-pipeline/data/speaker_clips/satellite-events.jsonl  (transcripts)
Writes ~/voice-pipeline/data/speaker_labels.jsonl     (append-only; last wins)

Labels: brad / adrienne / kid / other (guest, TV, noise) / skip (unsure).
Only brad/adrienne/kid feed enrollment; skip and other are excluded.
"""
from __future__ import annotations

import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

DATA = Path.home() / "voice-pipeline" / "data"
CLIPS_DIR = DATA / "speaker_clips"
LABELS_PATH = DATA / "speaker_labels.jsonl"
EVENTS_PATH = CLIPS_DIR / "satellite-events.jsonl"
VALID_LABELS = {"brad", "adrienne", "kid", "other", "skip"}


def load_transcripts() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not EVENTS_PATH.exists():
        return out
    for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        clip = ev.get("clip")
        if clip:
            out[clip] = {
                "transcript": ev.get("transcript") or "",
                "intent": ev.get("intent") or ev.get("type") or "",
            }
    return out


def load_labels() -> dict[str, str]:
    out: dict[str, str] = {}
    if not LABELS_PATH.exists():
        return out
    for line in LABELS_PATH.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("clip") and rec.get("label") in VALID_LABELS:
            out[rec["clip"]] = rec["label"]
    return out


def clip_list() -> list[dict]:
    meta = load_transcripts()
    labels = load_labels()
    clips = sorted(CLIPS_DIR.glob("cmd-*.wav")) + sorted(CLIPS_DIR.glob("verify-ok-*.wav"))
    return [
        {
            "name": p.name,
            "transcript": meta.get(p.name, {}).get("transcript", ""),
            "intent": meta.get(p.name, {}).get("intent", ""),
            "label": labels.get(p.name),
        }
        for p in clips
    ]


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Speaker labeling</title>
<style>
  body{font-family:system-ui;background:#111;color:#eee;margin:0;padding:1rem;
       display:flex;flex-direction:column;align-items:center;gap:1rem}
  #card{max-width:32rem;width:100%;background:#1c1c1e;border-radius:12px;
        padding:1rem;box-sizing:border-box}
  #progress{color:#888;font-size:.9rem}
  #transcript{font-size:1.25rem;min-height:3.5rem;margin:.5rem 0}
  #meta{color:#888;font-size:.8rem;word-break:break-all}
  audio{width:100%;margin:.75rem 0}
  .row{display:flex;gap:.5rem;flex-wrap:wrap}
  button{flex:1 1 40%;padding:1rem .5rem;font-size:1.1rem;border:0;
         border-radius:10px;color:#fff;cursor:pointer}
  #b-brad{background:#0a84ff} #b-adrienne{background:#bf5af2}
  #b-kid{background:#30d158} #b-other{background:#636366}
  #b-skip{background:#333;flex-basis:100%} #b-back{background:#333;flex-basis:100%}
  #done{font-size:1.3rem;text-align:center;display:none}
  #counts{color:#888;font-size:.85rem}
</style></head><body>
<div id="card">
  <div id="progress"></div>
  <div id="transcript"></div>
  <div id="meta"></div>
  <audio id="player" controls></audio>
  <div class="row">
    <button id="b-brad" onclick="label('brad')">Brad</button>
    <button id="b-adrienne" onclick="label('adrienne')">Adrienne</button>
    <button id="b-kid" onclick="label('kid')">Kid</button>
    <button id="b-other" onclick="label('other')">Other / noise</button>
    <button id="b-skip" onclick="label('skip')">Unsure — skip</button>
    <button id="b-back" onclick="back()">&#8592; Back one</button>
  </div>
  <div id="counts"></div>
</div>
<div id="done">All clips labeled &#127881;</div>
<script>
let clips=[], idx=0;
function fmtCounts(){
  const c={};
  clips.forEach(x=>{ if(x.label) c[x.label]=(c[x.label]||0)+1; });
  return Object.entries(c).map(([k,v])=>k+": "+v).join("  ·  ");
}
function firstUnlabeled(){
  const i=clips.findIndex(c=>!c.label);
  return i<0?clips.length:i;
}
function show(){
  const card=document.getElementById('card'), done=document.getElementById('done');
  document.getElementById('counts').textContent=fmtCounts();
  if(idx>=clips.length){card.style.display='none';done.style.display='block';return;}
  card.style.display='block';done.style.display='none';
  const c=clips[idx];
  document.getElementById('progress').textContent=
    (idx+1)+" / "+clips.length+(c.label?"  (labeled: "+c.label+")":"");
  document.getElementById('transcript').textContent=
    c.transcript ? "“"+c.transcript+"”" : "(no transcript — listen)";
  document.getElementById('meta').textContent=c.name+(c.intent?"  ·  "+c.intent:"");
  const p=document.getElementById('player');
  p.src="/clip/"+encodeURIComponent(c.name);
  p.play().catch(()=>{});
}
async function label(l){
  const c=clips[idx];
  await fetch('/api/label',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({clip:c.name,label:l})});
  c.label=l; idx++;
  while(idx<clips.length && clips[idx].label) idx++;
  show();
}
function back(){ if(idx>0){idx--; show();} }
fetch('/api/state').then(r=>r.json()).then(d=>{clips=d.clips; idx=firstUnlabeled(); show();});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body, ctype="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._send(HTTPStatus.OK, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._send(HTTPStatus.OK, json.dumps({"clips": clip_list()}).encode())
        elif self.path.startswith("/clip/"):
            name = unquote(self.path[len("/clip/"):])
            target = (CLIPS_DIR / name).resolve()
            if target.parent != CLIPS_DIR.resolve() or not target.suffix == ".wav" or not target.exists():
                self._send(HTTPStatus.NOT_FOUND, b'{"error":"no such clip"}')
                return
            self._send(HTTPStatus.OK, target.read_bytes(), "audio/wav")
        else:
            self._send(HTTPStatus.NOT_FOUND, b'{"error":"unknown path"}')

    def do_POST(self):
        if self.path != "/api/label":
            self._send(HTTPStatus.NOT_FOUND, b'{"error":"unknown path"}')
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            rec = json.loads(self.rfile.read(length))
            clip, lab = rec["clip"], rec["label"]
            assert lab in VALID_LABELS and (CLIPS_DIR / clip).exists()
        except Exception:
            self._send(HTTPStatus.BAD_REQUEST, b'{"error":"bad label record"}')
            return
        with LABELS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"clip": clip, "label": lab,
                                 "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}) + "\n")
        self._send(HTTPStatus.OK, b'{"ok":true}')


if __name__ == "__main__":
    print(f"[labeler] {len(clip_list())} clips, labels -> {LABELS_PATH}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8791), Handler).serve_forever()
