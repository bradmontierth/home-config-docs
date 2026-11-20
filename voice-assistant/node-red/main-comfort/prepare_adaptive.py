"""Build adaptive update from an Admin API snapshot; does not deploy."""
import copy
import json
from pathlib import Path
import sys
from prepare import replace_once

HERE = Path(__file__).resolve().parent


def prepare(tab):
    tab = copy.deepcopy(tab)
    nodes = {n['id']: n for n in tab['nodes']}
    code = nodes['11ea46a27b5ca06a']['func']
    code = replace_once(code, 'const comfortValid = comfortHold && [-1, 1].includes(comfortHold.delta) &&', '''const adaptive = comfortHold?.version === 2;
const adaptiveTarget = comfortHold?.hvac === "cool" ? comfortHold?.startAt : comfortHold?.heatTarget;
const adaptiveBase = comfortHold?.hvac === "cool" ? 79 : heatHomeSP;
const adaptiveShift = (adaptiveTarget - adaptiveBase) * (comfortHold?.kind === "hot" ? -1 : 1);
const adaptiveValid = adaptive && ["hot", "cold"].includes(comfortHold.kind) &&
  ["heat", "cool"].includes(comfortHold.hvac) && comfortHold.baseline === adaptiveBase &&
  Number.isFinite(adaptiveTarget) && adaptiveTarget >= 45 && adaptiveTarget <= 92 &&
  adaptiveShift >= 1 - 0.00001 && adaptiveShift <= 4 + 0.00001 &&
  (comfortHold.hvac !== "cool" || (Number.isFinite(comfortHold.stopAt) && comfortHold.stopAt >= 45 &&
    Math.abs(comfortHold.startAt - comfortHold.stopAt - 1) < 0.00001));
const comfortValid = comfortHold && (adaptive ? adaptiveValid : [-1, 1].includes(comfortHold.delta)) &&''')
    code = replace_once(code, 'const comfortDelta = comfortValid && comfortEligible ? comfortHold.delta : 0;', '''const comfortApplied = comfortValid && comfortEligible &&
  (!adaptive || comfortHold.hvac === currentEcobeeState?.hvacHeatCool);
const comfortDelta = comfortApplied ? (adaptive ? adaptiveTarget - adaptiveBase : comfortHold.delta) : 0;
const comfortStart = comfortApplied && adaptive ? comfortHold.startAt : 79 + comfortDelta;
const comfortStop = comfortApplied && adaptive ? comfortHold.stopAt : 77 + comfortDelta;''')
    code = replace_once(code, '      79 + comfortDelta,\n      77 + comfortDelta', '      comfortStart,\n      comfortStop')
    code = replace_once(code, 'requestedDelta: comfortValid ? comfortHold.delta : 0,', 'requestedDelta: comfortValid ? (adaptive ? adaptiveTarget - adaptiveBase : comfortHold.delta) : 0,')
    code = replace_once(code, 'weatherStartAt: 79 + comfortDelta,\n    weatherStopAt: 77 + comfortDelta', '''weatherStartAt: currentEcobeeState?.hvacHeatCool === "cool" ? comfortStart : 79,
    weatherStopAt: currentEcobeeState?.hvacHeatCool === "cool" ? comfortStop : 77,
    version: adaptive ? 2 : 1,
    heatTarget: comfortApplied && adaptive ? comfortHold.heatTarget ?? null : null''')
    nodes['11ea46a27b5ca06a']['func'] = code
    handler = nodes['ec_comfort_hold']
    handler.update(func=(HERE/'policy.js').read_text()+'\n'+(HERE/'adaptive_hold.js').read_text(),
                   outputs=2, wires=[['ec_grid_outage_current'], ['ec_comfort_http_response']])
    for id in ('ec_comfort_hot', 'ec_comfort_cold'):
        nodes[id]['wires'] = [['ec_comfort_read_grid']]

    def add(id, type, name, wires, **fields):
        assert id not in nodes
        tab['nodes'].append(dict(id=id,type=type,z=tab['id'],name=name,x=700,y=1760+len(tab['nodes'])*2,wires=wires,**fields))

    # Cached HA reads through existing server config; no network credentials added.
    for id, entity, prop, target in [
        ('ec_comfort_read_grid','binary_sensor.powerwall_grid_outage','gridOutage','ec_comfort_read_climate'),
        ('ec_comfort_read_climate','climate.my_ecobee','comfortClimate','ec_comfort_read_weather'),
        ('ec_comfort_read_weather','sensor.weather_station_indoor_temperature','comfortWeather','ec_comfort_hold')]:
        n=copy.deepcopy(nodes['ec_grid_outage_current'])
        n.update(id=id,name='Comfort read '+entity,entity_id=entity,wires=[[target]],x=500,y=1780+len(tab['nodes'])*2)
        n['outputProperties']=[dict(property=prop,propertyType='msg',value='',valueType='entityState' if prop=='gridOutage' else 'entity')]
        tab['nodes'].append(n)
    add('ec_comfort_preview','http in','Preview comfort request',[['ec_comfort_read_grid']],
        url='/main-comfort/preview',method='get',upload=False,swaggerDoc='')
    add('ec_comfort_http_response','http response','Comfort JSON response',[],statusCode='',headers={})
    add('ec_comfort_apply','mqtt in','Apply approved comfort request',[['ec_comfort_read_grid']],
        topic='voice/main_comfort/apply',qos='1',datatype='utf8',broker='82f540b7378c2e35',nl=False,rap=True,rh=0,inputs=0)
    add('ec_comfort_result','http in','Read comfort request result',[['ec_comfort_result_fn']],
        url='/main-comfort/result/:id',method='get',upload=False,swaggerDoc='')
    add('ec_comfort_result_fn','function','Comfort request acknowledgement',[['ec_comfort_http_response']],
        outputs=1,timeout=0,noerr=0,initialize='',finalize='',libs=[],func='''const found = (flow.get("mainComfortResults") || []).find(r => r.id === msg.req.params.id && Date.now() - r.at < 120000);
msg.payload = found ? found.result : {pending: true};
msg.headers = {"Content-Type": "application/json", "Cache-Control": "no-store"};
return msg;''')
    return tab


if __name__ == '__main__':
    folder=Path(sys.argv[1])
    before=json.loads((folder/'tab.before.json').read_text())
    (folder/'tab.after.json').write_text(json.dumps(prepare(before),indent=2)+'\n')
    print('Prepared adaptive Ecobee tab; no deploy.')
