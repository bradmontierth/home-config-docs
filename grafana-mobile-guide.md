# Grafana Mobile guide

Phone-first viewer over the existing Grafana dashboards.

- URL: `http://192.168.10.217:8800` (Beelink, container `grafana-mobile`)
- Repo: `/home/pi/grafana-mobile` (local git, no GitHub remote yet)
- Source of truth: Grafana at `http://192.168.10.217:3001` (container `grafana-grafana-1`)

## Android app

"Dashboards" (`com.local.grafanamobile`, repo `/home/pi/grafana-mobile-android`) is
a full-screen WebView shell, installed from the F-Droid repo or
`http://192.168.10.217:3000/apk/grafana-mobile-latest.apk`. Build/publish steps are
in `android-apk-publishing-guide.md`. Page changes need no APK rebuild.

## How it works

Read-only. It fetches dashboard JSON from Grafana and runs each panel's saved
query through Grafana's `/api/ds/query`; it holds no database credentials and
the browser never sends SQL. Edit dashboards in Grafana as usual; the phone
view picks the change up within 60 s. Favorites / hidden dashboards:
`/home/pi/grafana-mobile/config/config.json` (no rebuild).

Deploy: `cd /home/pi/grafana-mobile && docker compose up -d --build`.

## Troubleshooting

- Empty chart *with* an error line: the query fails in Grafana too (e.g.
  Weather by City → missing table `weatherBradley`).
- Empty chart *without* an error, everywhere, after a Grafana upgrade: the
  internal `/api/ds/query` frame format changed; see the repo README "Risk".
- Panel types other than stat/gauge/timeseries/bargauge are links into Grafana.

## Known Grafana-side issues found while building it (2026-10-06)

- Grafana anonymous access is **Editor** (`GF_AUTH_ANONYMOUS_ORG_ROLE=Editor`):
  anyone on the LAN can edit dashboards and run arbitrary SQL via
  `/api/ds/query`. Viewer would keep this app working. Not changed.
- 181 targets reference datasource uid `ZxF7RtM4z`, which no longer exists;
  they work only via Grafana's fallback to the panel datasource.
- Weather → Pressure chart queries `name='baromrel'`, which has no rows; the
  Pressure stat tile works (different query).
- Air Quality CO2 / TVOC / radon panels: no rows in the last 30 days.
