"""Explicit one/two-color chaos requests, using the existing home-control path."""
import json
import re
from pathlib import Path
import httpx
from . import config
from .weather import _token

COLORS = json.loads(Path(__file__).with_name('chaos_colors.json').read_text())
# Spoken names match with spaces/hyphens ignored: "hot pink", "hotpink", "hot-pink".
_LOOKUP = {re.sub(r'[\s-]', '', name): name for name in COLORS}
_REQUEST = re.compile(
    r'^(?:(?:please|can you|could you) )?'
    r'(?:(?:give me|set me|set|make|turn on|start|enable|switch to|put on) )?'
    r'chaos(?: mode)? (?:in|to)\s*(.*?)\s*(?: please)?[.!?]*$')

def parse(command: str) -> list[str] | None:
    """None is unrelated; an empty list is a chaos request needing clarification."""
    match = _REQUEST.fullmatch(' '.join(command.lower().split()))
    if not match:
        return None
    parts = [re.sub(r'^the |[\s-]', '', part.strip()) for part in re.split(r'\s+and\s+|\s*,\s*', match[1])]
    names = [_LOOKUP.get(part) for part in parts]
    return names if 1 <= len(names) <= 2 and all(names) else []

async def handle(names: list[str]) -> dict:
    if not names:
        return {'ok': False, 'response': 'Choose one or two colors, like blue or blue and red.'}
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.post(
            config.HA_URL + '/api/services/mqtt/publish',
            headers={'Authorization': 'Bearer ' + _token()},
            json={'topic': 'voice/chaos/colors',
                  'payload': json.dumps({'colors': names, 'hsv': [COLORS[n] for n in names]}), 'qos': 0, 'retain': False})
        response.raise_for_status()
    return {'ok': True, 'response': 'Setting chaos to ' + ' and '.join(names) + '.'}
