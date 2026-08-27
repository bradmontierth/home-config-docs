# Kitchen (.251) helper scripts — copies of what runs on big-speaker-mini-pc

- `clip-browser.py` + `clip-browser.service` — read-only clip browser at
  http://192.168.10.251:8782/ (lists `~/voice-pipeline/data/clips`, joins
  transcript/intent/response from events.jsonl, `<audio>` per clip).
  Installed 2026-08-26 as `/home/pi/clip-browser.py`, systemd unit enabled.
- `aec-trial.sh` — XVF3800 residual-echo-suppressor trial steps
  (`show|step1|step2|step3|nuke|revert|results|save`), see
  `kitchen-aec-guide.md` § "Post-chime first-word loss".
