-- Guarded weather archive/raw pruning and DeviceMonitoring retention.
-- MariaDB 11.x, database hubitat_logging, server/session time zone UTC.
--
-- Applying this file is non-destructive. Pruning remains locked until
-- WeatherRetentionState.PruningEnabled is explicitly set to 1.

CREATE TABLE IF NOT EXISTS WeatherRetentionState (
    Id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
    WeatherRetentionDays INT UNSIGNED NOT NULL DEFAULT 14,
    DeviceRetentionDays INT UNSIGNED NOT NULL DEFAULT 90,
    ConsumersMigrated TINYINT(1) NOT NULL DEFAULT 1,
    PruningEnabled TINYINT(1) NOT NULL DEFAULT 0,
    LastArchiveSuccessUTC DATETIME NULL,
    LastValidationUTC DATETIME NULL,
    LastWeatherPruneSuccessUTC DATETIME NULL,
    LastDevicePruneSuccessUTC DATETIME NULL,
    LastFullSuccessUTC DATETIME NULL,
    LastNote VARCHAR(1000) NULL,
    UpdatedAt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;

INSERT INTO WeatherRetentionState
    (Id,WeatherRetentionDays,DeviceRetentionDays,ConsumersMigrated,PruningEnabled,LastNote)
VALUES
    (1,14,90,1,0,'Guarded retention installed; pruning locked pending archive validation')
ON DUPLICATE KEY UPDATE Id=VALUES(Id);

CREATE TABLE IF NOT EXISTS WeatherRetentionAudit (
    Id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    RunId CHAR(36) NOT NULL,
    Mode VARCHAR(24) NOT NULL,
    TargetName VARCHAR(64) NOT NULL,
    CutoffUTC DATETIME NULL,
    EligibleRows BIGINT UNSIGNED NOT NULL DEFAULT 0,
    SourceGroups BIGINT UNSIGNED NOT NULL DEFAULT 0,
    MissingArchiveGroups BIGINT UNSIGNED NOT NULL DEFAULT 0,
    MismatchedArchiveGroups BIGINT UNSIGNED NOT NULL DEFAULT 0,
    RowsAffected BIGINT UNSIGNED NOT NULL DEFAULT 0,
    RemainingRows BIGINT UNSIGNED NULL,
    Status VARCHAR(32) NOT NULL,
    StartedAtUTC DATETIME NOT NULL,
    FinishedAtUTC DATETIME NULL,
    Note VARCHAR(1000) NULL,
    KEY WeatherRetentionAudit_RunId (RunId),
    KEY WeatherRetentionAudit_TargetTime (TargetName,StartedAtUTC),
    KEY WeatherRetentionAudit_StatusTime (Status,StartedAtUTC)
) ENGINE=InnoDB;

DELIMITER //

DROP PROCEDURE IF EXISTS validate_weather_archive//
CREATE PROCEDURE validate_weather_archive(
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
END//

DROP PROCEDURE IF EXISTS archive_weather_to_cutoff//
CREATE PROCEDURE archive_weather_to_cutoff(
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
END//

DROP PROCEDURE IF EXISTS archive_weather_guarded//
CREATE PROCEDURE archive_weather_guarded()
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
END//

DROP PROCEDURE IF EXISTS dry_run_weather_retention//
CREATE PROCEDURE dry_run_weather_retention()
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
END//

DROP PROCEDURE IF EXISTS assert_weather_pruning_ready//
CREATE PROCEDURE assert_weather_pruning_ready()
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
END//

DROP PROCEDURE IF EXISTS prune_weather_to_cutoff//
CREATE PROCEDURE prune_weather_to_cutoff(
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
END//

DROP PROCEDURE IF EXISTS prune_weather_guarded//
CREATE PROCEDURE prune_weather_guarded(
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
END//

DROP PROCEDURE IF EXISTS prune_device_monitoring_to_cutoff//
CREATE PROCEDURE prune_device_monitoring_to_cutoff(
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
END//

DROP PROCEDURE IF EXISTS prune_device_monitoring_guarded//
CREATE PROCEDURE prune_device_monitoring_guarded(
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
END//

DROP PROCEDURE IF EXISTS maintain_weather_device_retention//
CREATE PROCEDURE maintain_weather_device_retention()
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
END//

DELIMITER ;

CREATE OR REPLACE VIEW v_weather_retention_readiness AS
SELECT
    Id,
    WeatherRetentionDays,
    DeviceRetentionDays,
    ConsumersMigrated,
    PruningEnabled,
    (ConsumersMigrated AND PruningEnabled) ReadyToPrune,
    LastArchiveSuccessUTC,
    LastValidationUTC,
    LastWeatherPruneSuccessUTC,
    LastDevicePruneSuccessUTC,
    LastFullSuccessUTC,
    LastNote,
    UpdatedAt
FROM WeatherRetentionState;
