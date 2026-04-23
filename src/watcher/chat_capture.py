"""Capture du chat Twitch IRC via twitchio.

Emet des ChatEvent dans la queue pour le Detector.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from twitchio.ext import commands
from ..core.events import ChatEvent
from ..core.logging import logger


# Detection simple d'emotes: mots en majuscules style KEKW, PogChamp, etc.
# Twitch envoie les emotes dans les tags IRC mais la liste officielle par stream
# est complexe. On se contente d'une detection textuelle pour le MVP (la plupart
# des emotes BTTV/FFZ/7TV apparaissent comme du texte dans le message).
_EMOTE_TOKEN_RE = re.compile(r"\b([A-Z][A-Za-z0-9]{2,19})\b")


def extract_emote_tokens(message: str, known_emotes: set[str]) -> list[str]:
    """Retourne les emotes connues presentes dans le message."""
    candidates = _EMOTE_TOKEN_RE.findall(message)
    return [c for c in candidates if c in known_emotes]


class ChatBot(commands.Bot):
    """Client IRC Twitch qui pousse chaque message dans une queue."""

    def __init__(
        self,
        token: str,
        nick: str,
        channel: str,
        queue: asyncio.Queue[ChatEvent],
        known_emotes: set[str],
    ):
        super().__init__(
            token=token,
            prefix="!",
            initial_channels=[channel],
            nick=nick,
        )
        self._channel = channel
        self._queue = queue
        self._known_emotes = known_emotes

    async def event_ready(self):
        logger.info(f"[chat] connecte comme {self.nick} sur #{self._channel}")

    async def event_message(self, message):
        # Ignore messages du bot lui-meme
        if message.echo:
            return
        if message.author is None:
            return

        content = message.content or ""
        emotes = extract_emote_tokens(content, self._known_emotes)

        event = ChatEvent(
            timestamp=datetime.now(timezone.utc),
            channel=self._channel,
            author=message.author.name,
            content=content,
            emotes=emotes,
        )
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("[chat] queue pleine, message drop")


async def run_chat_capture(
    token: str,
    nick: str,
    channel: str,
    queue: asyncio.Queue[ChatEvent],
    known_emotes: set[str],
) -> None:
    """Lance le bot IRC. A executer dans une task asyncio."""
    bot = ChatBot(token=token, nick=nick, channel=channel, queue=queue, known_emotes=known_emotes)
    try:
        await bot.start()
    except Exception as e:
        logger.error(f"[chat] erreur: {e!r}")
        raise
