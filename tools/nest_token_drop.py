#!/usr/bin/env python3
"""Temporary LAN drop box for Nest googleAuth values (homebridge-nest). Never logs request bodies.

Usage: python3 -I tools/nest_token_drop.py <random-path-key>  -> http://192.168.10.217:8841/<key>
Saves cecret_lake/nest/issueToken2 + cookie2 (0600) and tests them against Google live.
Then write them into the Homebridge Nest googleAuth config (config.json is root-owned:
write via docker exec) and restart homebridge-homebridge-1. Stop this server afterwards."""
import html, json, os, re, sys, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

KEY = sys.argv[1]
DIR = '/home/pi/cecret_lake/nest'
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 '
      '(KHTML, like Gecko) Version/18.0 Safari/605.1.15')

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>Nest token drop</title>
<style>body{font:16px -apple-system,sans-serif;max-width:800px;margin:24px auto;padding:0 16px}
textarea{width:100%%;height:110px;font:12px monospace}button{font-size:18px;padding:8px 20px}
.r{padding:12px;border-radius:8px;margin:12px 0}.ok{background:#d4f7d4}.bad{background:#fbd5d5}</style>
<h2>Nest → Homebridge drop box</h2>%s
<form method=post>
<p><b>1. issueToken URL</b> (Headers → Summary → URL, starts with https://accounts.google.com/o/oauth2/iframerpc?action=issueToken)</p>
<textarea name=token autocapitalize=off autocorrect=off spellcheck=false></textarea>
<p><b>2. Cookie</b>: the one-line <code>Cookie:</code> value from Request Headers, <i>or</i> all rows of the Cookies table (⌘A, ⌘C)</p>
<textarea name=cookie autocapitalize=off autocorrect=off spellcheck=false></textarea>
<p><button>Save &amp; test</button></p></form>"""


def norm_cookie(raw):
    raw = raw.strip()
    if raw.lower().startswith('cookie:'):
        raw = raw[7:].strip()
    if '\t' in raw or ('\n' in raw and ';' not in raw):
        pairs = []
        for line in raw.splitlines():
            parts = [p.strip() for p in re.split(r'\t+', line.strip()) if p.strip()]
            if len(parts) >= 2 and parts[0].lower() not in ('name', 'cookie'):
                pairs.append(f'{parts[0]}={parts[1]}')
        return '; '.join(pairs)
    return ' '.join(raw.split())


def test(token, cookie):
    req = urllib.request.Request(token, headers={
        'Cookie': cookie, 'Sec-Fetch-Mode': 'cors', 'X-Requested-With': 'XmlHttpRequest',
        'Referer': 'https://accounts.google.com/o/oauth2/iframe', 'User-Agent': UA})
    try:
        body = urllib.request.urlopen(req, timeout=15).read()
    except urllib.error.HTTPError as e:
        body = e.read()
    try:
        j = json.loads(body)
    except Exception:
        return False, 'Google returned a non-JSON reply'
    if 'access_token' in j:
        return True, 'Google accepted it — access token issued.'
    return False, f"Google said: {j.get('error')} — {j.get('detail', '')}"


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        sys.stderr.write('%s %s\n' % (self.command, self.path.split('?')[0][:8]))

    def ok_path(self):
        if self.path.rstrip('/') != '/' + KEY:
            self.send_error(404)
            return False
        return True

    def page(self, msg=''):
        b = (PAGE % msg).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.ok_path():
            self.page()

    def do_POST(self):
        if not self.ok_path():
            return
        n = int(self.headers.get('Content-Length', 0))
        f = urllib.parse.parse_qs(self.rfile.read(n).decode(), keep_blank_values=True)
        token = ''.join(f.get('token', [''])[0].split())
        cookie = norm_cookie(f.get('cookie', [''])[0])
        if not token.startswith('https://accounts.google.com/o/oauth2/iframerpc?action=issueToken'):
            return self.page('<div class="r bad">That URL doesn\'t look like the issueToken one.</div>')
        if '=' not in cookie:
            return self.page('<div class="r bad">Cookie box looks empty or wrong.</div>')
        for name, val in (('issueToken2', token), ('cookie2', cookie)):
            p = os.path.join(DIR, name)
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as fh:
                fh.write(val + '\n')
        names = [c.split('=', 1)[0] for c in cookie.split('; ')]
        has_ts = any('PSIDTS' in x for x in names)
        good, msg = test(token, cookie)
        extra = '' if has_ts else '<br>Note: no <code>PSIDTS</code> cookie in what you pasted.'
        cls = 'ok' if good else 'bad'
        self.page(f'<div class="r {cls}"><b>Saved {len(names)} cookies.</b> {html.escape(msg)}{extra}'
                  + ('<br>All done — close the Private window (don\'t sign out) and tell Claude.' if good else '')
                  + '</div>')


HTTPServer(('192.168.10.217', 8841), H).serve_forever()
