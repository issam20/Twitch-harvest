"""Scorer: fusionne les 3 signaux (chat velocity, emote density, audio peak)
et emet des ClipCandidate avec cooldown global pour eviter les doublons.

Ponderation par defaut:
- velocity  : 0.45 (signal le plus robuste)
- emote     : 0.35 (confirme le type de moment)
- audio     : 0.20 (precision temporelle)

Si audio non disponible (None), la ponderation est redistribuee sur velocity+emote.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..core.events import ClipCandidate, MomentCategory
from ..core.logging import logger


class ViralScorer:
    def __init__(
        self,
        min_viral_score: int = 60,
        cooldown_seconds: int = 60,
        weights: dict[str, float] | None = None,
    ):
        self.min_viral_score = min_viral_score
        self.cooldown = timedelta(seconds=cooldown_seconds)
        self.weights = weights or {"velocity": 0.45, "emote": 0.35, "audio": 0.20}
        self._last_trigger_at: datetime | None = None

    def evaluate(
        self,
        now: datetime,
        channel: str,
        velocity_score: float,
        velocity_debug: dict,
        emote_score: float,
        emote_category: MomentCategory,
        emote_debug: dict,
        audio_score: float | None,
        audio_debug: dict | None,
        sample_messages: list[str],
    ) -> ClipCandidate | None:
        """Retourne un ClipCandidate si le score total >= seuil et cooldown ok, sinon None."""

        # Fusion avec redistribution si audio manquant
        if audio_score is None:
            w_v = self.weights["velocity"] / (self.weights["velocity"] + self.weights["emote"])
            w_e = self.weights["emote"] / (self.weights["velocity"] + self.weights["emote"])
            total_score = w_v * velocity_score + w_e * emote_score
            audio_score_for_log = None
        else:
            total_score = (
                self.weights["velocity"] * velocity_score
                + self.weights["emote"] * emote_score
                + self.weights["audio"] * audio_score
            )
            audio_score_for_log = round(audio_score, 1)

        # Log periodique (utile pour tuner les seuils)
        if total_score >= self.min_viral_score * 0.7:
            logger.debug(
                f"[scorer] score={total_score:.1f} "
                f"(v={velocity_score:.1f}, e={emote_score:.1f}, a={audio_score_for_log}) "
                f"vdbg={velocity_debug} edbg={emote_debug}"
            )

        if total_score < self.min_viral_score:
            return None

        # Cooldown check
        if self._last_trigger_at is not None:
            elapsed = now - self._last_trigger_at
            if elapsed < self.cooldown:
                logger.debug(f"[scorer] score {total_score:.1f} OK mais cooldown actif ({elapsed.total_seconds():.0f}s)")
                return None

        # Declenchement
        self._last_trigger_at = now

        reason = (
            f"score={total_score:.1f} | "
            f"velocity {velocity_debug.get('velocity', '?')} msg/s "
            f"(x{velocity_debug.get('ratio', '?')} baseline) | "
            f"emotes {emote_debug.get('density', '?')*100:.0f}%"
        )
        if audio_score is not None:
            reason += f" | audio_z={audio_debug.get('zscore', '?') if audio_debug else '?'}"

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
