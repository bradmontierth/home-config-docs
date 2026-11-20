#!/usr/bin/env python3
"""Read-only paid knowledge benchmark, no playback or production history writes."""
import argparse, asyncio, json, sys, time
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from common import canonical_hash, latency_summary, now_utc, write_json
VARIANTS=[('gpt54-medium','openai/gpt-5.4','medium'),('gpt54-low','openai/gpt-5.4','low'),('flash38-low','google/gemini-3.8-flash','low')]

async def complete(client, body, headers, url, sentinel):
    start=time.perf_counter(); row={'raw_text':'','annotations':[],'usage':None,'first_token_ms':None,'spoken_ready_ms':None,'error':None,'finish_reason':None}
    try:
        async with client.stream('POST',url,json=body,headers=headers) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith('data:'): continue
                payload=line[5:].strip()
                if payload=='[DONE]': break
                event=json.loads(payload)
                if event.get('error'): raise RuntimeError(json.dumps(event['error']))
                row['generation_id']=event.get('id',row.get('generation_id'))
                row['served_model']=event.get('model',row.get('served_model'))
                row['provider']=event.get('provider',row.get('provider'))
                if event.get('usage'): row['usage']=event['usage']
                for choice in event.get('choices',[]):
                    delta=choice.get('delta',{}); text=delta.get('content') or ''
                    if text and row['first_token_ms'] is None: row['first_token_ms']=round((time.perf_counter()-start)*1000,1)
                    row['raw_text']+=text
                    row['annotations'].extend(delta.get('annotations') or choice.get('message',{}).get('annotations') or [])
                    if sentinel in row['raw_text'] and row['spoken_ready_ms'] is None: row['spoken_ready_ms']=round((time.perf_counter()-start)*1000,1)
                    if choice.get('finish_reason'): row['finish_reason']=choice['finish_reason']
    except Exception as exc: row['error']=f'{type(exc).__name__}: {exc}'
    row['total_ms']=round((time.perf_counter()-start)*1000,1)
    row['spoken']=row['raw_text'].split(sentinel)[0].strip()
    row['sentinel_present']=sentinel in row['raw_text']
    return row

async def main(args):
    sys.path.insert(0,args.voice_root)
    from orchestrator import ask, openrouter, config
    cases=json.loads(Path(__file__).with_name('cases.json').read_text())
    out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    stamp = now_utc().replace('-', '').replace(':', '').replace('+0000', 'Z')
    # Freeze the exact production prompt/date across all candidates.
    prompt=ask._system('kitchen'); headers={'Authorization':'Bearer '+openrouter._read_key(),'Content-Type':'application/json','X-Title':'Household isolated knowledge benchmark'}
    results={}; histories={v:{} for v,_,_ in VARIANTS}
    async with httpx.AsyncClient(timeout=120) as client:
        catalog=(await client.get(config.OPENROUTER_BASE_URL+'/models')).json()['data']
        for label,model,effort in VARIANTS:
            model_info=next(x for x in catalog if x['id']==model)
            results[label]={'benchmark':'knowledge_qa','benchmark_version':'1.0.0','run_label':label,'started_at':now_utc(),'requested_model':model,'reasoning_effort':effort,'model_catalog':model_info,'prompt':prompt,'prompt_sha256':canonical_hash(prompt),'case_sha256':canonical_hash(cases),'cases':[],'scoring_status':'agent_review_pending_human_approval','measurement':'text generation only; spoken_ready_ms is complete short answer before TTS; no audio played'}
        for index,case in enumerate(cases):
            variants=VARIANTS[index%3:]+VARIANTS[:index%3]
            for label,model,effort in variants:
                messages=[{'role':'system','content':prompt}]
                if case.get('follows'):
                    prev=histories[label].get(case['follows'])
                    if prev is None or prev['error']:
                        results[label]['cases'].append({**case,'error':'dependency failed','spoken_ready_ms':None,'total_ms':0,'spoken':''}); continue
                    messages += [{'role':'user','content':prev['text']},{'role':'assistant','content':prev['raw_text']}]
                messages.append({'role':'user','content':case['text']})
                body={'model':model,'messages':messages,'temperature':0.3,'max_tokens':config.OPENROUTER_MAX_TOKENS,'stream':True,'stream_options':{'include_usage':True},'reasoning':{'effort':effort},'plugins':[{'id':'web','engine':'native'}],'web_search_options':{'search_context_size':config.OPENROUTER_WEB_CONTEXT}}
                row={**case,**await complete(client,body,headers,config.OPENROUTER_BASE_URL+'/chat/completions',ask.SENTINEL),'request':body}
                histories[label][case['id']]=row; results[label]['cases'].append(row)
                print(label,case['id'],'spoken_ms=',row['spoken_ready_ms'],'total_ms=',row['total_ms'],'cost=',(row.get('usage') or {}).get('cost'),'error=',row['error'],flush=True)
                write_json(out/f'knowledge-{stamp}-{label}.json',results[label])
        # Separate accounting requests from latency measurements. Keep raw server records.
        for label,d in results.items():
            for row in d['cases']:
                if not row.get('generation_id'): continue
                try:
                    r=await client.get(config.OPENROUTER_BASE_URL+'/generation',params={'id':row['generation_id']},headers=headers); r.raise_for_status(); row['accounting']=r.json().get('data')
                except Exception as exc: row['accounting_error']=str(exc)
            d['finished_at']=now_utc(); d['summary']={'spoken_ready':latency_summary(r['spoken_ready_ms'] for r in d['cases'] if r.get('spoken_ready_ms') is not None),'total_latency':latency_summary(r['total_ms'] for r in d['cases']),'errors':sum(bool(r.get('error')) for r in d['cases'])}
            costs = [(r.get('usage') or {}).get('cost') for r in d['cases']]
            d['summary']['total_cost_usd'] = sum(costs) if all(c is not None for c in costs) else None
            write_json(out/f'knowledge-{stamp}-{label}.json',d)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--voice-root',default='/app'); p.add_argument('--output-dir',required=True); asyncio.run(main(p.parse_args()))
