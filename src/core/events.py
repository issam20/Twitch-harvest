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
