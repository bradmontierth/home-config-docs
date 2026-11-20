"""Verify deployed Vue labels, scaling and cadence through the native API.
Run inside ESPHome container; reads credentials without printing them.
"""
import asyncio,json,logging,math,statistics,time
from pathlib import Path
import yaml
from aioesphomeapi import APIClient
logging.getLogger('zeroconf').setLevel(logging.ERROR)
async def main():
 config=yaml.safe_load(Path('/config/emporia-vue3.yaml').read_text())
 manifest=json.loads(Path('/tmp/vue-labeled-streams.json').read_text())
 client=APIClient('192.168.10.226',6053,None,noise_psk=config['api']['encryption']['key'])
 await client.connect(login=True)
 try:
  info=await client.device_info();entities,_=await client.list_entities_services();names={e.key:e.name for e in entities}
  samples={};latest={};errors={}
  desired={m['power_live']:m for m in manifest}
  desired_current={m['current_live']:m for m in manifest if m['current_live']!=m['raw_current']}
  start=time.monotonic();start_wall=time.time()
  def state(s):
   if not hasattr(s,'state') or not isinstance(s.state,(int,float)) or not math.isfinite(s.state):return
   name=names.get(s.key,'');now=time.monotonic();samples.setdefault(name,[]).append((now,s.state));latest[name]=(now,s.state)
   m=desired.get(name)
   if m and now-start>5:
    raw=latest.get(m['raw_power'])
    if raw and abs(now-raw[0])<0.15 and abs(raw[1])>20:
     expected=max(m['sign']*raw[1],0)*m['power_multiplier']
     errors.setdefault(name,[]).append(abs(s.state-expected))
   m=desired_current.get(name)
   if m and now-start>5:
    raw=latest.get(m['raw_current'])
    if raw and abs(now-raw[0])<0.15:errors.setdefault(name,[]).append(abs(s.state-raw[1]))
  client.subscribe_states(state)
  await asyncio.sleep(130)
  results={};failures=[]
  for m in manifest:
   for key in ['power_live','current_live','power_average','current_average']:
    name=m[key];rows=samples.get(name,[]);gaps=[b[0]-a[0] for a,b in zip(rows,rows[1:])];e=errors.get(name,[])
    results[name]={'reports':len(rows),'last':rows[-1][1] if rows else None,'median_interval_s':round(statistics.median(gaps),3) if gaps else None,'max_interval_s':round(max(gaps),3) if gaps else None,'scaling_comparisons':len(e),'max_scaling_error':max(e) if e else None}
    if len(rows)<(115 if key.endswith('live') else 2):failures.append(name+': insufficient reports')
    if key.endswith('live') and gaps and not 0.9<statistics.median(gaps)<1.1:failures.append(name+': cadence')
    if e and max(e)>(0.02 if key=='current_live' else 2.0):failures.append(name+': scaling mismatch')
  result={'build':info.compilation_time,'started_at':start_wall,'finished_at':time.time(),'sensor_count':len(entities),'streams':results,'failures':failures}
  print(json.dumps(result,indent=2))
  if failures:raise SystemExit(1)
 finally:await client.disconnect()
asyncio.run(main())
