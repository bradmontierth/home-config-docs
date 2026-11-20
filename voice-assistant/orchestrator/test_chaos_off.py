import unittest
from unittest.mock import AsyncMock,patch
from orchestrator import app,home_control
from orchestrator.test_clarify import _SlotTestBase
class ChaosOff(_SlotTestBase):
 def test_phrases(self):
  for phrase in ['chaos off','turn off chaos','turn off chaos mode','disable chaos mode','stop chaos mode','exit chaos mode']:
   with self.subTest(phrase=phrase),patch.object(home_control,'_press',AsyncMock()) as press,patch.object(app.broadcast_mod,'send',AsyncMock()),patch.object(app.intent_mod,'parse',AsyncMock()) as parse:
    r=self._run(phrase);self.assertTrue(r['ok']);press.assert_awaited_once_with('button.voice_chaos_off');parse.assert_not_awaited()
if __name__ == "__main__":
 unittest.main()
