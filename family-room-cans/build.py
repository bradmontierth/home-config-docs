"""Build scoped Admin API tab updates from the saved live tab snapshots."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKUP = Path((ROOT / 'backup-path.txt').read_text().strip())
OUT = ROOT / 'prepared'
OUT.mkdir(exist_ok=True)
CIRCADIAN = 'be47927df555c65e'
MOTION = 'fb8d8707af18844f'
UPDATE = '55523c67b181dd7b'
BUTTON1 = '194f33df5b26685e'
BUTTON2 = 'f21c376895b9f802'
VOICE = '294429bac2b766ff'
TABS = [CIRCADIAN, MOTION, UPDATE, BUTTON1, BUTTON2, VOICE]
tabs = {i: json.loads((BACKUP / (i + '.json')).read_text()) for i in TABS}
all_nodes = {n['id']: n for t in tabs.values() for n in t['nodes']}
def nid(num):
    return f'fc0910{num:010x}'
def add(tab, node):
    node['z'] = tab
    tabs[tab]['nodes'].append(node)
    return node
def function(tab, num, name, code, wires, x=600, y=3000):
    return add(tab, dict(id=nid(num), type='function', name=name, func=code,
        outputs=1, timeout=0, noerr=0, initialize='', finalize='', libs=[],
        x=x, y=y, wires=[wires]))
def replace(tab, ident, node):
    old = all_nodes[ident]
    node.update(id=ident, z=tab, x=old['x'], y=old['y'])
    tabs[tab]['nodes'][tabs[tab]['nodes'].index(old)] = node
def route(tab, ident, name, command, outnum):
    replace(tab, ident, dict(type='function', name=name, outputs=1, timeout=0,
        noerr=0, initialize='', finalize='', libs=[],
        func=f'return {{ frCansCommand: "{command}" }};', wires=[[nid(outnum)]]))
def linkout(tab, num, y):
    add(tab, dict(id=nid(num), type='link out', name='Family room cans controller OUT',
        mode='link', links=[nid(1)], x=1300, y=y, wires=[]))

# All policy and timers live in one ordinary tab; no subflow-definition deploy.
outs = [101, 102, 103, 104, 105]
add(CIRCADIAN, dict(id=nid(1), type='link in', name='Family room cans controller IN',
    links=[nid(i) for i in outs], x=100, y=3000, wires=[[nid(2)]]))
function(CIRCADIAN, 2, 'Cans: clean request',
    'return {frCansCommand: msg.frCansCommand || "tick"};', [nid(3)], 280, 3000)

# Use the same Hubitat cache as existing vacancy logic. Its HA mirror is stale.
hub = copy.deepcopy(all_nodes['6543138d87592f24'])
hub.update(id=nid(3), name='Cans: kitchen motion cache', x=530, y=3000, wires=[[nid(4)]])
add(CIRCADIAN, hub)
function(CIRCADIAN, 4, 'Cans: save occupancy',
    'msg.frKitchenMotion = msg.payload?.value; return msg;', [nid(5)], 790, 3000)

template = all_nodes['cafe000000000021']
reads = [
    (5, 'Cans: current group', 'light.family_room_can_group', 'frCansEntity', 'entity'),
    (6, 'Cans: porch motion', 'binary_sensor.porch_lights_motion_switch_js_motion_detection', 'frPorchMotion', 'entityState'),
    (7, 'Cans: automation disabled', 'input_boolean.disable_main_floor_lights', 'frDisabled', 'entityState'),
    (8, 'Cans: chaos mode', 'input_boolean.chaos_mode', 'frChaos', 'entityState'),
    (9, 'Cans: current house mode', 'input_select.mode', 'frMode', 'entityState'),
]
for num, name, entity, prop, value_type in reads:
    n = copy.deepcopy(template)
    n.update(id=nid(num), name=name, entity_id=entity, state_type='str',
        blockInputOverrides=True, outputProperties=[dict(property=prop, propertyType='msg', value='', valueType=value_type)],
        x=300 + (num - 5) * 250, y=3100, wires=[[nid(num + 1)]])
    add(CIRCADIAN, n)
function(CIRCADIAN, 10, 'Family room cans policy: 1–50%, daylight, Pico',
    (ROOT / 'controller.js').read_text(), [nid(11)], 600, 3240)
action = copy.deepcopy(all_nodes['cafe000000000016'])
action.update(id=nid(11), name='Family room cans: apply', x=1060, y=3240, wires=[[nid(14)]])
add(CIRCADIAN, action)
function(CIRCADIAN, 14, 'Cans: last successful service call',
    'global.set("FamilyRoomCansLastApplied", {at: Date.now(), reason: msg.frCansReason, payload: msg.payload}); return null;',
    [], 1380, 3240)
add(CIRCADIAN, dict(id=nid(12), type='inject', name='Cans: check daylight / curve / override each minute',
    props=[dict(p='frCansCommand', v='tick', vt='str')], repeat='60', crontab='',
    once=True, onceDelay='35', topic='', x=380, y=3340, wires=[[nid(2)]]))
observe = copy.deepcopy(next(n for n in tabs[MOTION]['nodes'] if n['id'] == '9c159a2769172f46'))
observe.update(id=nid(13), name='Cans: observe ON/OFF and mode changes',
    entities=dict(entity=['light.family_room_can_group', 'input_select.mode'], substring=[], regex=[]),
    stateType='str', outputInitially=False,
    outputProperties=[dict(property='frCansCommand', propertyType='msg', value='observe', valueType='str')],
    x=400, y=3420, wires=[[nid(2)]])
add(CIRCADIAN, observe)
add(CIRCADIAN, dict(id=nid(15), type='catch', name='Cans: report controller errors',
    scope=[nid(i) for i in range(2, 12)], uncaught=False, x=350, y=3500, wires=[[nid(16)]]))
function(CIRCADIAN, 16, 'Cans: error details',
    'global.set("FamilyRoomCansLastError", {at: Date.now(), error: msg.error}); node.warn("Family room cans: " + (msg.error?.message || "unknown error")); return null;',
    [], 760, 3500)
add(CIRCADIAN, dict(id=nid(17), type='comment', name='Family room cans only — approved 2026-09-10',
    info='Separate FamilyRoomCansBri; legacy BriCurveCans also feeds other fixtures. Source/test/deploy artifacts: /home/pi/home_config/family-room-cans. Read family-room-cans-guide.md before modifying.',
    x=430, y=2920, wires=[]))

# Replace only the cans' leg of the TV gate, preserving the other fixtures.
gate = all_nodes['64a3c959e0eb5861']
assert gate['wires'][1] == ['0641ca96b3efb391', '387fd7f2a5b1f74b']
gate['wires'][1][0] = '45aff6a87ff48227'
route(MOTION, '45aff6a87ff48227', 'Family room cans: motion policy', 'motion', 101)
linkout(MOTION, 101, 2360)
# The old solar-radiation / 8pm nodes become disconnected reference nodes.

# Forward the scheduled repaint entry, before its old current-state/override gates.
route(UPDATE, 'c6de008f2166733c', 'Family room cans: repaint policy', 'repaint', 102)
linkout(UPDATE, 102, 340)

# Replace three subflow instances with explicit manual requests. Definitions stay intact.
for ident in ['b6363be1676d6bb0', 'c2a4360c67108125']:
    route(BUTTON1, ident, 'Family room cans: staged ON', 'on', 103)
linkout(BUTTON1, 103, 4240)
route(BUTTON2, '272f801b00770ec2', 'Family room cans: staged ON', 'on', 104)
linkout(BUTTON2, 104, 4260)

# Voice uses the exact same stage decision/timestamp as the Picos.
voice = all_nodes['cafe000000000020']
start = voice['func'].index('} else if (cmd === "fr_cans_on"')
end = voice['func'].index('} else {\n    node.warn', start)
voice['func'] = voice['func'][:start] + '''} else if (["fr_cans_on", "fr_cans_brighter", "fr_cans_off"].includes(cmd)) {
    return [null, {frCansCommand: cmd.slice("fr_cans_".length)}];
''' + voice['func'][end:]
voice['wires'][1] = [nid(105)]
linkout(VOICE, 105, 860)
retired = {'cafe000000000021', 'cafe000000000022', 'cafe000000000023', 'cafe000000000024'}
tabs[VOICE]['nodes'] = [n for n in tabs[VOICE]['nodes'] if n['id'] not in retired]

summary = []
for ident, tab in tabs.items():
    before = json.loads((BACKUP / (ident + '.json')).read_text())
    old = {n['id']: n for n in before['nodes']}
    new = {n['id']: n for n in tab['nodes']}
    # Catch dangling wires and wrong two-sided links among these tabs.
    summary.append(dict(id=ident, label=tab['label'], added=sorted(new.keys() - old.keys()),
        removed=sorted(old.keys() - new.keys()), changed=[i for i in old.keys() & new.keys() if old[i] != new[i]]))
    (OUT / (ident + '.json')).write_text(json.dumps(tab, indent=2) + '\n')
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, indent=2))
