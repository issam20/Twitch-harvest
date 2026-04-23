"""Signal 1: velocity du chat (messages/seconde).

Compare la velocity instantanee (fenetre courte, 10s par defaut) a une baseline
rolling (60s par defaut). Un pic = ratio > velocity_multiplier.

Score 0-100 via tanh pour saturer en douceur.
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
        baseline_seconds: int = 60,
        velocity_threshold: float = 5.0,
        multiplier_threshold: float = 3.0,
    ):
        self.window_seconds = window_seconds
        self.baseline_seconds = baseline_seconds
        self.velocity_threshold = velocity_threshold
        self.multiplier_threshold = multiplier_threshold
        # on stocke juste les timestamps
        self._history: deque[datetime] = deque()

    def add(self, event: ChatEvent) -> None:
        self._history.append(event.timestamp)
        self._prune(event.timestamp)

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.baseline_seconds)
        while self._history and self._history[0] < cutoff:
            self._history.popleft()

    def velocity(self, now: datetime) -> float:
        """msg/s sur la fenetre courte."""
        cutoff = now - timedelta(seconds=self.window_seconds)
        count = sum(1 for t in self._history if t >= cutoff)
        return count / self.window_seconds

    def baseline(self, now: datetime) -> float:
        """msg/s moyen sur la fenetre longue, hors fenetre courte.

        On exclut la fenetre courte de la baseline pour eviter que le pic se
        pollue lui-meme (sinon la baseline monte avec le pic et le ratio reste bas).
        """
        short_cutoff = now - timedelta(seconds=self.window_seconds)
        long_cutoff = now - timedelta(seconds=self.baseline_seconds)

        count = sum(1 for t in self._history if long_cutoff <= t < short_cutoff)
        baseline_duration = self.baseline_seconds - self.window_seconds
        if baseline_duration <= 0:
            return 0.0
        return count / baseline_duration

    def score(self, now: datetime) -> tuple[float, dict]:
        """Retourne (score_0_100, debug_info)."""
        v = self.velocity(now)
        b = self.baseline(now)

        # Protection contre baseline nulle (debut de stream, petit stream)
        # On utilise un plancher de 0.2 msg/s = 12 msg/min
        b_eff = max(b, 0.2)
        ratio = v / b_eff

        # Seuil absolu: pas de pic sous le velocity_threshold quelle que soit la baseline
        if v < self.velocity_threshold:
            return 0.0, {"velocity": v, "baseline": b, "ratio": ratio, "reason": "below_abs"}

        # tanh-based: ratio=3 -> ~76, ratio=5 -> ~96, ratio=2 -> ~46
        # (v - velocity_threshold) pour ne pas donner de score au ras du seuil
        normalized = (ratio - 1.0) / self.multiplier_threshold
        score = 100.0 * math.tanh(normalized)
        score = max(0.0, score)

        return score, {
            "velocity": round(v, 2),
            "baseline": round(b, 2),
            "ratio": round(ratio, 2),
        }
