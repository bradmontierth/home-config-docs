"""Run inside voice-orchestrator with candidate command table on stdin.
Only the matcher runs; the HA button press is mocked and tables never write.
"""
import asyncio
import json
import sys
from unittest.mock import AsyncMock, patch
from orchestrator import home_control, main_comfort

commands = json.load(sys.stdin)
count = 0
with patch.object(home_control, "_commands", return_value=commands), \
     patch.object(home_control, "_press", new_callable=AsyncMock) as press, \
     patch.object(main_comfort, "request", new_callable=AsyncMock, return_value={"ok": True}) as comfort:
    for room, prefix in (("kitchen", "main_comfort"), ("familyroom", "main_comfort"),
                         ("simon", "simon_too"), ("claire", "claire_too")):
        for kind in ("hot", "cold"):
            expected = f"{prefix}_{kind}"
            for phrase in (f"it's {kind} in here", f"it is {kind} in here",
                           f"it's too {kind} in here", f"its {kind} in here"):
                assert home_control.has_exact_match(phrase, room), (room, phrase)
                assert home_control.fuzzy_match(phrase, room) == expected, (room, phrase)
                press.reset_mock()
                result = asyncio.run(home_control.handle({"query":phrase}, phrase, room))
                assert result["ok"], result
                if prefix == "main_comfort":
                    press.assert_not_awaited()
                    comfort.assert_awaited_with(kind)
                else:
                    press.assert_awaited_once_with(f"button.voice_{expected}")
                count += 1
    for phrase in ("it's hot in here", "it's cold in here"):
        assert home_control.fuzzy_match(phrase,"master") is None
        count += 1
    for phrase, expected in (("turn on the fan","fr_fan_on"),
                             ("close the blinds","blinds_kitchen_close"),
                             ("open the garage",None),
                             ("unlock the front door",None)):
        assert home_control.fuzzy_match(phrase,"kitchen") == expected, phrase
        count += 1
    # Bare AC/heat phrases cannot intercept these commands (the old cans
    # collision is also fixed as part of the adaptive voice deployment).
    baseline = {k:v for k,v in commands.items() if not k.startswith("main_comfort_")}
    for phrase in ("turn on the ac", "turn off the ac", "turn on the heat", "turn off the heat"):
        actual = home_control.fuzzy_match(phrase,"kitchen")
        with patch.object(home_control,"_commands",return_value=baseline):
            assert actual == home_control.fuzzy_match(phrase,"kitchen"), phrase
        assert not (actual or "").startswith("main_comfort_"), phrase
        count += 1
print(f"PASS: {count} room-scoping and mocked button-dispatch checks; no live presses.")
