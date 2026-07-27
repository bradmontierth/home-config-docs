# Transform a Grafana GET /api/dashboards/uid/:uid response into a
# POST /api/dashboards/db payload. Pass the dashboard UID as --arg uid.

def replace_retention_sources:
  gsub("\\bEnergyPowerHoursByMinute\\b"; "v_energy_power_energy_all")
  | gsub("\\bIotaWattHoursSolarbyMinute\\b"; "v_iotawatt_solar_energy_all")
  | gsub("\\bIotaWattHoursbyMinute\\b"; "v_iotawatt_energy_all")
  | gsub("\\bSolarCostTrend\\b"; "v_solar_cost_trend_all")
  | gsub("\\bIotaWattSolar\\b"; "v_iotawatt_solar_power_all")
  | gsub("\\bIotaWatt\\b"; "v_iotawatt_power_all");

def solar_accounting_sql($panel_id):
  if $panel_id == 10 then
    "SELECT\n  unix_timestamp(MAX(DateDTS)) AS \"time\",\n  SUM(Cost) AS \"Credit (Bill)\"\nFROM v_iotawatt_solar_accounting_all\nWHERE\n  $__timeFilter(DateDTS)"
  elif $panel_id == 11 then
    "SELECT\n  unix_timestamp(MAX(DateDTS)) AS \"time\",\n  SUM(SolarWh) / 1000.0 * 0.1 AS \"Solar Value\"\nFROM v_iotawatt_solar_accounting_all\nWHERE\n  $__timeFilter(DateDTS)"
  elif $panel_id == 12 then
    "SELECT\n  unix_timestamp(MAX(DateDTS)) AS \"time\",\n  -SUM(ConsumedWh) / 1000.0 * 0.1 AS \"Usage Cost\"\nFROM v_iotawatt_solar_accounting_all\nWHERE\n  $__timeFilter(DateDTS)"
  elif $panel_id == 13 then
    "SELECT\n  unix_timestamp(MAX(DateDTS)) AS \"time\",\n  SUM(SelfConsumedWh) / NULLIF(SUM(SolarWh), 0) AS \"Solar Utilization\"\nFROM v_iotawatt_solar_accounting_all\nWHERE\n  $__timeFilter(DateDTS)"
  else null
  end;

def migrate_panel($uid):
  if type == "object" and has("targets") then
    . as $panel
    | .targets |= map(
        if has("rawSql") and (.rawSql | type == "string") then
          if $uid == "debnyj9w02t4wa" and ($panel.id == 10 or $panel.id == 11 or $panel.id == 12 or $panel.id == 13) then
            .rawSql = solar_accounting_sql($panel.id)
          else
            .rawSql |= replace_retention_sources
          end
        else . end)
  else . end;

{
  dashboard: (.dashboard | walk(migrate_panel($uid))),
  folderUid: (.meta.folderUid // ""),
  overwrite: true,
  message: "Migrate IotaWatt Grafana queries to hourly-history compatibility views"
}
