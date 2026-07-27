-- IotaWatt Phase 2 Grafana compatibility views.
--
-- These views expose one continuous contract over permanent hourly history and
-- retained current data. Every UNION uses a strict, hour-aligned boundary:
-- archive timestamp < boundary; retained-source timestamp >= boundary.
-- Boundaries are source-relative and use the configured retention durations.

-- Complete the reserved sample-level solar self-consumption archive while the
-- legacy NetUsage history is still present. This is idempotent and preserves
-- the original Solar Utilization panel's numerator exactly at hourly grain.
INSERT INTO IotaWattSolarHourly (
    BucketStart, NetUsageSampleCount, SolarSelfConsumedWh, DerivedQuality
)
SELECT
    CAST(DATE_FORMAT(DateDTS, '%Y-%m-%d %H:00:00') AS DATETIME),
    COUNT(*),
    SUM(CASE WHEN Net>0 THEN IFNULL(Consumed,0) ELSE IFNULL(Solar,0) END)/720.0,
    'legacy_netusage_5s_assumed'
FROM NetUsage
GROUP BY CAST(DATE_FORMAT(DateDTS, '%Y-%m-%d %H:00:00') AS DATETIME)
ON DUPLICATE KEY UPDATE
    NetUsageSampleCount=VALUES(NetUsageSampleCount),
    SolarSelfConsumedWh=VALUES(SolarSelfConsumedWh),
    DerivedQuality=VALUES(DerivedQuality);

CREATE OR REPLACE VIEW v_iotawatt_power_all AS
SELECT
    BucketStart AS `Time`,
    TotalAvgW AS Total,
    WaterHeaterAvgW AS WaterHeater,
    FurnaceAvgW AS Furnace,
    FridgeAvgW AS Fridge,
    KitchenAvgW AS Kitchen,
    ACAvgW AS ACTotal,
    DryerAvgW AS DryerTotal,
    EVAvgW AS EVTotal,
    RangeAvgW AS RangeTotal,
    AllOtherAvgW AS AllOther,
    VoltageAvg AS Voltage,
    FrequencyAvg AS Frequency,
    UpstairsBedsAvgW AS UpstairsBeds,
    OfficeAvgW AS Office,
    UtilityClosetAvgW AS UtilityCloset,
    ClaireMiniAvgW AS ClaireMini
FROM IotaWattHourly
WHERE BucketStart < CAST(DATE_FORMAT(
    DATE_SUB((SELECT MAX(r.`Time`) FROM IotaWatt r),
             INTERVAL (SELECT RawRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY),
    '%Y-%m-%d %H:00:00') AS DATETIME)
UNION ALL
SELECT
    `Time`, Total, WaterHeater, Furnace, Fridge, Kitchen, ACTotal,
    DryerTotal, EVTotal, RangeTotal, AllOther, Voltage, Frequency,
    UpstairsBeds, Office, UtilityCloset, ClaireMini
FROM IotaWatt
WHERE `Time` >= CAST(DATE_FORMAT(
    DATE_SUB((SELECT MAX(r.`Time`) FROM IotaWatt r),
             INTERVAL (SELECT RawRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY),
    '%Y-%m-%d %H:00:00') AS DATETIME);

CREATE OR REPLACE VIEW v_iotawatt_solar_power_all AS
SELECT
    BucketStart AS `Time`,
    TotalGridAvgW AS TotalGrid,
    TotalSolarAvgW AS TotalSolar,
    TotalHeatPumpAvgW AS TotalHeatPump
FROM IotaWattSolarHourly
WHERE BucketStart < CAST(DATE_FORMAT(
    DATE_SUB((SELECT MAX(r.`Time`) FROM IotaWattSolar r),
             INTERVAL (SELECT RawRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY),
    '%Y-%m-%d %H:00:00') AS DATETIME)
UNION ALL
SELECT `Time`, TotalGrid, TotalSolar, TotalHeatPump
FROM IotaWattSolar
WHERE `Time` >= CAST(DATE_FORMAT(
    DATE_SUB((SELECT MAX(r.`Time`) FROM IotaWattSolar r),
             INTERVAL (SELECT RawRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY),
    '%Y-%m-%d %H:00:00') AS DATETIME);

CREATE OR REPLACE VIEW v_iotawatt_energy_all AS
SELECT
    BucketStart AS DateDTS,
    TotalWh AS TotalWattHours,
    ACWh AS ACHours,
    DryerWh AS DryerHours,
    RangeWh AS RangeHours,
    WaterHeaterWh AS WaterHeaterHours,
    EVWh AS EVHours,
    FurnaceWh AS FurnaceHours,
    FridgeWh AS FridgeHours,
    KitchenWh AS KitchenHours,
    AllOtherWh AS AllOtherHours,
    UpstairsBedsWh AS UpstairsBedsHours,
    OfficeWh AS OfficeHours,
    UtilityClosetWh AS UtilityClosetHours,
    ClaireMiniWh AS ClaireMiniHours
FROM IotaWattHourly
WHERE BucketStart < CAST(DATE_FORMAT(
    DATE_SUB((SELECT MAX(m.DateDTS) FROM IotaWattHoursbyMinute m),
             INTERVAL (SELECT MinuteRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY),
    '%Y-%m-%d %H:00:00') AS DATETIME)
UNION ALL
SELECT
    DateDTS, TotalWattHours, ACHours, DryerHours, RangeHours,
    WaterHeaterHours, EVHours, FurnaceHours, FridgeHours, KitchenHours,
    AllOtherHours, UpstairsBedsHours, OfficeHours, UtilityClosetHours,
    ClaireMiniHours
FROM IotaWattHoursbyMinute
WHERE DateDTS >= CAST(DATE_FORMAT(
    DATE_SUB((SELECT MAX(m.DateDTS) FROM IotaWattHoursbyMinute m),
             INTERVAL (SELECT MinuteRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY),
    '%Y-%m-%d %H:00:00') AS DATETIME);

CREATE OR REPLACE VIEW v_iotawatt_solar_energy_all AS
SELECT
    BucketStart AS DateDTS,
    TotalGridWh AS TotalGridHours,
    TotalSolarWh AS TotalSolarHours,
    TotalHeatPumpWh AS TotalHeatPumpHours
FROM IotaWattSolarHourly
WHERE BucketStart < CAST(DATE_FORMAT(
    DATE_SUB((SELECT MAX(m.DateDTS) FROM IotaWattHoursSolarbyMinute m),
             INTERVAL (SELECT MinuteRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY),
    '%Y-%m-%d %H:00:00') AS DATETIME)
UNION ALL
SELECT DateDTS, TotalGridHours, TotalSolarHours, TotalHeatPumpHours
FROM IotaWattHoursSolarbyMinute
WHERE DateDTS >= CAST(DATE_FORMAT(
    DATE_SUB((SELECT MAX(m.DateDTS) FROM IotaWattHoursSolarbyMinute m),
             INTERVAL (SELECT MinuteRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY),
    '%Y-%m-%d %H:00:00') AS DATETIME);

-- EnergyPower timestamps are UTC. BucketStartUTC deliberately remains DateDTS
-- so existing Grafana SQL retains the source's UTC behavior.
CREATE OR REPLACE VIEW v_energy_power_energy_all AS
SELECT
    BucketStartUTC AS DateDTS,
    SolarWh, EffectiveUsageWh, PanelUsageWh, PowerwallOutWh,
    BatteryChargingWh, HeatPumpWh, GridWh
FROM EnergyPowerHourly
WHERE BucketStartUTC < CAST(DATE_FORMAT(
    DATE_SUB((SELECT MAX(m.DateDTS) FROM EnergyPowerHoursByMinute m),
             INTERVAL (SELECT MinuteRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY),
    '%Y-%m-%d %H:00:00') AS DATETIME)
UNION ALL
SELECT
    DateDTS, SolarWh, EffectiveUsageWh, PanelUsageWh, PowerwallOutWh,
    BatteryChargingWh, HeatPumpWh, GridWh
FROM EnergyPowerHoursByMinute
WHERE DateDTS >= CAST(DATE_FORMAT(
    DATE_SUB((SELECT MAX(m.DateDTS) FROM EnergyPowerHoursByMinute m),
             INTERVAL (SELECT MinuteRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY),
    '%Y-%m-%d %H:00:00') AS DATETIME);

-- Retain the legacy SolarCostTrend contract. Historical Cost is already in
-- dollars, while SolarCostTrend stores the legacy value at 60,000 units/USD.
CREATE OR REPLACE VIEW v_solar_cost_trend_all AS
SELECT BucketStart AS DateDTS, Cost * 60000.0 AS SolarCost
FROM IotaWattSolarHourly
WHERE BucketStart < CAST(DATE_FORMAT(
    DATE_SUB((SELECT MAX(m.DateDTS) FROM SolarCostTrend m),
             INTERVAL (SELECT MinuteRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY),
    '%Y-%m-%d %H:00:00') AS DATETIME)
  AND Cost IS NOT NULL
UNION ALL
SELECT DateDTS, SolarCost
FROM SolarCostTrend
WHERE DateDTS >= CAST(DATE_FORMAT(
    DATE_SUB((SELECT MAX(m.DateDTS) FROM SolarCostTrend m),
             INTERVAL (SELECT MinuteRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY),
    '%Y-%m-%d %H:00:00') AS DATETIME);

-- Additive solar-accounting units used by the Solar dashboard. The retained
-- branch converts each five-second NetUsage sample to Wh and dollars, matching
-- the original dashboard divisors. The archive branch is hourly and exact for
-- cost and sample-level self-consumption after the companion backfill.
CREATE OR REPLACE VIEW v_iotawatt_solar_accounting_all AS
SELECT
    BucketStart AS DateDTS,
    TotalSolarWh AS SolarWh,
    TotalGridWh AS ConsumedWh,
    SolarSelfConsumedWh AS SelfConsumedWh,
    Cost
FROM IotaWattSolarHourly
WHERE BucketStart < CAST(DATE_FORMAT(
    DATE_SUB((SELECT MAX(n.DateDTS) FROM NetUsage n),
             INTERVAL (SELECT RawRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY),
    '%Y-%m-%d %H:00:00') AS DATETIME)
UNION ALL
SELECT
    DateDTS,
    Solar / 720.0 AS SolarWh,
    Consumed / 720.0 AS ConsumedWh,
    (CASE WHEN Net > 0 THEN Consumed ELSE Solar END) / 720.0 AS SelfConsumedWh,
    Cost / 720000.0 AS Cost
FROM NetUsage
WHERE DateDTS >= CAST(DATE_FORMAT(
    DATE_SUB((SELECT MAX(n.DateDTS) FROM NetUsage n),
             INTERVAL (SELECT RawRetentionDays FROM IotaWattRetentionState WHERE Id=1) DAY),
    '%Y-%m-%d %H:00:00') AS DATETIME);
