"""VideoEditor — squelette phase 2 (rendu ffmpeg non implémenté)."""
from __future__ import annotations

from ..core.events import TwitchClip
from ..core.logging import logger
from .ai_analyzer import EditPlan


class VideoEditor:
    async def render(self, clip: TwitchClip, plan: EditPlan) -> None:
        logger.info("[editor] render non implémenté (phase 2)")
