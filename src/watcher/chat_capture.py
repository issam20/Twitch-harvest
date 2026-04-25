"""Capture du chat Twitch via connexion IRC directe (asyncio TCP).

Remplace l'ancienne implémentation twitchio (incompatible v3.x).
Protocole IRC Twitch standard — aucune dépendance externe.
Se reconnecte automatiquement si la connexion est perdue.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from ..core.events import ChatEvent
from ..core.logging import logger

_IRC_HOST = "irc.chat.twitch.tv"
_IRC_PORT = 6667
_PRIVMSG_RE = re.compile(r"^:(\w+)!\w+@\w+\.tmi\.twitch\.tv PRIVMSG #\w+ :(.+)$")
_EMOTE_TOKEN_RE = re.compile(r"\b([A-Z][A-Za-z0-9]{2,19})\b")


def extract_emote_tokens(message: str, known_emotes: set[str]) -> list[str]:
    """Retourne les emotes connues présentes dans le message."""
    return [c for c in _EMOTE_TOKEN_RE.findall(message) if c in known_emotes]


async def run_chat_capture(
    token: str,
    nick: str,
    channel: str,
    queue: asyncio.Queue[ChatEvent],
    known_emotes: set[str],
) -> None:
    """Lance la lecture IRC. Se reconnecte automatiquement sur déconnexion."""
    while True:
        try:
            await _irc_session(token, nick, channel, queue, known_emotes)
        except asyncio.CancelledError:
            raise
        except ConnectionError as exc:
            # Token invalide ou nick introuvable → inutile de retry
            logger.error(f"[chat] {exc}")
            raise
        except Exception as exc:
            logger.warning(f"[chat] connexion perdue ({exc!r}), reconnexion dans 10s…")
            await asyncio.sleep(10)


async def _irc_session(
    token: str,
    nick: str,
    channel: str,
    queue: asyncio.Queue[ChatEvent],
    known_emotes: set[str],
) -> None:
    reader, writer = await asyncio.open_connection(_IRC_HOST, _IRC_PORT)
    try:
        bare = token.removeprefix("oauth:")
        writer.write(f"PASS oauth:{bare}\r\nNICK {nick}\r\n".encode())
        await writer.drain()

        # Attente confirmation de connexion (001 = Welcome, 376 = End of MOTD)
        async for line in _lines(reader):
            if "001" in line or "376" in line:
                break
            if "NOTICE" in line and "Login authentication failed" in line:
                raise ConnectionError(
                    f"Authentification IRC échouée — vérifie TWITCH_IRC_TOKEN et TWITCH_IRC_NICK\n"
                    f"  Réponse serveur: {line}"
                )

        writer.write(f"JOIN #{channel}\r\n".encode())
        await writer.drain()
        logger.info(f"[chat] connecté comme {nick!r} sur #{channel}")

        async for line in _lines(reader):
            # Keep-alive PING/PONG
            if line.startswith("PING"):
                writer.write(b"PONG :tmi.twitch.tv\r\n")
                await writer.drain()
                continue

            m = _PRIVMSG_RE.match(line)
            if not m:
                continue

            author, content = m.group(1), m.group(2)
            event = ChatEvent(
                timestamp=datetime.now(timezone.utc),
                channel=channel,
                author=author,
                content=content,
                emotes=extract_emote_tokens(content, known_emotes),
            )
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("[chat] queue pleine, message ignoré")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _lines(reader: asyncio.StreamReader):
    """Itérateur asynchrone sur les lignes IRC décodées."""
    while True:
        raw = await reader.readline()
        if not raw:
            raise ConnectionError("Connexion IRC fermée par le serveur")
        yield raw.decode(errors="ignore").strip()
