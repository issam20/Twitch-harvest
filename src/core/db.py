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
            # Migrations idempotentes via PRAGMA table_info (robuste)
            existing_cols = await self._table_columns(db, "clips")
            for col_name, col_sql in (
                ("thumbnail_url", "ALTER TABLE clips ADD COLUMN thumbnail_url TEXT"),
                ("processed_path", "ALTER TABLE clips ADD COLUMN processed_path TEXT"),
                ("category", "ALTER TABLE clips ADD COLUMN category TEXT"),
                ("edit_plan_json", "ALTER TABLE clips ADD COLUMN edit_plan_json TEXT"),
            ):
                if col_name not in existing_cols:
                    await db.execute(col_sql)
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
        category: str = "unknown",
        local_path: str | None = None,
    ) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO clips
                   (session_id, twitch_id, url, title, duration, created_at,
                    v_score, e_score, u_score, c_score, r_score, composite_score,
                    thumbnail_url, category, local_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, twitch_id, url, title, duration, created_at.isoformat(),
                    v_score, e_score, u_score, c_score, r_score, composite_score,
                    thumbnail_url, category, local_path,
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

    async def update_clip_edit_result(
        self,
        clip_id: int,
        edit_plan_json: str,
        category: str | None = None,
        processed_path: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """UPDATE clips
                   SET edit_plan_json = ?,
                       category = COALESCE(?, category),
                       processed_path = COALESCE(?, processed_path)
                   WHERE id = ?""",
                (edit_plan_json, category, processed_path, clip_id),
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

    async def get_last_session(self) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_session(self, session_id: int) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_clips_by_session(self, session_id: int) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM clips WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_clip_by_twitch_id(self, twitch_id: str) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM clips WHERE twitch_id = ?", (twitch_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_clip_by_id(self, clip_id: int) -> dict | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM clips WHERE id = ?", (clip_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_unprocessed_clips(self, session_id: int) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM clips
                   WHERE session_id = ?
                     AND local_path IS NOT NULL
                     AND processed_path IS NULL
                   ORDER BY composite_score DESC""",
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_clip_processed_path(self, twitch_id: str, processed_path: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE clips SET processed_path = ? WHERE twitch_id = ?",
                (processed_path, twitch_id),
            )
            await db.commit()

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

    @staticmethod
    async def _table_columns(db, table: str) -> set[str]:
        """Retourne l'ensemble des noms de colonnes d'une table via PRAGMA."""
        cursor = await db.execute(f"PRAGMA table_info({table})")
        rows = await cursor.fetchall()
        return {row[1] for row in rows}
