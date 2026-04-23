"""SQLite async pour l'etat: clips produits, dernier pic par channel, etc."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    path TEXT NOT NULL,
    score REAL NOT NULL,
    category TEXT NOT NULL,
    reason TEXT,
    peak_ts TEXT NOT NULL,
    created_at TEXT NOT NULL,
    published INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_clips_channel_ts ON clips(channel, peak_ts);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def record_clip(
        self,
        channel: str,
        path: str,
        score: float,
        category: str,
        reason: str,
        peak_ts: datetime,
    ) -> int:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                """INSERT INTO clips (channel, path, score, category, reason, peak_ts, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    channel,
                    path,
                    score,
                    category,
                    reason,
                    peak_ts.isoformat(),
                    datetime.utcnow().isoformat(),
                ),
            )
            await db.commit()
            return cursor.lastrowid or -1
