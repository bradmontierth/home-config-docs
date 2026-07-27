-- IotaWatt Phase 3: guarded, audited, bounded retention pruning.
--
-- Applying this file is non-destructive. It creates validation and pruning
-- procedures, but does not enable pruning or delete telemetry. Deletion still
-- requires all three IotaWattRetentionState gates to be true.

ALTER TABLE IotaWattRetentionState
    ADD COLUMN IF NOT EXISTS LastPruneSuccessUTC DATETIME NULL AFTER LastValidationUTC,
    ADD COLUMN IF NOT EXISTS LastPruneValidationUTC DATETIME NULL AFTER LastPruneSuccessUTC,
    ADD COLUMN IF NOT EXISTS LastPruneTable VARCHAR(64) NULL AFTER LastPruneValidationUTC,
    ADD COLUMN IF NOT EXISTS LastPruneNote VARCHAR(1000) NULL AFTER LastPruneTable;

CREATE TABLE IF NOT EXISTS IotaWattPruneAudit (
    Id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    RunId CHAR(36) NOT NULL,
    Mode VARCHAR(16) NOT NULL,
    TableName VARCHAR(64) NOT NULL,
    Cutoff DATETIME NULL,
    EligibleRows BIGINT UNSIGNED NOT NULL DEFAULT 0,
    MissingArchiveGroups BIGINT UNSIGNED NOT NULL DEFAULT 0,
    MismatchedArchiveGroups BIGINT UNSIGNED NOT NULL DEFAULT 0,
    RowsDeleted BIGINT UNSIGNED NOT NULL DEFAULT 0,
    RemainingRows BIGINT UNSIGNED NULL,
    Status VARCHAR(32) NOT NULL,
    StartedAtUTC DATETIME NOT NULL,
    FinishedAtUTC DATETIME NULL,
    Note VARCHAR(1000) NULL,
    KEY IotaWattPruneAudit_RunId (RunId),
    KEY IotaWattPruneAudit_TableTime (TableName, StartedAtUTC),
    KEY IotaWattPruneAudit_StatusTime (Status, StartedAtUTC)
) ENGINE=InnoDB;

DELIMITER //

DROP PROCEDURE IF EXISTS validate_iotawatt_prune_table//
CREATE PROCEDURE validate_iotawatt_prune_table(
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
                    ((h.SolarAvgW IS NULL)<>(s.SolarAvgW IS NULL)) OR
                    (h.SolarAvgW IS NOT NULL AND s.SolarAvgW IS NOT NULL AND
                     ABS(h.SolarAvgW-s.SolarAvgW)>0.01) OR
                    ((h.PanelUsageAvgW IS NULL)<>(s.PanelAvgW IS NULL)) OR
                    (h.PanelUsageAvgW IS NOT NULL AND s.PanelAvgW IS NOT NULL AND
                     ABS(h.PanelUsageAvgW-s.PanelAvgW)>0.01) OR
                    ((h.HeatPumpAvgW IS NULL)<>(s.HeatPumpAvgW IS NULL)) OR
                    (h.HeatPumpAvgW IS NOT NULL AND s.HeatPumpAvgW IS NOT NULL AND
                     ABS(h.HeatPumpAvgW-s.HeatPumpAvgW)>0.01))),0)
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
END//

DROP PROCEDURE IF EXISTS dry_run_iotawatt_pruning//
CREATE PROCEDURE dry_run_iotawatt_pruning()
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
END//

DROP PROCEDURE IF EXISTS prune_iotawatt_table//
CREATE PROCEDURE prune_iotawatt_table(
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
END//

DROP PROCEDURE IF EXISTS maintain_iotawatt_retention//
CREATE PROCEDURE maintain_iotawatt_retention()
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
END//

DELIMITER ;

CREATE OR REPLACE VIEW v_iotawatt_pruning_readiness AS
SELECT
    Id,RawRetentionDays,MinuteRetentionDays,GrafanaMigrated,ConsumersMigrated,
    PruningEnabled,(GrafanaMigrated AND ConsumersMigrated AND PruningEnabled) ReadyToPrune,
    LastArchiveSuccessUTC,LastValidationUTC,LastPruneSuccessUTC,
    LastPruneValidationUTC,LastPruneTable,LastPruneNote,Note,UpdatedAt
FROM IotaWattRetentionState;
