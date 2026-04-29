"""Tests pour VideoEditor (nouveau pipeline Remotion) et CaptionRenderer (legacy)."""
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


# ------------------------------------------------------------------
# VideoEditor — tests render()
# ------------------------------------------------------------------

async def test_render_skips_if_no_local_path():
    """render() retourne None sans appeler ffmpeg quand local_path est None."""
    editor = VideoEditor()
    clip = _make_clip(local_path=None)
    plan = _make_plan()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        result = await editor.render(clip, plan)

    assert result is None
    mock_exec.assert_not_called()


async def test_render_skips_if_file_missing(tmp_path):
    """render() retourne None si le fichier local est absent."""
    editor = VideoEditor()
    clip = _make_clip(local_path=str(tmp_path / "absent.mp4"))
    plan = _make_plan()

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        result = await editor.render(clip, plan)

    assert result is None
    mock_exec.assert_not_called()


# ------------------------------------------------------------------
# VideoEditor — _fallback_words
# ------------------------------------------------------------------

def test_fallback_words_distributes_evenly():
    """_fallback_words distribue les mots uniformément sur la durée."""
    editor = VideoEditor()
    plan = _make_plan(caption="KEKW KEKW KEKW")
    words = editor._fallback_words(plan, 3.0)

    assert len(words) == 3
    assert words[0]["start"] == 0.0
    assert abs(words[-1]["end"] - 3.0) < 0.01
    # Chaque mot occupe 1 seconde
    for w in words:
        assert abs((w["end"] - w["start"]) - 1.0) < 0.01


def test_fallback_words_uses_caption_over_title():
    """_fallback_words préfère plan.caption à plan.title."""
    editor = VideoEditor()
    plan = _make_plan(caption="CHAT WENT WILD", title="titre ignoré")
    words = editor._fallback_words(plan, 2.0)

    texts = [w["word"] for w in words]
    assert "CHAT" in texts
    assert "WENT" in texts
    assert "WILD" in texts


def test_fallback_words_fallback_on_empty(tmp_path):
    """_fallback_words retourne un segment unique si caption et title sont vides."""
    editor = VideoEditor()
    plan = _make_plan(caption="", title="")
    words = editor._fallback_words(plan, 5.0)

    assert len(words) == 1
    assert words[0]["start"] == 0.0
    assert words[0]["end"] == 5.0


def test_fallback_words_uppercase():
    """_fallback_words produit des mots en majuscules."""
    editor = VideoEditor()
    plan = _make_plan(caption="chat en pls", title="")
    words = editor._fallback_words(plan, 3.0)

    for w in words:
        assert w["word"] == w["word"].upper()


# ------------------------------------------------------------------
# CaptionRenderer — conservé comme module autonome
# ------------------------------------------------------------------

def test_caption_renderer_generates_ass(tmp_path):
    """build_ass() crée un fichier ASS valide avec les sections attendues."""
    renderer = CaptionRenderer(576, 1024, "/fake/font.ttf", "Impact")
    segments = [
        {"start": 0.0, "end": 1.0, "text": "ET PAR DES"},
        {"start": 1.0, "end": 2.0, "text": "PERSONNES QUI"},
        {"start": 2.0, "end": 3.2, "text": "REGARDENT"},
    ]
    ass_file = tmp_path / "test.ass"
    result = renderer.build_ass(segments, "titre ignoré", ass_file)

    assert result.exists()
    content = result.read_text(encoding="utf-8")
    assert "[V4+ Styles]" in content
    assert "Highlight" in content
    assert "[Events]" in content
    assert "REGARDENT" in content


def test_detect_strong_word_last():
    """_detect_strong_word retourne le dernier mot si aucun mot n'est en majuscules."""
    renderer = CaptionRenderer(576, 1024, "/fake/font.ttf", "Impact")
    idx = renderer._detect_strong_word(["de", "personnes", "qui"])
    assert idx == 2


def test_detect_strong_word_uppercase():
    """_detect_strong_word retourne l'index du mot en majuscules en priorité."""
    renderer = CaptionRenderer(576, 1024, "/fake/font.ttf", "Impact")
    idx = renderer._detect_strong_word(["PAS", "mis", "plus"])
    assert idx == 0
