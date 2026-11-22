# UniFi Network API helper (2026-11-22). Reads the automation login from cecret_lake/unifi/automation_login; never prints it.
# Usage: python3 -I -c "exec(open('tools/unifi_api.py').read()); login(); print(net('stat/device')['data'][0]['name'])"
import json, ssl, sys, urllib.request, http.cookiejar
BASE = 'https://192.168.10.1'
u, p = open('/home/pi/cecret_lake/unifi/automation_login').read().split('\n')[:2]
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), urllib.request.HTTPCookieProcessor(cj))
CSRF = {}
def call(method, path, body=None):
    h = {'Content-Type': 'application/json', **CSRF}
    r = op.open(urllib.request.Request(BASE + path, json.dumps(body).encode() if body is not None else None, h, method=method), timeout=15)
    t = r.headers.get('x-csrf-token') or r.headers.get('X-Updated-Csrf-Token')
    if t: CSRF['x-csrf-token'] = t
    return json.loads(r.read() or b'{}')
def login():
    call('POST', '/api/auth/login', {'username': u.strip(), 'password': p, 'rememberMe': False})
def net(path, method='GET', body=None):
    return call(method, '/proxy/network/api/s/default/' + path, body)
