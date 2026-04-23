"""Clipper: extrait un clip du buffer video autour d'un timestamp donne.

Le VideoCapture ecrit des chunks seg_XXXXX.ts de `segment_duration` secondes
chacun. Le premier chunk commence a `capture_started_at`.

Pour produire un clip de [peak - pre, peak + post]:
1. On identifie les chunks couvrant cette fenetre
2. On les concatene via ffmpeg concat demuxer
3. On re-encode la sortie en mp4 (H264 + AAC) avec un trim precis aux bornes
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from pathlib import Path

import aiofiles

from ..core.events import Clip, ClipCandidate
from ..core.logging import logger


class Clipper:
    def __init__(
        self,
        output_dir: Path,
        pre_seconds: int = 15,
        post_seconds: int = 15,
        segment_duration: int = 10,
    ):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.pre_seconds = pre_seconds
        self.post_seconds = post_seconds
        self.segment_duration = segment_duration
        self._seg_re = re.compile(r"seg_(\d+)\.ts$")

    async def extract(
        self,
        candidate: ClipCandidate,
        buffer_chunks: list[Path],
        capture_started_at: datetime,
    ) -> Clip | None:
        """Extrait un clip mp4. Retourne None si le buffer ne couvre pas la fenetre."""
        if not buffer_chunks:
            logger.warning("[clipper] aucun chunk dans le buffer")
            return None

        # Fenetre cible en secondes depuis capture_started_at
        peak_offset = (candidate.timestamp - capture_started_at).total_seconds()
        target_start = peak_offset - self.pre_seconds
        target_end = peak_offset + self.post_seconds

        # Indice du premier et dernier chunk necessaires
        first_idx = max(0, int(target_start // self.segment_duration))
        last_idx = int(target_end // self.segment_duration)

        # Matching avec les chunks dispos
        available = {self._chunk_index(p): p for p in buffer_chunks if self._chunk_index(p) is not None}
        if not available:
            return None

        # Si le buffer ne couvre pas le debut (ex: pic juste apres demarrage capture)
        # on prend ce qu'on peut
        selected_indices = sorted(i for i in range(first_idx, last_idx + 1) if i in available)
        if not selected_indices:
            logger.warning(
                f"[clipper] buffer ne couvre pas la fenetre "
                f"[{first_idx}..{last_idx}], disponibles: {sorted(available.keys())}"
            )
            return None

        selected_paths = [available[i] for i in selected_indices]

        # Recalcul des offsets reels (les chunks selectionnes peuvent commencer
        # plus tard que target_start)
        actual_buffer_start = selected_indices[0] * self.segment_duration
        trim_start = max(0.0, target_start - actual_buffer_start)
        trim_duration = self.pre_seconds + self.post_seconds
        # ajustement si on a perdu du debut
        if target_start < actual_buffer_start:
            trim_duration -= (actual_buffer_start - target_start)

        output_path = self.output_dir / (
            f"{candidate.channel}_"
            f"{candidate.timestamp.strftime('%Y%m%d_%H%M%S')}_"
            f"s{int(candidate.score)}.mp4"
        )

        success = await self._ffmpeg_concat_and_cut(
            selected_paths,
            output_path,
            trim_start,
            trim_duration,
        )
        if not success:
            return None

        logger.info(
            f"[clipper] clip cree: {output_path.name} "
            f"(score={candidate.score:.1f}, cat={candidate.category.value}, "
            f"duree~{trim_duration:.0f}s)"
        )

        return Clip(
            path=str(output_path),
            channel=candidate.channel,
            candidate=candidate,
            duration=trim_duration,
        )

    def _chunk_index(self, path: Path) -> int | None:
        m = self._seg_re.search(path.name)
        return int(m.group(1)) if m else None

    async def _ffmpeg_concat_and_cut(
        self,
        chunks: list[Path],
        output: Path,
        start_offset: float,
        duration: float,
    ) -> bool:
        """Concat les chunks .ts via le demuxer concat, puis trim et reencode."""
        # Creation du fichier de concat list
        concat_list = output.with_suffix(".txt")
        async with aiofiles.open(concat_list, "w", encoding="utf-8") as f:
            for c in chunks:
                # format "file 'path'" - attention aux quotes
                await f.write(f"file '{c.as_posix()}'\n")

        try:
            # concat + trim + reencode en une passe
            args = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_list),
                "-ss", f"{start_offset:.3f}",
                "-t", f"{duration:.3f}",
                # Reencode: necessaire apres un trim precis, et donne un mp4 lisible partout
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "22",
                "-c:a", "aac",
                "-b:a", "160k",
                "-movflags", "+faststart",
                str(output),
            ]

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error(f"[clipper] ffmpeg echec (code {proc.returncode}): {stderr.decode(errors='ignore')[:500]}")
                return False
            return True
        finally:
            try:
                concat_list.unlink(missing_ok=True)
            except OSError:
                pass
