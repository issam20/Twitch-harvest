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
