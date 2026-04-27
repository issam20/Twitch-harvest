"""Tests unitaires pour DeepSeekAnalyzer (sans appel réseau)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.events import ClipCandidate, MomentCategory, TwitchClip
from src.editor.ai_analyzer import DeepSeekAnalyzer, EditPlan
from src.editor.clip_edit_queue import ClipEditQueue


def _valid_plan(**overrides) -> dict:
    base = {
        "worth_editing": True,
        "confidence": 0.87,
        "category": "funny",
        "trim_start": 2.0,
        "trim_end": 17.0,
        "highlight_moment": 8.0,
        "title": "Chat explose sur le stream de Kamet0",
        "caption": "Le moment le plus dingue du stream",
        "hashtags": ["#twitch", "#tiktok", "#viral", "#kamet0"],
        "caption_style": "impact",
        "caption_position": "bottom",
        "color_grade": "viral",
        "add_zoom": True,
    }
    base.update(overrides)
    return base


def _make_clip() -> TwitchClip:
    return TwitchClip(
        id="TestClipABC123",
        url="https://www.twitch.tv/clips/TestClipABC123",
        title="Auto-clip #1",
        channel="kamet0",
        creator_name="auto",
        view_count=0,
        duration=60.0,
        created_at=datetime.now(timezone.utc),
        thumbnail_url="",
    )


def _make_candidate() -> ClipCandidate:
    return ClipCandidate(
        timestamp=datetime.now(timezone.utc),
        channel="kamet0",
        score=75.0,
        category=MomentCategory.FUNNY,
        reason="velocity spike + emotes",
        chat_velocity=5.0,
        emote_density=0.4,
        sample_messages=["LUL LUL LUL", "OMEGALUL", "KEKW KEKW"],
    )


@pytest.fixture
def mock_openai():
    with patch("src.editor.ai_analyzer.AsyncOpenAI") as mock_cls:
        client = AsyncMock()
        mock_cls.return_value = client
        yield client


async def test_valid_json_returns_edit_plan(mock_openai):
    mock_openai.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps(_valid_plan())))]
    )

    analyzer = DeepSeekAnalyzer(api_key="test_key")
    plan = await analyzer.analyze(_make_clip(), _make_candidate())

    assert isinstance(plan, EditPlan)
    assert plan.worth_editing is True
    assert plan.confidence == 0.87
    assert plan.title == "Chat explose sur le stream de Kamet0"
    assert plan.category == "funny"
    mock_openai.chat.completions.create.assert_called_once()


async def test_invalid_json_retries_and_fallback(mock_openai):
    mock_openai.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="pas du json valide {{{{"))]
    )

    analyzer = DeepSeekAnalyzer(api_key="test_key")
    plan = await analyzer.analyze(_make_clip(), _make_candidate())

    assert plan.worth_editing is False
    assert plan.confidence == 0.0
    assert mock_openai.chat.completions.create.call_count == 3


async def test_worth_editing_false_on_low_score(mock_openai):
    mock_openai.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(
            content=json.dumps(_valid_plan(worth_editing=False, confidence=0.1))
        ))]
    )

    editor = AsyncMock()
    analyzer = DeepSeekAnalyzer(api_key="test_key")
    queue = ClipEditQueue(analyzer=analyzer, editor=editor)
    await queue.start()

    queue.push(_make_clip(), _make_candidate())
    await asyncio.sleep(0.1)
    await queue.stop()

    editor.render.assert_not_called()
