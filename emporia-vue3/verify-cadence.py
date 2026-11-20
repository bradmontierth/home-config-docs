"""Run inside the ESPHome container; reads its mounted config, emits no secrets."""
import asyncio, json, logging, statistics, time
import yaml
from aioesphomeapi import APIClient
logging.getLogger('zeroconf').setLevel(logging.ERROR)
async def main():
    config = yaml.safe_load(open('/config/emporia-vue3.yaml'))
    client = APIClient('192.168.10.226', 6053, None,
                       noise_psk=config['api']['encryption']['key'])
    await client.connect(login=True)
    try:
        info = await client.device_info()
        entities, _ = await client.list_entities_services()
        names = {e.key: e.name for e in entities}
        samples = {}
        def state(s):
            if hasattr(s, 'state'):
                samples.setdefault(names.get(s.key, str(s.key)), []).append((time.monotonic(), s.state))
        client.subscribe_states(state)
        print(json.dumps({'build': info.compilation_time, 'started_at': time.time()}), flush=True)
        await asyncio.sleep(130)
        result = {}
        for name, rows in samples.items():
            gaps = [b[0]-a[0] for a, b in zip(rows, rows[1:])]
            result[name] = {'reports': len(rows), 'last': rows[-1][1],
                            'median_interval_s': round(statistics.median(gaps), 3) if gaps else None,
                            'max_interval_s': round(max(gaps), 3) if gaps else None}
        print(json.dumps(result, indent=2), flush=True)
    finally:
        await client.disconnect()
asyncio.run(main())
