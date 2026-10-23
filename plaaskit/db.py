"""SQLite storage. Standard library only, so it installs on Termux with no compiling."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def data_dir() -> Path:
    d = Path(os.environ.get("PLAASKIT_HOME") or Path.home() / ".plaaskit")
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "plaas.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS farm (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  name TEXT NOT NULL,
  code TEXT NOT NULL,
  owner TEXT,
  province TEXT,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  ggn TEXT,
  puc TEXT,
  water_allocation_m3 REAL
);
CREATE TABLE IF NOT EXISTS field (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  area_ha REAL NOT NULL CHECK (area_ha > 0),
  lat REAL,
  lon REAL,
  soil TEXT
);
CREATE TABLE IF NOT EXISTS planting (
  id INTEGER PRIMARY KEY,
  field_id INTEGER NOT NULL REFERENCES field(id),
  crop TEXT NOT NULL,
  variety TEXT,
  planted_on TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','closed'))
);
CREATE TABLE IF NOT EXISTS input_use (
  id INTEGER PRIMARY KEY,
  planting_id INTEGER NOT NULL REFERENCES planting(id),
  applied_on TEXT NOT NULL,
  kind TEXT NOT NULL,
  product TEXT NOT NULL,
  active_ingredient TEXT,
  reg_no TEXT,
  dose REAL,
  unit TEXT,
  target TEXT,
  phi_days INTEGER NOT NULL DEFAULT 0,
  operator TEXT,
  note TEXT
);
CREATE TABLE IF NOT EXISTS harvest (
  id INTEGER PRIMARY KEY,
  planting_id INTEGER NOT NULL REFERENCES planting(id),
  harvested_on TEXT NOT NULL,
  lot_code TEXT NOT NULL UNIQUE,
  qty_kg REAL NOT NULL CHECK (qty_kg > 0),
  destination TEXT,
  phi_override INTEGER NOT NULL DEFAULT 0,
  note TEXT
);
CREATE TABLE IF NOT EXISTS water_log (
  id INTEGER PRIMARY KEY,
  field_id INTEGER NOT NULL REFERENCES field(id),
  logged_on TEXT NOT NULL,
  source TEXT,
  volume_m3 REAL NOT NULL,
  mm REAL NOT NULL,
  note TEXT
);
CREATE TABLE IF NOT EXISTS subscriber (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  phone TEXT NOT NULL UNIQUE,
  field_id INTEGER REFERENCES field(id),
  lang TEXT NOT NULL DEFAULT 'en',
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS alert_log (
  id INTEGER PRIMARY KEY,
  sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  phone TEXT NOT NULL,
  alert_key TEXT NOT NULL,
  message TEXT NOT NULL,
  status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_input_planting ON input_use(planting_id, applied_on);
CREATE INDEX IF NOT EXISTS ix_water_field ON water_log(field_id, logged_on);
"""


def connect(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
