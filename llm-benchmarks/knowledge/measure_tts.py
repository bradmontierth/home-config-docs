#!/usr/bin/env python3
"""Synthesize saved short answers without playback; keep timings separate from LLM runs."""
import argparse, asyncio, io, json, sys, time, wave
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import now_utc, write_json
async def main(args):
    sys.path.insert(0,args.voice_root)
    from orchestrator import ask, clients, config
    for p in sorted(Path(args.results).glob('knowledge-*.json')):
        d=json.loads(p.read_text())
        for row in d['cases']:
            if row.get('error') or (row.get('tts') and row['tts'].get('audio_seconds', 0) < 3600): continue
            if row.get('tts'):
                row.setdefault('tts_diagnostics', []).append({**row['tts'], 'invalid_duration_reason': 'streaming WAV placeholder frame count; excluded'})
            start=time.perf_counter()
            try:
                audio=await clients.synthesize(ask._strip_links(row['spoken']))
                with wave.open(io.BytesIO(audio)) as w:
                    # Streaming WAV uses placeholder frame counts; measure actual PCM bytes.
                    duration=len(w.readframes(w.getnframes()))/(w.getframerate()*w.getnchannels()*w.getsampwidth())
                row['tts']={'elapsed_ms':round((time.perf_counter()-start)*1000,1),'audio_seconds':round(duration,2),'bytes':len(audio),'voice':config.TTS_VOICE,'measured_at':now_utc(),'played':False}
            except Exception as exc: row['tts']={'error':str(exc)}
            print(d['run_label'],row['id'],row['tts'],flush=True)
            write_json(p,d)
if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--voice-root',default='/app'); p.add_argument('--results',required=True); asyncio.run(main(p.parse_args()))
