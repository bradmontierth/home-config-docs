# Execute in dashboard_webapp with app.config available. Auth is never printed.
import os,json,urllib.request,asyncio,pathlib,time
import websockets
from app.config import _secret
BASE=os.getenv('HA_BASE_URL','http://127.0.0.1:8123').rstrip('/');TOKEN=_secret('HA_TOKEN')
def api(path,data=None):
 req=urllib.request.Request(BASE+'/api/'+path,data=None if data is None else json.dumps(data).encode(),headers={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'})
 return json.load(urllib.request.urlopen(req,timeout=40))
async def ws_call(kind,**kwargs):
 async with websockets.connect(BASE.replace('http','ws',1)+'/api/websocket',max_size=16*1024*1024) as ws:
  await ws.recv();await ws.send(json.dumps({'type':'auth','access_token':TOKEN}));assert json.loads(await ws.recv())['type']=='auth_ok'
  await ws.send(json.dumps({'id':1,'type':kind,**kwargs}));r=json.loads(await ws.recv());assert r.get('success'),r;return r['result']
def ws(kind,**kwargs):return asyncio.run(ws_call(kind,**kwargs))
manifest=json.load(open('/tmp/vue-energy-manifest.json'));entries=json.load(open('/tmp/vue-energy-entries.json'))
old={1:('fridge','sensor.daily_fridge_energy'),2:('dryer','sensor.daily_dryer_energy'),3:('ac_total','sensor.daily_ac_energy'),4:('range','sensor.daily_range_energy'),7:('furnace','sensor.daily_furnace_energy'),8:('water_heater','sensor.daily_water_heater_energy'),10:('kitchen','sensor.daily_kitchen_energy'),11:('ev_charging','sensor.daily_tesla_energy'),12:('claire_mini_split','sensor.claire_mini_daily_energy'),13:('office','sensor.office_daily_energy_2')}
registry=ws('config/entity_registry/list');byentry={e['config_entry_id']:e['entity_id'] for e in registry if e.get('config_entry_id')}
states={s['entity_id']:s for s in api('states')}
result={'before_states':{},'circuits':[],'changes':[]}
def create(domain,data):
 f=api('config/config_entries/flow',{'handler':domain,'show_advanced_options':False});assert f['type']=='form',f
 r=api('config/config_entries/flow/'+f['flow_id'],data);assert r['type']=='create_entry',r
 return r['result']['entry_id'] if isinstance(r.get('result'),dict) else r['result']
for m in manifest:
 if not m['input'].isdigit():continue
 port=int(m['input']);slug=m['label'].lower().replace(' ','_');source='sensor.emporia_vue3_'+m['power_average'].lower().replace(' ','_');assert source in states
 live='sensor.emporia_vue3_'+m['power_live'].lower().replace(' ','_')
 if port in old:
  legacy,daily=old[port];entry=next(e for e in entries if e['domain']=='integration' and e['options'].get('source')=='sensor.iotawatt_'+legacy+'_power');energy=byentry[entry['entry_id']]
  result['before_states'][energy]=states[energy];result['before_states'][daily]=states[daily]
  f=api('config/config_entries/options/flow',{'handler':entry['entry_id']});data={'source':source,'method':entry['options'].get('method','trapezoidal')}
  for k in ['round','max_sub_interval']:
   if k in entry['options']:data[k]=entry['options'][k]
  r=api('config/config_entries/options/flow/'+f['flow_id'],data);assert r['type']=='create_entry',r
  result['changes'].append({'entry_id':entry['entry_id'],'old_options':entry['options'],'new_source':source})
 else:
  name='Vue '+m['label']+' Energy';existing=next((e for e in entries if e['domain']=='integration' and e.get('title')==name),None)
  entry_id=existing['entry_id'] if existing else create('integration',{'name':name,'source':source,'unit_prefix':'k','unit_time':'h','method':'trapezoidal','round':6})
  registry=ws('config/entity_registry/list');energy=next(e['entity_id'] for e in registry if e.get('config_entry_id')==entry_id)
  daily_name='Vue '+m['label']+' Daily Energy';existing_daily=next((e for e in entries if e['domain']=='utility_meter' and e.get('title')==daily_name),None)
  daily_id=existing_daily['entry_id'] if existing_daily else create('utility_meter',{'name':daily_name,'source':energy,'cycle':'daily','offset':0,'tariffs':[],'net_consumption':False,'delta_values':False,'periodically_resetting':False,'always_available':False})
  registry=ws('config/entity_registry/list');daily=next(e['entity_id'] for e in registry if e.get('config_entry_id')==daily_id)
 result['circuits'].append({'port':port,'label':m['label'],'live':live,'average':source,'energy':energy,'daily':daily})
 pathlib.Path('/tmp/vue-energy-ha-result.json').write_text(json.dumps(result,indent=2))
 print('Migrated',m['label'],energy,daily,flush=True)
result['after_states']={s['entity_id']:s for s in api('states') if s['entity_id'] in result['before_states']}
pathlib.Path('/tmp/vue-energy-ha-result.json').write_text(json.dumps(result,indent=2))
