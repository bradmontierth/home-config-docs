-- Full first minute after the verified phase-two Vue total-home cutover.
-- Legacy local-time source series remain untouched; all new query contracts are UTC.
CREATE OR REPLACE VIEW v_home_solar_power_all AS
SELECT CONVERT_TZ(Time,'America/Denver','UTC') AS Time,TotalGrid,TotalSolar,TotalHeatPump
FROM v_iotawatt_solar_power_all
WHERE CONVERT_TZ(Time,'America/Denver','UTC') < '2026-09-27 23:48:00'
UNION ALL
SELECT BucketStartUTC,PanelUsageAvgW,SolarAvgW,HeatPumpAvgW
FROM EnergyPowerHourly
WHERE BucketStartUTC >= '2026-09-27 23:48:00'
AND BucketStartUTC < CAST(DATE_FORMAT((SELECT MAX(Time) FROM EnergyPowerRaw)-INTERVAL (SELECT RawRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY,'%Y-%m-%d %H:00:00') AS DATETIME)
UNION ALL
SELECT Time,PanelUsageW,SolarW,HeatPumpW FROM EnergyPowerRaw
WHERE Time >= '2026-09-27 23:48:00'
AND Time >= CAST(DATE_FORMAT((SELECT MAX(Time) FROM EnergyPowerRaw)-INTERVAL (SELECT RawRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY,'%Y-%m-%d %H:00:00') AS DATETIME);

CREATE OR REPLACE VIEW v_home_solar_energy_all AS
SELECT CONVERT_TZ(DateDTS,'America/Denver','UTC') AS DateDTS,TotalGridHours,TotalSolarHours,TotalHeatPumpHours
FROM v_iotawatt_solar_energy_all
WHERE CONVERT_TZ(DateDTS,'America/Denver','UTC') < '2026-09-27 23:48:00'
UNION ALL
SELECT DateDTS,PanelUsageWh,SolarWh,HeatPumpWh FROM v_energy_power_energy_all
WHERE DateDTS >= '2026-09-27 23:48:00';

CREATE OR REPLACE VIEW v_home_solar_energy_local AS
SELECT CONVERT_TZ(DateDTS,'UTC','America/Denver') AS DateDTS,TotalGridHours,TotalSolarHours,TotalHeatPumpHours FROM v_home_solar_energy_all;

-- Preserve the existing flat-rate energy-value model. These are estimates of
-- generation versus household consumption, NOT metered grid bills or battery ROI.
CREATE OR REPLACE VIEW v_home_solar_accounting_all AS
SELECT CONVERT_TZ(DateDTS,'America/Denver','UTC') AS DateDTS,SolarWh,ConsumedWh,SelfConsumedWh,Cost
FROM v_iotawatt_solar_accounting_all
WHERE CONVERT_TZ(DateDTS,'America/Denver','UTC') < '2026-09-27 23:48:00'
UNION ALL
SELECT DateDTS,SolarWh,PanelUsageWh,LEAST(SolarWh,PanelUsageWh),
 (SolarWh-PanelUsageWh)/1000.0 * CASE WHEN SolarWh>PanelUsageWh THEN 0.05 ELSE 0.10 END
FROM v_energy_power_energy_all
WHERE DateDTS >= '2026-09-27 23:48:00';

CREATE OR REPLACE VIEW v_home_solar_cost_trend_all AS
SELECT CONVERT_TZ(DateDTS,'America/Denver','UTC') AS DateDTS,SolarCost FROM v_solar_cost_trend_all
WHERE CONVERT_TZ(DateDTS,'America/Denver','UTC') < '2026-09-27 23:48:00'
UNION ALL
SELECT DateDTS,Cost*60000.0 FROM v_home_solar_accounting_all
WHERE DateDTS >= '2026-09-27 23:48:00';
