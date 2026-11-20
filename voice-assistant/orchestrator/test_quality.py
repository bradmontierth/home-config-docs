"""Quality observation cannot select/dispatch, mis-pair turns or mix scores."""
import asyncio
import io
import json
import math
import tempfile
import time
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from . import app as app_mod, clients, config, quality, turns


def wav():
    out = io.BytesIO()
    samples = array('h', [0]*40000 + [100]*8000 + [1000]*8000)
    with wave.open(out, 'wb') as w:
        w.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        w.writeframes(samples.tobytes())
    return out.getvalue()


def payload(score=-.2, text='Okay computer what time is it'):
    return {'model':'cohere', 'transcript_text':text,
            'summary':{'confidence':score, 'confidence_valid':True,
                       'confidence_method':'mean_token_logprob_v1', 'decoder':'greedy',
                       'bias_weight':0, 'bias_signature':'same'}}


class QualityTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = turns._db
        turns._db = None
        self.patches = [patch.object(config,'DB_PATH',str(Path(self.tmp.name)/'test.db')),
                        patch.object(config,'MIC_QUALITY_OBSERVE',True),
                        patch.object(config,'MIC_QUALITY_SELECT',False),
                        patch.object(config,'FINAL_ASR_URL','http://test/cohere')]
        for p in self.patches: p.start()
        self.primary = turns.start('kitchen','wake',verified=True)
        self.shadow = turns.start('familyroom','wake',verified=False,
                                  reject_reason='suppressed',arb_turn_id=self.primary)

    async def asyncTearDown(self):
        if quality._tasks: await asyncio.gather(*quality._tasks)
        for key,pair in list(quality._live.items()): quality._expire(key,pair)
        if turns._db: turns._db.close()
        turns._db = self.old_db
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def test_pairing_rejects_wrong_room_id_and_old_turn(self):
        self.assertTrue(quality.valid_pair(self.primary,'familyroom','shadow',self.shadow))
        self.assertFalse(quality.valid_pair(self.primary,'kitchen','shadow',self.shadow))
        other = turns.start('kitchen','wake',verified=True)
        self.assertFalse(quality.valid_pair(other,'familyroom','shadow',self.shadow))
        self.assertFalse(quality.valid_pair('../bad','familyroom','shadow',self.shadow))
        turns._conn().execute('UPDATE turns SET at=? WHERE turn_id=?',(time.time()-200,self.primary))
        turns._conn().commit()
        self.assertFalse(quality.valid_pair(self.primary,'familyroom','shadow',self.shadow))

    async def test_reverse_arrival_persists_pair_audio_and_labels(self):
        for role,sat,loser,score in [('shadow','familyroom',self.shadow,-.1),('primary','kitchen',None,-.4)]:
            obs=quality.begin(self.primary,sat,role,wav(),loser)
            quality.complete(obs,payload(score),80)
            quality.complete(obs,error='must_not_overwrite')
        await asyncio.gather(*quality._tasks)
        row=quality.recent()[0]
        self.assertEqual(row['proposed_sat'],'familyroom')
        self.assertEqual(len(row['candidates']),2)
        self.assertTrue(all(c['error'] is None for c in row['candidates']))
        self.assertAlmostEqual(row['score_gap_shadow_minus_primary'],.3)
        self.assertEqual(row['agreement_ratio'],100)
        self.assertEqual(quality.acoustic(wav())['level_contrast_db'],20)
        await quality.label(self.primary,quality.Label(expected_sat='kitchen',notes='fan on'))
        self.assertEqual(quality.recent()[0]['label']['notes'],'fan on')

    def test_invalid_or_mixed_scores_never_rank(self):
        a={'role':'primary','sat':'kitchen','completed_at':1,'asr':payload()}
        b={'role':'shadow','sat':'familyroom','completed_at':2,'asr':payload(-.1)}
        for score in (None,float('nan'),float('inf'),True):
            b['asr']['summary']['confidence']=score
            self.assertIsNone(quality.comparison([a,b])['proposed_sat'])
        b['asr']=payload(-.1); b['asr']['model']='parakeet'
        self.assertEqual(quality.comparison([a,b])['reason'],'incomparable_decoders')
        b['asr']=payload(-.1); b['asr']['summary']['decoder']='beam4'
        self.assertIsNone(quality.comparison([a,b])['proposed_sat'])

    async def test_shadow_calls_cohere_once_and_never_dispatches(self):
        async def decode(url, body, client):
            self.assertEqual(url,config.FINAL_ASR_URL)
            obs=quality.context.get();self.assertEqual(obs['turn_id'],self.primary)
            quality.complete(obs,payload(),90)
            return 'test text','cohere',90
        transport=httpx.ASGITransport(app=app_mod.app)
        with patch.object(clients,'_asr_request',new=decode), patch.object(app_mod,'_dispatch',new_callable=AsyncMock) as dispatch:
            async with httpx.AsyncClient(transport=transport,base_url='http://local') as c:
                r=await c.post('/command/shadow',params={'sat':'familyroom','arb_turn_id':self.primary,'turn_id':self.shadow},content=wav())
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()['transcript'],'test text')
            dispatch.assert_not_called()
        self.assertIsNone(quality.context.get())

    async def test_timeout_does_not_dispatch_or_redecode(self):
        with patch.object(clients,'_asr_request',new_callable=AsyncMock,side_effect=TimeoutError), patch.object(clients,'transcribe',new_callable=AsyncMock) as fallback:
            transport=httpx.ASGITransport(app=app_mod.app)
            async with httpx.AsyncClient(transport=transport,base_url='http://local') as c:
                r=await c.post('/command/shadow',params={'sat':'familyroom','arb_turn_id':self.primary,'turn_id':self.shadow},content=wav())
            self.assertEqual(r.status_code,200)
            fallback.assert_not_called()
        await asyncio.gather(*quality._tasks)
        self.assertEqual(quality.recent()[0]['candidates'][0]['error'],'TimeoutError')

    async def test_off_and_legacy_shadow_keep_old_path(self):
        with patch.object(config,'MIC_QUALITY_OBSERVE',False), patch.object(clients,'transcribe',new_callable=AsyncMock,return_value='legacy') as transcribe:
            transport=httpx.ASGITransport(app=app_mod.app)
            async with httpx.AsyncClient(transport=transport,base_url='http://local') as c:
                r=await c.post('/command/shadow?sat=familyroom',content=wav())
            self.assertEqual(r.json()['transcript'],'legacy')
            transcribe.assert_awaited_once()

    async def test_client_requests_scores_only_for_observed_cohere(self):
        seen=[]
        def respond(request):
            seen.append(dict(request.url.params))
            return httpx.Response(200,json=payload())
        real_client=httpx.AsyncClient
        obs=quality.begin(self.primary,'kitchen','primary',wav())
        token=quality.context.set(obs)
        try:
            with patch.object(clients.httpx,'AsyncClient',side_effect=lambda **kw: real_client(transport=httpx.MockTransport(respond),**kw)):
                await clients._asr_request(config.ASR_URL,wav(),None)
                self.assertFalse(obs['recorded'])
                await clients._asr_request(config.FINAL_ASR_URL,wav(),None)
        finally: quality.context.reset(token)
        self.assertNotIn('confidence',seen[0])
        self.assertEqual(seen[1]['confidence'],'true')
        self.assertTrue(obs['recorded'])

    async def test_primary_dispatch_stays_primary_even_when_shadow_scores_higher(self):
        shadow=quality.begin(self.primary,'familyroom','shadow',wav(),self.shadow)
        quality.complete(shadow,payload(-.01),70)
        async def final(body):
            quality.complete(quality.context.get(),payload(-.5,'original command'),90)
            return clients.AsrResult('original command','cohere',90)
        with patch.object(clients,'transcribe_final',new=final), \
             patch.object(app_mod.policy_mod,'evaluate',new_callable=AsyncMock,return_value={'allowed':True}), \
             patch.object(app_mod,'_turn_event',new_callable=AsyncMock), \
             patch.object(app_mod,'_dispatch',new_callable=AsyncMock,return_value={'ok':True,'intent':'none','response':''}) as dispatch, \
             patch.object(app_mod.speaker_mod,'shadow',new_callable=AsyncMock), \
             patch.object(config,'SPEAKER_MODE','shadow'):
            transport=httpx.ASGITransport(app=app_mod.app)
            async with httpx.AsyncClient(transport=transport,base_url='http://local') as c:
                r=await c.post('/command/audio',params={'sat':'kitchen','turn_id':self.primary,'stitched':'true'},content=wav())
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()['transcript'],'original command')
            self.assertEqual(dispatch.await_args.args[0],'original command')
            dispatch.assert_awaited_once()
        await asyncio.gather(*quality._tasks)
        self.assertEqual(quality.recent()[0]['proposed_sat'],'familyroom')


class PickerTest(QualityTest):
    def ready_primary(self):
        config.MIC_QUALITY_SELECT=True
        obs=quality.begin(self.primary,'kitchen','primary',wav())
        quality.complete(obs,payload(-.120875,'Okay computer what gains it'),100)
        return obs

    def ready_shadow(self, text='Okay computer what day is it', score=-.097058):
        obs=quality.begin(self.primary,'familyroom','shadow',wav(),self.shadow)
        quality.complete(obs,payload(score,text),90)
        return obs

    async def pick(self, obs, **kwargs):
        return await quality.select_candidate(obs,text='Okay computer what gains it',
            model='cohere',fallback_reason=kwargs.get('fallback'),sanity_check=clients._sanity_reason)

    async def test_todays_correction_and_durable_decision(self):
        obs=self.ready_primary(); self.ready_shadow()
        picked=await self.pick(obs)
        self.assertEqual(picked['sat'],'familyroom')
        self.assertEqual(picked['wav'],wav())
        await asyncio.gather(*quality._tasks)
        d=quality.recent()[0]['selection']
        self.assertEqual(d['reason'],'better_shadow')
        self.assertEqual(d['wait_ms'],0)
        self.assertTrue(quality._live[self.primary].sealed)

    async def test_shadow_arriving_during_wait(self):
        obs=self.ready_primary()
        async def later():
            await asyncio.sleep(.02);self.ready_shadow()
        task=asyncio.create_task(later())
        self.assertIsNotNone(await self.pick(obs));await task

    async def test_late_shadow_cannot_change_decision(self):
        obs=self.ready_primary()
        with patch.object(config,'MIC_QUALITY_WAIT_MS',10):
            self.assertIsNone(await self.pick(obs))
        self.ready_shadow()
        self.assertEqual(quality._live[self.primary].candidates,{})
        await asyncio.gather(*quality._tasks)
        self.assertEqual(quality.recent()[0]['selection']['reason'],'shadow_timeout')

    async def test_no_peer_does_not_wait(self):
        obs=self.ready_primary()
        turns._conn().execute('DELETE FROM turns WHERE turn_id=?',(self.shadow,));turns._conn().commit()
        self.assertIsNone(await self.pick(obs))
        await asyncio.gather(*quality._tasks)
        self.assertEqual(quality.recent()[0]['selection']['wait_ms'],0)

    async def test_fallback_cannot_be_overridden(self):
        obs=self.ready_primary();self.ready_shadow()
        self.assertIsNone(await self.pick(obs,fallback='timeout'))

    async def test_agreement_and_small_margin_keep_original(self):
        for text,score,reason in [('Okay computer what gains it',-.01,'transcripts_agree'),
                                   ('Okay computer what day is it',-.115,'original_score_or_small_gap')]:
            a={'role':'primary','sat':'kitchen','completed_at':1,'asr':payload(-.12,'Okay computer what gains it')}
            b={'role':'shadow','sat':'familyroom','completed_at':1,'asr':payload(score,text)}
            r,p,_=quality.evaluate(a,b,margin=.01,sanity_check=clients._sanity_reason)
            self.assertEqual(r,reason);self.assertIsNone(p)

    async def test_selected_text_and_audio_dispatch_once_with_original_owner(self):
        config.MIC_QUALITY_SELECT=True
        self.ready_shadow()
        async def final(body):
            quality.complete(quality.context.get(),payload(-.120875,'Okay computer what gains it'),100)
            return clients.AsrResult('Okay computer what gains it','cohere',100)
        with patch.object(clients,'transcribe_final',new=final), \
             patch.object(app_mod.policy_mod,'evaluate',new_callable=AsyncMock,return_value={'allowed':True}), \
             patch.object(app_mod,'_turn_event',new_callable=AsyncMock), \
             patch.object(app_mod,'_dispatch',new_callable=AsyncMock,return_value={'ok':True,'intent':'none','response':''}) as dispatch, \
             patch.object(app_mod.speaker_mod,'shadow',new_callable=AsyncMock) as speaker, \
             patch.object(config,'SPEAKER_MODE','shadow'):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_mod.app),base_url='http://local') as c:
                r=await c.post('/command/audio',params={'sat':'kitchen','turn_id':self.primary,'stitched':'true'},content=wav()+b'primary')
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()['transcript'],'what day is it')
            dispatch.assert_awaited_once()
            await asyncio.sleep(0)
            self.assertEqual(speaker.call_args.args[0],wav())
            self.assertEqual(speaker.call_args.kwargs['sat'],'kitchen')
