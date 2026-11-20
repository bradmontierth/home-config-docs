"""No network/device/audio calls: adaptive dispatch and conversation isolation."""
import copy
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from . import app, home_control, main_comfort


def proposal():
    return dict(version=2,kind="hot",hvac="cool",baseline=79,startAt=76.8,stopAt=75.8,
                createdAt=time.time()*1000,requestId="test_request_123")


class ConversationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved=copy.deepcopy(app._SESSIONS)
        app._SESSIONS.clear()
        self.token=app._CUR_SAT.set("kitchen")
        self.patchers=[patch.object(app,"_finalize",new=AsyncMock(side_effect=lambda r,i:r)),
                       patch.object(app,"_turn_event",new=AsyncMock()),
                       patch.object(main_comfort,"apply",new=AsyncMock(return_value={"ok":True,"response":"Applied."})),
                       patch.object(app.intent_mod,"parse",new=AsyncMock(return_value={"intent":"none"})),
                       patch.object(app.home_mod,"has_exact_match",return_value=False)]
        for p in self.patchers:p.start()
        self.p=proposal()
        app._session().update(pending={"op":"main_comfort","proposal":self.p},ts=time.time())

    async def asyncTearDown(self):
        for p in reversed(self.patchers):p.stop()
        app._CUR_SAT.reset(self.token)
        app._SESSIONS.clear();app._SESSIONS.update(self.saved)

    async def test_yes_applies_exact_proposal_once(self):
        await app.handle_command("yes please",followup=True)
        main_comfort.apply.assert_awaited_once_with(self.p,confirmed=True)
        self.assertIsNone(app.session_pending())
        await app.handle_command("yes",followup=True)
        self.assertEqual(main_comfort.apply.await_count,1)

    async def test_no_leaves_hold_alone(self):
        r=await app.handle_command("no thanks",followup=True)
        self.assertIn("left the thermostat alone",r["response"])
        main_comfort.apply.assert_not_awaited()

    async def test_silence_closes_pending(self):
        await app.session_idle("kitchen")
        self.assertIsNone(app.session_pending())
        await app.handle_command("yes",followup=True)
        main_comfort.apply.assert_not_awaited()

    async def test_other_room_cannot_confirm_or_clear(self):
        await app.session_idle("claire")
        token=app._CUR_SAT.set("claire")
        try:await app.handle_command("yes",followup=True)
        finally:app._CUR_SAT.reset(token)
        main_comfort.apply.assert_not_awaited()
        self.assertIsNotNone(app.session_pending())

    async def test_fresh_wake_does_not_confirm(self):
        await app.handle_command("yes",followup=False)
        main_comfort.apply.assert_not_awaited()
        self.assertIsNone(app.session_pending())

    async def test_unrelated_reply_abandons(self):
        await app.handle_command("what time is it",followup=True)
        main_comfort.apply.assert_not_awaited()
        self.assertIsNone(app.session_pending())

    async def test_expired_pending_cannot_confirm(self):
        app._session()["ts"]=time.time()-100
        await app.handle_command("yes",followup=True)
        main_comfort.apply.assert_not_awaited()

    async def test_prompt_opens_long_listen_and_saves_room_proposal(self):
        app.session_clear_pending()
        with patch.object(app.home_mod,"has_exact_match",return_value=True), \
             patch.object(app.home_mod,"handle",new=AsyncMock(return_value={"ok":True,"response":"Go ahead?","awaiting_slot":True,"_comfort_proposal":self.p})):
            r=await app.handle_command("its hot in here")
        self.assertTrue(r["awaiting_slot"])
        self.assertNotIn("_comfort_proposal",r)
        self.assertEqual(app.session_pending()["proposal"],self.p)
        main_comfort.apply.assert_not_awaited()


class TransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_automatic_tier_publishes_without_confirmation(self):
        p={**proposal(),"startAt":77.5,"stopAt":76.5}
        calls=[]
        def respond(req):
            calls.append(req)
            if req.url.path.endswith('/preview'):
                return httpx.Response(200,json={"ok":True,"proposal":p,"requires_confirmation":False})
            if req.method=='POST':return httpx.Response(200,json=[])
            return httpx.Response(200,json={"ok":True,"proposal":p,"expiresAt":12345})
        real_client=httpx.AsyncClient
        with patch.object(main_comfort.httpx,"AsyncClient",side_effect=lambda **kw:real_client(transport=httpx.MockTransport(respond))), \
             patch.object(main_comfort,"_token",return_value="test"):
            r=await main_comfort.request('hot')
        self.assertTrue(r['ok'])
        self.assertNotIn('_comfort_proposal',r)
        body=json.loads(json.loads(next(c.content for c in calls if c.method=='POST'))['payload'])
        self.assertFalse(body['confirmed'])

    async def test_preview_confirmation_never_publishes(self):
        calls=[]
        def respond(req):
            calls.append(req)
            return httpx.Response(200,json={"ok":True,"proposal":proposal(),"requires_confirmation":True})
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.object(main_comfort.httpx,"AsyncClient",return_value=client):
            r=await main_comfort.request("hot")
        self.assertTrue(r["awaiting_slot"])
        self.assertEqual([c.method for c in calls],["GET"])

    async def test_apply_uses_ha_and_waits_for_ack(self):
        p=proposal();calls=[]
        def respond(req):
            calls.append(req)
            if req.method=="POST":return httpx.Response(200,json=[])
            return httpx.Response(200,json={"ok":True,"proposal":p,"expiresAt":12345})
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.object(main_comfort.httpx,"AsyncClient",return_value=client),patch.object(main_comfort,"_token",return_value="test"):
            r=await main_comfort.apply(p,True)
        self.assertTrue(r["ok"])
        self.assertTrue(calls[0].url.path.endswith('/api/services/mqtt/publish'))
        body=json.loads(calls[0].content)
        self.assertFalse(body["retain"])
        self.assertEqual(json.loads(body["payload"]),{"proposal":p,"confirmed":True})
        self.assertIn('75.8',r["response"])

    async def test_rejected_commit_is_not_success(self):
        def respond(req):return httpx.Response(200,json=[] if req.method=='POST' else {"ok":False,"response":"The thermostat settings changed."})
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.object(main_comfort.httpx,"AsyncClient",return_value=client),patch.object(main_comfort,"_token",return_value="test"):
            r=await main_comfort.apply(proposal(),True)
        self.assertFalse(r["ok"])

    async def test_main_commands_use_adaptive_path_kids_keep_buttons(self):
        with patch.object(main_comfort,"request",new=AsyncMock(return_value={"ok":True})), \
             patch.object(home_control,"_press",new=AsyncMock()) as press:
            for sat in ('kitchen','familyroom'):
                for kind in ('hot','cold'):
                    await home_control.handle({'query':f'its {kind} in here'},'',sat)
            self.assertEqual(main_comfort.request.await_count,4)
            press.assert_not_awaited()
            await home_control.handle({'query':'its hot in here'},'','claire')
            press.assert_awaited_once_with('button.voice_claire_too_hot')

    def test_affirmations_are_whole_utterances(self):
        for text in ('yes please','go ahead','okay'):
            self.assertEqual(main_comfort.affirmation(text),'yes')
        for text in ("yes but don't do that",'okay computer turn off the lights','sure is hot today'):
            self.assertIsNone(main_comfort.affirmation(text))
