"""Tests for the `edit` CLI command."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from src.main import app

runner = CliRunner()

_SESSION_ROW = {
    "id": 42,
    "streamer": "kamet0",
    "started_at": "2024-01-01T00:00:00+00:00",
    "ended_at": None,
}

_CLIP_ROW = {
    "id": 1,
    "twitch_id": "TestClipXYZ",
    "url": "https://clips.twitch.tv/TestClipXYZ",
    "title": "Test clip",
    "duration": 30.0,
    "created_at": "2024-01-01T00:00:00+00:00",
    "composite_score": 75.0,
    "category": "funny",
    "v_score": 50.0,
    "e_score": 30.0,
    "u_score": 20.0,
    "c_score": 10.0,
    "r_score": 5.0,
    "thumbnail_url": None,
    "local_path": None,
    "edit_plan_json": None,
    "processed_path": None,
}


def test_edit_last_no_sessions():
    """edit --last exits 1 when no session exists in DB."""
    mock_db = AsyncMock()
    mock_db.get_last_session.return_value = None

    with patch("src.main.Database", return_value=mock_db), \
         patch("src.main.Env") as mock_env_cls:
        mock_env_cls.return_value.data_dir = Path("/tmp/fake")
        mock_env_cls.return_value.deepseek_api_key = ""

        result = runner.invoke(app, ["edit", "--last"])

    assert result.exit_code == 1


def test_edit_by_twitch_id():
    """edit --clip <id> calls analyze and persists the edit plan to DB."""
    mock_plan = MagicMock()
    mock_plan.worth_editing = False
    mock_plan.model_dump_json.return_value = '{"worth_editing": false}'

    mock_analyzer = AsyncMock()
    mock_analyzer.analyze.return_value = mock_plan

    mock_db = AsyncMock()
    mock_db.get_clip_by_twitch_id.return_value = _CLIP_ROW

    with patch("src.main.Database", return_value=mock_db), \
         patch("src.main.Env") as mock_env_cls, \
         patch("src.editor.ai_analyzer.DeepSeekAnalyzer", return_value=mock_analyzer):
        mock_env_cls.return_value.data_dir = Path("/tmp/fake")
        mock_env_cls.return_value.deepseek_api_key = "test_key"

        result = runner.invoke(app, ["edit", "--clip", "TestClipXYZ"])

    assert result.exit_code == 0
    mock_db.update_clip_edit_result.assert_called_once_with(1, '{"worth_editing": false}')


def test_edit_dry_run_skips_api_and_render():
    """edit --last --dry-run lists clips without calling DeepSeek or updating DB."""
    mock_db = AsyncMock()
    mock_db.get_last_session.return_value = _SESSION_ROW
    mock_db.get_clips_by_session.return_value = [_CLIP_ROW]

    with patch("src.main.Database", return_value=mock_db), \
         patch("src.main.Env") as mock_env_cls:
        mock_env_cls.return_value.data_dir = Path("/tmp/fake")
        mock_env_cls.return_value.deepseek_api_key = "test_key"

        result = runner.invoke(app, ["edit", "--last", "--dry-run"])

    assert result.exit_code == 0
    mock_db.update_clip_edit_result.assert_not_called()
