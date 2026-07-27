-- IotaWatt Phase 2: permanent hourly archives and archive-only maintenance.
-- Applied to hubitat_logging on 2026-07-21.
--
-- Safety invariant: this migration creates and fills archive objects only. It
-- does not delete, truncate, or alter any source telemetry table. Pruning is
-- locked in IotaWattRetentionState until the Grafana and consumer migration is
-- complete and separately verified.

CREATE TABLE IF NOT EXISTS IotaWattRetentionState (
    Id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
    RawRetentionDays SMALLINT UNSIGNED NOT NULL,
    MinuteRetentionDays SMALLINT UNSIGNED NOT NULL,
    EnergyDailyStartUTC DATETIME NOT NULL,
    EnergyMinuteCutoverUTC DATETIME NOT NULL,
    GrafanaMigrated BOOLEAN NOT NULL DEFAULT FALSE,
    ConsumersMigrated BOOLEAN NOT NULL DEFAULT FALSE,
    PruningEnabled BOOLEAN NOT NULL DEFAULT FALSE,
    LastArchiveSuccessUTC DATETIME NULL,
    LastValidationUTC DATETIME NULL,
    Note VARCHAR(1000) NULL,
    UpdatedAt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

INSERT INTO IotaWattRetentionState (
    Id, RawRetentionDays, MinuteRetentionDays,
    EnergyDailyStartUTC, EnergyMinuteCutoverUTC,
    GrafanaMigrated, ConsumersMigrated, PruningEnabled, Note
) VALUES (
    1, 14, 30,
    '2026-03-05 12:00:00', '2026-06-02 18:34:00',
    FALSE, FALSE, FALSE,
    'Phase 2 archive-only mode. EnergyPower timestamps are UTC; IotaWatt timestamps are America/Denver local wall time.'
)
ON DUPLICATE KEY UPDATE
    RawRetentionDays = VALUES(RawRetentionDays),
    MinuteRetentionDays = VALUES(MinuteRetentionDays),
    EnergyDailyStartUTC = VALUES(EnergyDailyStartUTC),
    EnergyMinuteCutoverUTC = VALUES(EnergyMinuteCutoverUTC);

CREATE TABLE IF NOT EXISTS IotaWattHourly (
    BucketStart DATETIME NOT NULL PRIMARY KEY,
    MinuteCount SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    RawSampleCount SMALLINT UNSIGNED NULL,
    CoveragePct DECIMAL(6,2) NULL,
    FirstSample DATETIME NULL,
    LastSample DATETIME NULL,
    SourceName VARCHAR(64) NOT NULL DEFAULT 'IotaWatt',
    SourceGrain VARCHAR(32) NOT NULL DEFAULT 'minute+raw',
    StatsQuality VARCHAR(32) NOT NULL DEFAULT 'minute_average_extrema',

    TotalMinW DECIMAL(18,4) NULL, TotalMaxW DECIMAL(18,4) NULL, TotalAvgW DECIMAL(18,4) NULL, TotalWh DECIMAL(20,4) NULL,
    WaterHeaterMinW DECIMAL(18,4) NULL, WaterHeaterMaxW DECIMAL(18,4) NULL, WaterHeaterAvgW DECIMAL(18,4) NULL, WaterHeaterWh DECIMAL(20,4) NULL,
    FurnaceMinW DECIMAL(18,4) NULL, FurnaceMaxW DECIMAL(18,4) NULL, FurnaceAvgW DECIMAL(18,4) NULL, FurnaceWh DECIMAL(20,4) NULL,
    FridgeMinW DECIMAL(18,4) NULL, FridgeMaxW DECIMAL(18,4) NULL, FridgeAvgW DECIMAL(18,4) NULL, FridgeWh DECIMAL(20,4) NULL,
    KitchenMinW DECIMAL(18,4) NULL, KitchenMaxW DECIMAL(18,4) NULL, KitchenAvgW DECIMAL(18,4) NULL, KitchenWh DECIMAL(20,4) NULL,
    ACMinW DECIMAL(18,4) NULL, ACMaxW DECIMAL(18,4) NULL, ACAvgW DECIMAL(18,4) NULL, ACWh DECIMAL(20,4) NULL,
    DryerMinW DECIMAL(18,4) NULL, DryerMaxW DECIMAL(18,4) NULL, DryerAvgW DECIMAL(18,4) NULL, DryerWh DECIMAL(20,4) NULL,
    EVMinW DECIMAL(18,4) NULL, EVMaxW DECIMAL(18,4) NULL, EVAvgW DECIMAL(18,4) NULL, EVWh DECIMAL(20,4) NULL,
    RangeMinW DECIMAL(18,4) NULL, RangeMaxW DECIMAL(18,4) NULL, RangeAvgW DECIMAL(18,4) NULL, RangeWh DECIMAL(20,4) NULL,
    AllOtherMinW DECIMAL(18,4) NULL, AllOtherMaxW DECIMAL(18,4) NULL, AllOtherAvgW DECIMAL(18,4) NULL, AllOtherWh DECIMAL(20,4) NULL,
    UpstairsBedsMinW DECIMAL(18,4) NULL, UpstairsBedsMaxW DECIMAL(18,4) NULL, UpstairsBedsAvgW DECIMAL(18,4) NULL, UpstairsBedsWh DECIMAL(20,4) NULL,
    OfficeMinW DECIMAL(18,4) NULL, OfficeMaxW DECIMAL(18,4) NULL, OfficeAvgW DECIMAL(18,4) NULL, OfficeWh DECIMAL(20,4) NULL,
    UtilityClosetMinW DECIMAL(18,4) NULL, UtilityClosetMaxW DECIMAL(18,4) NULL, UtilityClosetAvgW DECIMAL(18,4) NULL, UtilityClosetWh DECIMAL(20,4) NULL,
    ClaireMiniMinW DECIMAL(18,4) NULL, ClaireMiniMaxW DECIMAL(18,4) NULL, ClaireMiniAvgW DECIMAL(18,4) NULL, ClaireMiniWh DECIMAL(20,4) NULL,
    VoltageMin DECIMAL(10,4) NULL, VoltageMax DECIMAL(10,4) NULL, VoltageAvg DECIMAL(10,4) NULL,
    FrequencyMin DECIMAL(10,4) NULL, FrequencyMax DECIMAL(10,4) NULL, FrequencyAvg DECIMAL(10,4) NULL,
    UpdatedAt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY IotaWattHourly_LocalDate (BucketStart),
    KEY IotaWattHourly_UpdatedAt (UpdatedAt)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS IotaWattSolarHourly (
    BucketStart DATETIME NOT NULL PRIMARY KEY,
    MinuteCount SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    RawSampleCount SMALLINT UNSIGNED NULL,
    NetUsageSampleCount SMALLINT UNSIGNED NULL,
    CoveragePct DECIMAL(6,2) NULL,
    FirstSample DATETIME NULL,
    LastSample DATETIME NULL,
    SourceName VARCHAR(64) NOT NULL DEFAULT 'IotaWattSolar',
    SourceGrain VARCHAR(32) NOT NULL DEFAULT 'minute+raw',
    StatsQuality VARCHAR(32) NOT NULL DEFAULT 'minute_average_extrema',

    TotalGridMinW DECIMAL(18,4) NULL, TotalGridMaxW DECIMAL(18,4) NULL, TotalGridAvgW DECIMAL(18,4) NULL, TotalGridWh DECIMAL(20,4) NULL,
    TotalSolarMinW DECIMAL(18,4) NULL, TotalSolarMaxW DECIMAL(18,4) NULL, TotalSolarAvgW DECIMAL(18,4) NULL, TotalSolarWh DECIMAL(20,4) NULL,
    TotalHeatPumpMinW DECIMAL(18,4) NULL, TotalHeatPumpMaxW DECIMAL(18,4) NULL, TotalHeatPumpAvgW DECIMAL(18,4) NULL, TotalHeatPumpWh DECIMAL(20,4) NULL,
    TeslaTotalWh DECIMAL(20,4) NULL,
    TeslaGreenWh DECIMAL(20,4) NULL,
    Cost DECIMAL(20,6) NULL,
    GridImportWh DECIMAL(20,4) NULL,
    GridExportWh DECIMAL(20,4) NULL,
    SolarSelfConsumedWh DECIMAL(20,4) NULL,
    DerivedQuality VARCHAR(64) NULL,
    UpdatedAt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY IotaWattSolarHourly_LocalDate (BucketStart),
    KEY IotaWattSolarHourly_UpdatedAt (UpdatedAt)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS EnergyPowerHourly (
    BucketStartUTC DATETIME NOT NULL PRIMARY KEY,
    BucketStartLocal DATETIME NOT NULL,
    LocalDate DATE NOT NULL,
    UTCOffsetMinutes SMALLINT NOT NULL,
    MinuteCount SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    RawSampleCount SMALLINT UNSIGNED NULL,
    CoveragePct DECIMAL(6,2) NULL,
    FirstSampleUTC DATETIME NULL,
    LastSampleUTC DATETIME NULL,
    SourceName VARCHAR(64) NOT NULL DEFAULT 'EnergyPower',
    SourceGrain VARCHAR(32) NOT NULL,
    StatsQuality VARCHAR(32) NOT NULL,

    SolarMinW DECIMAL(18,4) NULL, SolarMaxW DECIMAL(18,4) NULL, SolarAvgW DECIMAL(18,4) NULL, SolarWh DECIMAL(20,4) NULL,
    EffectiveUsageMinW DECIMAL(18,4) NULL, EffectiveUsageMaxW DECIMAL(18,4) NULL, EffectiveUsageAvgW DECIMAL(18,4) NULL, EffectiveUsageWh DECIMAL(20,4) NULL,
    PanelUsageMinW DECIMAL(18,4) NULL, PanelUsageMaxW DECIMAL(18,4) NULL, PanelUsageAvgW DECIMAL(18,4) NULL, PanelUsageWh DECIMAL(20,4) NULL,
    PowerwallOutMinW DECIMAL(18,4) NULL, PowerwallOutMaxW DECIMAL(18,4) NULL, PowerwallOutAvgW DECIMAL(18,4) NULL, PowerwallOutWh DECIMAL(20,4) NULL,
    BatteryChargingMinW DECIMAL(18,4) NULL, BatteryChargingMaxW DECIMAL(18,4) NULL, BatteryChargingAvgW DECIMAL(18,4) NULL, BatteryChargingWh DECIMAL(20,4) NULL,
    HeatPumpMinW DECIMAL(18,4) NULL, HeatPumpMaxW DECIMAL(18,4) NULL, HeatPumpAvgW DECIMAL(18,4) NULL, HeatPumpWh DECIMAL(20,4) NULL,
    GridMinW DECIMAL(18,4) NULL, GridMaxW DECIMAL(18,4) NULL, GridAvgW DECIMAL(18,4) NULL, GridWh DECIMAL(20,4) NULL,
    GridImportWh DECIMAL(20,4) NULL,
    GridExportWh DECIMAL(20,4) NULL,
    SolarSelfConsumedWh DECIMAL(20,4) NULL,
    UpdatedAt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY EnergyPowerHourly_Local (BucketStartLocal),
    KEY EnergyPowerHourly_LocalDate (LocalDate),
    KEY EnergyPowerHourly_UpdatedAt (UpdatedAt)
) ENGINE=InnoDB;

DELIMITER //

DROP PROCEDURE IF EXISTS archive_iotawatt_hours//
CREATE PROCEDURE archive_iotawatt_hours(IN p_start DATETIME, IN p_end DATETIME)
BEGIN
    IF p_start IS NULL OR p_end IS NULL OR p_start >= p_end THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'archive_iotawatt_hours requires start < end';
    END IF;

    INSERT INTO IotaWattHourly (
        BucketStart, MinuteCount, CoveragePct, FirstSample, LastSample,
        SourceName, SourceGrain, StatsQuality,
        TotalMinW, TotalMaxW, TotalAvgW, TotalWh,
        WaterHeaterMinW, WaterHeaterMaxW, WaterHeaterAvgW, WaterHeaterWh,
        FurnaceMinW, FurnaceMaxW, FurnaceAvgW, FurnaceWh,
        FridgeMinW, FridgeMaxW, FridgeAvgW, FridgeWh,
        KitchenMinW, KitchenMaxW, KitchenAvgW, KitchenWh,
        ACMinW, ACMaxW, ACAvgW, ACWh,
        DryerMinW, DryerMaxW, DryerAvgW, DryerWh,
        EVMinW, EVMaxW, EVAvgW, EVWh,
        RangeMinW, RangeMaxW, RangeAvgW, RangeWh,
        AllOtherMinW, AllOtherMaxW, AllOtherAvgW, AllOtherWh,
        UpstairsBedsMinW, UpstairsBedsMaxW, UpstairsBedsAvgW, UpstairsBedsWh,
        OfficeMinW, OfficeMaxW, OfficeAvgW, OfficeWh,
        UtilityClosetMinW, UtilityClosetMaxW, UtilityClosetAvgW, UtilityClosetWh,
        ClaireMiniMinW, ClaireMiniMaxW, ClaireMiniAvgW, ClaireMiniWh
    )
    SELECT
        CAST(DATE_FORMAT(DateDTS, '%Y-%m-%d %H:00:00') AS DATETIME),
        COUNT(*), LEAST(100.00, COUNT(*) * 100.0 / 60.0), MIN(DateDTS), MAX(DateDTS),
        'IotaWatt', 'minute', 'minute_average_extrema',
        MIN(TotalWattHours*60), MAX(TotalWattHours*60), SUM(TotalWattHours), SUM(TotalWattHours),
        MIN(WaterHeaterHours*60), MAX(WaterHeaterHours*60), SUM(WaterHeaterHours), SUM(WaterHeaterHours),
        MIN(FurnaceHours*60), MAX(FurnaceHours*60), SUM(FurnaceHours), SUM(FurnaceHours),
        MIN(FridgeHours*60), MAX(FridgeHours*60), SUM(FridgeHours), SUM(FridgeHours),
        MIN(KitchenHours*60), MAX(KitchenHours*60), SUM(KitchenHours), SUM(KitchenHours),
        MIN(ACHours*60), MAX(ACHours*60), SUM(ACHours), SUM(ACHours),
        MIN(DryerHours*60), MAX(DryerHours*60), SUM(DryerHours), SUM(DryerHours),
        MIN(EVHours*60), MAX(EVHours*60), SUM(EVHours), SUM(EVHours),
        MIN(RangeHours*60), MAX(RangeHours*60), SUM(RangeHours), SUM(RangeHours),
        MIN(AllOtherHours*60), MAX(AllOtherHours*60), SUM(AllOtherHours), SUM(AllOtherHours),
        MIN(UpstairsBedsHours*60), MAX(UpstairsBedsHours*60), SUM(UpstairsBedsHours), SUM(UpstairsBedsHours),
        MIN(OfficeHours*60), MAX(OfficeHours*60), SUM(OfficeHours), SUM(OfficeHours),
        MIN(UtilityClosetHours*60), MAX(UtilityClosetHours*60), SUM(UtilityClosetHours), SUM(UtilityClosetHours),
        MIN(ClaireMiniHours*60), MAX(ClaireMiniHours*60), SUM(ClaireMiniHours), SUM(ClaireMiniHours)
    FROM IotaWattHoursbyMinute
    WHERE DateDTS >= p_start AND DateDTS < p_end
    GROUP BY CAST(DATE_FORMAT(DateDTS, '%Y-%m-%d %H:00:00') AS DATETIME)
    ON DUPLICATE KEY UPDATE
        MinuteCount=VALUES(MinuteCount), CoveragePct=VALUES(CoveragePct),
        FirstSample=VALUES(FirstSample), LastSample=VALUES(LastSample),
        TotalWh=VALUES(TotalWh), WaterHeaterWh=VALUES(WaterHeaterWh), FurnaceWh=VALUES(FurnaceWh),
        FridgeWh=VALUES(FridgeWh), KitchenWh=VALUES(KitchenWh), ACWh=VALUES(ACWh),
        DryerWh=VALUES(DryerWh), EVWh=VALUES(EVWh), RangeWh=VALUES(RangeWh), AllOtherWh=VALUES(AllOtherWh),
        UpstairsBedsWh=VALUES(UpstairsBedsWh), OfficeWh=VALUES(OfficeWh),
        UtilityClosetWh=VALUES(UtilityClosetWh), ClaireMiniWh=VALUES(ClaireMiniWh);

    INSERT INTO IotaWattHourly (
        BucketStart, RawSampleCount, FirstSample, LastSample, SourceName, SourceGrain, StatsQuality,
        TotalMinW, TotalMaxW, TotalAvgW,
        WaterHeaterMinW, WaterHeaterMaxW, WaterHeaterAvgW,
        FurnaceMinW, FurnaceMaxW, FurnaceAvgW,
        FridgeMinW, FridgeMaxW, FridgeAvgW,
        KitchenMinW, KitchenMaxW, KitchenAvgW,
        ACMinW, ACMaxW, ACAvgW,
        DryerMinW, DryerMaxW, DryerAvgW,
        EVMinW, EVMaxW, EVAvgW,
        RangeMinW, RangeMaxW, RangeAvgW,
        AllOtherMinW, AllOtherMaxW, AllOtherAvgW,
        UpstairsBedsMinW, UpstairsBedsMaxW, UpstairsBedsAvgW,
        OfficeMinW, OfficeMaxW, OfficeAvgW,
        UtilityClosetMinW, UtilityClosetMaxW, UtilityClosetAvgW,
        ClaireMiniMinW, ClaireMiniMaxW, ClaireMiniAvgW,
        VoltageMin, VoltageMax, VoltageAvg, FrequencyMin, FrequencyMax, FrequencyAvg,
        TotalWh, WaterHeaterWh, FurnaceWh, FridgeWh, KitchenWh, ACWh, DryerWh,
        EVWh, RangeWh, AllOtherWh, UpstairsBedsWh, OfficeWh, UtilityClosetWh, ClaireMiniWh
    )
    SELECT
        CAST(DATE_FORMAT(Time, '%Y-%m-%d %H:00:00') AS DATETIME),
        COUNT(*), MIN(Time), MAX(Time), 'IotaWatt', 'raw', 'raw_5s',
        MIN(Total), MAX(Total), AVG(Total),
        MIN(WaterHeater), MAX(WaterHeater), AVG(WaterHeater),
        MIN(Furnace), MAX(Furnace), AVG(Furnace),
        MIN(Fridge), MAX(Fridge), AVG(Fridge),
        MIN(Kitchen), MAX(Kitchen), AVG(Kitchen),
        MIN(ACTotal), MAX(ACTotal), AVG(ACTotal),
        MIN(DryerTotal), MAX(DryerTotal), AVG(DryerTotal),
        MIN(EVTotal), MAX(EVTotal), AVG(EVTotal),
        MIN(RangeTotal), MAX(RangeTotal), AVG(RangeTotal),
        MIN(AllOther), MAX(AllOther), AVG(AllOther),
        MIN(UpstairsBeds), MAX(UpstairsBeds), AVG(UpstairsBeds),
        MIN(Office), MAX(Office), AVG(Office),
        MIN(UtilityCloset), MAX(UtilityCloset), AVG(UtilityCloset),
        MIN(ClaireMini), MAX(ClaireMini), AVG(ClaireMini),
        MIN(Voltage), MAX(Voltage), AVG(Voltage), MIN(Frequency), MAX(Frequency), AVG(Frequency),
        SUM(IFNULL(Total,0))/720.0, SUM(IFNULL(WaterHeater,0))/720.0,
        SUM(IFNULL(Furnace,0))/720.0, SUM(IFNULL(Fridge,0))/720.0,
        SUM(IFNULL(Kitchen,0))/720.0, SUM(IFNULL(ACTotal,0))/720.0,
        SUM(IFNULL(DryerTotal,0))/720.0, SUM(IFNULL(EVTotal,0))/720.0,
        SUM(IFNULL(RangeTotal,0))/720.0, SUM(IFNULL(AllOther,0))/720.0,
        SUM(IFNULL(UpstairsBeds,0))/720.0, SUM(IFNULL(Office,0))/720.0,
        SUM(IFNULL(UtilityCloset,0))/720.0, SUM(IFNULL(ClaireMini,0))/720.0
    FROM IotaWatt
    WHERE Time >= p_start AND Time < p_end
    GROUP BY CAST(DATE_FORMAT(Time, '%Y-%m-%d %H:00:00') AS DATETIME)
    ON DUPLICATE KEY UPDATE
        RawSampleCount=VALUES(RawSampleCount), FirstSample=LEAST(FirstSample,VALUES(FirstSample)),
        LastSample=GREATEST(LastSample,VALUES(LastSample)),
        SourceGrain=IF(MinuteCount>0,'minute+raw','raw'), StatsQuality='raw_5s',
        TotalMinW=VALUES(TotalMinW), TotalMaxW=VALUES(TotalMaxW), TotalAvgW=VALUES(TotalAvgW),
        WaterHeaterMinW=VALUES(WaterHeaterMinW), WaterHeaterMaxW=VALUES(WaterHeaterMaxW), WaterHeaterAvgW=VALUES(WaterHeaterAvgW),
        FurnaceMinW=VALUES(FurnaceMinW), FurnaceMaxW=VALUES(FurnaceMaxW), FurnaceAvgW=VALUES(FurnaceAvgW),
        FridgeMinW=VALUES(FridgeMinW), FridgeMaxW=VALUES(FridgeMaxW), FridgeAvgW=VALUES(FridgeAvgW),
        KitchenMinW=VALUES(KitchenMinW), KitchenMaxW=VALUES(KitchenMaxW), KitchenAvgW=VALUES(KitchenAvgW),
        ACMinW=VALUES(ACMinW), ACMaxW=VALUES(ACMaxW), ACAvgW=VALUES(ACAvgW),
        DryerMinW=VALUES(DryerMinW), DryerMaxW=VALUES(DryerMaxW), DryerAvgW=VALUES(DryerAvgW),
        EVMinW=VALUES(EVMinW), EVMaxW=VALUES(EVMaxW), EVAvgW=VALUES(EVAvgW),
        RangeMinW=VALUES(RangeMinW), RangeMaxW=VALUES(RangeMaxW), RangeAvgW=VALUES(RangeAvgW),
        AllOtherMinW=VALUES(AllOtherMinW), AllOtherMaxW=VALUES(AllOtherMaxW), AllOtherAvgW=VALUES(AllOtherAvgW),
        UpstairsBedsMinW=VALUES(UpstairsBedsMinW), UpstairsBedsMaxW=VALUES(UpstairsBedsMaxW), UpstairsBedsAvgW=VALUES(UpstairsBedsAvgW),
        OfficeMinW=VALUES(OfficeMinW), OfficeMaxW=VALUES(OfficeMaxW), OfficeAvgW=VALUES(OfficeAvgW),
        UtilityClosetMinW=VALUES(UtilityClosetMinW), UtilityClosetMaxW=VALUES(UtilityClosetMaxW), UtilityClosetAvgW=VALUES(UtilityClosetAvgW),
        ClaireMiniMinW=VALUES(ClaireMiniMinW), ClaireMiniMaxW=VALUES(ClaireMiniMaxW), ClaireMiniAvgW=VALUES(ClaireMiniAvgW),
        VoltageMin=VALUES(VoltageMin), VoltageMax=VALUES(VoltageMax), VoltageAvg=VALUES(VoltageAvg),
        FrequencyMin=VALUES(FrequencyMin), FrequencyMax=VALUES(FrequencyMax), FrequencyAvg=VALUES(FrequencyAvg),
        TotalWh=COALESCE(TotalWh,VALUES(TotalWh)), WaterHeaterWh=COALESCE(WaterHeaterWh,VALUES(WaterHeaterWh)),
        FurnaceWh=COALESCE(FurnaceWh,VALUES(FurnaceWh)), FridgeWh=COALESCE(FridgeWh,VALUES(FridgeWh)),
        KitchenWh=COALESCE(KitchenWh,VALUES(KitchenWh)), ACWh=COALESCE(ACWh,VALUES(ACWh)),
        DryerWh=COALESCE(DryerWh,VALUES(DryerWh)), EVWh=COALESCE(EVWh,VALUES(EVWh)),
        RangeWh=COALESCE(RangeWh,VALUES(RangeWh)), AllOtherWh=COALESCE(AllOtherWh,VALUES(AllOtherWh)),
        UpstairsBedsWh=COALESCE(UpstairsBedsWh,VALUES(UpstairsBedsWh)), OfficeWh=COALESCE(OfficeWh,VALUES(OfficeWh)),
        UtilityClosetWh=COALESCE(UtilityClosetWh,VALUES(UtilityClosetWh)), ClaireMiniWh=COALESCE(ClaireMiniWh,VALUES(ClaireMiniWh));
END//

DROP PROCEDURE IF EXISTS archive_iotawatt_solar_hours//
CREATE PROCEDURE archive_iotawatt_solar_hours(IN p_start DATETIME, IN p_end DATETIME)
BEGIN
    IF p_start IS NULL OR p_end IS NULL OR p_start >= p_end THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'archive_iotawatt_solar_hours requires start < end';
    END IF;

    INSERT INTO IotaWattSolarHourly (
        BucketStart, MinuteCount, CoveragePct, FirstSample, LastSample,
        SourceName, SourceGrain, StatsQuality,
        TotalGridMinW, TotalGridMaxW, TotalGridAvgW, TotalGridWh,
        TotalSolarMinW, TotalSolarMaxW, TotalSolarAvgW, TotalSolarWh,
        TotalHeatPumpMinW, TotalHeatPumpMaxW, TotalHeatPumpAvgW, TotalHeatPumpWh
    )
    SELECT
        CAST(DATE_FORMAT(DateDTS, '%Y-%m-%d %H:00:00') AS DATETIME),
        COUNT(*), LEAST(100.00,COUNT(*)*100.0/60.0), MIN(DateDTS), MAX(DateDTS),
        'IotaWattSolar', 'minute', 'minute_average_extrema',
        MIN(TotalGridHours*60), MAX(TotalGridHours*60), SUM(TotalGridHours), SUM(TotalGridHours),
        MIN(TotalSolarHours*60), MAX(TotalSolarHours*60), SUM(TotalSolarHours), SUM(TotalSolarHours),
        MIN(TotalHeatPumpHours*60), MAX(TotalHeatPumpHours*60), SUM(TotalHeatPumpHours), SUM(TotalHeatPumpHours)
    FROM IotaWattHoursSolarbyMinute
    WHERE DateDTS >= p_start AND DateDTS < p_end
    GROUP BY CAST(DATE_FORMAT(DateDTS, '%Y-%m-%d %H:00:00') AS DATETIME)
    ON DUPLICATE KEY UPDATE
        MinuteCount=VALUES(MinuteCount), CoveragePct=VALUES(CoveragePct),
        FirstSample=VALUES(FirstSample), LastSample=VALUES(LastSample),
        SourceGrain=IF(RawSampleCount IS NULL,'minute',SourceGrain),
        StatsQuality=IF(RawSampleCount IS NULL,'minute_average_extrema',StatsQuality),
        TotalGridWh=VALUES(TotalGridWh), TotalSolarWh=VALUES(TotalSolarWh),
        TotalHeatPumpWh=VALUES(TotalHeatPumpWh);

    INSERT INTO IotaWattSolarHourly (
        BucketStart, RawSampleCount, FirstSample, LastSample, SourceName, SourceGrain, StatsQuality,
        TotalGridMinW, TotalGridMaxW, TotalGridAvgW,
        TotalSolarMinW, TotalSolarMaxW, TotalSolarAvgW,
        TotalHeatPumpMinW, TotalHeatPumpMaxW, TotalHeatPumpAvgW,
        TotalGridWh, TotalSolarWh, TotalHeatPumpWh
    )
    SELECT
        CAST(DATE_FORMAT(Time, '%Y-%m-%d %H:00:00') AS DATETIME),
        COUNT(*), MIN(Time), MAX(Time), 'IotaWattSolar', 'raw', 'raw_5s',
        MIN(TotalGrid), MAX(TotalGrid), AVG(TotalGrid),
        MIN(TotalSolar), MAX(TotalSolar), AVG(TotalSolar),
        MIN(TotalHeatPump), MAX(TotalHeatPump), AVG(TotalHeatPump),
        SUM(IFNULL(TotalGrid,0))/720.0, SUM(IFNULL(TotalSolar,0))/720.0,
        SUM(IFNULL(TotalHeatPump,0))/720.0
    FROM IotaWattSolar
    WHERE Time >= p_start AND Time < p_end
    GROUP BY CAST(DATE_FORMAT(Time, '%Y-%m-%d %H:00:00') AS DATETIME)
    ON DUPLICATE KEY UPDATE
        RawSampleCount=VALUES(RawSampleCount), FirstSample=LEAST(FirstSample,VALUES(FirstSample)),
        LastSample=GREATEST(LastSample,VALUES(LastSample)),
        SourceGrain=IF(MinuteCount>0,'minute+raw','raw'), StatsQuality='raw_5s',
        TotalGridMinW=VALUES(TotalGridMinW), TotalGridMaxW=VALUES(TotalGridMaxW), TotalGridAvgW=VALUES(TotalGridAvgW),
        TotalSolarMinW=VALUES(TotalSolarMinW), TotalSolarMaxW=VALUES(TotalSolarMaxW), TotalSolarAvgW=VALUES(TotalSolarAvgW),
        TotalHeatPumpMinW=VALUES(TotalHeatPumpMinW), TotalHeatPumpMaxW=VALUES(TotalHeatPumpMaxW), TotalHeatPumpAvgW=VALUES(TotalHeatPumpAvgW),
        TotalGridWh=COALESCE(TotalGridWh,VALUES(TotalGridWh)),
        TotalSolarWh=COALESCE(TotalSolarWh,VALUES(TotalSolarWh)),
        TotalHeatPumpWh=COALESCE(TotalHeatPumpWh,VALUES(TotalHeatPumpWh));

    INSERT INTO IotaWattSolarHourly (
        BucketStart, NetUsageSampleCount, TeslaTotalWh, TeslaGreenWh,
        SolarSelfConsumedWh, DerivedQuality,
        SourceName, SourceGrain, StatsQuality
    )
    SELECT
        CAST(DATE_FORMAT(DateDTS, '%Y-%m-%d %H:00:00') AS DATETIME),
        COUNT(*), SUM(IFNULL(TeslaTotal,0))/720.0, SUM(IFNULL(TeslaGreen,0))/720.0,
        SUM(CASE WHEN Net>0 THEN IFNULL(Consumed,0) ELSE IFNULL(Solar,0) END)/720.0,
        'legacy_netusage_5s_assumed', 'NetUsage', 'derived', 'derived_only'
    FROM NetUsage
    WHERE DateDTS >= p_start AND DateDTS < p_end
    GROUP BY CAST(DATE_FORMAT(DateDTS, '%Y-%m-%d %H:00:00') AS DATETIME)
    ON DUPLICATE KEY UPDATE
        NetUsageSampleCount=VALUES(NetUsageSampleCount), TeslaTotalWh=VALUES(TeslaTotalWh),
        TeslaGreenWh=VALUES(TeslaGreenWh), SolarSelfConsumedWh=VALUES(SolarSelfConsumedWh),
        DerivedQuality=VALUES(DerivedQuality),
        SourceName=IF(MinuteCount=0 AND RawSampleCount IS NULL,'NetUsage',SourceName),
        SourceGrain=IF(MinuteCount=0 AND RawSampleCount IS NULL,'derived',SourceGrain),
        StatsQuality=IF(MinuteCount=0 AND RawSampleCount IS NULL,'derived_only',StatsQuality);

    INSERT INTO IotaWattSolarHourly (BucketStart, Cost, SourceName, SourceGrain, StatsQuality)
    SELECT
        CAST(DATE_FORMAT(DateDTS, '%Y-%m-%d %H:00:00') AS DATETIME),
        SUM(SolarCost)/60000.0, 'SolarCostTrend', 'derived', 'derived_only'
    FROM SolarCostTrend
    WHERE DateDTS >= p_start AND DateDTS < p_end
    GROUP BY CAST(DATE_FORMAT(DateDTS, '%Y-%m-%d %H:00:00') AS DATETIME)
    ON DUPLICATE KEY UPDATE
        Cost=VALUES(Cost),
        SourceName=IF(MinuteCount=0 AND RawSampleCount IS NULL,'SolarCostTrend',SourceName),
        SourceGrain=IF(MinuteCount=0 AND RawSampleCount IS NULL,'derived',SourceGrain),
        StatsQuality=IF(MinuteCount=0 AND RawSampleCount IS NULL,'derived_only',StatsQuality);
END//

DROP PROCEDURE IF EXISTS archive_energy_power_hours//
CREATE PROCEDURE archive_energy_power_hours(IN p_start_utc DATETIME, IN p_end_utc DATETIME)
BEGIN
    DECLARE v_cutover DATETIME;
    DECLARE v_continuous_start DATETIME;

    IF p_start_utc IS NULL OR p_end_utc IS NULL OR p_start_utc >= p_end_utc THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'archive_energy_power_hours requires start < end';
    END IF;

    SELECT EnergyMinuteCutoverUTC INTO v_cutover FROM IotaWattRetentionState WHERE Id=1;

    -- Pre-cutover rows are one trustworthy whole-day solar total stored at
    -- noon UTC. Keep their original UTC key and label their grain honestly.
    INSERT INTO EnergyPowerHourly (
        BucketStartUTC, BucketStartLocal, LocalDate, UTCOffsetMinutes,
        MinuteCount, CoveragePct, FirstSampleUTC, LastSampleUTC,
        SourceName, SourceGrain, StatsQuality, SolarWh
    )
    SELECT
        DateDTS,
        CONVERT_TZ(DateDTS,'UTC','America/Denver'),
        DATE(CONVERT_TZ(DateDTS,'UTC','America/Denver')),
        TIMESTAMPDIFF(MINUTE,DateDTS,CONVERT_TZ(DateDTS,'UTC','America/Denver')),
        0, NULL, DateDTS, DateDTS,
        'EnergyPowerHoursByMinute', 'daily', 'daily_total_only', SolarWh
    FROM EnergyPowerHoursByMinute
    WHERE DateDTS >= p_start_utc AND DateDTS < p_end_utc AND DateDTS < v_cutover
      AND SolarWh IS NOT NULL AND PanelUsageWh IS NULL
    ON DUPLICATE KEY UPDATE
        BucketStartLocal=VALUES(BucketStartLocal), LocalDate=VALUES(LocalDate),
        UTCOffsetMinutes=VALUES(UTCOffsetMinutes), SourceGrain='daily',
        StatsQuality='daily_total_only', SolarWh=VALUES(SolarWh);

    SET v_continuous_start = GREATEST(p_start_utc,v_cutover);

    INSERT INTO EnergyPowerHourly (
        BucketStartUTC, BucketStartLocal, LocalDate, UTCOffsetMinutes,
        MinuteCount, CoveragePct, FirstSampleUTC, LastSampleUTC,
        SourceName, SourceGrain, StatsQuality,
        SolarMinW, SolarMaxW, SolarAvgW, SolarWh,
        EffectiveUsageMinW, EffectiveUsageMaxW, EffectiveUsageAvgW, EffectiveUsageWh,
        PanelUsageMinW, PanelUsageMaxW, PanelUsageAvgW, PanelUsageWh,
        PowerwallOutMinW, PowerwallOutMaxW, PowerwallOutAvgW, PowerwallOutWh,
        BatteryChargingMinW, BatteryChargingMaxW, BatteryChargingAvgW, BatteryChargingWh,
        HeatPumpMinW, HeatPumpMaxW, HeatPumpAvgW, HeatPumpWh,
        GridMinW, GridMaxW, GridAvgW, GridWh,
        GridImportWh, GridExportWh, SolarSelfConsumedWh
    )
    SELECT
        CAST(DATE_FORMAT(DateDTS,'%Y-%m-%d %H:00:00') AS DATETIME) BucketUTC,
        CONVERT_TZ(CAST(DATE_FORMAT(DateDTS,'%Y-%m-%d %H:00:00') AS DATETIME),'UTC','America/Denver'),
        DATE(CONVERT_TZ(CAST(DATE_FORMAT(DateDTS,'%Y-%m-%d %H:00:00') AS DATETIME),'UTC','America/Denver')),
        TIMESTAMPDIFF(MINUTE,
            CAST(DATE_FORMAT(DateDTS,'%Y-%m-%d %H:00:00') AS DATETIME),
            CONVERT_TZ(CAST(DATE_FORMAT(DateDTS,'%Y-%m-%d %H:00:00') AS DATETIME),'UTC','America/Denver')),
        COUNT(*), LEAST(100.00,COUNT(*)*100.0/60.0), MIN(DateDTS), MAX(DateDTS),
        'EnergyPowerHoursByMinute', 'minute+raw', 'minute_average_extrema',
        MIN(SolarWh*60), MAX(SolarWh*60), SUM(SolarWh), SUM(SolarWh),
        MIN(EffectiveUsageWh*60), MAX(EffectiveUsageWh*60), SUM(EffectiveUsageWh), SUM(EffectiveUsageWh),
        MIN(PanelUsageWh*60), MAX(PanelUsageWh*60), SUM(PanelUsageWh), SUM(PanelUsageWh),
        MIN(PowerwallOutWh*60), MAX(PowerwallOutWh*60), SUM(PowerwallOutWh), SUM(PowerwallOutWh),
        MIN(BatteryChargingWh*60), MAX(BatteryChargingWh*60), SUM(BatteryChargingWh), SUM(BatteryChargingWh),
        MIN(HeatPumpWh*60), MAX(HeatPumpWh*60), SUM(HeatPumpWh), SUM(HeatPumpWh),
        MIN((PanelUsageWh-SolarWh-PowerwallOutWh)*60),
        MAX((PanelUsageWh-SolarWh-PowerwallOutWh)*60),
        SUM(PanelUsageWh-SolarWh-PowerwallOutWh),
        SUM(PanelUsageWh-SolarWh-PowerwallOutWh),
        SUM(GREATEST(PanelUsageWh-SolarWh-PowerwallOutWh,0)),
        SUM(GREATEST(-(PanelUsageWh-SolarWh-PowerwallOutWh),0)),
        SUM(GREATEST(SolarWh-GREATEST(-(PanelUsageWh-SolarWh-PowerwallOutWh),0),0))
    FROM EnergyPowerHoursByMinute
    WHERE DateDTS >= v_continuous_start AND DateDTS < p_end_utc
    GROUP BY CAST(DATE_FORMAT(DateDTS,'%Y-%m-%d %H:00:00') AS DATETIME)
    ON DUPLICATE KEY UPDATE
        BucketStartLocal=VALUES(BucketStartLocal), LocalDate=VALUES(LocalDate), UTCOffsetMinutes=VALUES(UTCOffsetMinutes),
        MinuteCount=VALUES(MinuteCount), CoveragePct=VALUES(CoveragePct),
        FirstSampleUTC=VALUES(FirstSampleUTC), LastSampleUTC=VALUES(LastSampleUTC),
        SourceGrain='minute+raw', StatsQuality='minute_average_extrema',
        SolarWh=VALUES(SolarWh), EffectiveUsageWh=VALUES(EffectiveUsageWh), PanelUsageWh=VALUES(PanelUsageWh),
        PowerwallOutWh=VALUES(PowerwallOutWh), BatteryChargingWh=VALUES(BatteryChargingWh), HeatPumpWh=VALUES(HeatPumpWh),
        GridWh=VALUES(GridWh), GridImportWh=VALUES(GridImportWh), GridExportWh=VALUES(GridExportWh),
        SolarSelfConsumedWh=VALUES(SolarSelfConsumedWh);

    INSERT INTO EnergyPowerHourly (
        BucketStartUTC, BucketStartLocal, LocalDate, UTCOffsetMinutes,
        RawSampleCount, FirstSampleUTC, LastSampleUTC,
        SourceName, SourceGrain, StatsQuality,
        SolarMinW, SolarMaxW, SolarAvgW,
        EffectiveUsageMinW, EffectiveUsageMaxW, EffectiveUsageAvgW,
        PanelUsageMinW, PanelUsageMaxW, PanelUsageAvgW,
        PowerwallOutMinW, PowerwallOutMaxW, PowerwallOutAvgW,
        BatteryChargingMinW, BatteryChargingMaxW, BatteryChargingAvgW,
        HeatPumpMinW, HeatPumpMaxW, HeatPumpAvgW,
        GridMinW, GridMaxW, GridAvgW
    )
    SELECT
        CAST(DATE_FORMAT(Time,'%Y-%m-%d %H:00:00') AS DATETIME) BucketUTC,
        CONVERT_TZ(CAST(DATE_FORMAT(Time,'%Y-%m-%d %H:00:00') AS DATETIME),'UTC','America/Denver'),
        DATE(CONVERT_TZ(CAST(DATE_FORMAT(Time,'%Y-%m-%d %H:00:00') AS DATETIME),'UTC','America/Denver')),
        TIMESTAMPDIFF(MINUTE,
            CAST(DATE_FORMAT(Time,'%Y-%m-%d %H:00:00') AS DATETIME),
            CONVERT_TZ(CAST(DATE_FORMAT(Time,'%Y-%m-%d %H:00:00') AS DATETIME),'UTC','America/Denver')),
        COUNT(*), MIN(Time), MAX(Time), 'EnergyPowerRaw', 'minute+raw', 'raw_15s',
        MIN(SolarW), MAX(SolarW), AVG(SolarW),
        MIN(EffectiveUsageW), MAX(EffectiveUsageW), AVG(EffectiveUsageW),
        MIN(PanelUsageW), MAX(PanelUsageW), AVG(PanelUsageW),
        MIN(PowerwallOutW), MAX(PowerwallOutW), AVG(PowerwallOutW),
        MIN(BatteryChargingW), MAX(BatteryChargingW), AVG(BatteryChargingW),
        MIN(HeatPumpW), MAX(HeatPumpW), AVG(HeatPumpW),
        MIN(PanelUsageW-SolarW-PowerwallOutW),
        MAX(PanelUsageW-SolarW-PowerwallOutW),
        AVG(PanelUsageW-SolarW-PowerwallOutW)
    FROM EnergyPowerRaw
    WHERE Time >= v_continuous_start AND Time < p_end_utc
    GROUP BY CAST(DATE_FORMAT(Time,'%Y-%m-%d %H:00:00') AS DATETIME)
    ON DUPLICATE KEY UPDATE
        RawSampleCount=VALUES(RawSampleCount), FirstSampleUTC=LEAST(FirstSampleUTC,VALUES(FirstSampleUTC)),
        LastSampleUTC=GREATEST(LastSampleUTC,VALUES(LastSampleUTC)), StatsQuality='raw_15s',
        SolarMinW=VALUES(SolarMinW), SolarMaxW=VALUES(SolarMaxW), SolarAvgW=VALUES(SolarAvgW),
        EffectiveUsageMinW=VALUES(EffectiveUsageMinW), EffectiveUsageMaxW=VALUES(EffectiveUsageMaxW), EffectiveUsageAvgW=VALUES(EffectiveUsageAvgW),
        PanelUsageMinW=VALUES(PanelUsageMinW), PanelUsageMaxW=VALUES(PanelUsageMaxW), PanelUsageAvgW=VALUES(PanelUsageAvgW),
        PowerwallOutMinW=VALUES(PowerwallOutMinW), PowerwallOutMaxW=VALUES(PowerwallOutMaxW), PowerwallOutAvgW=VALUES(PowerwallOutAvgW),
        BatteryChargingMinW=VALUES(BatteryChargingMinW), BatteryChargingMaxW=VALUES(BatteryChargingMaxW), BatteryChargingAvgW=VALUES(BatteryChargingAvgW),
        HeatPumpMinW=VALUES(HeatPumpMinW), HeatPumpMaxW=VALUES(HeatPumpMaxW), HeatPumpAvgW=VALUES(HeatPumpAvgW),
        GridMinW=VALUES(GridMinW), GridMaxW=VALUES(GridMaxW), GridAvgW=VALUES(GridAvgW);
END//

DROP PROCEDURE IF EXISTS maintain_iotawatt_archive//
CREATE PROCEDURE maintain_iotawatt_archive()
BEGIN
    DECLARE v_run_id BIGINT UNSIGNED DEFAULT NULL;
    DECLARE v_iota_end DATETIME;
    DECLARE v_solar_end DATETIME;
    DECLARE v_energy_end DATETIME;
    DECLARE v_message TEXT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_message = MESSAGE_TEXT;
        IF v_run_id IS NOT NULL THEN
            UPDATE maintenance_runs
            SET finished_at=CURRENT_TIMESTAMP, status='error', note=LEFT(v_message,1000)
            WHERE id=v_run_id;
        END IF;
        RESIGNAL;
    END;

    INSERT INTO maintenance_runs(job_name,status,note)
    VALUES('maintain_iotawatt_archive','running','Archive-only Phase 2 maintenance; pruning is disabled');
    SET v_run_id=LAST_INSERT_ID();

    SELECT CAST(DATE_FORMAT(MAX(DateDTS),'%Y-%m-%d %H:00:00') AS DATETIME)
      INTO v_iota_end FROM IotaWattHoursbyMinute;
    SELECT CAST(DATE_FORMAT(MAX(DateDTS),'%Y-%m-%d %H:00:00') AS DATETIME)
      INTO v_solar_end FROM IotaWattHoursSolarbyMinute;
    SELECT CAST(DATE_FORMAT(MAX(DateDTS),'%Y-%m-%d %H:00:00') AS DATETIME)
      INTO v_energy_end FROM EnergyPowerHoursByMinute;

    CALL archive_iotawatt_hours(v_iota_end-INTERVAL 3 DAY,v_iota_end);
    CALL archive_iotawatt_solar_hours(v_solar_end-INTERVAL 3 DAY,v_solar_end);
    CALL archive_energy_power_hours(v_energy_end-INTERVAL 3 DAY,v_energy_end);

    UPDATE IotaWattRetentionState
    SET LastArchiveSuccessUTC=UTC_TIMESTAMP(),
        Note='Phase 2 archive maintenance succeeded; pruning remains locked'
    WHERE Id=1;

    UPDATE maintenance_runs
    SET finished_at=CURRENT_TIMESTAMP, status='success',
        rows_affected=(SELECT COUNT(*) FROM IotaWattHourly WHERE BucketStart>=v_iota_end-INTERVAL 3 DAY)
                    +(SELECT COUNT(*) FROM IotaWattSolarHourly WHERE BucketStart>=v_solar_end-INTERVAL 3 DAY)
                    +(SELECT COUNT(*) FROM EnergyPowerHourly WHERE BucketStartUTC>=v_energy_end-INTERVAL 3 DAY),
        note=CONCAT('Archive-only through IotaWatt ',v_iota_end,
                    ', solar ',v_solar_end,', EnergyPower UTC ',v_energy_end)
    WHERE id=v_run_id;
END//

DROP PROCEDURE IF EXISTS assert_iotawatt_pruning_ready//
CREATE PROCEDURE assert_iotawatt_pruning_ready()
BEGIN
    DECLARE v_ready INT DEFAULT 0;
    SELECT GrafanaMigrated AND ConsumersMigrated AND PruningEnabled
      INTO v_ready FROM IotaWattRetentionState WHERE Id=1;
    IF IFNULL(v_ready,0) <> 1 THEN
        SIGNAL SQLSTATE '45000'
          SET MESSAGE_TEXT='IotaWatt pruning locked: GrafanaMigrated, ConsumersMigrated, and PruningEnabled must all be true';
    END IF;
END//

DELIMITER ;

CREATE OR REPLACE VIEW v_iotawatt_daily AS
SELECT
    DATE(BucketStart) LocalDate,
    COUNT(*) HourCount,
    SUM(MinuteCount) MinuteCount,
    ROUND(SUM(MinuteCount)*100.0/1440.0,2) CoveragePct,
    SUM(TotalWh) TotalWh,
    SUM(WaterHeaterWh) WaterHeaterWh,
    SUM(FurnaceWh) FurnaceWh,
    SUM(FridgeWh) FridgeWh,
    SUM(KitchenWh) KitchenWh,
    SUM(ACWh) ACWh,
    SUM(DryerWh) DryerWh,
    SUM(EVWh) EVWh,
    SUM(RangeWh) RangeWh,
    SUM(AllOtherWh) AllOtherWh,
    SUM(UpstairsBedsWh) UpstairsBedsWh,
    SUM(OfficeWh) OfficeWh,
    SUM(UtilityClosetWh) UtilityClosetWh,
    SUM(ClaireMiniWh) ClaireMiniWh
FROM IotaWattHourly
GROUP BY DATE(BucketStart);

CREATE OR REPLACE VIEW v_iotawatt_solar_daily AS
SELECT
    DATE(BucketStart) LocalDate,
    COUNT(*) HourCount,
    SUM(MinuteCount) MinuteCount,
    ROUND(SUM(MinuteCount)*100.0/1440.0,2) CoveragePct,
    SUM(TotalGridWh) TotalGridWh,
    SUM(TotalSolarWh) TotalSolarWh,
    SUM(TotalHeatPumpWh) TotalHeatPumpWh,
    SUM(TeslaTotalWh) TeslaTotalWh,
    SUM(TeslaGreenWh) TeslaGreenWh,
    SUM(Cost) Cost,
    SUM(GridImportWh) GridImportWh,
    SUM(GridExportWh) GridExportWh,
    SUM(SolarSelfConsumedWh) SolarSelfConsumedWh
FROM IotaWattSolarHourly
GROUP BY DATE(BucketStart);

CREATE OR REPLACE VIEW v_energy_power_daily AS
SELECT
    LocalDate,
    SUM(SourceGrain='minute+raw') HourCount,
    SUM(MinuteCount) MinuteCount,
    CASE WHEN SUM(MinuteCount)>0 THEN ROUND(SUM(MinuteCount)*100.0/1440.0,2) ELSE NULL END CoveragePct,
    MAX(SourceGrain='daily') HasDailyBackfill,
    SUM(SolarWh) SolarWh,
    SUM(EffectiveUsageWh) EffectiveUsageWh,
    SUM(PanelUsageWh) PanelUsageWh,
    SUM(PowerwallOutWh) PowerwallOutWh,
    SUM(BatteryChargingWh) BatteryChargingWh,
    SUM(HeatPumpWh) HeatPumpWh,
    SUM(GridWh) GridWh,
    SUM(GridImportWh) GridImportWh,
    SUM(GridExportWh) GridExportWh,
    SUM(SolarSelfConsumedWh) SolarSelfConsumedWh
FROM EnergyPowerHourly
GROUP BY LocalDate;

CREATE OR REPLACE VIEW v_iotawatt_pruning_readiness AS
SELECT
    Id,
    RawRetentionDays,
    MinuteRetentionDays,
    GrafanaMigrated,
    ConsumersMigrated,
    PruningEnabled,
    (GrafanaMigrated AND ConsumersMigrated AND PruningEnabled) ReadyToPrune,
    LastArchiveSuccessUTC,
    LastValidationUTC,
    Note,
    UpdatedAt
FROM IotaWattRetentionState;
