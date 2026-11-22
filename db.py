from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
UPLOAD_DIR = DATA_DIR / "sources"
CLIP_DIR = DATA_DIR / "clips"
TEMP_DIR = DATA_DIR / "tmp"
for directory in (DATA_DIR, UPLOAD_DIR, CLIP_DIR, TEMP_DIR):
    directory.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "clipforge.db"


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    return db


@contextmanager
def db_session():
    db = connect()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    with db_session() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS app_jobs (
            id TEXT PRIMARY KEY,
            source_path TEXT NOT NULL DEFAULT '',
            source_name TEXT NOT NULL,
            status TEXT NOT NULL,
            instructions TEXT NOT NULL DEFAULT '',
            settings_json TEXT NOT NULL DEFAULT '{}',
            transcript_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            finished_at TEXT,
            error TEXT,
            source_platform TEXT NOT NULL DEFAULT 'youtube'
        );
        CREATE TABLE IF NOT EXISTS app_clips (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES app_jobs(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            title TEXT NOT NULL,
            caption TEXT NOT NULL DEFAULT '',
            start REAL NOT NULL,
            end REAL NOT NULL,
            words_json TEXT NOT NULL DEFAULT '[]',
            edit_json TEXT NOT NULL DEFAULT '{}',
            render_status TEXT NOT NULL DEFAULT 'ready',
            render_error TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_posts (
            id TEXT PRIMARY KEY,
            clip_id TEXT NOT NULL REFERENCES app_clips(id),
            platform TEXT NOT NULL,
            scheduled_at TEXT NOT NULL,
            caption TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            error TEXT,
            external_id TEXT,
            created_at TEXT NOT NULL,
            published_at TEXT
        );
        CREATE TABLE IF NOT EXISTS app_oauth_states (state TEXT PRIMARY KEY, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS app_secrets (name TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON app_jobs(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_clips_job ON app_clips(job_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_posts_due ON app_posts(status, scheduled_at);
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(app_jobs)").fetchall()}
        if "source_platform" not in columns:
            db.execute("ALTER TABLE app_jobs ADD COLUMN source_platform TEXT NOT NULL DEFAULT 'youtube'")
        for col, statement in (
            ("transcript_json", "ALTER TABLE app_jobs ADD COLUMN transcript_json TEXT NOT NULL DEFAULT '{}'"),
            ("finished_at", "ALTER TABLE app_jobs ADD COLUMN finished_at TEXT"),
        ):
            if col not in columns:
                db.execute(statement)
        db.execute("CREATE INDEX IF NOT EXISTS idx_jobs_platform_date ON app_jobs(source_platform, created_at)")


def json_load(value: str | None, fallback: Any):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
