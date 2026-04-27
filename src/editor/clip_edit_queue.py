"""File d'attente asynchrone non-bloquante pour l'analyse IA et le montage des clips."""
from __future__ import annotations

import asyncio

from ..core.events import ClipCandidate, TwitchClip
from ..core.logging import logger


class ClipEditQueue:
    def __init__(self, analyzer, editor=None) -> None:
        self._analyzer = analyzer
        self._editor = editor
        self._queue: asyncio.Queue[tuple[TwitchClip, ClipCandidate]] = asyncio.Queue(maxsize=50)
        self._worker_task: asyncio.Task | None = None

    def push(self, clip: TwitchClip, candidate: ClipCandidate) -> None:
        try:
            self._queue.put_nowait((clip, candidate))
        except asyncio.QueueFull:
            logger.warning(f"[edit_queue] queue pleine — clip {clip.id} ignoré")

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._worker(), name="edit_queue_worker")
        logger.info("[edit_queue] worker démarré")

    async def stop(self) -> None:
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        logger.info("[edit_queue] worker arrêté")

    async def _worker(self) -> None:
        while True:
            try:
                clip, candidate = await self._queue.get()
            except asyncio.CancelledError:
                raise
            try:
                plan = await self._analyzer.analyze(clip, candidate)
                logger.info(
                    f"[edit_queue] clip {clip.id} traité — worth_editing={plan.worth_editing}"
                    + (f" | title={plan.title!r}" if plan.worth_editing else "")
                )
                if plan.worth_editing and self._editor is not None:
                    await self._editor.render(clip, plan)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"[edit_queue] erreur inattendue sur clip {clip.id}: {exc!r}")
            finally:
                self._queue.task_done()
