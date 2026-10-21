"""SQLite: схема и мелкие помощники. Время везде — ISO-строки в UTC."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',      -- running | complete | incomplete | failed
    source_total INTEGER,                         -- сколько машин заявляет сам источник
    collected INTEGER DEFAULT 0,
    prev_active INTEGER DEFAULT 0,
    completeness REAL,
    requests INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    blocked INTEGER DEFAULT 0,
    new_count INTEGER DEFAULT 0,
    changed_count INTEGER DEFAULT 0,
    removed_count INTEGER DEFAULT 0,
    returned_count INTEGER DEFAULT 0,
    relisted_count INTEGER DEFAULT 0,
    sold_count INTEGER DEFAULT 0,
    cards_fetched INTEGER DEFAULT 0,
    details_pending INTEGER DEFAULT 0,
    duration_s REAL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS offers (
    id TEXT PRIMARY KEY,                -- числовой ID объявления Авто.ру
    hash TEXT NOT NULL,                 -- вторая часть ID из ссылки
    url TEXT NOT NULL,
    section TEXT NOT NULL,              -- new | used
    mark_code TEXT, mark TEXT, model_code TEXT, model TEXT,
    generation TEXT, complectation TEXT, complectation_id TEXT,
    year INTEGER, mileage INTEGER,
    price INTEGER, price_discount INTEGER,
    discount_tradein INTEGER, discount_credit INTEGER, discount_insurance INTEGER, discount_max INTEGER,
    engine_volume REAL, engine_cc INTEGER, engine_power INTEGER, fuel TEXT, transmission TEXT, drive TEXT,
    body TEXT, color TEXT, color_hex TEXT,
    vin TEXT, vin_full INTEGER DEFAULT 0,
    owners INTEGER, pts TEXT, customs_cleared INTEGER,
    availability TEXT,
    description TEXT,
    photos TEXT,                        -- JSON-список ссылок в исходном разрешении, по порядку
    photo_count INTEGER,
    photo_hash TEXT,                    -- dHash первого фото (64 бита hex)
    created_at_source TEXT, fresh_at_source TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',   -- active | removed | sold
    removed_at TEXT, sold_at TEXT,
    relisted_from TEXT, duplicate_of TEXT,
    anomalies TEXT,                     -- JSON-список кодов аномалий
    listing_sig TEXT, details_at TEXT, details_sig TEXT
);
CREATE INDEX IF NOT EXISTS offers_status ON offers(status);
CREATE INDEX IF NOT EXISTS offers_mark_model ON offers(mark_code, model_code);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id TEXT NOT NULL,
    run_id INTEGER,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,   -- appeared | price_changed | discount_changed | availability_changed | mileage_changed
                          -- description_changed | photos_changed | complectation_changed
                          -- removed | returned | relisted | probably_sold | duplicate | still_active
    old TEXT, new TEXT, details TEXT
);
CREATE INDEX IF NOT EXISTS events_offer ON events(offer_id, ts);
CREATE INDEX IF NOT EXISTS events_type ON events(type, ts);

CREATE TABLE IF NOT EXISTS price_history (
    offer_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    price INTEGER, price_discount INTEGER,
    run_id INTEGER
);
CREATE INDEX IF NOT EXISTS price_history_offer ON price_history(offer_id, ts);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                  -- daily_new | price_drop | manual
    dedup_key TEXT NOT NULL UNIQUE,
    offer_ids TEXT,                      -- JSON
    payload TEXT NOT NULL,               -- JSON: text, photos
    source TEXT DEFAULT 'service',       -- service | mcp
    status TEXT NOT NULL DEFAULT 'new',  -- new | moderation | approved | sending | rejected | sent | failed
    target_chat TEXT,
    admin_message_id INTEGER,
    created_at TEXT NOT NULL,
    decided_at TEXT, decided_by TEXT,
    sent_at TEXT,
    attempts INTEGER DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS posts_status ON posts(status);

CREATE TABLE IF NOT EXISTS llm_cache (
    desc_sha TEXT PRIMARY KEY,
    facts TEXT NOT NULL,
    dropped INTEGER DEFAULT 0,
    model TEXT, tokens_in INTEGER, tokens_out INTEGER, cost REAL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_usage (
    day TEXT PRIMARY KEY,
    calls INTEGER DEFAULT 0, tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0,
    cost REAL DEFAULT 0, facts_kept INTEGER DEFAULT 0, facts_dropped INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Добавляет колонки, появившиеся после создания базы."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(offers)")}
    if "engine_cc" not in have:
        conn.execute("ALTER TABLE offers ADD COLUMN engine_cc INTEGER")
        conn.commit()


def kv_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))


def row_to_offer(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["photos"] = json.loads(d["photos"]) if d.get("photos") else []
    d["anomalies"] = json.loads(d["anomalies"]) if d.get("anomalies") else []
    for k in ("vin_full", "customs_cleared"):
        if d.get(k) is not None:
            d[k] = bool(d[k])
    for k in ("listing_sig", "details_sig"):
        d.pop(k, None)
    return d
