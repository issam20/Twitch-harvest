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
