"""Transcription audio mot-à-mot via faster-whisper."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..core.logging import logger


class Transcriber:
    def __init__(self, model_size: str = "medium") -> None:
        self._model_size = model_size

    async def transcribe(self, video_path: Path) -> list[dict]:
        """Transcrit la piste audio et retourne les mots horodatés.

        Returns: [{"word": str, "start": float, "end": float}, ...]
        Retourne [] si faster-whisper n'est pas installé ou si la transcription échoue.
        """
        loop = asyncio.get_event_loop()
        try:
            words = await loop.run_in_executor(None, self._transcribe_sync, video_path)
            logger.info(f"[whisper] {len(words)} mots transcrits → {video_path.name}")
            return words
        except RuntimeError:
            raise
        except Exception as exc:
            logger.warning(f"[whisper] transcription échouée : {exc!r}")
            return []

    def _transcribe_sync(self, video_path: Path) -> list[dict]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper non installé : pip install 'faster-whisper>=1.0'"
            ) from exc

        logger.info(f"[whisper] modèle {self._model_size!r} — {video_path.name}")
        model = WhisperModel(self._model_size, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(video_path), word_timestamps=True)

        words: list[dict] = []
        for seg in segments:
            for w in seg.words or []:
                word_text = w.word.strip()
                if word_text:
                    words.append({"word": word_text, "start": w.start, "end": w.end})

        return words
