# Orchestrator-served sounds

Short WAVs the orchestrator serves at `GET /sounds/<name>` and asks the kitchen
satellite to play by URL (`POST /speak {"url": "/sounds/…"}` — the satellite
fetches it and plays through its own volume/duck path).

The satellite has its own `sounds/` for everything it triggers itself (wake
chime, alarm themes). These are for sounds the ORCHESTRATOR triggers, where the
satellite has no reason to know the file exists.

| file | used by | source |
| --- | --- | --- |
| `reminder.wav` | a due reminder popping on the kitchen display (`/reminder/due`) | copy of the satellite's unused `vad_alt_bright.wav` |

`reminder.wav` is deliberately NOT the wake chime or the dismiss chirp: those
already mean something to the family, and a reminder arriving on its own (with
nobody having said anything) must not sound like the assistant woke up.
