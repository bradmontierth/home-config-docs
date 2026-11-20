# Work server "tank" (ThinkPad P51) — tailnet, file share, agent collaboration plan

Status 2026-10-15 eve: tank base build DONE (section 2.1/2.2/2.4): Ubuntu 26.04.1 patched,
TZ America/Denver, Docker 29.9.1 + compose v5.6 (pi in docker group, json-file log rotation
20m×5), tailscale 1.104.1 JOINED 2026-10-15 eve via login URL (no auth key needed): `tank.tail8880af.ts.net` / 100.107.129.101, `tag:work-server`, `--ssh`; the Illuminate tailnet already existed with Tom/Lincoln as members,
gh 2.102, jq/sqlite3/rsync. `/home/pi/cecret_lake` (0700) seeded with `pushover/.env`;
backup script + tank `config.json` installed, passphrase generated (env file + copy in
`cecret_lake/homelab-backup/passphrase` — Brad must store it off-machine), Pushover test OK,
first local archive OK, cron 04:05 local-only. **Open before S3 works:** tank needs its own
AWS profile `tank-backup` (do NOT copy the Beelink key — Tom will have root-equivalent
access to tank); then add `--upload-s3` to the crontab line. Services not migrated yet.
Plan written 2026-10-15 after a design discussion; sections 1–4 are the proposal, 5 is the
phase ledger to keep current.

## 0. Current state (facts, checked 2026-10-15)

**Hardware.** Xeon E3-1505M v6, 8 threads, **61 GB RAM**, 477 GB NVMe. Plenty for DuckDB/dbt
on CMS public files; the limiting factor will be disk, not RAM. Wired 192.168.10.33 (UDM DHCP
reservation, MAC 8c:16:45:a0:82:11), Wi-Fi lease .97 comes and goes.

**Current tailnet is the personal one.** `tail65546d.ts.net` is owned by
bradmontierth@gmail.com. Members: Brad's gmail devices (BradIlluminate, BradLaptop, Pixel 8
Pro), tagged devices (N100, n100mini, kitchen-pi-display, smartpanel, steinhorst/montierth
displays), a pile of `funnel-ingress-node`s, and **tom@ / lincoln@ /
brad@illuminatehealthanalytics.com appear only as "device-of-shared-to-user"** (node
sharing, not membership). Beelink has Funnel ON. This is the "crowded, homelab exposed to
Tom/Lincoln" problem.

**Work services on the Beelink today.**

| Service | Path | Repo | State to move |
|---|---|---|---|
| lumen-board (lumens-per-watt, Next.js + Prisma) `:3010` + backup sidecar | `/home/pi/lumens-per-watt` | `illuminatehealth/lumens-per-watt` (clean, HEAD 8d39d04 2026-10-14) | `prisma/` (SQLite) + `backups/`; compose mounts the docker socket |
| slack-reminder-bot (pr-reminder + slack-assistant) | `/home/pi/slack/slack-reminder-bot` | `illuminatehealth/slack-reminder-bot` (12 uncommitted files, HEAD 2026-05-20) | docker volume `pr-reminder-data`; secrets in `/home/pi/cecret_lake/slack-reminder-bot` |

Both are in the homelab's encrypted nightly backup only by accident (compose discovery), so
moving them off the Beelink also means giving tank its own backup (section 2.4).

## 1. Tailnet: new one, owned by the work domain

**Decision: create a second tailnet under illuminatehealthanalytics.com.** Not ACL-tagging
inside the personal one. Reasons: membership instead of node-sharing for Tom/Lincoln, no
homelab/grandkid nodes in the same admin console, no risk that a personal ACL edit exposes
Home Assistant to a colleague, and the Personal plan (3 users, 100 devices) fits Brad+Tom+
Lincoln exactly.

Steps:
1. Sign in to Tailscale with brad@illuminatehealthanalytics.com → new tailnet. Anyone with
   that Google Workspace domain auto-joins; Tom and Lincoln just install the client and sign in.
2. `tailscale up` on tank with an auth key from the new tailnet, advertise `tag:work-server`.
   One tailnet per device; tank is work-only so that is fine. Brad's laptop/phone keep both
   accounts and switch (on the home LAN Brad reaches tank at .33 directly anyway).
3. ACL (policy file) — minimal:
   ```jsonc
   {
     "tagOwners": { "tag:work-server": ["autogroup:admin"] },
     "acls": [
       { "action": "accept", "src": ["autogroup:member"], "dst": ["tag:work-server:*"] }
     ],
     "ssh": [
       { "action": "accept", "src": ["autogroup:member"], "dst": ["tag:work-server"], "users": ["pi"] }
     ]
   }
   ```
   Broad on purpose: Tom gets every port on tank and Tailscale SSH as `pi`. Tighten later by
   port if a database shows up that should be SSH-only.
4. **Tailscale Serve, not Funnel**, for every web service on tank: HTTPS cert for
   `tank.<new-tailnet>.ts.net`, reachable only from the tailnet, and tailscaled injects
   `Tailscale-User-Login` / `Tailscale-User-Name` headers → apps get identity with no login
   screen. Funnel only ever for a deliberate public path (e.g. a webhook), never the default.
5. After tank is live and Tom is on the new tailnet: remove the Tom/Lincoln shares from the
   personal tailnet. Homelab exposure to colleagues → zero.

## 2. tank base build

2.1 Docker + compose, git, `gh`, Tailscale, Tailscale SSH on. Keep `pi` password-sudo unless
Brad wants unattended root for agents (`/etc/sudoers.d/pi`).
2.2 Secrets: `/home/pi/cecret_lake` on tank with the same rules as the Beelink one (one dir
per app, files owned by `pi`). Slack bot secrets move here.
2.3 Migrate the two services: clone from the org, copy `prisma/` + `backups/` and the
`pr-reminder-data` volume, `docker compose up`, verify, then stop them on the Beelink and
remove their compose dirs from Beelink discovery. Commit the slack bot's 12 loose files first.
2.4 Backups: a tank copy of `homelab_config_backup.py` (same S3 bucket, prefix `tank/`),
scheduled by cron on tank, Pushover on failure (reuse the alert design from
`homelab-backups.md`). Rebuild-focused like the Beelink one, plus the SQLite stores.
2.5 Later: GitHub Actions self-hosted runner on tank (`illuminatehealth` org) for CI/CD;
DuckDB + dbt for CMS public-file scraping into the term++ models. 61 GB RAM makes a
single-node DuckDB pipeline the right first shape; no Postgres until something needs it.

## 3. File share: minimal, and it IS the collaboration store

A `work-docs` git repo on tank (bare repo + working tree, mirrored to GitHub org), exposed two
ways: a Samba share of the working tree over the tailnet (Tom drags files in from his laptop)
and the docs service in section 4 (renders it, comments on it, agents commit to it). No
Nextcloud/Drive clone. "Important docs" = markdown + PDFs + spreadsheets in that tree.

## 4. Collaboration with agents ("Google Drive was failing")

**The problem is transport, not storage:** Drive holds the artifact but neither Claude nor
Codex can see the comments, so every round trip is copy → paste → link → copy. Fix: put the
comments where both agents read and write, and make authorship first class
(`brad`, `tom`, `brad/claude`, `tom/codex`).

**Build: a small docs service on tank** (Next.js + Prisma + SQLite, same stack as lumen-board
so nothing new; sibling service, not a lumen-board module, so the board stays stable).

Phase 1 — docs + comments (~2 sessions)
- Renders markdown from the `work-docs` repo (tree, doc view, per-commit diff view).
- Threaded comments anchored to a paragraph; thread states open/resolved (+reopen); @mentions.
- Identity from the Tailscale headers for humans; per-agent API tokens for agents.
- Anchoring: fingerprint of the paragraph text (normalised hash) + heading path; when the
  paragraph disappears the thread falls back to document level with a "moved" marker. Same
  approach code-review tools use; this is the main design risk.
- Comments in SQLite (Prisma), nightly backup via 2.4.

Phase 2 — agent access (~1–2 sessions)
- MCP server (HTTP) + REST on the same service. Tools: `list_docs`, `read_doc`,
  `list_threads(filter: open | mentions me | doc)`, `reply`, `resolve`, `commit_edit(doc,
  new_text, message)`.
- Both Claude Code and Codex connect; each agent has its own token → author `tom/codex` etc.
- Round trip becomes: Tom comments → his agent (or Brad's) reads unresolved threads → edits →
  `commit_edit` as the agent → replies → resolves.

Phase 3 — change review
- Every agent edit is a git commit; UI shows the diff and either side comments on it.
- "Proposal" mode: agent commits to a branch and a human merges from the UI.

Phase 4 — attachments + digest
- File uploads next to the docs (the Samba tree), served through the same identity.
- Slack digest of open threads via the existing slack bot.

Phase 1b — Word-like editing (decided 2026-10-15 eve with Brad: build tiers 1+2, tier 3 open)
- Tier 1 = Phase 1 above (rendered page-style doc view + margin comments, Google Docs "comment mode").
- Tier 2 = rich editor (TipTap/ProseMirror, markdown round-trip) so humans never see raw
  markdown; autosave = git commit authored by the Tailscale user; soft per-doc edit lock
  ("Tom is editing" → read+comment until he leaves).
- Tier 3 (OPEN, not now) = live co-editing via TipTap + Yjs; upgrade path exists without a
  rewrite; the hard part is reconciling agent git commits with a live Yjs doc. Revisit only if
  Brad+Tom actually edit the same doc at the same time. Buy-alternative noted: Outline
  (Postgres not git, lossy markdown export) — rejected while the git/file-share model matters.

**Not building:** a general wiki, a Drive clone, public access.

**Where it lives:** code repo `workdocs` (dev copy on the Beelink `/home/pi/workdocs`, rsync'd to
tank `/home/pi/workdocs`, runs as systemd `workdocs.service` = `npm run start` on 127.0.0.1:3020, Node 22
from NodeSource; SQLite at `data/workdocs.db`; agent tokens via `npm run token`); content repo `work-docs` working tree on
tank `/home/pi/work-docs` (Samba-shared, service commits into it, pushed to GitHub when the org
repo exists). Both GitHub repos `illuminatehealth/{workdocs,work-docs}` still to be created by Brad.

**Shortcut considered and rejected:** claude.ai Docs already does shared docs with comments
routed to Claude (and Brad's Claude Code can read/answer them). Fastest for a Claude-only
pair, but Codex cannot participate and the content lives outside the repo. Tom is on Codex →
build the git-backed version.

## 5. Risks / unknowns

- Comment anchoring across edits (mitigated above; accept some "moved to doc level").
- Two agents editing the same doc at once → git conflict; keep docs small, commit per change,
  the service rebases and reports conflicts as a thread rather than failing silently.
- Tailscale header trust: only valid because nothing but tailscaled can reach the app ports;
  bind app ports to localhost on tank so LAN clients also go through Serve (or accept LAN =
  Brad only and bind to .33 too — decide at build time).
- lumen-board mounts the docker socket; keep that habit off tank if possible.
- Account switching friction on Brad's devices; LAN access to .33 removes most of it.

## 6. Phase ledger

| # | Item | Status |
|---|---|---|
| 1 | Illuminate tailnet `tail8880af.ts.net` (already existed — Tom, Lincoln, Brad members), ACL above pasted, tank joined as `tank.tail8880af.ts.net` 100.107.129.101 `tag:work-server` + Tailscale SSH, Serve header-echo on http://tank (:8099) | DONE 2026-10-15 eve; HTTPS certs NOT enabled on the tailnet yet (DNS → HTTPS Certificates), SSH + header test from a member device pending |
| 2.1–2.2 | tank base + cecret_lake | DONE 2026-10-15 (tailscale installed, not joined) |
| 2.2b | tank server hardening: lid ignore, sleep masked, multi-user, battery 75–80 %, BIOS OnByAcAttach + WoL, iTCO watchdog 60 s, consoleblank | DONE 2026-10-15 eve, reboot-verified; physical lid-close + pull-the-plug test pending |
| 4.P1 | docs service tier 1: render + block-anchored threads + inbox + history/diff + REST + agent tokens; systemd `workdocs.service` on tank (127.0.0.1:3020 behind Serve at https://tank.tail8880af.ts.net); API round-trip + re-anchoring + 409 stale-write tested; screenshots reviewed | BUILT 2026-10-15 eve (repo `/home/pi/workdocs` on Beelink, 1 local commit; GitHub `illuminatehealth/workdocs` + `work-docs` still to create) |
| 4.P1b | tier 2 editor: TipTap 3 + `@tiptap/markdown` (round-trip verified byte-identical), autosave → one amended commit per session, soft edit lock w/ take-over, `/e/<path>`, + New document | BUILT 2026-10-15 late eve, browser-driven tests passed |
| 4.P2 | MCP server: tools defined once (`src/mcp/server.ts`), stateless streamable-HTTP at `/mcp` (bearer only) + stdio entry `bin/mcp` for ssh; beelink Claude Code registered user-scope (`ssh p51 /home/pi/workdocs/bin/mcp`, shows Connected) | BUILT 2026-10-15 late; tailnet-HTTPS path + Codex (Tom) not yet tried |
| 2.3 | lumen-board + slack bot migrated, stopped on Beelink | DONE 2026-10-16 16:15 MDT: both stacks on tank (`/home/pi/lumens-per-watt` app 127.0.0.1:3010 behind Serve https://tank.tail8880af.ts.net:3010 + backup sidecar; `/home/pi/slack/slack-reminder-bot` pr-reminder + assistant, state bind-mounted at `data/` via tank-only compose override), data copied (row counts verified), beelink containers `compose down` (volume + dirs kept as fallback). Slack bot's 12 loose files committed dd77a8e + pushed. OPEN: Brad adds tank deploy key (`~/.ssh-deploy/id_ed25519.pub` on tank) to lumens-per-watt as read-only deploy key for the in-app Deploy button; tell Tom/Lincoln the new URL; then 1.5 |
| 2.4 | tank backups to S3 + Pushover | DONE 2026-10-16: IAM user `tank-backup` (inline policy: s3:PutObject+GetObject on `bradmontierth/tank/*` only; list/delete/other prefixes verified denied), profile on tank `~/.aws`, cron now `--upload-s3`, first upload `tank/daily/homelab_config_2026-10-16.tar.gz.gpg` + `_latest` verified |
| 3 | work-docs repo + Samba | not started |
| 4 P1 | docs service: render + comments + resolve | not started |
| 4 P2 | MCP/REST agent access, two agent tokens | brad/claude token + MCP live; self-service `/tokens` page (Tom mints his own) 2026-10-15 |
| 4.P3 | autonomous `@docs` agent: AgentJob queue + Event log, Agent SDK worker on tank (`workdocs-agent.service`, MCP-only tools), thread badge | LIVE: first real run 2026-10-15 21:07 (7 turns, $0.48, edit + reply + resolve) |
| 4.P3b | client folders (`psw/ icon/ ballad/ illuminate-internal/`), new-doc dialog, create/move folder+doc via REST + MCP (`create_folder`, `move_doc`) | DONE 2026-10-16, deployed, screenshots reviewed |
| 4.P3c | hub landing page at `/` (Docs/Board/CRM/T++ tiles), docs list at `/docs`, `/tpp` health page reading `canary_status.json` (contract in workdocs README) | DONE 2026-10-16; T++ tile lights up once the canary (next) writes the file |
| 2.3b | lumen-board uses tailnet identity (User.email ↔ Tailscale-User-Login), picker only without the header, server-side 403 on impersonation, emails editable in Settings | DONE 2026-10-16, deployed on tank, curl + screenshot verified |
| 5.1 | **T++ canary** on tank: py3.12 venv (uv), `scripts/canary.py` (daily run → backfill `--skip-gate` → dbt build on DuckDB), systemd `terminology-canary.timer` 05:30 MT, writes `data/state/canary_status.json` for the hub `/tpp`; gate metadata CONTRACT checks + backfill-keeps-gated-baseline fix (94 tests pass) | BUILT 2026-10-16; baseline run #3 healthy (111 s, dbt 407/407); commits pushed (96dadba); deploy key + UMLS key + Slack webhook added by Brad 19:40, tank clone now tracks origin via `github.com-tpp`; first timer run 2026-10-17 05:30 MT |
| 4.P4 | per-person Slack digest from `Event` rows (10-min quiet window, 1/h ceiling, own actions excluded) | after the Slack bot migrates to tank |
| 4 P3 | diff review / proposal branches | not started |
| 4 P4 | attachments + Slack digest | not started |
| 1.5 | remove Tom/Lincoln shares from personal tailnet | after Tom is on the new tailnet |
