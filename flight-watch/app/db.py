"""SQLite store: every scraped fare, every run, every alert. One writer at a time."""
import os
import sqlite3
import threading

DB_PATH = os.environ.get("DB_PATH", "/data/flightwatch.db")
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
  queries INTEGER DEFAULT 0, ok INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
  empty INTEGER DEFAULT 0, rows INTEGER DEFAULT 0, status TEXT NOT NULL DEFAULT 'running', note TEXT);
CREATE TABLE IF NOT EXISTS fares (
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, ts TEXT NOT NULL,
  origin TEXT, dest TEXT, dep TEXT, ret TEXT, nights INTEGER, stops INTEGER,
  airlines TEXT, route TEXT, dep_time TEXT, arr_time TEXT, elapsed_min INTEGER,
  price INTEGER, url TEXT);
CREATE INDEX IF NOT EXISTS fares_run ON fares(run_id, origin);
CREATE INDEX IF NOT EXISTS fares_dates ON fares(origin, dep, ret);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, rule TEXT, origin TEXT, dep TEXT, ret TEXT,
  route TEXT, stops INTEGER, price INTEGER, message TEXT, sent INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS alert_state (key TEXT PRIMARY KEY, price INTEGER, ts TEXT);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init() -> None:
    with _lock, connect() as con:
        con.executescript(SCHEMA)


def write(fn):
    """Run fn(con) under the writer lock inside one transaction."""
    with _lock, connect() as con:
        return fn(con)


def read(fn):
    with connect() as con:
        return fn(con)
