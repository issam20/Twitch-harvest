"""Orchestrator: assemble toutes les pieces du pipeline MVP.

Flow:
 - VideoCapture (streamlink + ffmpeg segment) tourne en arriere-plan
 - AudioMonitor (streamlink + ffmpeg ebur128) tourne en arriere-plan
 - ChatBot (twitchio) alimente une queue de ChatEvent
 - Une tache "detector_loop" consomme la queue:
     - met a jour les trackers (velocity, emote)
     - toutes les X secondes, evalue le score global (inclut snapshot audio)
     - si candidat -> Clipper.extract() -> persist en DB
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from ..core.config import Settings, StreamerConfig, Env, apply_streamer_overrides
from ..core.db import Database
from ..core.events import ChatEvent, MomentCategory
from ..core.logging import logger
from ..watcher.chat_capture import run_chat_capture
from ..watcher.video_capture import VideoCapture
from ..watcher.audio_capture import AudioMonitor
from ..detector.chat_velocity import ChatVelocityTracker
from ..detector.emote_spam import EmoteDensityTracker
from ..detector.scorer import ViralScorer
from ..clipper.extractor import Clipper


class Pipeline:
    def __init__(self, env: Env, settings: Settings, streamer: StreamerConfig):
        self.env = env
        self.streamer = streamer
        # overrides specifique streamer
        self.settings = apply_streamer_overrides(settings, streamer)

        data_dir = env.data_dir
        self.buffer_dir = data_dir / "buffer"
        self.clips_dir = data_dir / "clips"
        self.db = Database(data_dir / "state.db")

        # Flatten emotes pour la detection dans les messages
        emote_categories = self.settings.detector.emotes
        known_emotes: set[str] = set()
        for _, emotes in emote_categories.items():
            known_emotes.update(emotes)
        self.known_emotes = known_emotes

        # Queue chat
        self.chat_queue: asyncio.Queue[ChatEvent] = asyncio.Queue(maxsize=10_000)

        # Components
        self.video = VideoCapture(
            channel=streamer.login,
            buffer_dir=self.buffer_dir,
            segment_duration=self.settings.clipper.segment_duration,
            buffer_seconds=self.settings.clipper.buffer_seconds,
            quality=self.settings.clipper.stream_quality,
        )
        self.audio = AudioMonitor(channel=streamer.login)
        self.velocity_tracker = ChatVelocityTracker(
            window_seconds=self.settings.detector.chat_window_seconds,
            baseline_seconds=self.settings.detector.baseline_window_seconds,
            velocity_threshold=self.settings.detector.chat_velocity_threshold,
            multiplier_threshold=self.settings.detector.velocity_multiplier,
        )
        self.emote_tracker = EmoteDensityTracker(
            window_seconds=self.settings.detector.chat_window_seconds,
            density_threshold=self.settings.detector.emote_density_threshold,
            emote_categories=emote_categories,
        )
        self.scorer = ViralScorer(
            min_viral_score=self.settings.detector.min_viral_score,
            cooldown_seconds=self.settings.detector.cooldown_seconds,
        )
        self.clipper = Clipper(
            output_dir=self.clips_dir,
            pre_seconds=self.settings.clipper.clip_pre_seconds,
            post_seconds=self.settings.clipper.clip_post_seconds,
            segment_duration=self.settings.clipper.segment_duration,
        )

        # Garde les derniers messages pour contexte (sample_messages)
        self._recent_msgs: deque[str] = deque(maxlen=20)

        self._stop = asyncio.Event()

    async def run(self) -> None:
        await self.db.init()

        tasks: list[asyncio.Task] = []
        try:
            # Video + audio en tasks
            tasks.append(asyncio.create_task(self.video.start(), name="video"))
            tasks.append(asyncio.create_task(self.audio.start(), name="audio"))

            # Chat capture
            tasks.append(asyncio.create_task(
                run_chat_capture(
                    token=self.env.twitch_irc_token,
                    nick=self.env.twitch_irc_nick,
                    channel=self.streamer.login,
                    queue=self.chat_queue,
                    known_emotes=self.known_emotes,
                ),
                name="chat",
            ))

            # Detector loops
            tasks.append(asyncio.create_task(self._chat_consumer_loop(), name="chat_consumer"))
            tasks.append(asyncio.create_task(self._scoring_loop(), name="scoring"))

            logger.info(f"[pipeline] demarre pour #{self.streamer.login} — Ctrl+C pour arreter")
            await self._stop.wait()
        finally:
            logger.info("[pipeline] arret en cours")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.audio.stop()
            await self.video.stop()
            logger.info("[pipeline] arrete")

    def stop(self) -> None:
        self._stop.set()

    async def _chat_consumer_loop(self) -> None:
        """Consomme la queue chat et alimente les trackers."""
        while not self._stop.is_set():
            try:
                event = await asyncio.wait_for(self.chat_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            self.velocity_tracker.add(event)
            self.emote_tracker.add(event)
            self._recent_msgs.append(f"{event.author}: {event.content}")

    async def _scoring_loop(self) -> None:
        """Toutes les 2s, calcule le score et emet un candidat si seuil atteint."""
        interval = 2.0
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            now = datetime.now(timezone.utc)

            v_score, v_debug = self.velocity_tracker.score(now)
            e_score, e_cat, e_debug = self.emote_tracker.score(now)

            is_peak, current_db, z = self.audio.is_peak()
            a_score = self.audio.peak_score() if len(self.audio._history) >= 20 else None
            a_debug = {"db": round(current_db, 1), "zscore": round(z, 2)} if a_score is not None else None

            sample = list(self._recent_msgs)[-10:]

            candidate = self.scorer.evaluate(
                now=now,
                channel=self.streamer.login,
                velocity_score=v_score,
                velocity_debug=v_debug,
                emote_score=e_score,
                emote_category=e_cat,
                emote_debug=e_debug,
                audio_score=a_score,
                audio_debug=a_debug,
                sample_messages=sample,
            )

            if candidate is None:
                continue

            logger.info(f"[pipeline] 🎯 candidat detecte: {candidate.reason}")

            # Extract
            chunks = self.video.list_buffer_chunks()
            capture_started = self.video.capture_started_at
            if capture_started is None:
                logger.warning("[pipeline] video pas encore prete, candidat ignore")
                continue

            clip = await self.clipper.extract(candidate, chunks, capture_started)
            if clip is None:
                continue

            await self.db.record_clip(
                channel=clip.channel,
                path=clip.path,
                score=candidate.score,
                category=candidate.category.value,
                reason=candidate.reason,
                peak_ts=candidate.timestamp,
            )
