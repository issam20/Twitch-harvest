"""Capture audio en continu via un second pipe ffmpeg qui analyse la loudness.

Strategie: on lance UN deuxieme streamlink (leger, on prend audio_only si dispo)
et on applique le filtre ebur128 qui sort en stderr les valeurs de loudness
integrees (M = momentary, 400ms). On parse ces valeurs a la volee et on les
pousse dans une fenetre glissante.

Pourquoi pas partager le flux video du VideoCapture ? Parce que le flux video
passe en `-c copy` (pas de decodage audio) pour etre peu couteux. Decoder en
plus l'audio la-bas ralentirait. Un second streamlink en audio-only est leger
(~50-100 KB/s).

Expose:
- AudioMonitor.get_recent_peaks(window_seconds) -> list de (timestamp, loudness_db)
- AudioMonitor.detect_peak() -> bool (vrai si on a un pic > mu + 2sigma)
"""
from __future__ import annotations

import asyncio
import re
from collections import deque
from datetime import datetime, timezone
from statistics import mean, stdev

from ..core.logging import logger


# ffmpeg ebur128 ecrit en stderr des lignes du type:
# [Parsed_ebur128_0 @ 0x...] t: 12.3    M: -18.2  S: -19.1  I: -20.5 LUFS  ...
_EBUR128_RE = re.compile(
    r"t:\s*([\d.]+)\s+M:\s*(-?[\d.]+|-?inf)",
    re.IGNORECASE,
)


class AudioMonitor:
    """Monitore la loudness momentaneous (400ms) en continu."""

    def __init__(
        self,
        channel: str,
        history_seconds: int = 60,
        peak_sigma: float = 2.0,
    ):
        self.channel = channel
        self.history_seconds = history_seconds
        self.peak_sigma = peak_sigma

        # deque de tuples (datetime, loudness_db_momentary)
        # avec ~2.5 echantillons/s sur ebur128 defaut, 60s = ~150 points
        self._history: deque[tuple[datetime, float]] = deque(maxlen=history_seconds * 4)

        self._streamlink_proc: asyncio.subprocess.Process | None = None
        self._ffmpeg_proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        url = f"https://twitch.tv/{self.channel}"
        logger.info(f"[audio] demarrage monitoring: {url}")

        # streamlink en audio_only si dispo, sinon plus basse qualite (on ne garde
        # que l'audio cote ffmpeg de toute facon)
        self._streamlink_proc = await asyncio.create_subprocess_exec(
            "streamlink",
            "--stdout",
            "--twitch-disable-ads",
            "--retry-streams", "10",
            "--retry-max", "3",
            url,
            "audio_only,worst",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        # ffmpeg: decode l'audio, applique ebur128, null sink
        # -hide_banner + loglevel info (ebur128 log en info)
        self._ffmpeg_proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "info",
            "-i", "pipe:0",
            "-vn",
            "-filter_complex", "ebur128=peak=none",
            "-f", "null",
            "-",
            stdin=self._streamlink_proc.stdout,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        self._running = True
        self._reader_task = asyncio.create_task(self._read_loudness_loop())
        logger.info("[audio] monitoring demarre")

    async def _read_loudness_loop(self) -> None:
        """Parse en continu stderr ffmpeg pour extraire la loudness momentaneous."""
        if self._ffmpeg_proc is None or self._ffmpeg_proc.stderr is None:
            return

        while self._running:
            try:
                line = await self._ffmpeg_proc.stderr.readline()
                if not line:
                    # EOF = ffmpeg mort
                    logger.warning("[audio] ffmpeg stderr ferme")
                    break

                txt = line.decode(errors="ignore")
                m = _EBUR128_RE.search(txt)
                if not m:
                    continue

                try:
                    loudness_raw = m.group(2)
                    # -inf quand silence total
                    if "inf" in loudness_raw.lower():
                        loudness = -70.0
                    else:
                        loudness = float(loudness_raw)
                except ValueError:
                    continue

                self._history.append((datetime.now(timezone.utc), loudness))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[audio] erreur lecture: {e!r}")
                await asyncio.sleep(1)

    def is_peak(self) -> tuple[bool, float, float]:
        """Retourne (peak_detected, current_db, zscore).

        Un pic = le dernier echantillon est au-dessus de mu + N*sigma de la fenetre.
        Renvoie aussi le zscore pour le scoring.
        """
        if len(self._history) < 20:
            return False, -70.0, 0.0

        values = [v for _, v in self._history]
        current = values[-1]
        # On calcule la baseline sur tout sauf les 3 derniers points (sinon le pic
        # se pollue lui-meme)
        baseline = values[:-3] if len(values) > 3 else values
        mu = mean(baseline)
        sigma = stdev(baseline) if len(baseline) > 1 else 1.0
        if sigma < 0.5:
            sigma = 0.5  # plancher pour eviter zscore exageres quand silence stable

        zscore = (current - mu) / sigma
        is_peak = zscore >= self.peak_sigma
        return is_peak, current, zscore

    def peak_score(self) -> float:
        """Score 0-100 base sur le zscore actuel. Sature a zscore=4."""
        _, _, z = self.is_peak()
        if z <= 0:
            return 0.0
        # normalisation: z=2 -> 50, z=3 -> 75, z=4+ -> 100
        return min(100.0, z * 25.0)

    async def stop(self) -> None:
        logger.info("[audio] arret monitoring")
        self._running = False
        if self._reader_task:
            self._reader_task.cancel()
        for proc in (self._ffmpeg_proc, self._streamlink_proc):
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (asyncio.TimeoutError, ProcessLookupError):
                    proc.kill()
