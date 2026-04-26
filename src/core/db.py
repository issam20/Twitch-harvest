"""SQLite async — sessions et clips avec scores de signaux."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    streamer TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    twitch_id TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    duration REAL NOT NULL DEFAULT 0,
    thumbnail_url TEXT,
    created_at TEXT NOT NULL,
    v_score REAL NOT NULL DEFAULT 0,
    e_score REAL NOT NULL DEFAULT 0,
    u_score REAL NOT NULL DEFAULT 0,
    c_score REAL NOT NULL DEFAULT 0,
    r_score REAL NOT NULL DEFAULT 0,
    composite_score REAL NOT NULL DEFAULT 0,
    local_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_clips_session ON clips(session_id);
CREATE INDEX IF NOT EXISTS idx_clips_composite ON clips(composite_score DESC);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            # Migration depuis l'ancien schéma (twitch_clips + clips sans sessions)
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
            )
            if not await cur.fetchone():
                await db.executescript(
                    "DROP TABLE IF EXISTS twitch_clips; DROP TABLE IF EXISTS clips;"
                )
            await db.executescript(SCHEMA)
            # Ajout colonne thumbnail_url si absente (migration idempotente)
            try:
                await db.execute("ALTER TABLE clips ADD COLUMN thumbnail_url TEXT")
            except Exception:
                pass
            await db.commit()

    async def create_session(self, streamer: str, started_at: datetime) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "INSERT INTO sessions (streamer, started_at) VALUES (?, ?)",
                (streamer, started_at.isoformat()),
            )
            await db.commit()
            return cursor.lastrowid or -1

    async def close_session(self, session_id: int, ended_at: datetime) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                (ended_at.isoformat(), session_id),
            )
            await db.commit()

    async def record_clip(
        self,
        session_id: int,
        twitch_id: str,
        url: str,
        title: str,
        duration: float,
        created_at: datetime,
        v_score: float,
        e_score: float,
        u_score: float,
        c_score: float,
        r_score: float,
        composite_score: float,
        thumbnail_url: str | None = None,
        local_path: str | None = None,
    ) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO clips
                   (session_id, twitch_id, url, title, duration, created_at,
                    v_score, e_score, u_score, c_score, r_score, composite_score,
                    thumbnail_url, local_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, twitch_id, url, title, duration, created_at.isoformat(),
                    v_score, e_score, u_score, c_score, r_score, composite_score,
                    thumbnail_url, local_path,
                ),
            )
            await db.commit()
            return cursor.lastrowid or -1

    async def update_clip_local_path(self, twitch_id: str, local_path: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE clips SET local_path = ? WHERE twitch_id = ?",
                (local_path, twitch_id),
            )
            await db.commit()

    async def get_session_stats(self, session_id: int) -> dict:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """SELECT COUNT(*) as clip_count, MAX(composite_score) as top_score
                   FROM clips WHERE session_id = ?""",
                (session_id,),
            )
            row = await cursor.fetchone()
            return {
                "clip_count": row[0] if row else 0,
                "top_score": row[1] or 0.0 if row else 0.0,
            }

    async def get_clips_by_session(self, session_id: int) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM clips WHERE session_id = ? ORDER BY composite_score DESC",
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_all_sessions(self) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT s.id, s.streamer, s.started_at, s.ended_at,
                          COUNT(c.id) as clip_count,
                          COALESCE(MAX(c.composite_score), 0.0) as top_score
                   FROM sessions s
                   LEFT JOIN clips c ON c.session_id = s.id
                   GROUP BY s.id
                   ORDER BY s.started_at DESC"""
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
