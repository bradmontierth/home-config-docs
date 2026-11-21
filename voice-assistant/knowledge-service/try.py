#!/usr/bin/env python3
"""Ask the knowledge service one question from a shell, with timings.

    ./try.py "how much juice is in a lime"
    ./try.py --room kitchen --when "Saturday, September 12, 2026, 08:40 PM" \
             --earlier "Q: ...|A: ..." "what about with the skin on?"

The system prompt is read from the running orchestrator (ask._system_static),
so this exercises exactly what production sends. Nothing is spoken, shown on
a display, or written to the answers table.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

ap = argparse.ArgumentParser()
ap.add_argument("question")
ap.add_argument("--room", default="kitchen")
ap.add_argument("--when", default=datetime.now().strftime("%A, %B %d, %Y, %I:%M %p"))
ap.add_argument("--earlier", default="", help="'Q: ...|A: ...' lines, | separated")
ap.add_argument("--url", default="http://127.0.0.1:8816")
ap.add_argument("--orch-root", default="/app", help="package root inside the container")
ap.add_argument("--full", action="store_true", help="print the full answer too")
a = ap.parse_args()

system = subprocess.run(
    ["docker", "exec", "-w", a.orch_root, "voice-orchestrator", "python", "-c",
     "import logging; logging.disable(logging.CRITICAL)\n"
     "from orchestrator import ask; print(ask._system_static(), end='')"],
    capture_output=True, text=True, check=True).stdout
parts = [f"<context>\nRoom: {a.room}\nLocal date and time: {a.when} "
         "(the user's home timezone)\n</context>"]
if a.earlier:
    parts.append("<earlier>\n" + "\n".join(a.earlier.split("|")) + "\n</earlier>")
parts.append(f"Question: {a.question}")
req = urllib.request.Request(a.url + "/ask", method="POST",
                             data=json.dumps({"system": system,
                                              "prompt": "\n\n".join(parts)}).encode(),
                             headers={"Content-Type": "application/json"})
t0 = time.monotonic()
acc, first, spoken_at, meta, done = "", None, None, {}, {}
with urllib.request.urlopen(req, timeout=180) as r:
    for line in r:
        ev = json.loads(line)
        ms = round((time.monotonic() - t0) * 1000)
        if ev["t"] == "delta":
            first = first or ms
            acc += ev["text"]
            if spoken_at is None and "===MORE===" in acc:
                spoken_at = ms
        elif ev["t"] == "meta":
            meta = ev
        elif ev["t"] == "done":
            done = ev
        elif ev["t"] == "error":
            sys.exit(f"ERROR after {ms} ms: {ev['error']}")
total = round((time.monotonic() - t0) * 1000)
spoken, _, full = acc.partition("===MORE===")
print(f"model={done.get('model')} warm={meta.get('warm')} fallback={meta.get('fallback')} "
      f"searches={done.get('searches')} first={first}ms spoken_ready={spoken_at}ms total={total}ms")
print("SPOKEN:", spoken.strip())
if a.full:
    print("FULL:", full.strip())
