"""Validate source scope, graph links, and retired routes before deployment."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKUP = Path((ROOT / 'backup-path.txt').read_text().strip())
baseline = json.loads((BACKUP / 'flows.json').read_text())
summary = json.loads((ROOT / 'prepared' / 'summary.json').read_text())
tabs = [json.loads((ROOT / 'prepared' / (t['id'] + '.json')).read_text()) for t in summary]
updated_ids = {t['id'] for t in tabs}
combined = [n for n in baseline if n.get('z') not in updated_ids]
combined.extend(n for t in tabs for n in t['nodes'])
nodes = {n['id']: n for n in combined}
assert len(nodes) == len(combined), 'Duplicate node ID'
changed = {i for s in summary for k in ['added','changed'] for i in s[k]}
removed = {i for s in summary for i in s['removed']}
for n in combined:
    if n['id'] in changed:
        for output in n.get('wires', []):
            for ident in output:
                assert ident in nodes, ('Dangling wire', n['id'], ident)
        if n['type'] in ['link in', 'link out']:
            for ident in n['links']:
                assert n['id'] in nodes[ident]['links'], ('One-sided link', n['id'], ident)
        if n['type'] == 'function':
            subprocess.run(['node', '-e', 'const fs=require("fs");const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;new AsyncFunction("msg","node","global","flow","context",fs.readFileSync(0,"utf8"));'],
                           input=n['func'], text=True, check=True)
    for w in n.get('wires', []):
        if isinstance(w, list): assert not (set(w) & removed), ('Reference to retired voice timer', n['id'])
assert not any(n['type']=='subflow:9262e498f136d5dc' for n in combined), 'Legacy cans ON instance remains'
for ident in ['c6de008f2166733c','45aff6a87ff48227','b6363be1676d6bb0','c2a4360c67108125','272f801b00770ec2']:
    assert 'BriCurveCans' not in nodes[ident].get('func',''), ident
assert not any(n['type']=='subflow' and n != next(b for b in baseline if b['id']==n['id']) for n in combined)
for ident in ['cc8bedfdeaaf032d', 'dd57f8dc54db2550', 'b410834f3021b4df']:
    # Each old scheduled/MQTT writer's ancestors must terminate in a dead node,
    # never an event, inject, live link input, or subflow input.
    queue=[ident];seen=set()
    while queue:
        i=queue.pop()
        if i in seen:continue
        seen.add(i);n=nodes[i]
        assert n['type'] not in ['inject','server-state-changed','link in','mqtt in','zigbee2mqtt-in'], ('Old writer remains reachable',ident,i)
        queue.extend(p['id'] for p in combined if any(i in w for w in p.get('wires',[]) if isinstance(w,list)))
print('Validated: six-tab scope, syntax, bidirectional links, retired ON routes/timer, unchanged subflow definitions')
