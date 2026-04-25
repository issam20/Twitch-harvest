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
