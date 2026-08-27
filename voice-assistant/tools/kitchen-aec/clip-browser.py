#!/usr/bin/env python3
"""Tiny read-only clip browser for the kitchen satellite: lists the audio the
satellite captured (command captures, verify-ok/rej wake clips) newest first,
with an <audio> player per clip and the transcript/intent the orchestrator
returned (joined from events.jsonl by clip name). Nothing here can write.

  http://192.168.10.251:8782/            newest 60 command + verify-ok clips
  ?kind=cmd|verify-ok|verify-rej|all      which clips (default cmd,verify-ok)
  ?date=20260826                          one day
  ?q=text                                 substring of transcript/name
  ?limit=200
"""
import html, json, os, re, wave
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DATA = "/home/pi/voice-pipeline/data"
CLIPS = os.path.join(DATA, "clips")
EVENTS = os.path.join(DATA, "events.jsonl")
NAME_RE = re.compile(r"^(cmd|verify-ok|verify-rej|near|mark)-(\d{8})-(\d{6})\.wav$")


def load_events():
    meta = {}
    try:
        with open(EVENTS) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                c = e.get("clip")
                if c:
                    meta[c] = e
    except FileNotFoundError:
        pass
    return meta


def duration(path):
    try:
        with wave.open(path) as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return 0.0


class H(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith("/clips/"):
            name = os.path.basename(u.path)
            p = os.path.join(CLIPS, name)
            if not NAME_RE.match(name) or not os.path.exists(p):
                self.send_error(404)
                return
            data = open(p, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path != "/":
            self.send_error(404)
            return
        q = parse_qs(u.query)
        kind = q.get("kind", ["cmd,verify-ok"])[0]
        kinds = None if kind == "all" else set(kind.split(","))
        date = q.get("date", [""])[0]
        needle = q.get("q", [""])[0].lower()
        limit = int(q.get("limit", ["60"])[0])
        meta = load_events()
        rows = []
        for n in sorted(os.listdir(CLIPS), reverse=True):
            m = NAME_RE.match(n)
            if not m:
                continue
            k, d, t = m.groups()
            if kinds and k not in kinds:
                continue
            if date and d != date:
                continue
            e = meta.get(n, {})
            text = " ".join(str(e.get(x) or "") for x in ("transcript", "intent", "response"))
            if needle and needle not in (text + n).lower():
                continue
            rows.append((n, k, d, t, e))
            if len(rows) >= limit:
                break
        out = [
            "<!doctype html><meta charset=utf-8><title>kitchen clips</title>",
            "<style>body{font:14px system-ui;margin:1em;max-width:1100px}table{border-collapse:collapse;width:100%}",
            "td,th{border-bottom:1px solid #ddd;padding:6px;vertical-align:top;text-align:left}",
            ".k-cmd{color:#06c}.k-verify-rej{color:#a00}.k-verify-ok{color:#080}.k-near{color:#a60}audio{width:280px}",
            "form input{margin-right:.5em}</style>",
            "<h2>Kitchen satellite clips (.251)</h2>",
            "<form>kind <input name=kind value='%s' size=16> date <input name=date value='%s' size=9 placeholder=YYYYMMDD> "
            "q <input name=q value='%s'> limit <input name=limit value='%d' size=4><button>go</button> "
            "<small>kind = cmd | verify-ok | verify-rej | near | all (comma-join)</small></form>"
            % (html.escape(kind), html.escape(date), html.escape(needle), limit),
            "<p><small>cmd clips = pre-roll (wake phrase) + command capture, exactly what the orchestrator transcribed. "
            "verify-* = the 2.5 s pre-roll that stage 2 judged. near = stage-1 scored 0.3-0.49 and never fired (peak in events). Transcript/intent/response come from events.jsonl.</small></p>",
            "<table><tr><th>when</th><th>kind</th><th>len</th><th>play</th><th>transcript &rarr; intent &rarr; response</th></tr>",
        ]
        for n, k, d, t, e in rows:
            when = "%s-%s-%s %s:%s:%s" % (d[:4], d[4:6], d[6:], t[:2], t[2:4], t[4:])
            desc = ""
            if e:
                desc = "<b>%s</b> &rarr; <i>%s</i> &rarr; %s" % tuple(
                    html.escape(str(e.get(x) or "")) for x in ("transcript", "intent", "response"))
            out.append(
                "<tr><td><a href='/clips/%s'>%s</a></td><td class='k-%s'>%s</td><td>%.1fs</td>"
                "<td><audio controls preload=none src='/clips/%s'></audio></td><td>%s</td></tr>"
                % (n, when, k, k, duration(os.path.join(CLIPS, n)), n, desc))
        out.append("</table>")
        body = "\n".join(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8782), H).serve_forever()
