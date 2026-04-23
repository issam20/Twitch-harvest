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
