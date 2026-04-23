"""Capture video via streamlink -> ffmpeg segment.

Ecrit des chunks .ts numerotes dans buffer_dir/<channel>/seg_XXXX.ts,
et maintient une fenetre glissante (supprime les plus vieux).

Expose get_buffer_files() pour que le Clipper recupere les chunks.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from datetime import datetime, timezone

from ..core.logging import logger


class VideoCaptureError(Exception):
    pass


class VideoCapture:
    """Gere le process streamlink | ffmpeg segment en tache de fond."""

    def __init__(
        self,
        channel: str,
        buffer_dir: Path,
        segment_duration: int = 10,
        buffer_seconds: int = 120,
        quality: str = "720p60,720p,best",
    ):
        self.channel = channel
        self.buffer_dir = buffer_dir / channel
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        self.segment_duration = segment_duration
        self.buffer_seconds = buffer_seconds
        self.quality = quality

        self._streamlink_proc: asyncio.subprocess.Process | None = None
        self._ffmpeg_proc: asyncio.subprocess.Process | None = None
        self._janitor_task: asyncio.Task | None = None
        # timestamp du debut de segment 0 (heure reelle) -> pour mapper un moment chat
        # a un fichier chunk
        self._capture_started_at: datetime | None = None

    @property
    def capture_started_at(self) -> datetime | None:
        return self._capture_started_at

    @property
    def segment_pattern(self) -> str:
        return str(self.buffer_dir / "seg_%05d.ts")

    async def start(self) -> None:
        """Demarre streamlink pipe vers ffmpeg segmenter."""
        url = f"https://twitch.tv/{self.channel}"
        logger.info(f"[video] demarrage capture: {url} quality={self.quality}")

        # streamlink sortie stdout
        self._streamlink_proc = await asyncio.create_subprocess_exec(
            "streamlink",
            "--stdout",
            "--twitch-disable-ads",
            "--retry-streams", "10",
            "--retry-max", "3",
            url,
            self.quality,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

        # ffmpeg qui lit stdin et segmente en .ts
        self._ffmpeg_proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-i", "pipe:0",
            "-c", "copy",                          # pas de re-encodage
            "-f", "segment",
            "-segment_time", str(self.segment_duration),
            "-reset_timestamps", "1",
            self.segment_pattern,
            stdin=self._streamlink_proc.stdout,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        self._capture_started_at = datetime.now(timezone.utc)
        self._janitor_task = asyncio.create_task(self._janitor_loop())

        # Log stderr ffmpeg en arriere plan (utile pour debug)
        asyncio.create_task(self._drain_stderr(self._ffmpeg_proc))

        logger.info(f"[video] capture demarree, chunks -> {self.buffer_dir}")

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stderr is None:
            return
        async for line in proc.stderr:
            txt = line.decode(errors="ignore").rstrip()
            if txt:
                logger.debug(f"[ffmpeg] {txt}")

    async def _janitor_loop(self) -> None:
        """Supprime les chunks plus vieux que buffer_seconds."""
        max_chunks = max(self.buffer_seconds // self.segment_duration, 1) + 2  # marge
        pattern = re.compile(r"seg_(\d+)\.ts$")
        while True:
            try:
                await asyncio.sleep(self.segment_duration)
                chunks = sorted(
                    self.buffer_dir.glob("seg_*.ts"),
                    key=lambda p: int(pattern.search(p.name).group(1)) if pattern.search(p.name) else 0,
                )
                # Garde les N plus recents
                to_delete = chunks[:-max_chunks] if len(chunks) > max_chunks else []
                for f in to_delete:
                    try:
                        f.unlink()
                    except OSError:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[video] janitor erreur: {e!r}")

    def list_buffer_chunks(self) -> list[Path]:
        """Liste triee des chunks actuellement en buffer."""
        pattern = re.compile(r"seg_(\d+)\.ts$")

        def key(p: Path) -> int:
            m = pattern.search(p.name)
            return int(m.group(1)) if m else 0

        return sorted(self.buffer_dir.glob("seg_*.ts"), key=key)

    async def stop(self) -> None:
        logger.info("[video] arret capture")
        if self._janitor_task:
            self._janitor_task.cancel()
        for proc in (self._ffmpeg_proc, self._streamlink_proc):
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (asyncio.TimeoutError, ProcessLookupError):
                    proc.kill()
