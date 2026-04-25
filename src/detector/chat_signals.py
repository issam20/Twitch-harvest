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
