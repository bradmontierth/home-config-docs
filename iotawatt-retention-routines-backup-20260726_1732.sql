/*!999999\- enable the sandbox mode */ 
-- MariaDB dump 10.19-11.4.2-MariaDB, for debian-linux-gnu (x86_64)
--
-- Host: localhost    Database: hubitat_logging
-- ------------------------------------------------------
-- Server version	11.4.2-MariaDB-ubu2404

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*M!100616 SET @OLD_NOTE_VERBOSITY=@@NOTE_VERBOSITY, NOTE_VERBOSITY=0 */;

--
-- Dumping routines for database 'hubitat_logging'
--
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `archive_energy_power_hours` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `archive_energy_power_hours`(IN p_start_utc DATETIME, IN p_end_utc DATETIME)
BEGIN
    DECLARE v_cutover DATETIME;
    DECLARE v_continuous_start DATETIME;

    IF p_start_utc IS NULL OR p_end_utc IS NULL OR p_start_utc >= p_end_utc THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'archive_energy_power_hours requires start < end';
    END IF;

    SELECT EnergyMinuteCutoverUTC INTO v_cutover FROM IotaWattRetentionState WHERE Id=1;

    
    
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
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `archive_iotawatt_hours` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `archive_iotawatt_hours`(IN p_start DATETIME, IN p_end DATETIME)
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
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `archive_iotawatt_solar_hours` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `archive_iotawatt_solar_hours`(IN p_start DATETIME, IN p_end DATETIME)
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
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `archive_weather_guarded` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `archive_weather_guarded`()
BEGIN
    DECLARE v_days INT UNSIGNED;
    DECLARE v_cutoff DATETIME;
    DECLARE v_run_id CHAR(36);

    SELECT WeatherRetentionDays INTO v_days
    FROM WeatherRetentionState WHERE Id=1;
    SET v_cutoff=CAST(DATE_FORMAT(UTC_TIMESTAMP()-INTERVAL v_days DAY,
                                 '%Y-%m-%d %H:00:00') AS DATETIME);
    SET v_run_id=UUID();
    CALL archive_weather_to_cutoff(v_cutoff,v_run_id);
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `archive_weather_to_cutoff` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `archive_weather_to_cutoff`(
    IN p_cutoff_utc DATETIME,
    IN p_run_id CHAR(36)
)
BEGIN
    DECLARE v_audit_id BIGINT UNSIGNED DEFAULT NULL;
    DECLARE v_rows BIGINT DEFAULT 0;
    DECLARE v_error TEXT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_error=MESSAGE_TEXT;
        IF v_audit_id IS NOT NULL THEN
            UPDATE WeatherRetentionAudit
            SET Status='failed',FinishedAtUTC=UTC_TIMESTAMP(),Note=LEFT(v_error,1000)
            WHERE Id=v_audit_id;
        END IF;
        RESIGNAL;
    END;

    INSERT INTO WeatherRetentionAudit
        (RunId,Mode,TargetName,CutoffUTC,Status,StartedAtUTC,Note)
    VALUES
        (p_run_id,'archive','weather',p_cutoff_utc,'running',UTC_TIMESTAMP(),
         'Idempotent hourly min/max/average archive');
    SET v_audit_id=LAST_INSERT_ID();

    INSERT INTO weather_hourly
        (name,bucket_start,sample_count,min_value,max_value,avg_value,
         first_value,last_value,first_created,last_created)
    SELECT
        name,
        FROM_UNIXTIME(FLOOR(UNIX_TIMESTAMP(created)/3600)*3600),
        COUNT(*),
        MIN(value),
        MAX(value),
        AVG(value),
        NULL,
        NULL,
        MIN(created),
        MAX(created)
    FROM weather
    WHERE created < p_cutoff_utc
    GROUP BY name,FROM_UNIXTIME(FLOOR(UNIX_TIMESTAMP(created)/3600)*3600)
    ON DUPLICATE KEY UPDATE
        sample_count=VALUES(sample_count),
        min_value=VALUES(min_value),
        max_value=VALUES(max_value),
        avg_value=VALUES(avg_value),
        first_created=VALUES(first_created),
        last_created=VALUES(last_created),
        updated_at=UTC_TIMESTAMP();

    SET v_rows=ROW_COUNT();
    UPDATE WeatherRetentionAudit
    SET RowsAffected=v_rows,Status='success',FinishedAtUTC=UTC_TIMESTAMP()
    WHERE Id=v_audit_id;
    UPDATE WeatherRetentionState
    SET LastArchiveSuccessUTC=UTC_TIMESTAMP(),
        LastNote=CONCAT('Weather archive completed through ',p_cutoff_utc,' UTC')
    WHERE Id=1;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `assert_iotawatt_pruning_ready` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `assert_iotawatt_pruning_ready`()
BEGIN
    DECLARE v_ready INT DEFAULT 0;
    SELECT GrafanaMigrated AND ConsumersMigrated AND PruningEnabled
      INTO v_ready FROM IotaWattRetentionState WHERE Id=1;
    IF IFNULL(v_ready,0) <> 1 THEN
        SIGNAL SQLSTATE '45000'
          SET MESSAGE_TEXT='IotaWatt pruning locked: GrafanaMigrated, ConsumersMigrated, and PruningEnabled must all be true';
    END IF;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `assert_weather_pruning_ready` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `assert_weather_pruning_ready`()
BEGIN
    DECLARE v_consumers TINYINT DEFAULT 0;
    DECLARE v_enabled TINYINT DEFAULT 0;

    SELECT ConsumersMigrated,PruningEnabled
    INTO v_consumers,v_enabled
    FROM WeatherRetentionState WHERE Id=1;

    IF v_consumers<>1 OR v_enabled<>1 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT='Weather/device pruning is locked by WeatherRetentionState';
    END IF;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `dry_run_iotawatt_pruning` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `dry_run_iotawatt_pruning`()
BEGIN
    DECLARE v_run CHAR(36) DEFAULT UUID();
    DECLARE v_table VARCHAR(64);
    DECLARE v_cutoff DATETIME;
    DECLARE v_eligible,v_missing,v_mismatch BIGINT DEFAULT 0;
    DECLARE v_done INT DEFAULT 0;
    DECLARE c CURSOR FOR
        SELECT TableName FROM (
            SELECT 1 n,'EnergyPowerRaw' TableName UNION ALL
            SELECT 2,'SolarCostTrend' UNION ALL
            SELECT 3,'EnergyPowerHoursByMinute' UNION ALL
            SELECT 4,'IotaWattHoursbyMinute' UNION ALL
            SELECT 5,'IotaWattHoursSolarbyMinute' UNION ALL
            SELECT 6,'NetUsage' UNION ALL
            SELECT 7,'IotaWattSolar' UNION ALL
            SELECT 8,'IotaWatt'
        ) q ORDER BY n;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done=1;

    OPEN c;
    read_loop: LOOP
        FETCH c INTO v_table;
        IF v_done=1 THEN LEAVE read_loop; END IF;
        CALL validate_iotawatt_prune_table(v_table,v_cutoff,v_eligible,v_missing,v_mismatch);
        INSERT INTO IotaWattPruneAudit
            (RunId,Mode,TableName,Cutoff,EligibleRows,MissingArchiveGroups,
             MismatchedArchiveGroups,Status,StartedAtUTC,FinishedAtUTC,Note)
        VALUES
            (v_run,'dry-run',v_table,v_cutoff,v_eligible,v_missing,v_mismatch,
             IF(v_missing=0 AND v_mismatch=0,'validated','blocked'),
             UTC_TIMESTAMP(),UTC_TIMESTAMP(),'No rows deleted');
    END LOOP;
    CLOSE c;

    UPDATE IotaWattRetentionState
    SET LastPruneValidationUTC=UTC_TIMESTAMP(),
        LastPruneNote=CONCAT('Dry run ',v_run)
    WHERE Id=1;

    SELECT RunId,TableName,Cutoff,EligibleRows,MissingArchiveGroups,
           MismatchedArchiveGroups,Status
    FROM IotaWattPruneAudit WHERE RunId=v_run ORDER BY Id;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `dry_run_weather_retention` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `dry_run_weather_retention`()
BEGIN
    DECLARE v_days INT UNSIGNED;
    DECLARE v_cutoff DATETIME;
    DECLARE v_eligible BIGINT DEFAULT 0;
    DECLARE v_groups BIGINT DEFAULT 0;
    DECLARE v_missing BIGINT DEFAULT 0;
    DECLARE v_mismatched BIGINT DEFAULT 0;
    DECLARE v_run_id CHAR(36);

    SELECT WeatherRetentionDays INTO v_days
    FROM WeatherRetentionState WHERE Id=1;
    SET v_cutoff=CAST(DATE_FORMAT(UTC_TIMESTAMP()-INTERVAL v_days DAY,
                                 '%Y-%m-%d %H:00:00') AS DATETIME);
    SET v_run_id=UUID();

    CALL validate_weather_archive(
        v_cutoff,v_eligible,v_groups,v_missing,v_mismatched
    );

    INSERT INTO WeatherRetentionAudit
        (RunId,Mode,TargetName,CutoffUTC,EligibleRows,SourceGroups,
         MissingArchiveGroups,MismatchedArchiveGroups,Status,
         StartedAtUTC,FinishedAtUTC,Note)
    VALUES
        (v_run_id,'dry-run','weather',v_cutoff,v_eligible,v_groups,
         v_missing,v_mismatched,
         IF(v_missing=0 AND v_mismatched=0,'validated','blocked'),
         UTC_TIMESTAMP(),UTC_TIMESTAMP(),'No rows deleted');

    UPDATE WeatherRetentionState
    SET LastValidationUTC=UTC_TIMESTAMP(),
        LastNote=CONCAT('Weather dry run: eligible=',v_eligible,
                        ', groups=',v_groups,', missing=',v_missing,
                        ', mismatched=',v_mismatched)
    WHERE Id=1;

    SELECT v_run_id RunId,v_cutoff CutoffUTC,v_eligible EligibleRows,
           v_groups SourceGroups,v_missing MissingArchiveGroups,
           v_mismatched MismatchedArchiveGroups,
           IF(v_missing=0 AND v_mismatched=0,'validated','blocked') Status;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `maintain_iotawatt_archive` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `maintain_iotawatt_archive`()
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
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `maintain_iotawatt_retention` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `maintain_iotawatt_retention`()
BEGIN
    DECLARE v_run_id BIGINT UNSIGNED DEFAULT NULL;
    DECLARE v_error TEXT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_error=MESSAGE_TEXT;
        IF v_run_id IS NOT NULL THEN
            UPDATE maintenance_runs SET status='failed',finished_at=CURRENT_TIMESTAMP,
                note=LEFT(v_error,1000) WHERE id=v_run_id;
        END IF;
        RESIGNAL;
    END;

    INSERT INTO maintenance_runs(job_name,started_at,status,note)
    VALUES('maintain_iotawatt_retention',CURRENT_TIMESTAMP,'running','archive then guarded prune');
    SET v_run_id=LAST_INSERT_ID();

    CALL maintain_iotawatt_archive();
    CALL assert_iotawatt_pruning_ready();
    CALL prune_iotawatt_table('EnergyPowerRaw',25000,200000,60);
    CALL prune_iotawatt_table('SolarCostTrend',25000,200000,60);
    CALL prune_iotawatt_table('EnergyPowerHoursByMinute',25000,200000,60);
    CALL prune_iotawatt_table('IotaWattHoursbyMinute',25000,200000,60);
    CALL prune_iotawatt_table('IotaWattHoursSolarbyMinute',25000,200000,60);
    CALL prune_iotawatt_table('NetUsage',25000,200000,60);
    CALL prune_iotawatt_table('IotaWattSolar',25000,200000,60);
    CALL prune_iotawatt_table('IotaWatt',25000,200000,60);

    UPDATE IotaWattRetentionState
    SET LastPruneSuccessUTC=UTC_TIMESTAMP(),
        LastPruneNote='Daily archive and all guarded prune steps succeeded'
    WHERE Id=1;
    UPDATE maintenance_runs SET status='success',finished_at=CURRENT_TIMESTAMP,
        note='Archive and all guarded prune steps succeeded' WHERE id=v_run_id;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `maintain_weather_device_retention` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `maintain_weather_device_retention`()
BEGIN
    DECLARE v_run_id BIGINT UNSIGNED DEFAULT NULL;
    DECLARE v_audit_run CHAR(36);
    DECLARE v_weather_days INT UNSIGNED;
    DECLARE v_device_days INT UNSIGNED;
    DECLARE v_weather_cutoff DATETIME;
    DECLARE v_device_cutoff DATETIME;
    DECLARE v_eligible BIGINT DEFAULT 0;
    DECLARE v_groups BIGINT DEFAULT 0;
    DECLARE v_missing BIGINT DEFAULT 0;
    DECLARE v_mismatched BIGINT DEFAULT 0;
    DECLARE v_weather_remaining BIGINT DEFAULT 0;
    DECLARE v_device_remaining BIGINT DEFAULT 0;
    DECLARE v_lock INT DEFAULT 0;
    DECLARE v_error TEXT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_error=MESSAGE_TEXT;
        IF v_run_id IS NOT NULL THEN
            UPDATE maintenance_runs
            SET status='failed',finished_at=UTC_TIMESTAMP(),note=LEFT(v_error,1000)
            WHERE id=v_run_id;
        END IF;
        IF v_lock=1 THEN
            DO RELEASE_LOCK('weather_device_guarded_retention');
        END IF;
        RESIGNAL;
    END;

    SELECT GET_LOCK('weather_device_guarded_retention',0) INTO v_lock;
    IF v_lock<>1 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT='Weather/device retention is already running';
    END IF;

    INSERT INTO maintenance_runs(job_name,started_at,status,note)
    VALUES('maintain_weather_device_retention',UTC_TIMESTAMP(),'running',
           'Archive and validate weather, then guarded weather/device prune');
    SET v_run_id=LAST_INSERT_ID();
    SET v_audit_run=UUID();

    SELECT WeatherRetentionDays,DeviceRetentionDays
    INTO v_weather_days,v_device_days
    FROM WeatherRetentionState WHERE Id=1;
    SET v_weather_cutoff=CAST(
        DATE_FORMAT(UTC_TIMESTAMP()-INTERVAL v_weather_days DAY,
                    '%Y-%m-%d %H:00:00') AS DATETIME
    );
    SET v_device_cutoff=UTC_TIMESTAMP()-INTERVAL v_device_days DAY;

    CALL archive_weather_to_cutoff(v_weather_cutoff,v_audit_run);
    CALL validate_weather_archive(
        v_weather_cutoff,v_eligible,v_groups,v_missing,v_mismatched
    );
    INSERT INTO WeatherRetentionAudit
        (RunId,Mode,TargetName,CutoffUTC,EligibleRows,SourceGroups,
         MissingArchiveGroups,MismatchedArchiveGroups,Status,
         StartedAtUTC,FinishedAtUTC,Note)
    VALUES
        (v_audit_run,'validate','weather',v_weather_cutoff,
         v_eligible,v_groups,v_missing,v_mismatched,
         IF(v_missing=0 AND v_mismatched=0,'validated','blocked'),
         UTC_TIMESTAMP(),UTC_TIMESTAMP(),'Daily pre-prune validation');
    UPDATE WeatherRetentionState
    SET LastValidationUTC=UTC_TIMESTAMP()
    WHERE Id=1;

    IF v_missing<>0 OR v_mismatched<>0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT='Daily weather archive validation failed';
    END IF;

    CALL assert_weather_pruning_ready();
    CALL prune_weather_to_cutoff(
        v_weather_cutoff,250000,60,v_audit_run
    );
    CALL prune_device_monitoring_to_cutoff(
        v_device_cutoff,50000,250000,60,v_audit_run
    );

    SELECT COUNT(*) INTO v_weather_remaining
    FROM weather WHERE created<v_weather_cutoff;
    SELECT COUNT(*) INTO v_device_remaining
    FROM DeviceMonitoring WHERE Created<v_device_cutoff;

    IF v_weather_remaining<>0 OR v_device_remaining<>0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT='Daily retention bounds exhausted before completion';
    END IF;

    UPDATE WeatherRetentionState
    SET LastFullSuccessUTC=UTC_TIMESTAMP(),
        LastNote=CONCAT('Daily weather/device retention succeeded; weather cutoff=',
                        v_weather_cutoff,' UTC, device cutoff=',
                        v_device_cutoff,' UTC')
    WHERE Id=1;
    UPDATE maintenance_runs
    SET status='success',finished_at=UTC_TIMESTAMP(),
        note=CONCAT('Weather cutoff=',v_weather_cutoff,
                    ' UTC; device cutoff=',v_device_cutoff,' UTC')
    WHERE id=v_run_id;
    DO RELEASE_LOCK('weather_device_guarded_retention');
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `maintain_weather_retention` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`%` PROCEDURE `maintain_weather_retention`()
BEGIN
  CALL rollup_weather_hourly(14);
  CALL prune_weather_raw(14);
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `prune_device_monitoring` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`%` PROCEDURE `prune_device_monitoring`(IN p_retention_days INT)
BEGIN
  DECLARE v_rows INT DEFAULT 1;
  DECLARE v_total BIGINT DEFAULT 0;
  DECLARE v_run_id BIGINT;
  INSERT INTO maintenance_runs(job_name, note) VALUES ('prune_device_monitoring', CONCAT('retention_days=', p_retention_days));
  SET v_run_id = LAST_INSERT_ID();
  WHILE v_rows > 0 DO
    DELETE FROM DeviceMonitoring
    WHERE Created < NOW() - INTERVAL p_retention_days DAY
    LIMIT 50000;
    SET v_rows = ROW_COUNT();
    SET v_total = v_total + v_rows;
    DO SLEEP(0.05);
  END WHILE;
  UPDATE maintenance_runs SET finished_at=NOW(), status='success', rows_affected=v_total WHERE id=v_run_id;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `prune_device_monitoring_guarded` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `prune_device_monitoring_guarded`(
    IN p_batch_rows INT UNSIGNED,
    IN p_max_rows BIGINT UNSIGNED,
    IN p_max_seconds INT UNSIGNED
)
BEGIN
    DECLARE v_days INT UNSIGNED;
    DECLARE v_cutoff DATETIME;

    SELECT DeviceRetentionDays INTO v_days
    FROM WeatherRetentionState WHERE Id=1;
    SET v_cutoff=UTC_TIMESTAMP()-INTERVAL v_days DAY;
    CALL prune_device_monitoring_to_cutoff(
        v_cutoff,p_batch_rows,p_max_rows,p_max_seconds,UUID()
    );
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `prune_device_monitoring_to_cutoff` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `prune_device_monitoring_to_cutoff`(
    IN p_cutoff_utc DATETIME,
    IN p_batch_rows INT UNSIGNED,
    IN p_max_rows BIGINT UNSIGNED,
    IN p_max_seconds INT UNSIGNED,
    IN p_run_id CHAR(36)
)
BEGIN
    DECLARE v_audit_id BIGINT UNSIGNED DEFAULT NULL;
    DECLARE v_started DATETIME;
    DECLARE v_eligible BIGINT DEFAULT 0;
    DECLARE v_deleted BIGINT DEFAULT 0;
    DECLARE v_rows BIGINT DEFAULT 1;
    DECLARE v_remaining BIGINT DEFAULT 0;
    DECLARE v_limit INT UNSIGNED;
    DECLARE v_error TEXT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_error=MESSAGE_TEXT;
        ROLLBACK;
        IF v_audit_id IS NOT NULL THEN
            UPDATE WeatherRetentionAudit
            SET RowsAffected=v_deleted,Status='failed',
                FinishedAtUTC=UTC_TIMESTAMP(),Note=LEFT(v_error,1000)
            WHERE Id=v_audit_id;
        END IF;
        RESIGNAL;
    END;

    IF p_batch_rows<1000 OR p_batch_rows>100000
       OR p_max_rows<1 OR p_max_seconds<1 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT='Device prune bounds are invalid';
    END IF;

    CALL assert_weather_pruning_ready();
    SET v_started=UTC_TIMESTAMP();
    SELECT COUNT(*) INTO v_eligible
    FROM DeviceMonitoring WHERE Created<p_cutoff_utc;

    INSERT INTO WeatherRetentionAudit
        (RunId,Mode,TargetName,CutoffUTC,EligibleRows,Status,
         StartedAtUTC,Note)
    VALUES
        (p_run_id,'prune','DeviceMonitoring',p_cutoff_utc,v_eligible,
         'running',v_started,
         CONCAT('batch=',p_batch_rows,', max_rows=',p_max_rows,
                ', max_seconds=',p_max_seconds));
    SET v_audit_id=LAST_INSERT_ID();

    prune_loop: WHILE v_rows>0
      AND v_deleted<p_max_rows
      AND TIMESTAMPDIFF(SECOND,v_started,UTC_TIMESTAMP())<p_max_seconds DO
        SET v_limit=LEAST(p_batch_rows,p_max_rows-v_deleted);
        START TRANSACTION;
        DELETE FROM DeviceMonitoring
        WHERE Created<p_cutoff_utc
        ORDER BY Created,ID
        LIMIT v_limit;
        SET v_rows=ROW_COUNT();
        COMMIT;
        SET v_deleted=v_deleted+v_rows;
        DO SLEEP(0.02);
    END WHILE;

    SELECT COUNT(*) INTO v_remaining
    FROM DeviceMonitoring WHERE Created<p_cutoff_utc;

    UPDATE WeatherRetentionAudit
    SET RowsAffected=v_deleted,RemainingRows=v_remaining,
        Status=IF(v_remaining=0,'success','partial'),
        FinishedAtUTC=UTC_TIMESTAMP()
    WHERE Id=v_audit_id;

    UPDATE WeatherRetentionState
    SET LastDevicePruneSuccessUTC=
            IF(v_remaining=0,UTC_TIMESTAMP(),LastDevicePruneSuccessUTC),
        LastNote=CONCAT('DeviceMonitoring prune deleted=',v_deleted,
                        ', remaining=',v_remaining,
                        ', cutoff=',p_cutoff_utc,' UTC')
    WHERE Id=1;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `prune_iotawatt_table` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `prune_iotawatt_table`(
    IN p_table VARCHAR(64),
    IN p_batch_size INT UNSIGNED,
    IN p_max_rows BIGINT UNSIGNED,
    IN p_max_seconds INT UNSIGNED
)
BEGIN
    DECLARE v_run CHAR(36) DEFAULT UUID();
    DECLARE v_cutoff DATETIME;
    DECLARE v_eligible,v_missing,v_mismatch BIGINT DEFAULT 0;
    DECLARE v_deleted,v_batch,v_remaining BIGINT DEFAULT 0;
    DECLARE v_batch_target INT UNSIGNED;
    DECLARE v_started DATETIME DEFAULT UTC_TIMESTAMP();
    DECLARE v_audit_id,v_maintenance_id BIGINT UNSIGNED DEFAULT NULL;
    DECLARE v_error TEXT;

    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_error=MESSAGE_TEXT;
        ROLLBACK;
        IF v_audit_id IS NOT NULL THEN
            UPDATE IotaWattPruneAudit SET Status='failed',RowsDeleted=v_deleted,
                FinishedAtUTC=UTC_TIMESTAMP(),Note=LEFT(v_error,1000) WHERE Id=v_audit_id;
        END IF;
        IF v_maintenance_id IS NOT NULL THEN
            UPDATE maintenance_runs SET status='failed',finished_at=CURRENT_TIMESTAMP,
                rows_affected=v_deleted,note=LEFT(v_error,1000) WHERE id=v_maintenance_id;
        END IF;
        RESIGNAL;
    END;

    IF p_batch_size<1000 OR p_batch_size>100000 OR p_max_rows<1 OR p_max_seconds<1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Invalid prune batch, row, or time limit';
    END IF;

    CALL assert_iotawatt_pruning_ready();
    CALL validate_iotawatt_prune_table(p_table,v_cutoff,v_eligible,v_missing,v_mismatch);
    IF v_cutoff IS NULL OR v_missing<>0 OR v_mismatch<>0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Prune validation failed: archive missing or mismatched';
    END IF;

    INSERT INTO IotaWattPruneAudit
        (RunId,Mode,TableName,Cutoff,EligibleRows,MissingArchiveGroups,
         MismatchedArchiveGroups,Status,StartedAtUTC,Note)
    VALUES (v_run,'execute',p_table,v_cutoff,v_eligible,v_missing,v_mismatch,
            'running',v_started,'Guarded batched deletion');
    SET v_audit_id=LAST_INSERT_ID();

    INSERT INTO maintenance_runs(job_name,started_at,status,note)
    VALUES (CONCAT('prune_',p_table),CURRENT_TIMESTAMP,'running',
            CONCAT('cutoff=',v_cutoff,', eligible=',v_eligible));
    SET v_maintenance_id=LAST_INSERT_ID();

    prune_loop: LOOP
        IF v_deleted>=p_max_rows OR TIMESTAMPDIFF(SECOND,v_started,UTC_TIMESTAMP())>=p_max_seconds THEN
            LEAVE prune_loop;
        END IF;
        SET v_batch_target=LEAST(p_batch_size,p_max_rows-v_deleted);

        IF p_table='IotaWatt' THEN
            DELETE FROM IotaWatt WHERE `Time`<v_cutoff ORDER BY `Time` LIMIT v_batch_target;
        ELSEIF p_table='IotaWattSolar' THEN
            DELETE FROM IotaWattSolar WHERE `Time`<v_cutoff ORDER BY `Time` LIMIT v_batch_target;
        ELSEIF p_table='EnergyPowerRaw' THEN
            DELETE FROM EnergyPowerRaw WHERE `Time`<v_cutoff ORDER BY `Time` LIMIT v_batch_target;
        ELSEIF p_table='NetUsage' THEN
            DELETE FROM NetUsage WHERE DateDTS<v_cutoff ORDER BY DateDTS LIMIT v_batch_target;
        ELSEIF p_table='IotaWattHoursbyMinute' THEN
            DELETE FROM IotaWattHoursbyMinute WHERE DateDTS<v_cutoff ORDER BY DateDTS LIMIT v_batch_target;
        ELSEIF p_table='IotaWattHoursSolarbyMinute' THEN
            DELETE FROM IotaWattHoursSolarbyMinute WHERE DateDTS<v_cutoff ORDER BY DateDTS LIMIT v_batch_target;
        ELSEIF p_table='EnergyPowerHoursByMinute' THEN
            DELETE FROM EnergyPowerHoursByMinute WHERE DateDTS<v_cutoff ORDER BY DateDTS LIMIT v_batch_target;
        ELSEIF p_table='SolarCostTrend' THEN
            DELETE FROM SolarCostTrend WHERE DateDTS<v_cutoff ORDER BY DateDTS LIMIT v_batch_target;
        ELSE
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Unsupported IotaWatt prune table';
        END IF;

        SET v_batch=ROW_COUNT(),v_deleted=v_deleted+v_batch;
        COMMIT;
        IF v_batch=0 THEN LEAVE prune_loop; END IF;
        DO SLEEP(0.05);
    END LOOP;

    IF p_table='IotaWatt' THEN SELECT COUNT(*) INTO v_remaining FROM IotaWatt WHERE `Time`<v_cutoff;
    ELSEIF p_table='IotaWattSolar' THEN SELECT COUNT(*) INTO v_remaining FROM IotaWattSolar WHERE `Time`<v_cutoff;
    ELSEIF p_table='EnergyPowerRaw' THEN SELECT COUNT(*) INTO v_remaining FROM EnergyPowerRaw WHERE `Time`<v_cutoff;
    ELSEIF p_table='NetUsage' THEN SELECT COUNT(*) INTO v_remaining FROM NetUsage WHERE DateDTS<v_cutoff;
    ELSEIF p_table='IotaWattHoursbyMinute' THEN SELECT COUNT(*) INTO v_remaining FROM IotaWattHoursbyMinute WHERE DateDTS<v_cutoff;
    ELSEIF p_table='IotaWattHoursSolarbyMinute' THEN SELECT COUNT(*) INTO v_remaining FROM IotaWattHoursSolarbyMinute WHERE DateDTS<v_cutoff;
    ELSEIF p_table='EnergyPowerHoursByMinute' THEN SELECT COUNT(*) INTO v_remaining FROM EnergyPowerHoursByMinute WHERE DateDTS<v_cutoff;
    ELSEIF p_table='SolarCostTrend' THEN SELECT COUNT(*) INTO v_remaining FROM SolarCostTrend WHERE DateDTS<v_cutoff;
    END IF;

    UPDATE IotaWattPruneAudit
    SET RowsDeleted=v_deleted,RemainingRows=v_remaining,
        Status=IF(v_remaining=0,'success','partial'),FinishedAtUTC=UTC_TIMESTAMP(),
        Note=CONCAT('batch=',p_batch_size,', max_rows=',p_max_rows,', max_seconds=',p_max_seconds)
    WHERE Id=v_audit_id;
    UPDATE maintenance_runs
    SET status=IF(v_remaining=0,'success','partial'),finished_at=CURRENT_TIMESTAMP,
        rows_affected=v_deleted,note=CONCAT('cutoff=',v_cutoff,', remaining=',v_remaining)
    WHERE id=v_maintenance_id;
    UPDATE IotaWattRetentionState
    SET LastPruneTable=p_table,
        LastPruneNote=CONCAT('deleted=',v_deleted,', remaining=',v_remaining,', cutoff=',v_cutoff),
        LastPruneSuccessUTC=IF(v_remaining=0,UTC_TIMESTAMP(),LastPruneSuccessUTC)
    WHERE Id=1;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `prune_weather_guarded` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `prune_weather_guarded`(
    IN p_max_rows BIGINT UNSIGNED,
    IN p_max_seconds INT UNSIGNED
)
BEGIN
    DECLARE v_days INT UNSIGNED;
    DECLARE v_cutoff DATETIME;

    SELECT WeatherRetentionDays INTO v_days
    FROM WeatherRetentionState WHERE Id=1;
    SET v_cutoff=CAST(DATE_FORMAT(UTC_TIMESTAMP()-INTERVAL v_days DAY,
                                 '%Y-%m-%d %H:00:00') AS DATETIME);
    CALL prune_weather_to_cutoff(v_cutoff,p_max_rows,p_max_seconds,UUID());
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `prune_weather_raw` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`%` PROCEDURE `prune_weather_raw`(IN p_raw_retention_days INT)
BEGIN
  DECLARE v_cutoff DATETIME;
  DECLARE v_rows INT DEFAULT 1;
  DECLARE v_total BIGINT DEFAULT 0;
  DECLARE v_run_id BIGINT;
  SET v_cutoff = DATE_FORMAT(NOW() - INTERVAL p_raw_retention_days DAY, '%Y-%m-%d %H:00:00');
  INSERT INTO maintenance_runs(job_name, note) VALUES ('prune_weather_raw', CONCAT('raw_retention_days=', p_raw_retention_days, ', cutoff=', v_cutoff));
  SET v_run_id = LAST_INSERT_ID();
  WHILE v_rows > 0 DO
    DELETE FROM weather
    WHERE created < v_cutoff
    LIMIT 100000;
    SET v_rows = ROW_COUNT();
    SET v_total = v_total + v_rows;
    DO SLEEP(0.05);
  END WHILE;
  UPDATE maintenance_runs SET finished_at=NOW(), status='success', rows_affected=v_total WHERE id=v_run_id;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `prune_weather_to_cutoff` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `prune_weather_to_cutoff`(
    IN p_cutoff_utc DATETIME,
    IN p_max_rows BIGINT UNSIGNED,
    IN p_max_seconds INT UNSIGNED,
    IN p_run_id CHAR(36)
)
BEGIN
    DECLARE v_audit_id BIGINT UNSIGNED DEFAULT NULL;
    DECLARE v_started DATETIME;
    DECLARE v_eligible BIGINT DEFAULT 0;
    DECLARE v_groups BIGINT DEFAULT 0;
    DECLARE v_missing BIGINT DEFAULT 0;
    DECLARE v_mismatched BIGINT DEFAULT 0;
    DECLARE v_deleted BIGINT DEFAULT 0;
    DECLARE v_rows BIGINT DEFAULT 1;
    DECLARE v_remaining BIGINT DEFAULT 0;
    DECLARE v_hour_end DATETIME;
    DECLARE v_hour_rows BIGINT DEFAULT 0;
    DECLARE v_error TEXT;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        GET DIAGNOSTICS CONDITION 1 v_error=MESSAGE_TEXT;
        ROLLBACK;
        IF v_audit_id IS NOT NULL THEN
            UPDATE WeatherRetentionAudit
            SET RowsAffected=v_deleted,Status='failed',
                FinishedAtUTC=UTC_TIMESTAMP(),Note=LEFT(v_error,1000)
            WHERE Id=v_audit_id;
        END IF;
        RESIGNAL;
    END;

    IF p_max_rows<1 OR p_max_seconds<1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Weather prune bounds must be positive';
    END IF;

    CALL assert_weather_pruning_ready();
    SET v_started=UTC_TIMESTAMP();
    INSERT INTO WeatherRetentionAudit
        (RunId,Mode,TargetName,CutoffUTC,Status,StartedAtUTC,
         Note)
    VALUES
        (p_run_id,'prune','weather',p_cutoff_utc,'running',v_started,
         CONCAT('Whole-hour batches; max_rows=',p_max_rows,
                ', max_seconds=',p_max_seconds));
    SET v_audit_id=LAST_INSERT_ID();

    CALL validate_weather_archive(
        p_cutoff_utc,v_eligible,v_groups,v_missing,v_mismatched
    );
    UPDATE WeatherRetentionAudit
    SET EligibleRows=v_eligible,SourceGroups=v_groups,
        MissingArchiveGroups=v_missing,
        MismatchedArchiveGroups=v_mismatched
    WHERE Id=v_audit_id;

    IF v_missing<>0 OR v_mismatched<>0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT='Weather prune blocked: archive validation failed';
    END IF;

    prune_loop: WHILE v_rows>0
      AND v_deleted<p_max_rows
      AND TIMESTAMPDIFF(SECOND,v_started,UTC_TIMESTAMP())<p_max_seconds DO
        SELECT LEAST(
            p_cutoff_utc,
            DATE_ADD(
                CAST(DATE_FORMAT(MIN(created),'%Y-%m-%d %H:00:00') AS DATETIME),
                INTERVAL 1 HOUR
            )
        )
        INTO v_hour_end
        FROM weather
        WHERE created<p_cutoff_utc;

        IF v_hour_end IS NULL THEN
            LEAVE prune_loop;
        END IF;

        SELECT COUNT(*) INTO v_hour_rows
        FROM weather WHERE created<v_hour_end;
        IF v_deleted>0 AND v_deleted+v_hour_rows>p_max_rows THEN
            LEAVE prune_loop;
        END IF;

        START TRANSACTION;
        DELETE FROM weather WHERE created<v_hour_end;
        SET v_rows=ROW_COUNT();
        COMMIT;
        SET v_deleted=v_deleted+v_rows;
        DO SLEEP(0.02);
    END WHILE;

    SELECT COUNT(*) INTO v_remaining
    FROM weather WHERE created<p_cutoff_utc;

    UPDATE WeatherRetentionAudit
    SET RowsAffected=v_deleted,RemainingRows=v_remaining,
        Status=IF(v_remaining=0,'success','partial'),
        FinishedAtUTC=UTC_TIMESTAMP()
    WHERE Id=v_audit_id;

    UPDATE WeatherRetentionState
    SET LastWeatherPruneSuccessUTC=
            IF(v_remaining=0,UTC_TIMESTAMP(),LastWeatherPruneSuccessUTC),
        LastNote=CONCAT('Weather prune deleted=',v_deleted,
                        ', remaining=',v_remaining,
                        ', cutoff=',p_cutoff_utc,' UTC')
    WHERE Id=1;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `rollup_weather_hourly` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`%` PROCEDURE `rollup_weather_hourly`(IN p_raw_retention_days INT)
BEGIN
  DECLARE v_cutoff DATETIME;
  DECLARE v_rows BIGINT DEFAULT 0;
  DECLARE v_run_id BIGINT;
  SET v_cutoff = DATE_FORMAT(NOW() - INTERVAL p_raw_retention_days DAY, '%Y-%m-%d %H:00:00');
  INSERT INTO maintenance_runs(job_name, note) VALUES ('rollup_weather_hourly', CONCAT('raw_retention_days=', p_raw_retention_days, ', cutoff=', v_cutoff));
  SET v_run_id = LAST_INSERT_ID();

  INSERT INTO weather_hourly (
    name, bucket_start, sample_count, min_value, max_value, avg_value,
    first_value, last_value, first_created, last_created
  )
  SELECT
    name,
    FROM_UNIXTIME(FLOOR(UNIX_TIMESTAMP(created) / 3600) * 3600) AS bucket_start,
    COUNT(*) AS sample_count,
    MIN(value) AS min_value,
    MAX(value) AS max_value,
    AVG(value) AS avg_value,
    NULL AS first_value,
    NULL AS last_value,
    MIN(created) AS first_created,
    MAX(created) AS last_created
  FROM weather
  WHERE created < v_cutoff
  GROUP BY name, FROM_UNIXTIME(FLOOR(UNIX_TIMESTAMP(created) / 3600) * 3600)
  ON DUPLICATE KEY UPDATE
    sample_count=VALUES(sample_count),
    min_value=VALUES(min_value),
    max_value=VALUES(max_value),
    avg_value=VALUES(avg_value),
    first_created=VALUES(first_created),
    last_created=VALUES(last_created),
    updated_at=NOW();

  SET v_rows = ROW_COUNT();
  UPDATE maintenance_runs SET finished_at=NOW(), status='success', rows_affected=v_rows WHERE id=v_run_id;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `validate_iotawatt_prune_table` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `validate_iotawatt_prune_table`(
    IN p_table VARCHAR(64),
    OUT p_cutoff DATETIME,
    OUT p_eligible BIGINT,
    OUT p_missing BIGINT,
    OUT p_mismatched BIGINT
)
BEGIN
    DECLARE v_raw_days INT;
    DECLARE v_minute_days INT;
    DECLARE v_energy_cutover DATETIME;

    SELECT RawRetentionDays, MinuteRetentionDays, EnergyMinuteCutoverUTC
      INTO v_raw_days, v_minute_days, v_energy_cutover
    FROM IotaWattRetentionState WHERE Id=1;

    SET p_cutoff=NULL, p_eligible=0, p_missing=0, p_mismatched=0;

    IF p_table='IotaWatt' THEN
        SELECT CAST(DATE_FORMAT(MAX(`Time`)-INTERVAL v_raw_days DAY,'%Y-%m-%d %H:00:00') AS DATETIME)
          INTO p_cutoff FROM IotaWatt;
        SELECT COUNT(*) INTO p_eligible FROM IotaWatt WHERE `Time`<p_cutoff;
        SELECT COALESCE(SUM(h.BucketStart IS NULL),0),
               COALESCE(SUM(h.BucketStart IS NOT NULL AND
                   (h.RawSampleCount<>s.c OR ABS(h.TotalAvgW-s.TotalAvgW)>0.01 OR
                    ABS(h.TotalMinW-s.TotalMinW)>0.01 OR ABS(h.TotalMaxW-s.TotalMaxW)>0.01)),0)
          INTO p_missing,p_mismatched
        FROM (
            SELECT CAST(DATE_FORMAT(`Time`,'%Y-%m-%d %H:00:00') AS DATETIME) b,
                   COUNT(*) c,AVG(Total) TotalAvgW,MIN(Total) TotalMinW,MAX(Total) TotalMaxW
            FROM IotaWatt WHERE `Time`<p_cutoff GROUP BY b
        ) s LEFT JOIN IotaWattHourly h ON h.BucketStart=s.b;

    ELSEIF p_table='IotaWattSolar' THEN
        SELECT CAST(DATE_FORMAT(MAX(`Time`)-INTERVAL v_raw_days DAY,'%Y-%m-%d %H:00:00') AS DATETIME)
          INTO p_cutoff FROM IotaWattSolar;
        SELECT COUNT(*) INTO p_eligible FROM IotaWattSolar WHERE `Time`<p_cutoff;
        SELECT COALESCE(SUM(h.BucketStart IS NULL),0),
               COALESCE(SUM(h.BucketStart IS NOT NULL AND
                   (h.RawSampleCount<>s.c OR ABS(h.TotalGridAvgW-s.GridAvgW)>0.01 OR
                    ABS(h.TotalSolarAvgW-s.SolarAvgW)>0.01 OR
                    ((h.TotalHeatPumpAvgW IS NULL)<>(s.HeatPumpAvgW IS NULL)) OR
                    (h.TotalHeatPumpAvgW IS NOT NULL AND s.HeatPumpAvgW IS NOT NULL AND
                     ABS(h.TotalHeatPumpAvgW-s.HeatPumpAvgW)>0.01))),0)
          INTO p_missing,p_mismatched
        FROM (
            SELECT CAST(DATE_FORMAT(`Time`,'%Y-%m-%d %H:00:00') AS DATETIME) b,
                   COUNT(*) c,AVG(TotalGrid) GridAvgW,AVG(TotalSolar) SolarAvgW,
                   AVG(TotalHeatPump) HeatPumpAvgW
            FROM IotaWattSolar WHERE `Time`<p_cutoff GROUP BY b
        ) s LEFT JOIN IotaWattSolarHourly h ON h.BucketStart=s.b;

    ELSEIF p_table='EnergyPowerRaw' THEN
        SELECT CAST(DATE_FORMAT(MAX(`Time`)-INTERVAL v_raw_days DAY,'%Y-%m-%d %H:00:00') AS DATETIME)
          INTO p_cutoff FROM EnergyPowerRaw;
        SELECT COUNT(*) INTO p_eligible FROM EnergyPowerRaw WHERE `Time`<p_cutoff;
        SELECT COALESCE(SUM(h.BucketStartUTC IS NULL),0),
               COALESCE(SUM(h.BucketStartUTC IS NOT NULL AND
                   (h.RawSampleCount<>s.c OR
                    NOT (ROUND(h.SolarAvgW,2) <=> ROUND(s.SolarAvgW,2)) OR
                    NOT (ROUND(h.PanelUsageAvgW,2) <=> ROUND(s.PanelAvgW,2)) OR
                    NOT (ROUND(h.HeatPumpAvgW,2) <=> ROUND(s.HeatPumpAvgW,2)))),0)
          INTO p_missing,p_mismatched
        FROM (
            SELECT CAST(DATE_FORMAT(`Time`,'%Y-%m-%d %H:00:00') AS DATETIME) b,
                   COUNT(*) c,AVG(SolarW) SolarAvgW,AVG(PanelUsageW) PanelAvgW,
                   AVG(HeatPumpW) HeatPumpAvgW
            FROM EnergyPowerRaw WHERE `Time`<p_cutoff GROUP BY b
        ) s LEFT JOIN EnergyPowerHourly h ON h.BucketStartUTC=s.b;

    ELSEIF p_table='NetUsage' THEN
        SELECT CAST(DATE_FORMAT(MAX(DateDTS)-INTERVAL v_raw_days DAY,'%Y-%m-%d %H:00:00') AS DATETIME)
          INTO p_cutoff FROM NetUsage;
        SELECT COUNT(*) INTO p_eligible FROM NetUsage WHERE DateDTS<p_cutoff;
        SELECT COALESCE(SUM(h.BucketStart IS NULL),0),
               COALESCE(SUM(h.BucketStart IS NOT NULL AND
                   (h.NetUsageSampleCount<>s.c OR
                    ABS(h.TeslaTotalWh-s.TeslaTotalWh)>0.01 OR
                    ABS(h.TeslaGreenWh-s.TeslaGreenWh)>0.01 OR
                    ABS(h.SolarSelfConsumedWh-s.SelfConsumedWh)>0.01)),0)
          INTO p_missing,p_mismatched
        FROM (
            SELECT CAST(DATE_FORMAT(DateDTS,'%Y-%m-%d %H:00:00') AS DATETIME) b,
                   COUNT(*) c,SUM(IFNULL(TeslaTotal,0))/720.0 TeslaTotalWh,
                   SUM(IFNULL(TeslaGreen,0))/720.0 TeslaGreenWh,
                   SUM(CASE WHEN Net>0 THEN IFNULL(Consumed,0) ELSE IFNULL(Solar,0) END)/720.0 SelfConsumedWh
            FROM NetUsage WHERE DateDTS<p_cutoff GROUP BY b
        ) s LEFT JOIN IotaWattSolarHourly h ON h.BucketStart=s.b;

    ELSEIF p_table='IotaWattHoursbyMinute' THEN
        SELECT CAST(DATE_FORMAT(MAX(DateDTS)-INTERVAL v_minute_days DAY,'%Y-%m-%d %H:00:00') AS DATETIME)
          INTO p_cutoff FROM IotaWattHoursbyMinute;
        SELECT COUNT(*) INTO p_eligible FROM IotaWattHoursbyMinute WHERE DateDTS<p_cutoff;
        SELECT COALESCE(SUM(h.BucketStart IS NULL),0),
               COALESCE(SUM(h.BucketStart IS NOT NULL AND
                   (h.MinuteCount<>s.c OR ABS(h.TotalWh-s.TotalWh)>0.01 OR
                    ABS(h.ACWh-s.ACWh)>0.01 OR ABS(h.EVWh-s.EVWh)>0.01 OR
                    ABS(h.FurnaceWh-s.FurnaceWh)>0.01 OR ABS(h.AllOtherWh-s.AllOtherWh)>0.01)),0)
          INTO p_missing,p_mismatched
        FROM (
            SELECT CAST(DATE_FORMAT(DateDTS,'%Y-%m-%d %H:00:00') AS DATETIME) b,
                   COUNT(*) c,SUM(TotalWattHours) TotalWh,SUM(ACHours) ACWh,
                   SUM(EVHours) EVWh,SUM(FurnaceHours) FurnaceWh,SUM(AllOtherHours) AllOtherWh
            FROM IotaWattHoursbyMinute WHERE DateDTS<p_cutoff GROUP BY b
        ) s LEFT JOIN IotaWattHourly h ON h.BucketStart=s.b;

    ELSEIF p_table='IotaWattHoursSolarbyMinute' THEN
        SELECT CAST(DATE_FORMAT(MAX(DateDTS)-INTERVAL v_minute_days DAY,'%Y-%m-%d %H:00:00') AS DATETIME)
          INTO p_cutoff FROM IotaWattHoursSolarbyMinute;
        SELECT COUNT(*) INTO p_eligible FROM IotaWattHoursSolarbyMinute WHERE DateDTS<p_cutoff;
        SELECT COALESCE(SUM(h.BucketStart IS NULL),0),
               COALESCE(SUM(h.BucketStart IS NOT NULL AND
                   (h.MinuteCount<>s.c OR ABS(h.TotalGridWh-s.GridWh)>0.01 OR
                    ABS(h.TotalSolarWh-s.SolarWh)>0.01 OR
                    (s.HeatPumpWh IS NOT NULL AND
                     (h.TotalHeatPumpWh IS NULL OR ABS(h.TotalHeatPumpWh-s.HeatPumpWh)>0.1)))),0)
          INTO p_missing,p_mismatched
        FROM (
            SELECT CAST(DATE_FORMAT(DateDTS,'%Y-%m-%d %H:00:00') AS DATETIME) b,
                   COUNT(*) c,SUM(TotalGridHours) GridWh,SUM(TotalSolarHours) SolarWh,
                   SUM(TotalHeatPumpHours) HeatPumpWh
            FROM IotaWattHoursSolarbyMinute WHERE DateDTS<p_cutoff GROUP BY b
        ) s LEFT JOIN IotaWattSolarHourly h ON h.BucketStart=s.b;

    ELSEIF p_table='EnergyPowerHoursByMinute' THEN
        SELECT CAST(DATE_FORMAT(MAX(DateDTS)-INTERVAL v_minute_days DAY,'%Y-%m-%d %H:00:00') AS DATETIME)
          INTO p_cutoff FROM EnergyPowerHoursByMinute;
        SELECT COUNT(*) INTO p_eligible FROM EnergyPowerHoursByMinute WHERE DateDTS<p_cutoff;
        SELECT COALESCE(SUM(h.BucketStartUTC IS NULL),0),
               COALESCE(SUM(h.BucketStartUTC IS NOT NULL AND
                   ((s.grain='daily' AND (h.SourceGrain<>'daily' OR NOT (ROUND(h.SolarWh,4) <=> ROUND(s.SolarWh,4)))) OR
                    (s.grain='minute' AND (h.MinuteCount<>s.c OR
                     NOT (ROUND(h.SolarWh,4) <=> ROUND(s.SolarWh,4)) OR
                     NOT (ROUND(h.PanelUsageWh,4) <=> ROUND(s.PanelUsageWh,4)) OR
                     NOT (ROUND(h.HeatPumpWh,4) <=> ROUND(s.HeatPumpWh,4)))))),0)
          INTO p_missing,p_mismatched
        FROM (
            SELECT CASE WHEN DateDTS<v_energy_cutover THEN DateDTS
                        ELSE CAST(DATE_FORMAT(DateDTS,'%Y-%m-%d %H:00:00') AS DATETIME) END b,
                   CASE WHEN DateDTS<v_energy_cutover THEN 'daily' ELSE 'minute' END grain,
                   COUNT(*) c,SUM(SolarWh) SolarWh,SUM(PanelUsageWh) PanelUsageWh,
                   SUM(HeatPumpWh) HeatPumpWh
            FROM EnergyPowerHoursByMinute WHERE DateDTS<p_cutoff
            GROUP BY b,grain
        ) s LEFT JOIN EnergyPowerHourly h ON h.BucketStartUTC=s.b;

    ELSEIF p_table='SolarCostTrend' THEN
        SELECT CAST(DATE_FORMAT(MAX(DateDTS)-INTERVAL v_minute_days DAY,'%Y-%m-%d %H:00:00') AS DATETIME)
          INTO p_cutoff FROM SolarCostTrend;
        SELECT COUNT(*) INTO p_eligible FROM SolarCostTrend WHERE DateDTS<p_cutoff;
        SELECT COALESCE(SUM(h.BucketStart IS NULL),0),
               COALESCE(SUM(h.BucketStart IS NOT NULL AND ABS(h.Cost-s.Cost)>0.0001),0)
          INTO p_missing,p_mismatched
        FROM (
            SELECT CAST(DATE_FORMAT(DateDTS,'%Y-%m-%d %H:00:00') AS DATETIME) b,
                   SUM(SolarCost)/60000.0 Cost
            FROM SolarCostTrend WHERE DateDTS<p_cutoff GROUP BY b
        ) s LEFT JOIN IotaWattSolarHourly h ON h.BucketStart=s.b;
    ELSE
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Unsupported IotaWatt prune table';
    END IF;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION' */ ;
/*!50003 DROP PROCEDURE IF EXISTS `validate_weather_archive` */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb3 */ ;
/*!50003 SET character_set_results = utf8mb3 */ ;
/*!50003 SET collation_connection  = utf8mb3_general_ci */ ;
DELIMITER ;;
CREATE DEFINER=`root`@`localhost` PROCEDURE `validate_weather_archive`(
    IN p_cutoff_utc DATETIME,
    OUT p_eligible_rows BIGINT,
    OUT p_source_groups BIGINT,
    OUT p_missing_groups BIGINT,
    OUT p_mismatched_groups BIGINT
)
BEGIN
    DROP TEMPORARY TABLE IF EXISTS tmp_weather_validation;
    CREATE TEMPORARY TABLE tmp_weather_validation (
        name VARCHAR(100) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
        bucket_start DATETIME NOT NULL,
        sample_count INT UNSIGNED NOT NULL,
        min_value DECIMAL(10,2) NULL,
        max_value DECIMAL(10,2) NULL,
        avg_value DECIMAL(12,4) NULL,
        first_created TIMESTAMP NULL,
        last_created TIMESTAMP NULL,
        PRIMARY KEY(name,bucket_start)
    ) ENGINE=InnoDB;

    INSERT INTO tmp_weather_validation
        (name,bucket_start,sample_count,min_value,max_value,avg_value,
         first_created,last_created)
    SELECT
        name,
        FROM_UNIXTIME(FLOOR(UNIX_TIMESTAMP(created)/3600)*3600),
        COUNT(*),
        MIN(value),
        MAX(value),
        ROUND(AVG(value),4),
        MIN(created),
        MAX(created)
    FROM weather
    WHERE created < p_cutoff_utc
    GROUP BY name,FROM_UNIXTIME(FLOOR(UNIX_TIMESTAMP(created)/3600)*3600);

    SELECT COUNT(*) INTO p_eligible_rows
    FROM weather WHERE created < p_cutoff_utc;

    SELECT COUNT(*) INTO p_source_groups FROM tmp_weather_validation;

    SELECT
        COALESCE(SUM(h.name IS NULL),0),
        COALESCE(SUM(
            h.name IS NOT NULL AND (
                h.sample_count<>s.sample_count OR
                NOT (h.min_value <=> s.min_value) OR
                NOT (h.max_value <=> s.max_value) OR
                NOT (h.avg_value <=> s.avg_value) OR
                NOT (h.first_created <=> s.first_created) OR
                NOT (h.last_created <=> s.last_created)
            )
        ),0)
    INTO p_missing_groups,p_mismatched_groups
    FROM tmp_weather_validation s
    LEFT JOIN weather_hourly h
      ON h.name=s.name AND h.bucket_start=s.bucket_start;

    DROP TEMPORARY TABLE tmp_weather_validation;
END ;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*M!100616 SET NOTE_VERBOSITY=@OLD_NOTE_VERBOSITY */;

-- Dump completed on 2026-07-26 23:57:24
