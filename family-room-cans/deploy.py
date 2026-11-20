"""Apply already-reviewed per-tab updates; refuse concurrent changes."""
import datetime
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent
BACKUP = Path((ROOT / 'backup-path.txt').read_text().strip())
BASE = 'http://127.0.0.1:1880'
ORDER = ['be47927df555c65e', '294429bac2b766ff', '55523c67b181dd7b',
         'fb8d8707af18844f', '194f33df5b26685e', 'f21c376895b9f802']
def get(ident):
    return json.load(urllib.request.urlopen(BASE + '/flow/' + ident, timeout=20))
def canonical(tab):
    t = dict(tab)
    nodes = []
    for original in t['nodes']:
        n = dict(original)
        # Admin API omits an empty wires array on link-out nodes.
        if n['type'] == 'link out' and not n.get('wires'):
            n.pop('wires', None)
        nodes.append(n)
    t['nodes'] = sorted(nodes, key=lambda n: n['id'])
    return t
for ident in ORDER:
    original = json.loads((BACKUP / (ident + '.json')).read_text())
    expected = json.loads((ROOT / 'prepared' / (ident + '.json')).read_text())
    assert canonical(get(ident)) in [canonical(original), canonical(expected)], 'Concurrent change: ' + ident
started = datetime.datetime.now(datetime.timezone.utc).isoformat()
if not (ROOT / 'deployed-at.txt').exists():
    (ROOT / 'deployed-at.txt').write_text(started + '\n')
for ident in ORDER:
    original = json.loads((BACKUP / (ident + '.json')).read_text())
    expected = json.loads((ROOT / 'prepared' / (ident + '.json')).read_text())
    current = canonical(get(ident))
    if current == canonical(expected):
        print('Already deployed and verified:', expected['label'], ident, flush=True)
        continue
    assert current == canonical(original), 'Concurrent change: ' + ident
    request = urllib.request.Request(BASE + '/flow/' + ident,
        data=json.dumps(expected).encode(), method='PUT',
        headers={'Content-Type': 'application/json'})
    response = json.load(urllib.request.urlopen(request, timeout=60))
    assert response['id'] == ident, response
    actual = get(ident)
    (ROOT / 'prepared' / (ident + '.verified.json')).write_text(json.dumps(actual, indent=2) + '\n')
    assert canonical(actual) == canonical(expected), 'Verification mismatch: ' + ident
    print('Verified deployed:', expected['label'], ident, flush=True)
print('All six scoped tab updates verified; started', started)
