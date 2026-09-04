#!/usr/bin/env python3
"""Tiny read-only clip browser for the kitchen satellite: lists the audio the
satellite captured (command captures, verify-ok/rej wake clips) newest first,
with an <audio> player per clip and the transcript/intent the orchestrator
returned (joined from events.jsonl by clip name). Nothing here can write.

  http://192.168.10.251:8782/            newest 60 command clips (pre-roll + full command)
  ?kind=cmd|verify-ok|verify-rej|all      which clips (default cmd; verify-* are the 2.5 s pre-roll only)
  ?date=20260826                          one day
  ?q=text                                 substring of transcript/name
  ?limit=200
  ?only=suspect                           cmd clips whose first word looks swallowed

Envelope column (2026-09-03): every cmd clip is PREROLL_S (2.5 s) of wake-phrase
pre-roll followed by the command capture, which starts AT the stage-1 trigger
(no drain on a wake turn), so the verify round trip and the chime itself sit
inside the capture; the grey band is the chime (trigger + chime_ms from the
matching verify event, ~1 s long) — the AEC cancels it, so what you see there
is the suppressor's clamp, not the ding. The sparkline is the 50 ms RMS
envelope; dotted line = capture start, yellow band = capture start → first
speech, red = onset. Since the two-stream capture (MIC_CHANNELS=2) the audio
after the dotted line is the command channel (USB ch1, linear AEC residual),
the pre-roll stays ch0. "clamp" = floor in the yellow band minus the clip's
tail floor (negative = mic held down); "onset" = seconds from capture start.
"""
import datetime, html, json, math, os, re, struct, wave
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    import audioop  # stdlib through 3.12; C-speed RMS
except Exception:  # noqa: BLE001
    audioop = None

DATA = os.getenv("CLIP_DATA", "/home/pi/voice-pipeline/data")
CLIPS = os.path.join(DATA, "clips")
EVENTS = os.path.join(DATA, "events.jsonl")
NAME_RE = re.compile(r"^(cmd|verify-ok|verify-rej|near|mark)-(\d{8})-(\d{6})\.wav$")
PREROLL_S = float(os.getenv("PREROLL_S", "2.5"))   # must match satellite PREROLL_S
FRAME_S = 0.05
CHIME_LEN_S = 0.97          # sounds/wake.wav
# Words a real command essentially never starts with — a transcript that opens
# on one of these is a command whose head got swallowed ("for fifteen minutes",
# "a timer for six minutes", "the glare", "three minutes to my ...").
NON_HEAD = {"for", "a", "an", "the", "to", "and", "of", "minutes", "minute", "seconds",
            "my", "me", "it", "is", "on", "off", "in", "up", "down", "more", "timer",
            "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "fifteen", "twenty", "thirty"}

_ENV_CACHE = {}


def load_events():
    """clip name -> event, plus the (epoch, chime_ms) of every confirmed wake
    so a cmd clip can be told where its ding sits."""
    meta, chimes = {}, []
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
                if e.get("type") == "verify" and e.get("chime_ms") is not None:
                    try:
                        chimes.append((datetime.datetime.fromisoformat(e["ts"]).timestamp(),
                                       float(e["chime_ms"])))
                    except Exception:
                        pass
    except FileNotFoundError:
        pass
    return meta, chimes


def chime_for(chimes, d, t):
    """Chime start (s into the clip) for the cmd clip stamped d/t: the cmd
    stamp is the END of the turn, so take the newest confirmed wake in the
    preceding 90 s. None if there is no match."""
    try:
        end = datetime.datetime.strptime(d + t, "%Y%m%d%H%M%S").timestamp()
    except ValueError:
        return None
    best = None
    for ts, ms in chimes:
        if ts <= end and end - ts <= 90 and (best is None or ts > best[0]):
            best = (ts, ms)
    return PREROLL_S + best[1] / 1000.0 if best else None


def envelope(path):
    """50 ms RMS envelope in dBFS (list of floats) + duration, cached by mtime."""
    try:
        st = os.stat(path)
    except OSError:
        return [], 0.0
    key = (path, st.st_mtime, st.st_size)
    hit = _ENV_CACHE.get(key)
    if hit:
        return hit
    try:
        with wave.open(path) as w:
            sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            raw = w.readframes(n)
    except Exception:
        return [], 0.0
    if sw != 2:
        return [], n / sr
    step = int(sr * FRAME_S) * ch * 2
    out = []
    for i in range(0, len(raw), step):
        seg = raw[i:i + step]
        if audioop:
            rms = audioop.rms(seg, 2) / 32768.0
        else:
            s = struct.unpack("<%dh" % (len(seg) // 2), seg)
            rms = math.sqrt(sum(x * x for x in s) / max(1, len(s))) / 32768.0
        out.append(20 * math.log10(rms + 1e-9))
    res = (out, n / sr)
    if len(_ENV_CACHE) > 400:
        _ENV_CACHE.clear()
    _ENV_CACHE[key] = res
    return res


def _med(v):
    v = sorted(v)
    return v[len(v) // 2] if v else float("nan")


def analyze_cmd(env, transcript):
    """Capture-start floor vs tail floor, speech onset after capture start, and
    a 'suspect' verdict for a cmd clip. Returns dict or None if too short."""
    p = int(PREROLL_S / FRAME_S)
    if len(env) < p + 12:
        return None
    tail = _med(env[-10:])                           # last 0.5 s (post-endpoint silence)
    onset = None
    for i in range(p, len(env) - 1):
        if env[i] > tail + 10 and env[i + 1] > tail + 6:
            onset = (i - p) * FRAME_S
            break
    # Floor between capture start and speech onset (capped at 1 s). Below the
    # clip's own tail floor = the suppressor is still holding the mic down.
    end = p + (min(20, int(onset / FRAME_S)) if onset is not None else 20)
    hang = _med(env[p:end]) if end - p >= 4 else float("nan")
    clamp = hang - tail if hang == hang else None
    head = (transcript or "").strip().lower().split(" ")[0].strip(".,?!'") if transcript else ""
    reasons = []
    if head in NON_HEAD:
        reasons.append("starts on '%s'" % head)
    # Speech that surfaces 0.2-1.2 s in, with the floor held >=4 dB down right
    # up to it, most likely STARTED earlier than the onset we can see.
    if clamp is not None and onset is not None and 0.2 <= onset <= 1.2 and clamp <= -4:
        reasons.append("floor held %.0f dB down until onset" % (-clamp))
    return {"hang": hang, "tail": tail, "clamp": clamp, "onset": onset, "reasons": reasons}


def sparkline(env, dur, onset=None, is_cmd=False, w=340, h=44, chime=None):
    if not env:
        return ""
    lo, hi = -75.0, -5.0
    n = len(env)
    xs = [i * w / max(1, n - 1) for i in range(n)]
    ys = [h - (min(hi, max(lo, v)) - lo) / (hi - lo) * h for v in env]
    pts = " ".join("%.1f,%.1f" % (x, y) for x, y in zip(xs, ys))
    parts = ["<svg width=%d height=%d viewBox='0 0 %d %d' style='background:#f6f6f6'>" % (w, h, w, h)]
    if is_cmd and dur > PREROLL_S:
        if chime is not None and chime < dur:
            cx0 = chime / dur * w
            cx1 = min(dur, chime + CHIME_LEN_S) / dur * w
            parts.append("<rect x='%.1f' y='0' width='%.1f' height='%d' fill='#bbb' opacity='.6'/>"
                         % (cx0, max(1.0, cx1 - cx0), h))
        x0 = PREROLL_S / dur * w
        x1 = (PREROLL_S + onset) / dur * w if onset is not None else w
        parts.append("<rect x='%.1f' y='0' width='%.1f' height='%d' fill='#fde68a' opacity='.7'/>"
                     % (x0, max(1.0, x1 - x0), h))
        parts.append("<line x1='%.1f' y1='0' x2='%.1f' y2='%d' stroke='#333' stroke-dasharray='3,2'/>"
                     % (x0, x0, h))
        if onset is not None:
            parts.append("<line x1='%.1f' y1='0' x2='%.1f' y2='%d' stroke='#c00'/>" % (x1, x1, h))
    parts.append("<polyline points='%s' fill='none' stroke='#06c' stroke-width='1.2'/>" % pts)
    parts.append("</svg>")
    return "".join(parts)


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
        kind = q.get("kind", ["cmd"])[0]
        only = q.get("only", [""])[0]
        if only == "suspect":
            kind = "cmd"
        kinds = None if kind == "all" else set(kind.split(","))
        date = q.get("date", [""])[0]
        needle = q.get("q", [""])[0].lower()
        limit = int(q.get("limit", ["60"])[0])
        meta, chimes = load_events()
        rows = []
        names = []
        for n in os.listdir(CLIPS):
            m = NAME_RE.match(n)
            if m:
                names.append((m.group(2), m.group(3), n))
        # newest first by TIME, not by name (a name sort would put every
        # verify-ok-* ahead of every cmd-* and hide the command clips)
        for _d, _t, n in sorted(names, reverse=True):
            m = NAME_RE.match(n)
            k, d, t = m.groups()
            if kinds and k not in kinds:
                continue
            if date and d != date:
                continue
            e = meta.get(n, {})
            text = " ".join(str(e.get(x) or "") for x in ("transcript", "intent", "response"))
            if needle and needle not in (text + n).lower():
                continue
            path = os.path.join(CLIPS, n)
            env, dur = envelope(path)
            ana = analyze_cmd(env, e.get("transcript")) if k == "cmd" else None
            if only == "suspect" and not (ana and ana["reasons"]):
                continue
            rows.append((n, k, d, t, e, env, dur, ana, chime_for(chimes, d, t) if k == "cmd" else None))
            if len(rows) >= limit:
                break
        out = [
            "<!doctype html><meta charset=utf-8><title>kitchen clips</title>",
            "<style>body{font:14px system-ui;margin:1em;max-width:1400px}table{border-collapse:collapse;width:100%}",
            "td,th{border-bottom:1px solid #ddd;padding:6px;vertical-align:top;text-align:left}",
            ".k-cmd{color:#06c}.k-verify-rej{color:#a00}.k-verify-ok{color:#080}.k-near{color:#a60}audio{width:260px}",
            ".sus{background:#fff1f0}.tag{color:#c00;font-weight:600}.num{font-variant-numeric:tabular-nums;white-space:nowrap}",
            "form input{margin-right:.5em}</style>",
            "<h2>Kitchen satellite clips (.251)</h2>",
            "<form>kind <input name=kind value='%s' size=16> date <input name=date value='%s' size=9 placeholder=YYYYMMDD> "
            "q <input name=q value='%s'> limit <input name=limit value='%d' size=4> "
            "<label><input type=checkbox name=only value=suspect %s> suspect only</label> <button>go</button> "
            "<small>kind = cmd | verify-ok | verify-rej | near | all (comma-join)</small></form>"
            % (html.escape(kind), html.escape(date), html.escape(needle), limit,
               "checked" if only == "suspect" else ""),
            "<p><small>cmd clips = 2.5 s pre-roll (wake phrase) + command capture, exactly what the orchestrator transcribed. "
            "verify-* = the 2.5 s pre-roll that stage 2 judged. near = stage-1 scored 0.3-0.49 and never fired. "
            "<b>Envelope</b>: dotted line = capture start (the stage-1 trigger); grey = the ding (cancelled by the AEC, so you see the clamp, not the chime); "
            "yellow band = capture start &rarr; first speech; red line = speech onset. After the dotted line the audio is the command channel (USB ch1, linear AEC residual) since 2026-09-03 20:03; the pre-roll is ch0. <b>clamp</b> = floor in the first second of capture minus the clip's tail floor "
            "(negative = the echo suppressor is still holding the mic down; a first word spoken there is attenuated or gone). "
            "<b>Listen test</b>: say a fixed phrase (\"set a timer for five minutes\") starting ON the ding, from where you normally stand; "
            "then play the clip and listen for the head of the phrase inside the yellow band — judge by ear and by the band, not by the transcript.</small></p>",
            "<table><tr><th>when</th><th>kind</th><th>len</th><th>play</th><th>envelope</th><th class=num>clamp / onset</th>"
            "<th>transcript &rarr; intent &rarr; response</th></tr>",
        ]
        for n, k, d, t, e, env, dur, ana, chime in rows:
            when = "%s-%s-%s %s:%s:%s" % (d[:4], d[4:6], d[6:], t[:2], t[2:4], t[4:])
            desc = ""
            if e:
                desc = "<b>%s</b> &rarr; <i>%s</i> &rarr; %s" % tuple(
                    html.escape(str(e.get(x) or "")) for x in ("transcript", "intent", "response"))
            stats, cls = "", ""
            if ana:
                on = "%.2fs" % ana["onset"] if ana["onset"] is not None else "—"
                cl = "%+.0f dB" % ana["clamp"] if ana["clamp"] is not None else "n/a"
                stats = "%s / %s" % (cl, on)
                if ana["reasons"]:
                    cls = " class=sus"
                    desc = "<span class=tag>SUSPECT: %s</span><br>%s" % (html.escape("; ".join(ana["reasons"])), desc)
            out.append(
                "<tr%s><td><a href='/clips/%s'>%s</a></td><td class='k-%s'>%s</td><td>%.1fs</td>"
                "<td><audio controls preload=none src='/clips/%s'></audio></td><td>%s</td><td class=num>%s</td><td>%s</td></tr>"
                % (cls, n, when, k, k, dur, n,
                   sparkline(env, dur, ana["onset"] if ana else None, k == "cmd", chime=chime), stats, desc))
        out.append("</table>")
        body = "\n".join(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("CLIP_PORT", "8782"))), H).serve_forever()
