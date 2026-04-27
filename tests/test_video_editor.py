"""Tests pour VideoEditor et CaptionRenderer (sans ffmpeg réel)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.events import TwitchClip
from src.editor.ai_analyzer import EditPlan
from src.editor.caption_renderer import CaptionRenderer
from src.editor.video_editor import VideoEditor


def _make_plan(**overrides) -> EditPlan:
    base = {
        "worth_editing": True,
        "confidence": 0.8,
        "category": "funny",
        "trim_start": 2.0,
        "trim_end": 17.0,
        "highlight_moment": 8.0,
        "title": "le chat en PLS 💀",
        "caption": "quand tout le monde spam KEKW",
        "hashtags": ["#twitch", "#tiktok"],
        "caption_style": "impact",
        "caption_position": "top",
        "color_grade": "viral",
        "add_zoom": False,
    }
    base.update(overrides)
    return EditPlan(**base)


def _make_clip(local_path: str | None = "/tmp/test.mp4") -> TwitchClip:
    return TwitchClip(
        id="TestClip123",
        url="https://clips.twitch.tv/TestClip123",
        title="Test",
        channel="test_channel",
        creator_name="auto",
        view_count=0,
        duration=30.0,
        created_at=datetime.now(timezone.utc),
        thumbnail_url="",
        local_path=local_path,
    )


async def test_render_skips_if_no_local_path():
    """render() retourne None sans appeler ffmpeg quand local_path est None."""
    editor = VideoEditor()
    clip = _make_clip(local_path=None)
    plan = _make_plan()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        result = await editor.render(clip, plan)

    assert result is None
    mock_exec.assert_not_called()


def test_caption_renderer_generates_ass(tmp_path):
    """build_ass() crée un fichier ASS valide avec les sections attendues."""
    renderer = CaptionRenderer(576, 1024, "/fake/font.ttf", "Impact")
    segments = [
        {"start": 0.0, "end": 1.0, "text": "ET PAR DES"},
        {"start": 1.0, "end": 2.0, "text": "PERSONNES QUI"},
        {"start": 2.0, "end": 3.2, "text": "REGARDENT"},
    ]
    ass_file = tmp_path / "test.ass"
    result = renderer.build_ass(segments, "frère 💀", ass_file)

    assert result.exists()
    content = result.read_text(encoding="utf-8")
    assert "[V4+ Styles]" in content
    assert "Highlight" in content
    assert "[Events]" in content
    assert "FRÈRE 💀" in content


def test_detect_strong_word_last():
    """_detect_strong_word retourne l'index du dernier mot quand aucun mot n'est en majuscules."""
    renderer = CaptionRenderer(576, 1024, "/fake/font.ttf", "Impact")
    idx = renderer._detect_strong_word(["de", "personnes", "qui"])
    assert idx == 2


def test_detect_strong_word_uppercase():
    """_detect_strong_word retourne l'index du mot en majuscules en priorité."""
    renderer = CaptionRenderer(576, 1024, "/fake/font.ttf", "Impact")
    idx = renderer._detect_strong_word(["PAS", "mis", "plus"])
    assert idx == 0
