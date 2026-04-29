"""Tests TelegramNotifier — 3 tests sans réseau (mock httpx)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.events import TwitchClip
from src.editor.ai_analyzer import EditPlan
from src.publisher.telegram_notifier import TelegramNotifier


def _make_plan(**overrides) -> EditPlan:
    base = {
        "worth_editing": True,
        "confidence": 0.9,
        "category": "funny",
        "trim_start": 0.0,
        "trim_end": 15.0,
        "highlight_moment": 5.0,
        "title": "Chat en PLS",
        "caption": "quand tout le monde spam KEKW",
        "hashtags": ["#twitch", "#tiktok"],
        "caption_style": "impact",
        "caption_position": "top",
        "color_grade": "viral",
        "add_zoom": False,
    }
    base.update(overrides)
    return EditPlan(**base)


def _make_clip(**overrides) -> TwitchClip:
    base = dict(
        id="TestClip123",
        url="https://clips.twitch.tv/TestClip123",
        title="Test",
        channel="test_channel",
        creator_name="auto",
        view_count=0,
        duration=15.0,
        created_at=datetime.now(timezone.utc),
        thumbnail_url="",
        composite_score=87.5,
    )
    base.update(overrides)
    return TwitchClip(**base)


def _mock_async_client(side_effect=None):
    """Construit un mock httpx.AsyncClient context-manager."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    if side_effect:
        mock_client.post = AsyncMock(side_effect=side_effect)
    else:
        mock_client.post = AsyncMock(return_value=mock_response)
    mock_cls = MagicMock()
    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_cls, mock_client


async def test_notify_sends_document(tmp_path):
    """Fichier < 50 MB → sendDocument appelé avec caption HTML."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video-data")

    notifier = TelegramNotifier(token="tok", chat_id="123")
    mock_cls, mock_client = _mock_async_client()

    with patch("src.publisher.telegram_notifier.httpx.AsyncClient", mock_cls):
        await notifier.notify(video, _make_plan(), _make_clip())

    mock_client.post.assert_called_once()
    url = mock_client.post.call_args.args[0]
    assert "sendDocument" in url
    data = mock_client.post.call_args.kwargs["data"]
    assert data["parse_mode"] == "HTML"
    assert "Chat en PLS" in data["caption"]
    assert "#twitch" in data["caption"]


async def test_notify_sends_text_on_large_file(tmp_path):
    """Fichier > 50 MB → sendMessage avec chemin local dans le texte."""
    video = tmp_path / "big.mp4"
    video.write_bytes(b"fake")

    notifier = TelegramNotifier(token="tok", chat_id="123")
    mock_cls, mock_client = _mock_async_client()

    stat_mock = MagicMock()
    stat_mock.st_size = 60 * 1024 * 1024  # 60 MB

    with patch("pathlib.Path.stat", return_value=stat_mock):
        with patch("src.publisher.telegram_notifier.httpx.AsyncClient", mock_cls):
            await notifier.notify(video, _make_plan(), _make_clip())

    url = mock_client.post.call_args.args[0]
    assert "sendMessage" in url
    payload = mock_client.post.call_args.kwargs["json"]
    assert str(video) in payload["text"]
    assert payload["parse_mode"] == "HTML"


async def test_notify_swallows_http_error(tmp_path):
    """Erreur HTTP → loggée, aucune exception propagée."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    notifier = TelegramNotifier(token="tok", chat_id="123")
    mock_cls, mock_client = _mock_async_client(side_effect=httpx.HTTPError("timeout"))

    with patch("src.publisher.telegram_notifier.httpx.AsyncClient", mock_cls):
        await notifier.notify(video, _make_plan(), _make_clip())  # ne doit pas lever
