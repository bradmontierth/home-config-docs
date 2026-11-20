"""Compact wire output must preserve the existing normalized dispatch contract."""
import json
import unittest
from unittest.mock import AsyncMock, patch
import httpx
from . import app, clients, intent
from .intent_schema import RESPONSE_FORMAT, SCHEMA
from .test_clarify import _SlotTestBase


class CompactContractTest(unittest.IsolatedAsyncioTestCase):
    def test_schema_covers_all_normalized_slots(self):
        self.assertEqual(list(SCHEMA['properties']), list(intent.validate({})))
        self.assertEqual(SCHEMA['required'], ['intent'])
        self.assertFalse(SCHEMA['additionalProperties'])

    def test_sparse_and_explicit_defaults_normalize_identically(self):
        sparse={'intent':'set_timer','duration_seconds':300}
        expanded={key:None for key in SCHEMA['properties']}
        expanded.update(sparse, scope='one', phone_action='ring', missing_content=False,
                        sound_theme='achievement_bell')
        self.assertEqual(intent.validate(sparse), intent.validate(expanded))

    async def test_request_enforces_schema_without_changing_messages(self):
        messages=[{'role':'user','content':'how much juice is in a lime'}]
        observed=[]
        def respond(request):
            observed.append(json.loads(request.content))
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"intent":"ask"}'}}]})
        original=httpx.AsyncClient
        with patch.object(clients.httpx,'AsyncClient',side_effect=lambda **kw: original(transport=httpx.MockTransport(respond),**kw)):
            raw=await clients.parse_intent_raw(messages)
        self.assertEqual(json.loads(raw),{'intent':'ask'})
        self.assertEqual(observed[0]['response_format'],RESPONSE_FORMAT)
        self.assertEqual(observed[0]['messages'],messages)
        self.assertFalse(observed[0]['chat_template_kwargs']['enable_thinking'])


class CompactDispatchTest(_SlotTestBase):
    def test_ask_without_query_forwards_original_question(self):
        command='how much juice is in a lime'
        answer={'response':'About two tablespoons.','ok':True}
        with patch.object(clients,'parse_intent_raw',AsyncMock(return_value='{"intent":"ask"}')), \
             patch.object(app.ask_mod,'handle_ask',AsyncMock(return_value=answer)) as ask:
            result=self._run(command)
        self.assertEqual(ask.await_args.args[0],command)
        self.assertTrue(result['ok'])

    def test_cleaned_query_is_not_replaced_by_original(self):
        command='why is the sky blue mommy i want a snack'
        clean='why is the sky blue'
        with patch.object(clients,'parse_intent_raw',AsyncMock(return_value=json.dumps({'intent':'ask','query':clean}))), \
             patch.object(app.ask_mod,'handle_ask',AsyncMock(return_value={'response':'Scattering.','ok':True})) as ask:
            self._run(command)
        self.assertEqual(ask.await_args.args[0],clean)

    def test_named_timer_query_keeps_its_target(self):
        self._run('set a chicken timer for five minutes')
        self._run('set a rice timer for ten minutes')
        with patch.object(clients,'parse_intent_raw',AsyncMock(return_value='{"intent":"timer_query","label":"chicken"}')), \
             patch.object(app.fmt,'report_query',wraps=app.fmt.report_query) as report:
            self._run('how much time remains on the chicken timer')
        selected=report.call_args.args[0]
        self.assertEqual(len(selected),1)
        self.assertEqual(selected[0]['label'],'chicken')

    def test_missing_duration_still_asks_for_the_slot(self):
        with patch.object(clients,'parse_intent_raw',AsyncMock(return_value='{"intent":"set_timer","label":"chicken","sound_theme":"cluck"}')):
            result=self._run('set a chicken timer for')
        self.assertTrue(result['awaiting_slot'])
        self.assertEqual(app.session_pending()['op'],'clarify')

if __name__=='__main__': unittest.main()
