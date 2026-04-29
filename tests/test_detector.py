"""Tests unitaires des 3 signaux et du scorer.

Pas de reseau, pas de ffmpeg. On injecte des evenements synthetiques.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.core.events import ChatEvent, MomentCategory
from src.detector.chat_velocity import ChatVelocityTracker
from src.detector.emote_spam import EmoteDensityTracker
from src.detector.scorer import ViralScorer


def _mk_event(ts: datetime, content: str = "hello", emotes: list[str] | None = None) -> ChatEvent:
    return ChatEvent(
        timestamp=ts,
        channel="test",
        author="u",
        content=content,
        emotes=emotes or [],
    )


class TestChatVelocity:
    def test_no_messages_returns_zero(self):
        tracker = ChatVelocityTracker()
        now = datetime.now(timezone.utc)
        score, _ = tracker.score(now)
        assert score == 0.0

    def test_below_abs_threshold_returns_zero(self):
        tracker = ChatVelocityTracker(velocity_floor=5.0)
        now = datetime.now(timezone.utc)
        # 3 msgs sur 10s = 0.3 msg/s, sous le seuil absolu
        for i in range(3):
            tracker.add(_mk_event(now - timedelta(seconds=i)))
        score, _ = tracker.score(now)
        assert score == 0.0

    def test_spike_triggers_high_score(self):
        tracker = ChatVelocityTracker(
            window_seconds=10,
            stats_window_seconds=60,
            velocity_floor=5.0,
            z_score_threshold=3.0,
            warmup_samples=5,
        )
        now = datetime.now(timezone.utc)
        # Phase 1: baseline (1 msg/2s) + warmup avec score() répétés
        ts = now - timedelta(seconds=60)
        for _ in range(6):  # 6 scores > warmup_samples=5
            for _ in range(1):  # 1 msg toutes les 2s
                tracker.add(_mk_event(ts))
                ts += timedelta(seconds=2)
            tracker.score(ts)
        # Phase 2: spike
        for i in range(80):
            tracker.add(_mk_event(now - timedelta(seconds=2 - i * 0.025)))
        score, debug = tracker.score(now)
        assert score > 70
        assert debug["velocity"] >= 5.0
        assert debug["z"] >= 3.0

    def test_old_messages_pruned(self):
        tracker = ChatVelocityTracker(stats_window_seconds=60)
        now = datetime.now(timezone.utc)
        # 50 msgs tres vieux (>60s)
        for i in range(50):
            tracker.add(_mk_event(now - timedelta(seconds=120 + i)))
        # reevaluer au now force le prune
        score, debug = tracker.score(now)
        assert score == 0.0


class TestEmoteDensity:
    def test_empty_returns_zero(self):
        tracker = EmoteDensityTracker(emote_categories={"laugh": ["KEKW"]})
        now = datetime.now(timezone.utc)
        score, cat, _ = tracker.score(now)
        assert score == 0.0
        assert cat == MomentCategory.UNKNOWN

    def test_below_threshold(self):
        tracker = EmoteDensityTracker(
            density_threshold=0.35,
            emote_categories={"laugh": ["KEKW"]},
        )
        now = datetime.now(timezone.utc)
        # 10 msgs dont 2 avec KEKW = 20%
        for i in range(10):
            emotes = ["KEKW"] if i < 2 else []
            tracker.add(_mk_event(now - timedelta(seconds=i * 0.5), emotes=emotes))
        score, _, debug = tracker.score(now)
        assert score == 0.0
        assert debug["density"] == 0.2

    def test_high_density_high_score(self):
        tracker = EmoteDensityTracker(
            density_threshold=0.35,
            emote_categories={"laugh": ["KEKW", "OMEGALUL"]},
        )
        now = datetime.now(timezone.utc)
        # 20 msgs dont 16 avec emote = 80%
        for i in range(20):
            emotes = ["KEKW"] if i < 16 else []
            tracker.add(_mk_event(now - timedelta(seconds=i * 0.3), emotes=emotes))
        score, cat, _ = tracker.score(now)
        assert score >= 90
        assert cat == MomentCategory.FUNNY

    def test_dominant_category(self):
        tracker = EmoteDensityTracker(
            density_threshold=0.35,
            emote_categories={"laugh": ["KEKW"], "hype": ["Pog"]},
        )
        now = datetime.now(timezone.utc)
        # majorite Pog -> hype
        for i in range(10):
            emotes = ["Pog"] if i < 6 else (["KEKW"] if i < 8 else [])
            tracker.add(_mk_event(now - timedelta(seconds=i * 0.3), emotes=emotes))
        _, cat, _ = tracker.score(now)
        assert cat == MomentCategory.HYPE


class TestScorerFusion:
    def test_low_scores_no_candidate(self):
        scorer = ViralScorer(min_viral_score=60, cooldown_seconds=30)
        now = datetime.now(timezone.utc)
        result = scorer.evaluate(
            now=now, channel="t",
            velocity_score=30, velocity_debug={"velocity": 1, "z": 1.5},
            emote_score=20, emote_category=MomentCategory.UNKNOWN, emote_debug={"density": 0.1},
            unique_score=0, unique_debug={},
            caps_score=0, caps_debug={},
            repetition_score=0, repetition_debug={},
            sample_messages=[],
        )
        assert result is None

    def test_high_scores_trigger(self):
        scorer = ViralScorer(min_viral_score=60, cooldown_seconds=30)
        now = datetime.now(timezone.utc)
        result = scorer.evaluate(
            now=now, channel="t",
            velocity_score=90, velocity_debug={"velocity": 10, "z": 5},
            emote_score=85, emote_category=MomentCategory.FUNNY, emote_debug={"density": 0.7},
            unique_score=0, unique_debug={},
            caps_score=0, caps_debug={},
            repetition_score=0, repetition_debug={},
            sample_messages=["KEKW", "LOL"],
        )
        assert result is not None
        assert result.score >= 60
        assert result.category == MomentCategory.FUNNY

    def test_cooldown_blocks_second(self):
        scorer = ViralScorer(min_viral_score=60, cooldown_seconds=30)
        now = datetime.now(timezone.utc)

        first = scorer.evaluate(
            now=now, channel="t",
            velocity_score=90, velocity_debug={"velocity": 10, "z": 5},
            emote_score=80, emote_category=MomentCategory.FUNNY, emote_debug={"density": 0.6},
            unique_score=0, unique_debug={},
            caps_score=0, caps_debug={},
            repetition_score=0, repetition_debug={},
            sample_messages=[],
        )
        assert first is not None

        # 10s apres -> cooldown doit bloquer
        second = scorer.evaluate(
            now=now + timedelta(seconds=10), channel="t",
            velocity_score=95, velocity_debug={"velocity": 12, "z": 6},
            emote_score=90, emote_category=MomentCategory.FUNNY, emote_debug={"density": 0.75},
            unique_score=0, unique_debug={},
            caps_score=0, caps_debug={},
            repetition_score=0, repetition_debug={},
            sample_messages=[],
        )
        assert second is None

        # 35s apres -> cooldown passe
        third = scorer.evaluate(
            now=now + timedelta(seconds=35), channel="t",
            velocity_score=85, velocity_debug={"velocity": 9, "z": 4.5},
            emote_score=75, emote_category=MomentCategory.FUNNY, emote_debug={"density": 0.55},
            unique_score=0, unique_debug={},
            caps_score=0, caps_debug={},
            repetition_score=0, repetition_debug={},
            sample_messages=[],
        )
        assert third is not None

    def test_high_scores_without_audio_trigger(self):
        scorer = ViralScorer(min_viral_score=60, cooldown_seconds=30)
        now = datetime.now(timezone.utc)
        result = scorer.evaluate(
            now=now, channel="t",
            velocity_score=90, velocity_debug={"velocity": 10, "z": 5},
            emote_score=80, emote_category=MomentCategory.HYPE, emote_debug={"density": 0.6},
            unique_score=0, unique_debug={},
            caps_score=0, caps_debug={},
            repetition_score=0, repetition_debug={},
            sample_messages=[],
        )
        assert result is not None
        assert result.score >= 70
