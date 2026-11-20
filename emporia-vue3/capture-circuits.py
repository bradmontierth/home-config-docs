"""Bounded commissioning capture, independent of HA recorder. Run in ESPHome container."""
import argparse, asyncio, fcntl, gzip, json, logging, math, time
from pathlib import Path
import yaml
from aioesphomeapi import APIClient
logging.getLogger('zeroconf').setLevel(logging.ERROR)

async def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output', required=True)
    ap.add_argument('--hours',type=float,default=48)
    args=ap.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    lock=(out/'capture.lock').open('w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    cfg=yaml.safe_load(open('/config/emporia-vue3.yaml'))
    started=time.time(); deadline=time.monotonic()+min(max(args.hours,0.01),48)*3600
    latest={}; connected=False; latest_report=0.; rows=0
    metadata={'started_at':started,'known_labels':{'1':'Fridge'},'max_hours':48,'max_disk_bytes':512*1024*1024,
              'notes':'Vue-only capture: IotaWatt CTs removed. Match against historical patterns and deliberate load changes. Signed power; branches L1 reference provisional; mains A=L1/B=L2 provisional. Ages are time since receipt, not acquisition.'}
    (out/'metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    async def vue():
        nonlocal connected,latest_report
        while time.monotonic()<deadline:
            client=APIClient('192.168.10.226',6053,None,noise_psk=cfg['api']['encryption']['key'])
            try:
                await asyncio.wait_for(client.connect(login=True),20)
                entities,_=await client.list_entities_services()
                names={x.key:x.name for x in entities}
                latest.clear();latest_report=time.monotonic();connected=True
                def update(s):
                    nonlocal latest_report
                    name=names.get(s.key,'')
                    if not hasattr(s,'state') or not (name.endswith(' Live') or name in ('L1 Voltage','L2 Voltage','Line Frequency','L2 Phase Angle')):return
                    if not isinstance(s.state,(float,int)) or not math.isfinite(s.state):return
                    now=time.monotonic();latest[name]=(round(s.state,4),now)
                    if name.endswith(' Live'):latest_report=now
                client.subscribe_states(update)
                print('Vue capture connected',flush=True)
                while time.monotonic()<deadline and time.monotonic()-latest_report<10:
                    await asyncio.sleep(1)
            except Exception as e:print('Vue retry:',type(e).__name__,flush=True)
            finally:
                connected=False
                try:await client.disconnect()
                except Exception:pass
            await asyncio.sleep(3)
    tasks=[asyncio.create_task(vue())]
    handle=None;last_hour=None;reason='duration_complete'
    try:
        while time.monotonic()<deadline:
            now=time.time();mono=time.monotonic();hour=time.strftime('%Y%m%dT%H',time.gmtime(now))
            if hour!=last_hour:
                if handle:handle.close()
                handle=gzip.open(out/('samples-'+hour+'.jsonl.gz'),'at',encoding='utf-8');last_hour=hour
            values={k:{'value':v if connected and mono-t<10 else None,'age_s':round(mono-t,3)} for k,(v,t) in latest.items()}
            handle.write(json.dumps({'ts':now,'vue_connected':connected,'vue':values},separators=(',',':'))+'\n');rows+=1
            if rows%10==0:
                handle.flush()
                status={'started_at':started,'updated_at':now,'rows':rows,'vue_connected':connected,'vue_sensor_count':len(latest),'remaining_s':round(max(0,deadline-mono))}
                tmp=out/'status.tmp';tmp.write_text(json.dumps(status)+'\n');tmp.replace(out/'status.json')
                if sum(p.stat().st_size for p in out.iterdir() if p.is_file())>=512*1024*1024:
                    reason='disk_limit';break
            await asyncio.sleep(max(0.01,1-(time.monotonic()-mono)))
    finally:
        if handle:handle.close()
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        (out/'finished.json').write_text(json.dumps({'finished_at':time.time(),'rows':rows,'reason':reason})+'\n')
        print('Capture finished',reason,rows,flush=True)
asyncio.run(main())
