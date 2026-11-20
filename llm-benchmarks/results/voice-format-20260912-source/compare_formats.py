#!/usr/bin/env python3
"""Isolated format experiment; imports production parser but never dispatches actions."""
import argparse, asyncio, importlib.util, json, sys, time
from pathlib import Path
import httpx
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common import canonical_hash, latency_summary, now_utc, run_metadata, write_json
spec = importlib.util.spec_from_file_location('voice_runner', Path(__file__).with_name('run.py'))
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
COMPACT = '''\nOUTPUT FORMAT OVERRIDE (field meanings and routing rules above still apply):
Return compact JSON with intent first and ONLY fields relevant to this intent that carry useful information. Omit nulls and default values (scope=one, phone_action=ring, missing_content=false, sound_theme=marimba). The application supplies defaults. Preserve nondefault timer sound themes and every meaningful slot. Never emit fields belonging to other intents.
For ask, omit query when the original user text is already the question; emit query only when needed to remove background speech or distinguish the actual question from a clarification. Examples: {"intent":"ask"}, {"intent":"timer_query"}, {"intent":"set_timer","duration_seconds":300}, {"intent":"timer_cancel","scope":"all"}. No prose or code fences.\n'''

def schema(intent):
    props = {k: {'type': ['string', 'null']} for k in intent.validate({})}
    for k in ('duration_seconds','music_volume','cover_position','climate_setpoint'):
        props[k] = {'type': ['integer','null']}
    props['missing_content'] = {'type': 'boolean'}
    props['intent'] = {'type': 'string', 'enum': list(intent.INTENTS)}
    for k, vals in {'scope':['one','all'], 'phone_action':['ring','stop'], 'music_action':list(intent.MUSIC_ACTIONS), 'sound_theme':list(intent.config.SOUND_THEMES), 'weather_when':list(intent.WEATHER_WHEN)}.items():
        props[k] = {'enum': vals + [None]}
    return {'type':'object','properties':props,'required':['intent'],'additionalProperties':False}

async def main(args):
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    sys.path.insert(0,args.voice_root)
    from orchestrator import intent, config
    cases = json.loads(Path(__file__).with_name('cases.json').read_text())
    # Add coverage for the newer intent without changing the frozen v1 fixture.
    if 'climate_set' in intent.INTENTS and not any(c['expected']['intent']=='climate_set' for c in cases):
        cases.append({'id':'climate_missing_target','text':'set the thermostat to seventy two degrees','expected':{'intent':'climate_set'},'expected_route':'llm'})
    for id_, question in [('ask_lime','How much juice is in a lime?'),('ask_apple','How should I serve an apple to a one-year-old, according to Solid Starts?'),('ask_light','When red and blue LEDs make purple, do their wavelengths average into one purple wavelength, or are my eyes seeing two separate wavelengths as purple?')]:
        cases.append({'id':id_,'text':question,'expected':{'intent':'ask'},'expected_route':'llm'})
    variants = args.variants.split(',')
    results = {}
    for variant in variants:
        results[variant] = run_metadata(benchmark='voice_intent',version='format-1.0',endpoint=args.endpoint,model=config.LLM_MODEL,cases=cases,prompt=intent._SYSTEM+intent._FOLLOWUP_NOTE+intent._CLARIFY_NOTE+(COMPACT if variant!='full' else ''))
        results[variant].update(run_label=f'Qwen 3.6 / {variant} JSON', cases=[], probes=[], format=variant, schema=schema(intent) if variant=='schema' else None, production_source=str(Path(intent.__file__)), measured_stages='classification only; no action dispatch or audio', source_sha256=canonical_hash(Path(intent.__file__).read_text()))
    state={}
    async def raw(messages):
        messages=[dict(m) for m in messages]
        if state['variant'] != 'full': messages[0]['content'] += COMPACT
        body={'model':config.LLM_MODEL,'messages':messages,'temperature':0,'max_tokens':config.LLM_MAX_TOKENS,'chat_template_kwargs':{'enable_thinking':False}}
        if state['variant']=='schema': body['response_format']={'type':'json_schema','json_schema':{'name':'intent','strict':True,'schema':schema(intent)}}
        state['request']=body
        start=time.perf_counter()
        async with httpx.AsyncClient(timeout=60) as client:
            r=await client.post(args.endpoint.rstrip('/')+'/chat/completions',json=body)
        state['http_ms']=(time.perf_counter()-start)*1000
        if r.status_code!=200: raise RuntimeError(f'HTTP {r.status_code}: {r.text[:1000]}')
        d=r.json(); state['usage']=d.get('usage'); state['response']=d
        return intent.clients._THINK_RE.sub('', d['choices'][0]['message']['content']).strip()
    intent.clients.parse_intent_raw=raw
    async def once(case, variant):
        state.clear(); state['variant']=variant
        parsed, text, elapsed, error=await runner.model_parse(intent,case)
        fast=runner.deterministic(intent,case); prod=fast if fast is not None else parsed
        me,mf=runner.subset_match(parsed,case['expected']); pe,pf=runner.subset_match(prod,case['expected'])
        strict=False
        try: strict=isinstance(json.loads(text),dict)
        except (ValueError,TypeError): pass
        return {**case,'model_prediction':parsed,'production_prediction':prod,'raw_response':text,'model_exact':me,'production_exact':pe,'model_failed_fields':mf,'production_failed_fields':pf,'actual_route':'deterministic' if fast is not None else 'llm','latency_ms':round(elapsed,1),'error':error,'strict_json':strict,'usage':state.get('usage'),'response':state.get('response'),'request_sha256':canonical_hash(state.get('request'))}
    # First requests recorded separately; do not evict a production cache to manufacture a cold start.
    for v in variants:
        row=await once(cases[0],v); results[v]['probes'].append(row)
        print('PROBE',v,row['latency_ms'],row['error'],flush=True)
        if row['error']: variants=[x for x in variants if x!=v]
    for index,case in enumerate(cases):
        order=variants[index%len(variants):]+variants[:index%len(variants)] if variants else []
        for v in order:
            row=await once(case,v); results[v]['cases'].append(row)
            print(index+1,v,case['id'],row['latency_ms'],row['model_exact'],row['error'],flush=True)
        for v,d in results.items():
            rows=d['cases']
            if rows:
                d['summary']={'model_only':runner.summarize(rows,'model'),'production_route':runner.summarize(rows,'production'),'latency':latency_summary(r['latency_ms'] for r in rows),'llm_route_latency':latency_summary(r['latency_ms'] for r in rows if r['actual_route']=='llm'),'invalid_or_error':sum(r['error'] is not None or not r['strict_json'] for r in rows),'completion_tokens':latency_summary((r['usage'] or {}).get('completion_tokens',0) for r in rows),'risk_failures_production':[r['id'] for r in rows if r.get('risk') and not r['production_exact']]}
            write_json(Path(args.output_dir)/f'voice-format-{v}.json',d)
    for v,d in results.items():
        d['finished_at']=now_utc(); write_json(Path(args.output_dir)/f'voice-format-{v}.json',d)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--voice-root',default='/app'); p.add_argument('--endpoint',default='http://192.168.10.187:8095/v1'); p.add_argument('--output-dir',required=True); p.add_argument('--variants',default='full,compact,schema'); asyncio.run(main(p.parse_args()))
