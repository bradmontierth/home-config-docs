-- Pixel phone performance logger (pixel-monitor). Database: hubitat_logging.
-- All timestamps are TIMESTAMP (stored UTC); the collector writes UTC.

CREATE TABLE IF NOT EXISTS pixel_metrics (
  ts               TIMESTAMP NOT NULL,
  device           VARCHAR(64) NOT NULL,
  uptime_s         INT UNSIGNED,
  mem_total_kb     INT UNSIGNED,
  mem_avail_kb     INT UNSIGNED,
  swap_total_kb    INT UNSIGNED,
  swap_free_kb     INT UNSIGNED,
  load1            DECIMAL(7,2),
  cpu_pct          DECIMAL(5,1),     -- whole-phone CPU busy % since previous sample
  io_some_avg60    DECIMAL(6,2),     -- /proc/pressure/io
  io_full_avg60    DECIMAL(6,2),
  swapin_ps        DECIMAL(10,1),    -- pages/s swapped in (thrash signal)
  swapout_ps       DECIMAL(10,1),
  majfault_ps      DECIMAL(10,1),
  direct_reclaim_ps DECIMAL(10,2),   -- allocstall: apps stalled waiting for memory
  thermal_status   TINYINT,          -- 0 none .. 6 shutdown
  skin_c           DECIMAL(5,1),
  soc_c            DECIMAL(5,1),
  cpu_big_c        DECIMAL(5,1),
  battery_c        DECIMAL(5,1),
  battery_pct      TINYINT,
  charging         TINYINT,
  screen_on        TINYINT,
  fg_app           VARCHAR(160),
  PRIMARY KEY (device, ts),
  KEY ix_ts (ts)
);

-- Top CPU processes each minute (top 5).
CREATE TABLE IF NOT EXISTS pixel_cpu (
  ts      TIMESTAMP NOT NULL,
  device  VARCHAR(64) NOT NULL,
  proc    VARCHAR(160) NOT NULL,
  cpu_pct DECIMAL(6,1),
  KEY ix_dev_ts (device, ts)
);

-- Per-process memory every 5 minutes (top 30 by RSS+swap).
CREATE TABLE IF NOT EXISTS pixel_proc_mem (
  ts      TIMESTAMP NOT NULL,
  device  VARCHAR(64) NOT NULL,
  proc    VARCHAR(160) NOT NULL,
  rss_kb  INT UNSIGNED,
  swap_kb INT UNSIGNED,
  KEY ix_dev_ts (device, ts),
  KEY ix_proc (proc, ts)
);

-- Discrete events: lmk_kill, launch, anr, crash, skipped_frames, davey,
-- lag_mark, reboot.
CREATE TABLE IF NOT EXISTS pixel_events (
  id      BIGINT AUTO_INCREMENT PRIMARY KEY,
  ts      TIMESTAMP(3) NOT NULL,
  device  VARCHAR(64) NOT NULL,
  kind    VARCHAR(32) NOT NULL,
  app     VARCHAR(160),
  value   DECIMAL(12,1),          -- launch ms / frames skipped / kill rss MB ...
  detail  VARCHAR(255),
  KEY ix_dev_kind_ts (device, kind, ts),
  KEY ix_ts (ts)
);
