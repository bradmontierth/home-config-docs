import json
from unittest.mock import AsyncMock, patch
import httpx
from . import app, chaos
from .test_clarify import _SlotTestBase

class ChaosColorTest(_SlotTestBase):
    def test_color_dispatch_publishes_explicit_slots_without_classifier(self):
        for phrase, names in [('give me chaos in blue', ['blue']),
                              ('give me chaos in red', ['red']),
                              ('give me chaos in blue and red', ['blue','red']),
                              ('set me chaos mode in purple and green please', ['purple','green']),
                              ('give me chaos in fuchsia', ['fuchsia']),
                              ('give me chaos in gold', ['gold']),
                              ('give me chaos in puce', ['puce']),
                              ('give me chaos in hot pink', ['hot pink']),
                              ('give me chaos in Deep Sky Blue and gold.', ['deep sky blue','gold']),
                              ('give me chaos in hotpink and the sea-foam', ['hot pink','seafoam'])]:
            calls=[]
            def respond(request):
                calls.append(json.loads(request.content))
                return httpx.Response(200,json=[])
            original=httpx.AsyncClient
            with self.subTest(phrase=phrase), \
                 patch.object(chaos, '_token', return_value='test'), \
                 patch.object(chaos.httpx,'AsyncClient',side_effect=lambda **kw: original(transport=httpx.MockTransport(respond),**kw)), \
                 patch.object(app.broadcast_mod,'send',AsyncMock()), \
                 patch.object(app.intent_mod,'parse',AsyncMock()) as parse:
                result=self._run(phrase)
                self.assertTrue(result['ok']);self.assertEqual(result['intent'],'home_control')
                parse.assert_not_awaited();self.assertEqual(len(calls),1)
                self.assertEqual(calls[0]['topic'],'voice/chaos/colors')
                self.assertEqual(json.loads(calls[0]['payload']),
                                 {'colors':names,'hs':[chaos.COLORS[n] for n in names]})
                self.assertFalse(calls[0]['retain'])

    def test_invalid_color_requests_clarify_without_actuation(self):
        for phrase in ['give me chaos in plaid','give me chaos in navy','give me chaos in gray','chaos in blue and red and green','chaos in']:
            with self.subTest(phrase=phrase),patch.object(chaos.httpx,'AsyncClient') as client, \
                 patch.object(app.broadcast_mod,'send',AsyncMock()),patch.object(app.intent_mod,'parse',AsyncMock()) as parse:
                result=self._run(phrase)
                self.assertFalse(result['ok']);self.assertIn('one or two colors',result['response'])
                client.assert_not_called();parse.assert_not_awaited()

    def test_other_requests_and_random_chaos_are_not_color_commands(self):
        for phrase in ['give me chaos mode','what is chaos in physics','turn off chaos mode',
                       'play blue and red','set the lights to blue']:
            self.assertIsNone(chaos.parse(phrase))

    def test_palette_values_are_publishable_hue_saturation(self):
        self.assertGreater(len(chaos.COLORS), 90)
        self.assertEqual(chaos.COLORS['purple'], [270, 100])  # hand palette beats CSS
        for name, (hue, sat) in chaos.COLORS.items():
            self.assertTrue(0 <= hue < 360 and 0 <= sat <= 100, name)
            self.assertNotIn(' and ', f' {name} ')
