# CONTEXT.md — Twitch Harvest

> Généré automatiquement le **2026-04-29 11:55:55 UTC**
> Ne pas éditer manuellement — mis à jour automatiquement à chaque push sur `main`.

## État du projet

| Module | Description | Statut |
|--------|-------------|--------|
| `src/core/config.py` | Config Pydantic — Env + Settings + StreamerConfig | ✅ |
| `src/core/db.py` | SQLite async — sessions + clips + scores | ✅ |
| `src/core/events.py` | Dataclasses partagées — TwitchClip, ClipCandidate, etc. | ✅ |
| `src/detector/scorer.py` | ViralScorer — fusion composite des signaux | ✅ |
| `src/detector/chat_velocity.py` | Velocity Z-score adaptatif | ✅ |
| `src/detector/chat_signals.py` | Pipeline de signaux IRC | ✅ |
| `src/detector/emote_spam.py` | Détection spam d'emotes funny/hype/shock | ✅ |
| `src/orchestrator/harvest.py` | HarvestPipeline — orchestration clip + edit | ✅ |
| `src/editor/ai_analyzer.py` | DeepSeek V4 Flash → EditPlan JSON structuré | ✅ |
| `src/editor/video_editor.py` | Pipeline render : ffmpeg + Whisper + Remotion | ✅ |
| `src/editor/ffmpeg_preprocessor.py` | FFmpeg trim + crop 9:16 faststart | 🔧 |
| `src/editor/whisper_transcriber.py` | Transcription mot/mot via faster-whisper | 🔧 |
| `src/remotion/TwitchClip/` | Composition React — captions + titre + grade + zoom | ✅ |

## Schéma base de données

```sql
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
```

## Fichiers source

### `src/core/config.py`

```python
"""Chargement de la configuration: env + YAML global + YAML par streamer."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------- ENV (secrets) ----------

class Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    twitch_client_id: str = ""
    twitch_client_secret: str = ""
    twitch_irc_token: str = ""         # format "oauth:xxxx" — pour IRC chat
    twitch_irc_nick: str = ""
    twitch_user_token: str = ""        # token avec clips:edit (sans prefix oauth:)

    anthropic_api_key: str = ""
    deepseek_api_key: str = ""

    data_dir: Path = Path("./data")


# ---------- YAML global ----------

class DetectorConfig(BaseModel):
    chat_window_seconds: int = 10
    baseline_window_seconds: int = 60       # utilisé par unique chatters tracker
    emote_density_threshold: float = 0.35
    min_viral_score: float = 60.0
    cooldown_seconds: int = 60
    emotes: dict[str, list[str]] = Field(default_factory=dict)

    # Velocity Z-score (remplace chat_velocity_threshold + velocity_multiplier)
    stats_window_seconds: int = 300         # historique pour μ/σ (5 min)
    z_score_threshold: float = 2.5          # σ au-dessus de μ pour déclencher
    velocity_floor: float = 0.3             # msg/s minimum absolu (anti-bruit)
    warmup_samples: int = 30               # ticks avant activation (~60 s)



class ClipperConfig(BaseModel):
    buffer_seconds: int = 120
    segment_duration: int = 10
    clip_pre_seconds: int = 15
    clip_post_seconds: int = 15
    stream_quality: str = "720p60,720p,best"


class OrchestratorConfig(BaseModel):
    log_level: str = "INFO"


class HookTemplates(BaseModel):
    funny: str = "WAIT FOR IT 😭"
    hype: str = "WATCH THIS 🔥"
    shock: str = "NO WAY 💀"
    unknown: str = "CHAT WENT CRAZY"


class EditorConfig(BaseModel):
    whisper_model: str = "medium"
    subtitle_fontsize: int = 75
    subtitle_color: str = "&H00FFFF"
    subtitle_outline: int = 3
    gameplay_ratio: float = 0.55
    clip_duration: int = 15
    pre_peak_seconds: int = 3
    peak_offset_seconds: int = -3
    hook_duration: float = 2.0
    hook_fontsize: int = 90
    hook_templates: HookTemplates = HookTemplates()


class Settings(BaseModel):
    detector: DetectorConfig = DetectorConfig()
    clipper: ClipperConfig = ClipperConfig()
    orchestrator: OrchestratorConfig = OrchestratorConfig()
    editor: EditorConfig = EditorConfig()


# ---------- YAML par streamer ----------

class WebcamZone(BaseModel):
    x_pct: float = 0.75
    y_pct: float = 0.70
    w_pct: float = 0.24
    h_pct: float = 0.28


class StreamerConfig(BaseModel):
    login: str
    display_name: str = ""
    language: str = "en"
    webcam_zone: WebcamZone = WebcamZone()
    detector_overrides: dict[str, Any] = Field(default_factory=dict)
    default_tags: list[str] = Field(default_factory=list)


# ---------- loaders ----------

def load_settings(path: Path = Path("config/settings.yaml")) -> Settings:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Settings(**data)


def load_streamer(login: str, base: Path = Path("config/streamers")) -> StreamerConfig:
    path = base / f"{login}.yaml"
    if not path.exists():
        # Config par defaut si absente
        return StreamerConfig(login=login, display_name=login)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return StreamerConfig(**data)


def apply_streamer_overrides(settings: Settings, streamer: StreamerConfig) -> Settings:
    """Retourne un nouveau Settings avec les overrides du streamer appliques."""
    if not streamer.detector_overrides:
        return settings
    detector_dict = settings.detector.model_dump()
    detector_dict.update(streamer.detector_overrides)
    return settings.model_copy(update={"detector": DetectorConfig(**detector_dict)})
```

### `src/core/db.py`

```python
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
```

### `src/core/events.py`

```python
"""Types de donnees echangees entre les modules via asyncio.Queue."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ChatEvent:
    """Un message chat IRC."""
    timestamp: datetime
    channel: str
    author: str
    content: str
    emotes: list[str] = field(default_factory=list)  # noms des emotes detectees


class MomentCategory(str, Enum):
    FUNNY = "funny"
    HYPE = "hype"
    SHOCK = "shock"
    UNKNOWN = "unknown"


@dataclass
class ClipCandidate:
    """Signal emis par le Detector -> a clipper."""
    timestamp: datetime          # moment pic
    channel: str
    score: float                 # 0-100
    category: MomentCategory
    reason: str                  # explication humaine (pour les logs)
    chat_velocity: float         # msg/s au moment du pic
    emote_density: float         # ratio emotes
    sample_messages: list[str] = field(default_factory=list)


@dataclass
class Clip:
    """Clip video extrait par le Clipper."""
    path: str
    channel: str
    candidate: ClipCandidate
    duration: float
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class TwitchClip:
    """Clip récupéré via l'API Helix Twitch."""
    id: str                  # slug unique Twitch (ex: "FunnyPlayXYZ")
    url: str                 # https://www.twitch.tv/clips/<id>
    title: str
    channel: str             # login du broadcaster
    creator_name: str
    view_count: int
    duration: float          # secondes
    created_at: datetime
    thumbnail_url: str
    local_path: str | None = None
    v_score: float = 0.0
    e_score: float = 0.0
    u_score: float = 0.0
    c_score: float = 0.0
    r_score: float = 0.0
    composite_score: float = 0.0
```

### `src/detector/scorer.py`

```python
"""Scorer: fusionne les 5 signaux chat et décide de clipper.

Règle de déclenchement :
  - Signal 1 (velocity / Z-score) OBLIGATOIRE — doit être > 0
  - Au moins 1 signal parmi {emote, unique chatters, caps, copypasta} doit être > 0
  → 2 signaux minimum dont velocity

Score global = 50% velocity + 50% moyenne des autres signaux déclenchés.

Cooldown de 120s entre deux clips pour éviter les doublons.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..core.events import ClipCandidate, MomentCategory
from ..core.logging import logger


class ViralScorer:
    def __init__(self, cooldown_seconds: int = 120, min_viral_score: float = 60.0) -> None:
        self.cooldown = timedelta(seconds=cooldown_seconds)
        self.min_viral_score = min_viral_score
        self._last_trigger_at: datetime | None = None

    def evaluate(
        self,
        now: datetime,
        channel: str,
        # Signal 1 — obligatoire
        velocity_score: float,
        velocity_debug: dict,
        # Signal 2
        emote_score: float,
        emote_category: MomentCategory,
        emote_debug: dict,
        # Signal 3
        unique_score: float,
        unique_debug: dict,
        # Signal 4
        caps_score: float,
        caps_debug: dict,
        # Signal 5
        repetition_score: float,
        repetition_debug: dict,
        # Contexte
        sample_messages: list[str],
    ) -> ClipCandidate | None:

        # --- Gate 1 : velocity obligatoire ---
        if velocity_score <= 0:
            return None

        # --- Gate 2 : ≥1 autre signal déclenché ---
        others = {
            "emote": emote_score,
            "chatters": unique_score,
            "caps": caps_score,
            "copypasta": repetition_score,
        }
        triggered_others = {name: s for name, s in others.items() if s > 0}
        if not triggered_others:
            return None

        # --- Score global ---
        avg_others = sum(triggered_others.values()) / len(triggered_others)
        total_score = 0.50 * velocity_score + 0.50 * avg_others

        triggered_names = ["velocity"] + list(triggered_others.keys())
        logger.debug(
            f"[scorer] score={total_score:.1f} signaux=[{', '.join(triggered_names)}] | "
            f"v={velocity_score:.0f}(Z={velocity_debug.get('z', '?')}) "
            f"e={emote_score:.0f} u={unique_score:.0f} c={caps_score:.0f} r={repetition_score:.0f}"
        )

        # --- Seuil minimum ---
        if total_score < self.min_viral_score:
            logger.debug(f"[scorer] score {total_score:.1f} < min {self.min_viral_score} — ignoré")
            return None

        # --- Cooldown ---
        if self._last_trigger_at is not None:
            elapsed = now - self._last_trigger_at
            if elapsed < self.cooldown:
                remaining = (self.cooldown - elapsed).total_seconds()
                logger.debug(f"[scorer] cooldown actif — {remaining:.0f}s restantes")
                return None

        self._last_trigger_at = now

        reason = (
            f"score={total_score:.1f} [{'+'.join(triggered_names)}] | "
            f"velocity {velocity_debug.get('velocity', '?')} msg/s "
            f"(Z={velocity_debug.get('z', '?')}, μ={velocity_debug.get('mean', '?')})"
        )
        if "emote" in triggered_others:
            reason += f" | emotes {emote_debug.get('density', 0)*100:.0f}%"
        if "chatters" in triggered_others:
            reason += f" | chatters ×{unique_debug.get('ratio', '?')}"
        if "caps" in triggered_others:
            reason += f" | caps {caps_debug.get('caps_ratio', 0)*100:.0f}%"
        if "copypasta" in triggered_others:
            reason += f" | copypasta {repetition_debug.get('top_ratio', 0)*100:.0f}%"

        return ClipCandidate(
            timestamp=now,
            channel=channel,
            score=total_score,
            category=emote_category,
            reason=reason,
            chat_velocity=velocity_debug.get("velocity", 0.0),
            emote_density=emote_debug.get("density", 0.0),
            sample_messages=sample_messages,
        )
```

### `src/detector/chat_velocity.py`

```python
"""Signal 1 : velocity du chat — Z-score adaptatif.

Compare la velocity courante (fenêtre de 10 s) à la distribution historique
sur les 5 dernières minutes. Un Z-score élevé = moment statistiquement
inhabituel, quelle que soit la popularité du streamer.

Algorithme :
  1. Chaque appel à score() produit un échantillon de velocity (msg/s sur 10 s).
  2. On maintient jusqu'à stats_window_seconds d'échantillons (~1 par 2 s).
  3. Z = (v_courante − μ) / max(σ, plancher)
  4. Score 0-100 via tanh si Z > z_score_threshold et v > velocity_floor.
  5. Pas de score pendant la période de chauffe (< warmup_samples).

Avantage vs ratio simple : se calibre automatiquement au niveau d'activité du
streamer. xQc (μ=80 msg/s, σ=12) exige ≈112 msg/s pour Z=2.5 ; zerator
(μ=5, σ=1.5) exige ≈9 msg/s pour le même Z.
"""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timedelta

from ..core.events import ChatEvent


class ChatVelocityTracker:
    def __init__(
        self,
        window_seconds: int = 10,
        stats_window_seconds: int = 300,
        velocity_floor: float = 0.3,        # msg/s minimum absolu (anti-bruit)
        z_score_threshold: float = 2.5,     # σ au-dessus de μ pour déclencher
        warmup_samples: int = 30,           # ~60 s à 2 s/tick avant de scorer
    ):
        self.window_seconds = window_seconds
        self.stats_window_seconds = stats_window_seconds
        self.velocity_floor = velocity_floor
        self.z_score_threshold = z_score_threshold
        self.warmup_samples = warmup_samples

        self._history: deque[datetime] = deque()
        self._samples: deque[float] = deque()        # velocity (msg/s) par tick
        self._samples_ts: deque[datetime] = deque()  # timestamp de chaque sample

    def add(self, event: ChatEvent) -> None:
        self._history.append(event.timestamp)
        cutoff = event.timestamp - timedelta(seconds=self.stats_window_seconds)
        while self._history and self._history[0] < cutoff:
            self._history.popleft()

    def velocity(self, now: datetime) -> float:
        """msg/s sur la fenêtre courte."""
        cutoff = now - timedelta(seconds=self.window_seconds)
        return sum(1 for t in self._history if t >= cutoff) / self.window_seconds

    def score(self, now: datetime) -> tuple[float, dict]:
        """Retourne (score_0_100, debug_info)."""
        v = self.velocity(now)

        # Purger les vieux samples
        cutoff_ts = now - timedelta(seconds=self.stats_window_seconds)
        while self._samples_ts and self._samples_ts[0] < cutoff_ts:
            self._samples_ts.popleft()
            self._samples.popleft()

        # Calculer les stats AVANT d'ajouter le sample courant (évite l'auto-contamination)
        n = len(self._samples)
        if n >= self.warmup_samples:
            vals = list(self._samples)
            mean = sum(vals) / n
            variance = sum((x - mean) ** 2 for x in vals) / n
            std = math.sqrt(variance)
            # Plancher sur σ : au moins 5 % de μ ou 0.2 msg/s pour éviter div/0 sur chats stables
            std_eff = max(std, mean * 0.05, 0.2)
            z = (v - mean) / std_eff
        else:
            mean = std = z = 0.0

        # Ajouter le sample courant APRÈS le calcul
        self._samples.append(v)
        self._samples_ts.append(now)

        debug: dict = {
            "velocity": round(v, 2),
            "mean": round(mean, 2),
            "std": round(std, 2),
            "z": round(z, 2),
            "samples": len(self._samples),
        }

        if n < self.warmup_samples:
            return 0.0, {**debug, "reason": f"warmup ({n}/{self.warmup_samples})"}

        if v < self.velocity_floor:
            return 0.0, {**debug, "reason": "below_floor"}

        if z < self.z_score_threshold:
            return 0.0, {**debug, "reason": f"z={z:.2f}<{self.z_score_threshold}"}

        # tanh : Z=z_threshold → ~0, Z=2×z_threshold → ~76, sature vers 100
        normalized = (z - self.z_score_threshold) / self.z_score_threshold
        s = 100.0 * math.tanh(normalized)
        return max(0.0, s), debug
```

### `src/detector/chat_signals.py`

```python
"""Signaux 3, 4, 5: unique chatters, ALL CAPS ratio, répétition/copypasta."""
from __future__ import annotations

import math
from collections import Counter, deque
from datetime import datetime, timedelta

from ..core.events import ChatEvent


class UniqueChattersTracker:
    """Signal 3 — chatters distincts dans la fenêtre vs baseline.

    Détecte l'effet de masse : beaucoup de gens différents qui réagissent
    en même temps, pas juste les habitués du chat.
    """

    def __init__(
        self,
        window_seconds: int = 10,
        baseline_seconds: int = 120,
        multiplier_threshold: float = 2.0,
    ) -> None:
        self.window_seconds = window_seconds
        self.baseline_seconds = baseline_seconds
        self.multiplier_threshold = multiplier_threshold
        self._history: deque[tuple[datetime, str]] = deque()  # (ts, author)

    def add(self, event: ChatEvent) -> None:
        self._history.append((event.timestamp, event.author))
        cutoff = event.timestamp - timedelta(seconds=self.baseline_seconds)
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def score(self, now: datetime) -> tuple[float, dict]:
        win_cutoff = now - timedelta(seconds=self.window_seconds)
        base_cutoff = now - timedelta(seconds=self.baseline_seconds)

        win_authors = [a for t, a in self._history if t >= win_cutoff]
        base_authors = [a for t, a in self._history if base_cutoff <= t < win_cutoff]

        base_duration = self.baseline_seconds - self.window_seconds
        if base_duration <= 0 or len(base_authors) < 5:
            return 0.0, {"reason": "no_baseline"}

        # Taux unique chatters/s dans chaque période
        current_rate = len(set(win_authors)) / self.window_seconds
        baseline_rate = len(set(base_authors)) / base_duration
        baseline_eff = max(baseline_rate, 0.05)

        ratio = current_rate / baseline_eff
        debug = {
            "current_unique": len(set(win_authors)),
            "baseline_rate_per_s": round(baseline_rate, 3),
            "ratio": round(ratio, 2),
        }

        if ratio < self.multiplier_threshold:
            return 0.0, debug

        score = 100.0 * math.tanh((ratio - self.multiplier_threshold) / self.multiplier_threshold)
        return round(score, 1), debug


class CapsRatioTracker:
    """Signal 4 — ratio de messages en majuscules.

    Un pic de CAPS = réaction émotionnelle collective (choc, hype soudaine).
    On considère un message "CAPS" si >60% de ses lettres sont en majuscules.
    """

    def __init__(self, window_seconds: int = 10, threshold: float = 0.25) -> None:
        self.window_seconds = window_seconds
        self.threshold = threshold
        self._history: deque[tuple[datetime, bool]] = deque()  # (ts, is_caps)

    def add(self, event: ChatEvent) -> None:
        content = event.content.strip()
        alpha = [c for c in content if c.isalpha()]
        is_caps = len(alpha) >= 3 and (sum(1 for c in alpha if c.isupper()) / len(alpha)) > 0.60
        self._history.append((event.timestamp, is_caps))
        cutoff = event.timestamp - timedelta(seconds=self.window_seconds)
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def score(self, now: datetime) -> tuple[float, dict]:
        cutoff = now - timedelta(seconds=self.window_seconds)
        window = [(t, c) for t, c in self._history if t >= cutoff]

        if len(window) < 5:
            return 0.0, {"reason": "not_enough_msgs"}

        ratio = sum(1 for _, c in window if c) / len(window)
        debug = {"caps_ratio": round(ratio, 3), "total_msgs": len(window)}

        if ratio < self.threshold:
            return 0.0, debug

        # Score linéaire : threshold → 50, 2×threshold → 100 (plafonné)
        score = min(100.0, ((ratio - self.threshold) / self.threshold) * 50.0 + 50.0)
        return round(score, 1), debug


class RepetitionTracker:
    """Signal 5 — ratio de messages copypasta (même texte répété).

    Un moment emblématique génère du copypasta : "OMEGALUL OMEGALUL OMEGALUL",
    un mème de chat, etc. On normalise le texte avant de comparer.
    """

    def __init__(self, window_seconds: int = 10, threshold: float = 0.20) -> None:
        self.window_seconds = window_seconds
        self.threshold = threshold
        self._history: deque[tuple[datetime, str]] = deque()  # (ts, normalized_msg)

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.lower().split())[:80]

    def add(self, event: ChatEvent) -> None:
        norm = self._normalize(event.content)
        self._history.append((event.timestamp, norm))
        cutoff = event.timestamp - timedelta(seconds=self.window_seconds)
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def score(self, now: datetime) -> tuple[float, dict]:
        cutoff = now - timedelta(seconds=self.window_seconds)
        window = [msg for t, msg in self._history if t >= cutoff]

        if len(window) < 5:
            return 0.0, {"reason": "not_enough_msgs"}

        top_msg, top_count = Counter(window).most_common(1)[0]
        ratio = top_count / len(window)
        debug = {"top_ratio": round(ratio, 3), "top_msg": top_msg[:40], "total_msgs": len(window)}

        if ratio < self.threshold:
            return 0.0, debug

        score = min(100.0, ((ratio - self.threshold) / self.threshold) * 50.0 + 50.0)
        return round(score, 1), debug
```

### `src/detector/emote_spam.py`

```python
"""Signal 2: densite d'emotes reactives dans le chat.

Pour chaque message recu, on regarde quelles emotes "reactives" il contient.
On track la densite = (messages contenant >=1 emote reactive) / (messages totaux)
sur la fenetre glissante. Au-dessus de density_threshold, c'est un signal fort.

On determine aussi la CATEGORIE dominante (laugh/hype/shock) pour aider l'Etage 2.
"""
from __future__ import annotations

from collections import Counter, deque
from datetime import datetime, timedelta

from ..core.events import ChatEvent, MomentCategory


class EmoteDensityTracker:
    def __init__(
        self,
        window_seconds: int = 10,
        density_threshold: float = 0.35,
        emote_categories: dict[str, list[str]] | None = None,
    ):
        self.window_seconds = window_seconds
        self.density_threshold = density_threshold

        # Mapping emote -> categorie (inverse du dict de config)
        self._emote_to_cat: dict[str, str] = {}
        if emote_categories:
            for cat, emotes in emote_categories.items():
                for e in emotes:
                    self._emote_to_cat[e] = cat

        # deque de (timestamp, categorie_trouvee_ou_None)
        self._history: deque[tuple[datetime, str | None]] = deque()

    def add(self, event: ChatEvent) -> None:
        # On prend la premiere categorie matchee dans le message (suffit pour ce signal)
        category = None
        for emote in event.emotes:
            if emote in self._emote_to_cat:
                category = self._emote_to_cat[emote]
                break
        self._history.append((event.timestamp, category))

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.window_seconds)
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def score(self, now: datetime) -> tuple[float, MomentCategory, dict]:
        """Retourne (score_0_100, categorie_dominante, debug_info)."""
        self._prune(now)

        if len(self._history) < 5:  # pas assez de signal
            return 0.0, MomentCategory.UNKNOWN, {"reason": "not_enough_msgs"}

        total = len(self._history)
        with_emote = [cat for _, cat in self._history if cat is not None]
        density = len(with_emote) / total

        # categorie dominante parmi les messages avec emote
        if with_emote:
            cat_counter = Counter(with_emote)
            dominant = cat_counter.most_common(1)[0][0]
            dominant_category = MomentCategory(dominant) if dominant in {"funny", "hype", "shock"} else MomentCategory.UNKNOWN
            # mapping laugh->funny
            if dominant == "laugh":
                dominant_category = MomentCategory.FUNNY
        else:
            dominant_category = MomentCategory.UNKNOWN

        # Scoring: 0 sous le seuil, progression lineaire au-dessus jusqu'a densite=0.8->100
        if density < self.density_threshold:
            score = 0.0
        else:
            # mapping lineaire [threshold, 0.8] -> [30, 100]
            ceiling = 0.8
            if density >= ceiling:
                score = 100.0
            else:
                progress = (density - self.density_threshold) / (ceiling - self.density_threshold)
                score = 30.0 + progress * 70.0

        return score, dominant_category, {
            "density": round(density, 3),
            "total_msgs": total,
            "with_emote": len(with_emote),
        }
```

### `src/orchestrator/harvest.py`
_(tronqué à 300 lignes sur 433)_

```python
"""HarvestPipeline — écoute le chat en continu et crée un clip Twitch natif
dès qu'un spike est détecté sur les 5 signaux.

Flow :
  1. Vérifie que le streamer est live → StreamerOfflineError sinon
  2. Lance le bot IRC en tâche de fond
  3. Toutes les 2s, évalue les 5 signaux et émet un snapshot au broadcaster
  4. Si velocity (obligatoire) + ≥1 autre signal déclenchés :
       → POST /helix/clips en tâche de fond (non-bloquant)
       → poll GET /helix/clips jusqu'à disponibilité
       → persiste en SQLite + notifie le broadcaster
  5. Cooldown 120s entre deux clips
  6. Toutes les 2min, vérifie que le stream est toujours live
  7. S'arrête sur Ctrl+C ou stream offline
"""
from __future__ import annotations

import shutil

import asyncio
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_PARIS = ZoneInfo("Europe/Paris")
from typing import TYPE_CHECKING

from ..api.twitch import TwitchAPIClient
from ..core.config import Env, Settings, StreamerConfig, apply_streamer_overrides
from ..core.db import Database
from ..core.events import ChatEvent, ClipCandidate, TwitchClip
from ..core.logging import logger
from ..detector.chat_signals import CapsRatioTracker, RepetitionTracker, UniqueChattersTracker
from ..detector.chat_velocity import ChatVelocityTracker
from ..detector.emote_spam import EmoteDensityTracker
from ..detector.scorer import ViralScorer
from ..watcher.chat_capture import run_chat_capture

if TYPE_CHECKING:
    from ..api.dashboard import SignalBroadcaster

_CLIP_PROCESS_DELAY  = 20
_CLIP_POLL_RETRIES   = 6
_CLIP_POLL_INTERVAL  = 10
_LIVENESS_CHECK_INTERVAL = 120
_CHAT_BATCH = 5   # messages chat envoyés au dashboard par snapshot


class HarvestPipeline:
    def __init__(
        self,
        env: Env,
        settings: Settings,
        streamer: StreamerConfig,
        cooldown_seconds: int = 120,
        broadcaster: SignalBroadcaster | None = None,
    ) -> None:
        self.env = env
        self.streamer = streamer
        self.settings = apply_streamer_overrides(settings, streamer)
        self.db = Database(env.data_dir / "state.db")
        self.broadcaster = broadcaster

        cfg = self.settings.detector
        self._known_emotes: set[str] = {e for emotes in cfg.emotes.values() for e in emotes}

        self.velocity_tracker = ChatVelocityTracker(
            window_seconds=cfg.chat_window_seconds,
            stats_window_seconds=cfg.stats_window_seconds,
            velocity_floor=cfg.velocity_floor,
            z_score_threshold=cfg.z_score_threshold,
            warmup_samples=cfg.warmup_samples,
        )
        self.emote_tracker = EmoteDensityTracker(
            window_seconds=cfg.chat_window_seconds,
            density_threshold=cfg.emote_density_threshold,
            emote_categories=cfg.emotes,
        )
        self.unique_tracker = UniqueChattersTracker(
            window_seconds=cfg.chat_window_seconds,
            baseline_seconds=cfg.baseline_window_seconds,
        )
        self.caps_tracker = CapsRatioTracker(window_seconds=cfg.chat_window_seconds)
        self.repetition_tracker = RepetitionTracker(window_seconds=cfg.chat_window_seconds)
        self.scorer = ViralScorer(
            cooldown_seconds=cooldown_seconds,
            min_viral_score=cfg.min_viral_score,
        )

        self._chat_queue: asyncio.Queue[ChatEvent] = asyncio.Queue(maxsize=10_000)
        self._recent_msgs: deque[str] = deque(maxlen=20)
        # Buffer de messages récents pour le dashboard (author, content)
        self._chat_batch: deque[dict] = deque(maxlen=_CHAT_BATCH)
        self._stop = asyncio.Event()
        self._collected: list[TwitchClip] = []
        self._msg_count: int = 0
        self._clip_in_progress: bool = False
        self._session_id: int | None = None

        self._raw_dir = env.data_dir / "clips" / "raw"
        self._processed_dir = env.data_dir / "clips" / "processed"
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self._processed_dir.mkdir(parents=True, exist_ok=True)

    async def run(self) -> list[TwitchClip]:
        await self.db.init()
        # Vérification système : streamlink doit être installé
        if shutil.which("streamlink") is None:
            logger.error("[harvest] streamlink introuvable dans le PATH — vérifier l'installation système")
            logger.error("  https://streamlink.github.io/install.html")
            self.stop()
            return []

        self._session_id = await self.db.create_session(
            self.streamer.login, datetime.now(timezone.utc)
        )
        logger.info(f"[harvest] session #{self._session_id} ouverte pour {self.streamer.login}")

        try:
            async with TwitchAPIClient(
                self.env.twitch_client_id, self.env.twitch_client_secret
            ) as api:
                stream = await api.require_live(self.streamer.login)
                broadcaster_id: str = stream["user_id"]
                started_at = datetime.fromisoformat(stream["started_at"].replace("Z", "+00:00"))

                logger.info(
                    f"[harvest] {self.streamer.login} est live depuis "
                    f"{started_at.astimezone(_PARIS).strftime('%H:%M:%S')} (Paris) — écoute du chat démarrée"
                )

                chat_task = asyncio.create_task(self._run_chat(), name="chat")
                tasks = [
                    chat_task,
                    asyncio.create_task(self._chat_consumer(), name="consumer"),
                    asyncio.create_task(self._scoring_loop(api, broadcaster_id), name="scorer"),
                    asyncio.create_task(self._liveness_loop(api), name="liveness"),
                    asyncio.create_task(self._watch_chat_task(chat_task), name="chat_watchdog"),
                ]

                await self._stop.wait()
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            logger.info(f"[harvest] terminé — {len(self._collected)} clip(s) créé(s)")
            if self._session_id is not None:
                await self.db.close_session(self._session_id, datetime.now(timezone.utc))
                stats = await self.db.get_session_stats(self._session_id)
                logger.info(
                    f"[harvest] session #{self._session_id} fermée — "
                    f"{stats['clip_count']} clips | top score {stats['top_score']:.1f}"
                )

        return self._collected

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------

    async def _run_chat(self) -> None:
        logger.info(f"[chat] connexion IRC nick={self.env.twitch_irc_nick!r} sur #{self.streamer.login}")
        try:
            await run_chat_capture(
                token=self.env.twitch_irc_token,
                nick=self.env.twitch_irc_nick,
                channel=self.streamer.login,
                queue=self._chat_queue,
                known_emotes=self._known_emotes,
            )
        except Exception as exc:
            logger.error(f"[chat] ERREUR IRC : {exc!r}")
            raise

    async def _watch_chat_task(self, chat_task: asyncio.Task) -> None:
        try:
            await chat_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(
                f"[chat] task IRC arrêtée : {exc!r}\n"
                "  Vérifie TWITCH_IRC_TOKEN et TWITCH_IRC_NICK."
            )
            self.stop()

    async def _chat_consumer(self) -> None:
        while not self._stop.is_set():
            try:
                event = await asyncio.wait_for(self._chat_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            self.velocity_tracker.add(event)
            self.emote_tracker.add(event)
            self.unique_tracker.add(event)
            self.caps_tracker.add(event)
            self.repetition_tracker.add(event)
            self._recent_msgs.append(f"{event.author}: {event.content}")
            self._chat_batch.append({"a": event.author, "c": event.content})
            self._msg_count += 1

    async def _scoring_loop(self, api: TwitchAPIClient, broadcaster_id: str) -> None:
        tick = 0
        while not self._stop.is_set():
            await asyncio.sleep(2.0)
            tick += 1
            now = datetime.now(_PARIS)

            v_score, v_debug = self.velocity_tracker.score(now)
            e_score, e_cat, e_debug = self.emote_tracker.score(now)
            u_score, u_debug = self.unique_tracker.score(now)
            c_score, c_debug = self.caps_tracker.score(now)
            r_score, r_debug = self.repetition_tracker.score(now)

            # Score composite : monte à 100 quand les 2 gates sont franchies
            other_scores = [e_score, u_score, c_score, r_score]
            triggered_others = [s for s in other_scores if s > 0]
            if v_score > 0 and triggered_others:
                avg_others = sum(triggered_others) / len(triggered_others)
                composite = 0.50 * v_score + 0.50 * avg_others
            else:
                composite = 0.0

            # Snapshot pour le dashboard (toutes les 2s)
            if self.broadcaster:
                snap: dict = {
                    "t": now.strftime("%H:%M:%S"),
                    "msgs": self._msg_count,
                    "v_val":     round(v_debug.get("velocity", 0.0), 2),
                    "v_base":    round(v_debug.get("mean", 0.0), 2),
                    "v_z":       round(v_debug.get("z", 0.0), 2),
                    "v_samples": v_debug.get("samples", 0),
                    "warmup_max": self.velocity_tracker.warmup_samples,
                    "v_score": round(v_score, 1),
                    "e_score": round(e_score, 1),
                    "u_score": round(u_score, 1),
                    "c_score": round(c_score, 1),
                    "r_score": round(r_score, 1),
                    "composite": round(composite, 1),
                    "chat_msgs": list(self._chat_batch),
                    "clip": None,
                }
                self._chat_batch.clear()
                await self.broadcaster.emit(snap)

            # Log toutes les 30s
            if tick % 15 == 0:
                v_vel  = v_debug.get("velocity", 0.0)
                v_mean = v_debug.get("mean", 0.0)
                v_z    = v_debug.get("z", 0.0)
                v_samples = v_debug.get("samples", 0)
                e_dens = e_debug.get("density", 0.0) if isinstance(e_debug, dict) else 0.0
                u_ratio = u_debug.get("ratio", 0.0)  if isinstance(u_debug, dict) else 0.0
                c_ratio = c_debug.get("caps_ratio", 0.0)
                r_ratio = r_debug.get("top_ratio", 0.0)
                triggered = (
                    (["VEL"] if v_score > 0 else []) +
                    (["EMO"] if e_score > 0 else []) +
                    (["UNI"] if u_score > 0 else []) +
                    (["CAP"] if c_score > 0 else []) +
                    (["REP"] if r_score > 0 else [])
                )
                warmup_note = f" [warmup {v_samples}/{self.velocity_tracker.warmup_samples}]" if v_score == 0 and v_samples < self.velocity_tracker.warmup_samples else ""
                logger.info(
                    f"[signals] msgs={self._msg_count} | "
                    f"VEL {v_vel:.2f}msg/s (μ={v_mean:.2f}, Z={v_z:.1f}) score={v_score:.0f}{warmup_note} | "
                    f"EMO {e_dens*100:.0f}% score={e_score:.0f} | "
                    f"UNI ×{u_ratio:.1f} score={u_score:.0f} | "
                    f"CAP {c_ratio*100:.0f}% score={c_score:.0f} | "
                    f"REP {r_ratio*100:.0f}% score={r_score:.0f} | "
                    f"COMP={composite:.0f}"
                    + (f"  ← [{'+'.join(triggered)}]" if triggered else "")
                )

            candidate = self.scorer.evaluate(
                now=now, channel=self.streamer.login,
                velocity_score=v_score, velocity_debug=v_debug,
                emote_score=e_score, emote_category=e_cat, emote_debug=e_debug,
                unique_score=u_score, unique_debug=u_debug,
                caps_score=c_score, caps_debug=c_debug,
                repetition_score=r_score, repetition_debug=r_debug,
                sample_messages=list(self._recent_msgs)[-10:],
            )

            if candidate is None:
                continue

            logger.info(f"[harvest] SPIKE — {candidate.reason}")

            if not self._clip_in_progress:
                self._clip_in_progress = True
                asyncio.create_task(self._clip_task(
                    api, broadcaster_id,
                    v_score, e_score, u_score, c_score, r_score, composite,
                    candidate.category.value,
                ))

    async def _clip_task(
```

### `src/editor/ai_analyzer.py`

```python
"""Analyse un clip Twitch via DeepSeek et produit un EditPlan structuré."""
from __future__ import annotations

import json

from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator

from ..core.events import ClipCandidate, TwitchClip
from ..core.logging import logger

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment,misc]


class EditPlan(BaseModel):
    worth_editing: bool
    confidence: float
    category: Literal["funny", "hype", "shock", "unknown"]
    trim_start: float
    trim_end: float
    highlight_moment: float
    title: str
    caption: str
    hashtags: list[str]
    caption_style: Literal["impact", "subtitles", "none"]
    caption_position: Literal["top", "bottom", "dynamic"]
    color_grade: Literal["viral", "cinematic", "raw"]
    add_zoom: bool

    @field_validator("title")
    @classmethod
    def title_must_be_short(cls, v: str) -> str:
        words = v.split()
        if len(words) > 10:
            return " ".join(words[:8])
        return v


_SYSTEM_PROMPT = """\
Tu es un expert en contenu viral TikTok/YouTube Shorts spécialisé dans les clips \
Twitch gaming. Tu connais parfaitement le ton de la gen Z francophone et anglophone \
sur TikTok et X (Twitter).

Tu reçois les métadonnées d'un moment viral détecté sur un stream Twitch.
Tu dois produire UNIQUEMENT un objet JSON valide selon le schéma fourni.

=== RÈGLES DE TITRE (CRITIQUE) ===

Le titre est un TEXT OVERLAY de 3-8 mots MAX affiché sur la vidéo.
Il doit stopper le scroll en < 1.7 seconde.

PATTERNS AUTORISÉS (choisis-en un) :
- Skull reaction : "frère a dit QUOI 💀" / "nah 💀" / "bro 😭"
- Cliffhanger : "il s'attendait pas à ça..." / "personne a vu venir..."
- Chat comme perso : "le chat a pété un câble" / "chat went insane"
- Hyperbole brute : "CLIP DE L'ANNÉE" / "GAME OVER 😭"
- Réaction minimale : "nan mais 💀" / "c'est fini frère"
- POV : "POV: t'es dans le chat quand ça arrive"

INTERDIT :
- Titres descriptifs ("Le fou rire incontrôlable")
- Titres qui EXPLIQUENT le clip (ça tue la curiosité)
- Phrases complètes avec sujet-verbe-complément
- Ton formel / journalistique / blog
- Plus de 8 mots
- Le mot "incontrôlable", "incroyable", "hilarant", "épique"

EXEMPLES CORRECTS :
  funny + chat spam → "le chat était en PLS 💀"
  hype + velocity spike → "TOUT LE MONDE A PERDU LA TÊTE"
  shock + caps → "nan mais c'est quoi ça 😭"
  funny + copypasta → "ils spamment tous la même chose mdr"
  hype + unique chatters → "même les lurkers sont sortis"

=== RÈGLES DE CAPTION ===
La caption est le sous-titre court affiché en overlay.
- 10 mots MAX
- Reprend la phrase/réaction clé du moment
- Peut être un message chat viral du moment
- Style : parler comme quelqu'un qui envoie un message à son pote

=== RÈGLES DE HASHTAGS ===
- 8 à 12 hashtags
- Structure : 5-8 niche (#twitchfr #kekw #[nomdustreamer] #[jeu]) \
+ 2-4 génériques (#fyp #viral #gaming)
- JAMAIS de hashtags morts (#france #funny #mdr seuls)

=== FORMAT ===
Aucun texte avant le JSON. Aucun texte après. Pas de ```json.
Le JSON doit être parseable par json.loads().\
"""

_USER_TEMPLATE = """\
Clip Twitch détecté — produis le EditPlan JSON.

SCHÉMA ATTENDU:
{edit_plan_schema}

EXEMPLES DE BONS EDIT PLANS :

Input: score=82, category=funny, reason="velocity+copypasta",
       chat_sample=["KEKW KEKW KEKW", "DEAD 💀", "OMEGALUL"]
Output: {{"title": "le chat en PLS 💀", "caption": "quand tout le monde spam KEKW en même temps", ...}}

Input: score=91, category=hype, reason="velocity+unique_chatters+caps",
       chat_sample=["LETS GOOO", "NO WAY", "POGGERS POGGERS"]
Output: {{"title": "MÊME LES LURKERS SONT SORTIS", "caption": "le chat x10 en 3 secondes", ...}}

Input: score=75, category=shock, reason="velocity+caps",
       chat_sample=["WTF", "NOOOO", "monkaS", "il a pas fait ça"]
Output: {{"title": "il a PAS fait ça 😭", "caption": "tout le monde a freeze", ...}}

Input: score=88, category=funny, reason="velocity+emote+repetition",
       chat_sample=["ICANT", "JE SUIS MORT", "AHAHAHAH", "ICANT ICANT"]
Output: {{"title": "c'est fini frère 💀", "caption": "0 survivant dans le chat", ...}}

DONNÉES DU MOMENT:
SIGNAL: {reason}
SCORE: {score}/100
CATÉGORIE: {category}
DURÉE CLIP: {duration}s
LANGUE STREAMER: {language}

MESSAGES CHAT (moment du pic):
{sample_messages}

TRANSCRIPT:
{transcript}\
"""


class DeepSeekAnalyzer:
    def __init__(self, api_key: str, model: str = "deepseek-v4-flash") -> None:
        if AsyncOpenAI is None:
            raise ImportError("openai>=1.0 requis : pip install openai")
        self._client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        self._model = model
        self._schema = json.dumps(EditPlan.model_json_schema(), indent=2)

    # Reasoning models (deepseek-reasoner, o-series) do NOT support temperature
    _REASONING_MODELS = frozenset({"deepseek-reasoner", "deepseek-v4-flash"})

    def _is_reasoning_model(self) -> bool:
        """Return True if the current model is a reasoning model that doesn't support temperature."""
        return self._model in self._REASONING_MODELS

    async def analyze(
        self,
        clip: TwitchClip,
        candidate: ClipCandidate,
        transcript: str | None = None,
    ) -> EditPlan:
        user_prompt = _USER_TEMPLATE.format(
            edit_plan_schema=self._schema,
            reason=candidate.reason,
            score=round(candidate.score, 1),
            category=candidate.category.value,
            duration=clip.duration,
            language="fr",
            sample_messages="\n".join(candidate.sample_messages) or "Aucun message disponible",
            transcript=transcript or "Non disponible",
        )

        for attempt in range(3):
            try:
                kwargs: dict = {
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 800,
                    # Force Non-Thinking mode : la réponse arrive dans content, pas reasoning_content
                    "extra_body": {"thinking": {"type": "disabled"}},
                }
                # Reasoning models (deepseek-reasoner, o1, o3, etc.) don't support temperature
                if not self._is_reasoning_model():
                    kwargs["temperature"] = 0.3

                response = await self._client.chat.completions.create(**kwargs)
                choice = response.choices[0]
                raw = choice.message.content or ""
                if not raw.strip():
                    reasoning = getattr(choice.message, "reasoning_content", "") or ""
                    logger.warning(
                        f"[analyzer] content vide sur {clip.id!r} — "
                        f"reasoning_content présent: {bool(reasoning)} | "
                        f"début: {reasoning[:120]!r}"
                    )
                logger.debug(f"[analyzer] finish_reason={choice.finish_reason!r} raw={raw[:200]!r}")
                # Extrait le premier bloc JSON valide meme si le modele ajoute du texte
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start == -1 or end == 0:
                    raise json.JSONDecodeError("aucun JSON trouve", raw, 0)
                plan = EditPlan.model_validate(json.loads(raw[start:end]))
                if plan.worth_editing:
                    logger.info(
                        f"[analyzer] clip {clip.id} -> worth_editing=True | "
                        f"title={plan.title!r} | confidence={plan.confidence:.2f}"
                    )
                else:
                    logger.info(f"[analyzer] clip {clip.id} -> worth_editing=False (score trop bas)")
                return plan
            except (json.JSONDecodeError, ValidationError) as exc:
                logger.warning(
                    f"[analyzer] parse échoué (tentative {attempt + 1}) — "
                    f"raw={raw[:200]!r} — {exc!r}"
                )
            except Exception as exc:
                logger.warning(f"[analyzer] erreur API tentative {attempt + 1}/3 : {exc!r}")

        logger.warning(
            f"[analyzer] clip {clip.id} -> worth_editing=False (parse error apres 3 essais)"
        )
        return EditPlan(
            worth_editing=False,
            confidence=0.0,
            category="unknown",
            trim_start=0.0,
            trim_end=clip.duration,
            highlight_moment=clip.duration / 2,
            title="",
            caption="",
            hashtags=[],
            caption_style="none",
            caption_position="bottom",
            color_grade="raw",
            add_zoom=False,
        )
```

### `src/editor/video_editor.py`

```python
"""VideoEditor — pipeline : trim+crop 9:16 (ffmpeg) → transcription (Whisper) → rendu (Remotion)."""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from ..core.config import EditorConfig
from ..core.events import TwitchClip
from ..core.logging import logger
from .ai_analyzer import EditPlan
from .transcriber import Transcriber

# Répertoire racine du projet Remotion (deux niveaux au-dessus de ce fichier)
_REMOTION_DIR = Path(__file__).parent.parent.parent / "remotion"
_RENDER_SCRIPT = _REMOTION_DIR / "scripts" / "render.mjs"

HIGHLIGHT_COLOR = "#E8003C"


class VideoEditor:
    OUTPUT_SUFFIX = "_edited.mp4"

    def __init__(
        self,
        output_dir: Path | None = None,
        settings: EditorConfig | None = None,
    ) -> None:
        self.output_dir = output_dir
        self._whisper_model = settings.whisper_model if settings else "medium"

    async def render(
        self,
        clip: TwitchClip,
        plan: EditPlan,
        transcript_words: list[dict] | None = None,
    ) -> Path | None:
        """Pipeline complet de montage. Retourne le Path du fichier édité ou None si échec."""
        if not clip.local_path:
            logger.warning(f"[editor] clip {clip.id!r} — pas de local_path, skip")
            return None

        input_path = Path(clip.local_path)
        if not input_path.exists():
            logger.warning(f"[editor] fichier introuvable : {input_path}")
            return None

        base_dir = self.output_dir or input_path.parent
        base_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._unique_output(base_dir, input_path.stem)

        tmp_dir = Path(tempfile.mkdtemp(prefix="harvest_render_"))
        try:
            ok = await self._render_pipeline(
                input_path, output_path, plan, transcript_words, tmp_dir
            )
        except asyncio.CancelledError:
            logger.info(f"[editor] render annulé pour {clip.id!r}")
            raise
        except Exception as exc:
            logger.warning(f"[editor] erreur inattendue : {exc!r}")
            return None
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if ok:
            logger.info(f"[editor] ✓ rendu terminé → {output_path.name}")
            return output_path
        logger.warning(f"[editor] render échoué pour {clip.id!r}")
        return None

    # ------------------------------------------------------------------
    # Pipeline interne
    # ------------------------------------------------------------------

    async def _render_pipeline(
        self,
        input_path: Path,
        output_path: Path,
        plan: EditPlan,
        transcript_words: list[dict] | None,
        tmp_dir: Path,
    ) -> bool:
        # --- Step 1: ffmpeg trim + crop 9:16 ---
        video_info = await self._ffprobe(input_path)
        if not video_info:
            return False

        step1 = tmp_dir / "step1.mp4"
        if not await self._step_trim_crop(input_path, step1, plan, video_info):
            return False

        # Dimensions réelles du fichier croppé (source de vérité pour Remotion)
        cropped_info = await self._ffprobe(step1)
        if not cropped_info:
            return False

        w = cropped_info["width"]
        h = cropped_info["height"]
        fps = round(cropped_info["fps"])           # Remotion exige un entier
        duration = cropped_info["duration"]
        duration_in_frames = max(1, round(duration * fps))

        logger.info(
            f"[ffmpeg] crop: {video_info['width']}x{video_info['height']} → {w}x{h} "
            f"| {duration:.2f}s @ {fps}fps ({duration_in_frames} frames)"
        )

        # --- Step 2: transcription Whisper ---
        if transcript_words is not None:
            words = transcript_words
            logger.info(f"[whisper] {len(words)} mots (fournis externalement)")
        else:
            try:
                words = await Transcriber(self._whisper_model).transcribe(step1)
            except RuntimeError as exc:
                logger.warning(f"[whisper] {exc} — fallback segments")
                words = []

        if not words:
            words = self._fallback_words(plan, duration)
            logger.info(f"[whisper] {len(words)} mots synthétiques (fallback)")

        # --- Step 3: rendu Remotion ---
        # output_path peut être relatif si data_dir l'est (Path("./data")) ;
        # Node.js path.resolve() le résoudrait depuis cwd=remotion/ → mauvais endroit.
        config = {
            "publicDir": str(tmp_dir),
            "outputPath": str(output_path.resolve()),
            "inputProps": {
                "videoSrc": "step1.mp4",
                "title": plan.title,
                "colorGrade": plan.color_grade,
                "addZoom": plan.add_zoom,
                "words": words,
                "highlightColor": HIGHLIGHT_COLOR,
                "durationInFrames": duration_in_frames,
                "fps": fps,
                "width": w,
                "height": h,
            },
        }
        config_path = tmp_dir / "render_config.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

        return await self._step_remotion(config_path)

    async def _step_trim_crop(
        self,
        input_path: Path,
        output_path: Path,
        plan: EditPlan,
        video_info: dict,
    ) -> bool:
        w = video_info["width"]
        h = video_info["height"]
        trim_start = plan.trim_start
        trim_end = (
            plan.trim_end
            if plan.trim_end > plan.trim_start
            else video_info.get("duration", 30.0)
        )
        duration = trim_end - trim_start

        filters: list[str] = []
        source_ratio = w / h if h else 1.0
        if abs(source_ratio - 9 / 16) > 0.05:
            target_w = h * 9 // 16
            crop_x = (w - target_w) // 2
            filters.append(f"crop={target_w}:{h}:{crop_x}:0")

        base_args = [
            "ffmpeg", "-y",
            "-ss", str(trim_start),
            "-i", str(input_path),
            "-t", str(duration),
        ]
        encode_args = [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            str(output_path),
        ]
        args = base_args + (["-vf", ",".join(filters)] if filters else []) + encode_args
        return await self._run_ffmpeg(args, "trim+crop")

    async def _step_remotion(self, config_path: Path) -> bool:
        """Lance le rendu Remotion via Node.js."""
        node = shutil.which("node")
        if not node:
            raise RuntimeError(
                "node introuvable dans le PATH — installer Node.js : https://nodejs.org"
            )
        if not _REMOTION_DIR.exists():
            raise RuntimeError(
                f"Projet Remotion introuvable : {_REMOTION_DIR}\n"
                "Lancer depuis la racine du projet : cd remotion && npm install"
            )
        if not (_REMOTION_DIR / "node_modules").exists():
            raise RuntimeError(
                f"node_modules absent dans {_REMOTION_DIR}\n"
                "Lancer : cd remotion && npm install"
            )

        args = [node, str(_RENDER_SCRIPT), str(config_path)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=_REMOTION_DIR,
            )
            stdout, stderr = await proc.communicate()
            for line in stdout.decode(errors="replace").splitlines():
                if line.strip():
                    logger.info(line)
            if proc.returncode != 0:
                logger.warning(
                    f"[remotion] rc={proc.returncode}\n"
                    f"{stderr.decode(errors='replace')[-1200:]}"
                )
                return False
            return True
        except Exception as exc:
            logger.warning(f"[remotion] erreur subprocess : {exc!r}")
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fallback_words(self, plan: EditPlan, duration: float) -> list[dict]:
        """Génère des mots synthétiques depuis plan.caption / plan.title."""
        text = plan.caption if plan.caption else plan.title
        raw_words = text.upper().split()
        if not raw_words:
            return [{"word": "...", "start": 0.0, "end": duration}]
        time_per_word = duration / len(raw_words)
        return [
            {"word": w, "start": round(i * time_per_word, 3), "end": round((i + 1) * time_per_word, 3)}
            for i, w in enumerate(raw_words)
        ]

    def _unique_output(self, base_dir: Path, stem: str) -> Path:
        output = base_dir / (stem + self.OUTPUT_SUFFIX)
        i = 1
        while output.exists():
            output = base_dir / f"{stem}{self.OUTPUT_SUFFIX[:-4]}_{i}.mp4"
            i += 1
        return output

    async def _ffprobe(self, path: Path) -> dict:
        """Retourne width, height, duration, fps via ffprobe JSON."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams", "-show_format",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(f"[editor] ffprobe rc={proc.returncode} pour {path.name}")
                return {}
            data = json.loads(stdout)
            video = next(
                (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
                {},
            )
            fmt = data.get("format", {})
            fps_str = video.get("r_frame_rate", "30/1")
            num, den = fps_str.split("/") if "/" in fps_str else (fps_str, "1")
            fps = float(num) / float(den) if float(den) else 30.0
            return {
                "width": int(video.get("width", 1280)),
                "height": int(video.get("height", 720)),
                "duration": float(fmt.get("duration", 0)),
                "fps": fps,
            }
        except Exception as exc:
            logger.warning(f"[editor] ffprobe erreur : {exc!r}")
            return {}

    async def _run_ffmpeg(self, args: list[str], step_name: str) -> bool:
        if not shutil.which("ffmpeg"):
            raise RuntimeError(
                "ffmpeg introuvable dans le PATH — "
                "installer : https://ffmpeg.org/download.html"
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(
                    f"[ffmpeg] [{step_name}] rc={proc.returncode}\n"
                    f"{stderr.decode(errors='replace')[-600:]}"
                )
                return False
            return True
        except Exception as exc:
            logger.warning(f"[ffmpeg] [{step_name}] erreur : {exc!r}")
            return False
```

### `remotion/src/types.ts`

```typescript
export type ColorGrade = "viral" | "cinematic" | "raw";

export interface WordSegment {
  word: string;
  start: number;
  end: number;
}

export interface TikTokClipProps {
  videoSrc: string;
  title: string;
  colorGrade: ColorGrade;
  addZoom: boolean;
  words: WordSegment[];
  highlightColor: string;
  // Composition metadata — read by calculateMetadata in Root.tsx
  durationInFrames: number;
  fps: number;
  width: number;
  height: number;
}
```

### `remotion/src/TwitchClip/index.tsx`

```tsx
import {
  AbsoluteFill,
  interpolate,
  OffthreadVideo,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { FC } from "react";
import type { TikTokClipProps, WordSegment } from "../types";
import { colorGradeFilter } from "./styles";
import { TitleOverlay } from "./TitleOverlay";
import { CaptionOverlay } from "./CaptionOverlay";

// Convertit les mots Whisper [{word, start, end}] en contenu SRT
// pour @remotion/captions parseSrt()
function wordsToSrt(words: WordSegment[]): string {
  return words
    .map((w, i) => {
      const start = msToSrtTimestamp(Math.round(w.start * 1000));
      const end = msToSrtTimestamp(Math.round(w.end * 1000));
      return `${i + 1}\n${start} --> ${end}\n${w.word}`;
    })
    .join("\n\n");
}

function msToSrtTimestamp(ms: number): string {
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1_000);
  const rest = ms % 1_000;
  return (
    String(h).padStart(2, "0") +
    ":" +
    String(m).padStart(2, "0") +
    ":" +
    String(s).padStart(2, "0") +
    "," +
    String(rest).padStart(3, "0")
  );
}

export const TwitchClip: FC<TikTokClipProps> = ({
  videoSrc,
  title,
  colorGrade,
  addZoom,
  words,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  const cssFilter = colorGradeFilter(colorGrade);
  const scale = addZoom
    ? interpolate(frame, [0, durationInFrames], [1.0, 1.08], {
        extrapolateRight: "clamp",
      })
    : 1.0;

  const srtContent = wordsToSrt(words);

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {/* Vidéo de base avec color grade + zoom */}
      <AbsoluteFill
        style={{
          transform: `scale(${scale})`,
          filter: cssFilter,
          transformOrigin: "center center",
        }}
      >
        <OffthreadVideo src={staticFile(videoSrc)} />
      </AbsoluteFill>

      <TitleOverlay title={title} width={width} height={height} />
      <CaptionOverlay srtContent={srtContent} width={width} height={height} />
    </AbsoluteFill>
  );
};
```

### `remotion/src/TwitchClip/CaptionOverlay.tsx`

```tsx
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { createTikTokStyleCaptions, parseSrt } from "@remotion/captions";
import type { FC } from "react";
import { COLORS, FONTS } from "./styles";

interface Props {
  srtContent: string;
  width: number;
  height: number;
}

export const CaptionOverlay: FC<Props> = ({ srtContent, width, height }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const timeMs = (frame / fps) * 1000;

  // FIX 4 — groupement : 500 ms max entre tokens d'une même page
  const { captions } = parseSrt({ input: srtContent });
  const { pages } = createTikTokStyleCaptions({
    captions,
    combineTokensWithinMilliseconds: 500,
  });

  const currentPage =
    pages.find(
      (p) => timeMs >= p.startMs && timeMs < p.startMs + p.durationMs
    ) ?? null;

  if (!currentPage || currentPage.tokens.length === 0) return null;

  // FIX 1 — taille de police basée sur la hauteur, pas la largeur
  const fontSize = Math.round(height * 0.055);
  const outlineWidth = Math.round(fontSize * 0.07);

  // FIX 3 — highlight sur le token actuellement prononcé
  const activeTokenIndex = currentPage.tokens.findIndex(
    (token) => timeMs >= token.fromMs && timeMs < token.toMs
  );
  const strongIndex =
    activeTokenIndex >= 0
      ? activeTokenIndex
      : currentPage.tokens.length - 1;

  return (
    <AbsoluteFill>
      {/* FIX 2 — position : top 38% au lieu du bas */}
      <div
        style={{
          position: "absolute",
          top: height * 0.38,
          left: 0,
          right: 0,
          display: "flex",
          justifyContent: "center",
          paddingLeft: 12,
          paddingRight: 12,
        }}
      >
        <div
          style={{
            fontFamily: FONTS.impact,
            fontSize,
            fontWeight: "normal",
            textAlign: "center",
            lineHeight: 1.1,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {currentPage.tokens.map((token, i) => (
            <span key={i}>
              {i > 0 ? " " : ""}
              <span
                style={
                  i === strongIndex
                    ? {
                        color: COLORS.red,
                        WebkitTextStroke: `${outlineWidth}px rgba(100,0,0,0.7)`,
                      }
                    : {
                        color: COLORS.white,
                        WebkitTextStroke: `${outlineWidth}px ${COLORS.black}`,
                      }
                }
              >
                {token.text.toUpperCase()}
              </span>
            </span>
          ))}
        </div>
      </div>
    </AbsoluteFill>
  );
};
```

### `remotion/src/TwitchClip/TitleOverlay.tsx`

```tsx
import { AbsoluteFill } from "remotion";
import type { FC } from "react";
import { COLORS, FONTS } from "./styles";

interface Props {
  title: string;
  width: number;
  height: number;
}

export const TitleOverlay: FC<Props> = ({ title, width, height }) => {
  if (!title) return null;

  const fontSize = Math.round(width * 0.072);

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-start",
        alignItems: "center",
        paddingTop: Math.round(height * 0.02),
        paddingLeft: 16,
        paddingRight: 16,
      }}
    >
      <div
        style={{
          backgroundColor: COLORS.titleBg,
          borderRadius: 16,
          paddingTop: 8,
          paddingBottom: 8,
          paddingLeft: 20,
          paddingRight: 20,
          fontFamily: FONTS.impact,
          fontSize,
          fontWeight: "normal",
          color: COLORS.titleText,
          textAlign: "center",
          textTransform: "uppercase",
          letterSpacing: "0.02em",
          maxWidth: "92%",
          lineHeight: 1.15,
        }}
      >
        {title}
      </div>
    </AbsoluteFill>
  );
};
```

### `remotion/src/TwitchClip/styles.ts`

```typescript
export const COLORS = {
  white: "#FFFFFF",
  black: "#000000",
  red: "#E8003C",
  titleBg: "rgba(255, 255, 255, 0.92)",
  titleText: "#000000",
} as const;

export const FONTS = {
  impact: 'Impact, "Arial Narrow", Arial, sans-serif',
} as const;

const GRADE_FILTERS: Record<string, string> = {
  viral: "saturate(1.3) contrast(1.1) brightness(1.03)",
  cinematic: "saturate(0.85) contrast(1.05)",
  raw: "none",
};

export function colorGradeFilter(grade: string): string {
  return GRADE_FILTERS[grade] ?? "none";
}
```

### `config/settings.yaml`

```yaml
# Configuration globale du pipeline

detector:
  # Fenetre glissante d'analyse du chat (secondes)
  chat_window_seconds: 10

  # --- Velocity Z-score (adaptatif) ---
  # Historique pour calculer μ et σ de la velocity (5 minutes)
  # Plus c'est long, plus la calibration est précise mais plus le warmup est long
  stats_window_seconds: 300

  # Seuil Z-score pour déclencher (Z = nb d'écarts-types au-dessus de μ)
  # Z=2.5 ≈ top 0.6% des moments → sélectif ; Z=2.0 ≈ top 2.3% → plus permissif
  # Augmenter pour les gros streamers si encore trop de clips (ex: 3.0)
  z_score_threshold: 2.5

  # Velocity minimale absolue (msg/s) — sécurité anti-bruit sur petits chats
  velocity_floor: 0.3

  # Nombre de ticks (1 tick ≈ 2 s) avant d'activer le scoring velocity
  # 30 ticks = ~60 s de chauffe pour établir μ/σ de base
  warmup_samples: 30

  # --- Autres seuils ---
  # Fenêtre baseline pour unique chatters (secondes)
  baseline_window_seconds: 60

  # Densite d'emotes "laugh/hype" dans la fenetre (ratio messages contenant une emote)
  emote_density_threshold: 0.35

  # Score composite minimum (0-100) pour créer un clip
  # Augmenter pour être plus sélectif (ex: 70 pour les gros streamers)
  min_viral_score: 60.0

  # Cooldown entre deux clips (secondes)
  cooldown_seconds: 120

  # Emotes trackees par categorie
  emotes:
    laugh: [KEKW, OMEGALUL, LULW, LUL, PepeLaugh, ICANT, PEEPOLAUGH]
    hype: [Pog, PogChamp, POGGERS, EZ, LETSGO, LETSGOO, WICKED]
    shock: [WutFace, monkaS, Sadge, NOOO, WAYTOODANK]

clipper:
  buffer_seconds: 120
  segment_duration: 10
  clip_pre_seconds: 15
  clip_post_seconds: 15
  stream_quality: "720p60,720p,best"

orchestrator:
  log_level: INFO

editor:
  whisper_model: medium
  subtitle_fontsize: 75
  subtitle_color: "&H00FFFF"
  subtitle_outline: 3
  gameplay_ratio: 0.55
  clip_duration: 15
  pre_peak_seconds: 3
  peak_offset_seconds: -3
  hook_duration: 2.0
  hook_fontsize: 90
  hook_templates:
    funny: "WAIT FOR IT 😭"
    hype: "WATCH THIS 🔥"
    shock: "NO WAY 💀"
    unknown: "CHAT WENT CRAZY"
```

### `pyproject.toml`

```toml
[project]
name = "twitch-viral-clipper"
version = "0.1.0"
description = "Auto-clipper Twitch -> TikTok pipeline"
requires-python = ">=3.12"
dependencies = [
    "twitchio>=2.9",
    "httpx>=0.27",
    "aiofiles>=24.1",
    "pyyaml>=6.0",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "loguru>=0.7",
    "aiosqlite>=0.20",
    "typer>=0.12",
    "fastapi>=0.111",
    "uvicorn>=0.30",
    "tzdata>=2024.1",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.5"]
# phase 2+
editor = ["faster-whisper>=1.0", "opencv-python>=4.10", "mediapipe>=0.10", "openai>=1.0"]
ai = ["anthropic>=0.39"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

### `src/main.py`
_(tronqué à 300 lignes sur 392)_

```python
"""Entrypoint CLI.

Usage:
    python -m src.main harvest --streamer <login>
    python -m src.main watch   --streamer <login>
"""
from __future__ import annotations

import asyncio
import signal
from datetime import datetime
from pathlib import Path

import typer

from .api.twitch import TwitchAPIClient
from .core.config import Env, load_settings, load_streamer
from .core.db import Database
from .core.errors import StreamerOfflineError
from .core.events import ClipCandidate, MomentCategory, TwitchClip
from .core.logging import logger, setup_logging
from .editor.processor import ClipProcessor
from .orchestrator.harvest import HarvestPipeline
from .orchestrator.pipeline import Pipeline

app = typer.Typer(add_completion=False, help="Twitch Viral Clipper - MVP Etage 1")


@app.command()
def auth(
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
):
    """Génère un token user (clips:edit + chat:read) via Device Flow et l'écrit dans .env."""
    setup_logging(level=log_level)
    env = Env()

    _PLACEHOLDERS = {"your_client_id", "your_client_secret", ""}
    if env.twitch_client_id in _PLACEHOLDERS or env.twitch_client_secret in _PLACEHOLDERS:
        logger.error("TWITCH_CLIENT_ID et TWITCH_CLIENT_SECRET doivent être remplis dans .env")
        raise typer.Exit(1)

    scopes = ["clips:edit", "chat:read"]
    logger.info(f"Démarrage du Device Flow (scopes: {scopes})")

    async def _run() -> str:
        token_data = await TwitchAPIClient.device_flow(env.twitch_client_id, scopes)
        return token_data["access_token"]

    try:
        access_token = asyncio.new_event_loop().run_until_complete(_run())
    except (RuntimeError, TimeoutError) as exc:
        logger.error(str(exc))
        raise typer.Exit(1)

    # Écrire le token dans .env
    env_path = Path(".env")
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        if "TWITCH_USER_TOKEN=" in content:
            lines = [
                f"TWITCH_USER_TOKEN={access_token}" if l.startswith("TWITCH_USER_TOKEN=") else l
                for l in content.splitlines()
            ]
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            with env_path.open("a", encoding="utf-8") as f:
                f.write(f"\nTWITCH_USER_TOKEN={access_token}\n")
    else:
        env_path.write_text(f"TWITCH_USER_TOKEN={access_token}\n", encoding="utf-8")

    logger.info(f"Token sauvegardé dans .env (TWITCH_USER_TOKEN)")
    logger.info("Tu peux maintenant lancer : python -m src.main harvest --streamer <login>")


@app.command()
def watch(
    streamer: str = typer.Option(..., "--streamer", "-s", help="Login Twitch (twitch.tv/<login>)"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
    config_path: Path = typer.Option(Path("config/settings.yaml"), "--config", "-c"),
):
    """Lance le pipeline de surveillance sur un streamer."""
    setup_logging(level=log_level)
    env = Env()  # charge .env
    settings = load_settings(config_path)
    streamer_cfg = load_streamer(streamer)

    # sanity checks
    missing = []
    if not env.twitch_irc_token:
        missing.append("TWITCH_IRC_TOKEN")
    if not env.twitch_irc_nick:
        missing.append("TWITCH_IRC_NICK")
    if missing:
        logger.error(f"Config manquante dans .env: {missing}")
        raise typer.Exit(1)

    pipeline = Pipeline(env, settings, streamer_cfg)

    loop = asyncio.new_event_loop()

    def _sig_handler():
        logger.info("signal recu, arret demande")
        pipeline.stop()

    try:
        loop.add_signal_handler(signal.SIGINT, _sig_handler)
        loop.add_signal_handler(signal.SIGTERM, _sig_handler)
    except NotImplementedError:
        # Windows: add_signal_handler n'est pas supporte sur ProactorEventLoop
        # -> on laisse Ctrl+C lever KeyboardInterrupt
        pass

    try:
        loop.run_until_complete(pipeline.run())
    except KeyboardInterrupt:
        pipeline.stop()
        loop.run_until_complete(asyncio.sleep(0.5))
    finally:
        loop.close()


@app.command()
def harvest(
    streamer: str = typer.Option(..., "--streamer", "-s", help="Login Twitch (twitch.tv/<login>)"),
    cooldown: int = typer.Option(120, "--cooldown", "-c", help="Cooldown entre deux clips (secondes)"),
    config_path: Path = typer.Option(Path("config/settings.yaml"), "--config"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
    dashboard: bool = typer.Option(False, "--dashboard", "-d", help="Ouvrir le dashboard web"),
    port: int = typer.Option(8000, "--port", help="Port du dashboard (défaut 8000)"),
):
    """Écoute un live et crée automatiquement des clips Twitch lors des spikes de chat."""
    setup_logging(level=log_level)
    env = Env()

    _PLACEHOLDERS = {"your_client_id", "your_client_secret", "your_bot_username", "", "xxx"}

    missing = []
    if env.twitch_client_id in _PLACEHOLDERS:
        missing.append("TWITCH_CLIENT_ID")
    if env.twitch_client_secret in _PLACEHOLDERS:
        missing.append("TWITCH_CLIENT_SECRET")
    if not env.twitch_irc_token or env.twitch_irc_token in _PLACEHOLDERS:
        missing.append("TWITCH_IRC_TOKEN (token OAuth pour le chat)")
    if not env.twitch_irc_nick or env.twitch_irc_nick in _PLACEHOLDERS:
        missing.append("TWITCH_IRC_NICK (login du compte bot)")
    if missing:
        logger.error(f"Credentials manquantes dans .env :\n  " + "\n  ".join(f"- {m}" for m in missing))
        raise typer.Exit(1)

    settings = load_settings(config_path)
    streamer_cfg = load_streamer(streamer)

    broadcaster = None
    if dashboard:
        try:
            import uvicorn
            from .api.dashboard import SignalBroadcaster, create_app
            broadcaster = SignalBroadcaster(channel=streamer)
        except ImportError:
            logger.error("fastapi et uvicorn requis : pip install fastapi uvicorn")
            raise typer.Exit(1)

    pipeline = HarvestPipeline(
        env, settings, streamer_cfg,
        cooldown_seconds=cooldown,
        broadcaster=broadcaster,
    )

    loop = asyncio.new_event_loop()

    def _sig_handler() -> None:
        logger.info("signal reçu, arrêt demandé")
        pipeline.stop()

    try:
        loop.add_signal_handler(signal.SIGINT, _sig_handler)
        loop.add_signal_handler(signal.SIGTERM, _sig_handler)
    except NotImplementedError:
        pass

    async def _run() -> list:
        if broadcaster is None:
            return await pipeline.run()
        dash_app = create_app(broadcaster, db_path=str(env.data_dir / "state.db"))
        server = uvicorn.Server(
            uvicorn.Config(dash_app, host="0.0.0.0", port=port, log_level="warning")
        )
        logger.info(f"Dashboard → http://localhost:{port}")
        harvest_task = asyncio.create_task(pipeline.run())
        server_task  = asyncio.create_task(server.serve())
        try:
            result = await harvest_task
        finally:
            server.should_exit = True
            await server_task
        return result

    try:
        clips = loop.run_until_complete(_run())
        if clips:
            logger.info(f"[harvest] session terminée — {len(clips)} clip(s) créé(s)")
            for c in clips:
                logger.info(f"  - {c.title!r} ({c.duration:.0f}s) → {c.url}")
        else:
            logger.warning("[harvest] session terminée sans clip détecté")
    except StreamerOfflineError as exc:
        logger.error(str(exc))
        raise typer.Exit(1)
    except KeyboardInterrupt:
        pipeline.stop()
        loop.run_until_complete(asyncio.sleep(0.3))
    finally:
        loop.close()


@app.command()
def process(
    session: int = typer.Option(..., "--session", "-s", help="ID de la session à traiter"),
    config_path: Path = typer.Option(Path("config/settings.yaml"), "--config"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
):
    """Post-traite les clips bruts d'une session : smart cut, 9:16, hook, sous-titres."""
    setup_logging(level=log_level)
    env = Env()
    settings = load_settings(config_path)

    async def _run() -> None:
        db = Database(env.data_dir / "state.db")
        await db.init()

        session_data = await db.get_session(session)
        if session_data is None:
            logger.error(f"[process] session #{session} introuvable en base")
            raise typer.Exit(1)

        clips = await db.get_unprocessed_clips(session)
        if not clips:
            logger.info(f"[process] aucun clip à traiter pour la session #{session}")
            return

        streamer_login = session_data["streamer"]
        streamer_cfg = load_streamer(streamer_login)
        output_dir = env.data_dir / "clips" / "processed" / streamer_login
        processor = ClipProcessor(output_dir=output_dir, settings=settings.editor)

        success = 0
        for i, clip in enumerate(clips, 1):
            logger.info(f"[process] clip {i}/{len(clips)} : {clip['title']!r}")
            try:
                category = MomentCategory(clip.get("category", "unknown"))
            except ValueError:
                category = MomentCategory.UNKNOWN

            result = await processor.process(
                Path(clip["local_path"]),
                streamer_cfg.webcam_zone,
                category=category,
                clip_duration_total=float(clip["duration"]),
            )
            if result is not None:
                await db.update_clip_processed_path(clip["twitch_id"], str(result))
                success += 1
                logger.info(f"[process] → {result.name}")

        logger.info(f"[process] terminé — {success}/{len(clips)} clips traités")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    except (SystemExit, typer.Exit):
        raise
    except KeyboardInterrupt:
        logger.info("[process] interrompu")
    finally:
        loop.close()


@app.command()
def edit(
    session: int | None = typer.Option(None, "--session", "-s", help="ID de la session à éditer"),
    clip: str | None = typer.Option(None, "--clip", "-c", help="Twitch clip ID à éditer"),
    last: bool = typer.Option(False, "--last", help="Utiliser la dernière session"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Liste les clips sans appeler l'API"),
    config_path: Path = typer.Option(Path("config/settings.yaml"), "--config"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
):
    """Analyse les clips d'une session avec DeepSeek et génère les Edit Plans."""
    setup_logging(level=log_level)
    env = Env()
    settings = load_settings(config_path)

    async def _run() -> None:
        db = Database(env.data_dir / "state.db")
        await db.init()

        if last:
            sess = await db.get_last_session()
            if sess is None:
                logger.error("[edit] aucune session en base")
                raise typer.Exit(1)
```

## Fichiers non trouvés

- `src/editor/ffmpeg_preprocessor.py`
- `src/editor/whisper_transcriber.py`
- `remotion/render.mjs`

---
_18 fichier(s) inclus — 3 non trouvé(s)_
