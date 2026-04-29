"""Notification Telegram post-render : envoie le MP4 + métadonnées au bot."""
from __future__ import annotations

from pathlib import Path

import httpx

from ..core.events import TwitchClip
from ..core.logging import logger
from ..editor.ai_analyzer import EditPlan

_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB — limite Bot API Telegram


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    def _build_caption(self, plan: EditPlan, clip: TwitchClip) -> str:
        duration = max(0.0, plan.trim_end - plan.trim_start)
        hashtags = " ".join(plan.hashtags)
        return (
            f"<b>{plan.title}</b>\n"
            f"{plan.caption}\n\n"
            f"{hashtags}\n\n"
            f"🎯 Score : {clip.composite_score:.1f}\n"
            f"⏱ Durée : {duration:.0f}s\n"
            f"📁 Session : {clip.id}\n\n"
            f'🎵 <a href="https://www.tiktok.com/upload">TikTok</a> · '
            f'📱 <a href="https://www.youtube.com/upload">YouTube Shorts</a>'
        )

    async def notify(self, video_path: Path, plan: EditPlan, clip: TwitchClip) -> None:
        """Envoie le clip édité sur Telegram. Ne lève jamais d'exception."""
        caption = self._build_caption(plan, clip)
        try:
            file_size = video_path.stat().st_size
            async with httpx.AsyncClient(timeout=120.0) as client:
                if file_size <= _MAX_FILE_BYTES:
                    with video_path.open("rb") as f:
                        resp = await client.post(
                            self._url("sendDocument"),
                            data={
                                "chat_id": self._chat_id,
                                "caption": caption,
                                "parse_mode": "HTML",
                            },
                            files={"document": (video_path.name, f, "video/mp4")},
                        )
                else:
                    logger.warning(
                        f"[telegram] {video_path.name} > 50 MB "
                        f"({file_size / 1_048_576:.1f} MB) — envoi texte uniquement"
                    )
                    text = caption + f"\n\n📂 <code>{video_path}</code>"
                    resp = await client.post(
                        self._url("sendMessage"),
                        json={
                            "chat_id": self._chat_id,
                            "text": text,
                            "parse_mode": "HTML",
                        },
                    )
                resp.raise_for_status()
            logger.info(f"[telegram] ✓ notification envoyée pour {video_path.name}")
        except Exception as exc:
            logger.error(f"[telegram] échec envoi : {exc!r}")
