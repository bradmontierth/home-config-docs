import asyncio,json,time,urllib.request,logging,yaml
from aioesphomeapi import APIClient
logging.getLogger('zeroconf').setLevel(logging.ERROR)
async def main():
    config=yaml.safe_load(open('/config/emporia-vue3.yaml'))
    client=APIClient('192.168.10.226',6053,None,noise_psk=config['api']['encryption']['key'])
    await client.connect(login=True)
    info=await client.device_info()
    print('DEVICE',info.name,info.esphome_version,info.compilation_time,flush=True)
    entities,_=await client.list_entities_services()
    names={x.key:x.name for x in entities}
    latest={}
    def state(s):
        if hasattr(s,'state'): latest[names.get(s.key,str(s.key))]=s.state
    client.subscribe_states(state)
    try:
        for _ in range(6):
            await asyncio.sleep(10)
            d=await asyncio.to_thread(lambda:json.load(urllib.request.urlopen('http://192.168.40.211/status?inputs=yes',timeout=5)))
            fridge=next(x for x in d['inputs'] if x['channel']==11)
            print(json.dumps({'time':time.time(),'vue':latest,'iotawatt_fridge':fridge}),flush=True)
    finally: await client.disconnect()
asyncio.run(main())
