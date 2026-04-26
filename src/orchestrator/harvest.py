"""HarvestPipeline — écoute le chat en continu et crée un clip Twitch natif
dès qu'un spike est détecté sur les 5 signaux.

Flow :
  1. Vérifie que le streamer est live → StreamerOfflineError sinon
  2. Lance le bot IRC en tâche de fond
  3. Toutes les 2s, évalue les 5 signaux et émet un snapshot au broadcaster
  4. Si velocity (obligatoire) + ≥1 autre signal déclenchés :
       → POST /helix/clips en tâche de fond (non-bloquant)
       → poll GET /helix/clips jusqu'à disponibilité
       → persiste en SQLite + notifie le broadcaster
  5. Cooldown 120s entre deux clips
  6. Toutes les 2min, vérifie que le stream est toujours live
  7. S'arrête sur Ctrl+C ou stream offline
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_PARIS = ZoneInfo("Europe/Paris")
from typing import TYPE_CHECKING

from ..api.twitch import TwitchAPIClient
from ..core.config import Env, Settings, StreamerConfig, apply_streamer_overrides
from ..core.db import Database
from ..core.events import ChatEvent, TwitchClip
from ..core.logging import logger
from ..detector.chat_signals import CapsRatioTracker, RepetitionTracker, UniqueChattersTracker
from ..detector.chat_velocity import ChatVelocityTracker
from ..detector.emote_spam import EmoteDensityTracker
from ..detector.scorer import ViralScorer
from ..watcher.chat_capture import run_chat_capture

if TYPE_CHECKING:
    from ..api.dashboard import SignalBroadcaster

_CLIP_PROCESS_DELAY  = 20
_CLIP_POLL_RETRIES   = 6
_CLIP_POLL_INTERVAL  = 10
_LIVENESS_CHECK_INTERVAL = 120
_CHAT_BATCH = 5   # messages chat envoyés au dashboard par snapshot


class HarvestPipeline:
    def __init__(
        self,
        env: Env,
        settings: Settings,
        streamer: StreamerConfig,
        cooldown_seconds: int = 120,
        broadcaster: SignalBroadcaster | None = None,
    ) -> None:
        self.env = env
        self.streamer = streamer
        self.settings = apply_streamer_overrides(settings, streamer)
        self.db = Database(env.data_dir / "state.db")
        self.broadcaster = broadcaster

        cfg = self.settings.detector
        self._known_emotes: set[str] = {e for emotes in cfg.emotes.values() for e in emotes}

        self.velocity_tracker = ChatVelocityTracker(
            window_seconds=cfg.chat_window_seconds,
            stats_window_seconds=cfg.stats_window_seconds,
            velocity_floor=cfg.velocity_floor,
            z_score_threshold=cfg.z_score_threshold,
            warmup_samples=cfg.warmup_samples,
        )
        self.emote_tracker = EmoteDensityTracker(
            window_seconds=cfg.chat_window_seconds,
            density_threshold=cfg.emote_density_threshold,
            emote_categories=cfg.emotes,
        )
        self.unique_tracker = UniqueChattersTracker(
            window_seconds=cfg.chat_window_seconds,
            baseline_seconds=cfg.baseline_window_seconds,
        )
        self.caps_tracker = CapsRatioTracker(window_seconds=cfg.chat_window_seconds)
        self.repetition_tracker = RepetitionTracker(window_seconds=cfg.chat_window_seconds)
        self.scorer = ViralScorer(
            cooldown_seconds=cooldown_seconds,
            min_viral_score=cfg.min_viral_score,
        )

        self._chat_queue: asyncio.Queue[ChatEvent] = asyncio.Queue(maxsize=10_000)
        self._recent_msgs: deque[str] = deque(maxlen=20)
        # Buffer de messages récents pour le dashboard (author, content)
        self._chat_batch: deque[dict] = deque(maxlen=_CHAT_BATCH)
        self._stop = asyncio.Event()
        self._collected: list[TwitchClip] = []
        self._msg_count: int = 0
        self._clip_in_progress: bool = False
        self._session_id: int | None = None

        self._raw_dir = env.data_dir / "clips" / "raw"
        self._processed_dir = env.data_dir / "clips" / "processed"
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self._processed_dir.mkdir(parents=True, exist_ok=True)

    async def run(self) -> list[TwitchClip]:
        await self.db.init()
        self._session_id = await self.db.create_session(
            self.streamer.login, datetime.now(timezone.utc)
        )
        logger.info(f"[harvest] session #{self._session_id} ouverte pour {self.streamer.login}")

        async with TwitchAPIClient(
            self.env.twitch_client_id, self.env.twitch_client_secret
        ) as api:
            stream = await api.require_live(self.streamer.login)
            broadcaster_id: str = stream["user_id"]
            started_at = datetime.fromisoformat(stream["started_at"].replace("Z", "+00:00"))

            logger.info(
                f"[harvest] {self.streamer.login} est live depuis "
                f"{started_at.astimezone(_PARIS).strftime('%H:%M:%S')} (Paris) — écoute du chat démarrée"
            )

            chat_task = asyncio.create_task(self._run_chat(), name="chat")
            tasks = [
                chat_task,
                asyncio.create_task(self._chat_consumer(), name="consumer"),
                asyncio.create_task(self._scoring_loop(api, broadcaster_id), name="scorer"),
                asyncio.create_task(self._liveness_loop(api), name="liveness"),
                asyncio.create_task(self._watch_chat_task(chat_task), name="chat_watchdog"),
            ]

            await self._stop.wait()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(f"[harvest] terminé — {len(self._collected)} clip(s) créé(s)")
        if self._session_id is not None:
            await self.db.close_session(self._session_id, datetime.now(timezone.utc))
            stats = await self.db.get_session_stats(self._session_id)
            logger.info(
                f"[harvest] session #{self._session_id} fermée — "
                f"{stats['clip_count']} clips | top score {stats['top_score']:.1f}"
            )
        return self._collected

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------

    async def _run_chat(self) -> None:
        logger.info(f"[chat] connexion IRC nick={self.env.twitch_irc_nick!r} sur #{self.streamer.login}")
        try:
            await run_chat_capture(
                token=self.env.twitch_irc_token,
                nick=self.env.twitch_irc_nick,
                channel=self.streamer.login,
                queue=self._chat_queue,
                known_emotes=self._known_emotes,
            )
        except Exception as exc:
            logger.error(f"[chat] ERREUR IRC : {exc!r}")
            raise

    async def _watch_chat_task(self, chat_task: asyncio.Task) -> None:
        try:
            await chat_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(
                f"[chat] task IRC arrêtée : {exc!r}\n"
                "  Vérifie TWITCH_IRC_TOKEN et TWITCH_IRC_NICK."
            )
            self.stop()

    async def _chat_consumer(self) -> None:
        while not self._stop.is_set():
            try:
                event = await asyncio.wait_for(self._chat_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            self.velocity_tracker.add(event)
            self.emote_tracker.add(event)
            self.unique_tracker.add(event)
            self.caps_tracker.add(event)
            self.repetition_tracker.add(event)
            self._recent_msgs.append(f"{event.author}: {event.content}")
            self._chat_batch.append({"a": event.author, "c": event.content})
            self._msg_count += 1

    async def _scoring_loop(self, api: TwitchAPIClient, broadcaster_id: str) -> None:
        tick = 0
        while not self._stop.is_set():
            await asyncio.sleep(2.0)
            tick += 1
            now = datetime.now(_PARIS)

            v_score, v_debug = self.velocity_tracker.score(now)
            e_score, e_cat, e_debug = self.emote_tracker.score(now)
            u_score, u_debug = self.unique_tracker.score(now)
            c_score, c_debug = self.caps_tracker.score(now)
            r_score, r_debug = self.repetition_tracker.score(now)

            # Score composite : monte à 100 quand les 2 gates sont franchies
            other_scores = [e_score, u_score, c_score, r_score]
            triggered_others = [s for s in other_scores if s > 0]
            if v_score > 0 and triggered_others:
                avg_others = sum(triggered_others) / len(triggered_others)
                composite = 0.50 * v_score + 0.50 * avg_others
            else:
                composite = 0.0

            # Snapshot pour le dashboard (toutes les 2s)
            if self.broadcaster:
                snap: dict = {
                    "t": now.strftime("%H:%M:%S"),
                    "msgs": self._msg_count,
                    "v_val":   round(v_debug.get("velocity", 0.0), 2),
                    "v_base":  round(v_debug.get("mean", 0.0), 2),
                    "v_score": round(v_score, 1),
                    "e_score": round(e_score, 1),
                    "u_score": round(u_score, 1),
                    "c_score": round(c_score, 1),
                    "r_score": round(r_score, 1),
                    "composite": round(composite, 1),
                    "chat_msgs": list(self._chat_batch),
                    "clip": None,
                }
                self._chat_batch.clear()
                await self.broadcaster.emit(snap)

            # Log toutes les 30s
            if tick % 15 == 0:
                v_vel  = v_debug.get("velocity", 0.0)
                v_mean = v_debug.get("mean", 0.0)
                v_z    = v_debug.get("z", 0.0)
                v_samples = v_debug.get("samples", 0)
                e_dens = e_debug.get("density", 0.0) if isinstance(e_debug, dict) else 0.0
                u_ratio = u_debug.get("ratio", 0.0)  if isinstance(u_debug, dict) else 0.0
                c_ratio = c_debug.get("caps_ratio", 0.0)
                r_ratio = r_debug.get("top_ratio", 0.0)
                triggered = (
                    (["VEL"] if v_score > 0 else []) +
                    (["EMO"] if e_score > 0 else []) +
                    (["UNI"] if u_score > 0 else []) +
                    (["CAP"] if c_score > 0 else []) +
                    (["REP"] if r_score > 0 else [])
                )
                warmup_note = f" [warmup {v_samples}/{self.velocity_tracker.warmup_samples}]" if v_score == 0 and v_samples < self.velocity_tracker.warmup_samples else ""
                logger.info(
                    f"[signals] msgs={self._msg_count} | "
                    f"VEL {v_vel:.2f}msg/s (μ={v_mean:.2f}, Z={v_z:.1f}) score={v_score:.0f}{warmup_note} | "
                    f"EMO {e_dens*100:.0f}% score={e_score:.0f} | "
                    f"UNI ×{u_ratio:.1f} score={u_score:.0f} | "
                    f"CAP {c_ratio*100:.0f}% score={c_score:.0f} | "
                    f"REP {r_ratio*100:.0f}% score={r_score:.0f} | "
                    f"COMP={composite:.0f}"
                    + (f"  ← [{'+'.join(triggered)}]" if triggered else "")
                )

            candidate = self.scorer.evaluate(
                now=now, channel=self.streamer.login,
                velocity_score=v_score, velocity_debug=v_debug,
                emote_score=e_score, emote_category=e_cat, emote_debug=e_debug,
                unique_score=u_score, unique_debug=u_debug,
                caps_score=c_score, caps_debug=c_debug,
                repetition_score=r_score, repetition_debug=r_debug,
                sample_messages=list(self._recent_msgs)[-10:],
            )

            if candidate is None:
                continue

            logger.info(f"[harvest] SPIKE — {candidate.reason}")

            if not self._clip_in_progress:
                self._clip_in_progress = True
                asyncio.create_task(self._clip_task(
                    api, broadcaster_id,
                    v_score, e_score, u_score, c_score, r_score, composite,
                ))

    async def _clip_task(
        self,
        api: TwitchAPIClient,
        broadcaster_id: str,
        v_score: float,
        e_score: float,
        u_score: float,
        c_score: float,
        r_score: float,
        composite: float,
    ) -> None:
        """Crée et récupère un clip en tâche de fond (non-bloquant pour le scorer)."""
        try:
            user_token = _bare_token(self.env.twitch_user_token or self.env.twitch_irc_token)
            clip_id = await api.create_clip(broadcaster_id, user_token)
            if clip_id is None:
                logger.warning("[harvest] échec création clip")
                return

            clip_data = await self._wait_for_clip(api, clip_id)
            if clip_data is None:
                logger.warning(f"[harvest] clip {clip_id} introuvable après {_CLIP_POLL_RETRIES} essais")
                return

            clip = TwitchClip(
                id=clip_data["id"],
                url=clip_data["url"],
                title=clip_data.get("title", clip_id),
                channel=self.streamer.login,
                creator_name=clip_data.get("creator_name", "auto"),
                view_count=clip_data.get("view_count", 0),
                duration=float(clip_data.get("duration", 0)),
                created_at=datetime.fromisoformat(clip_data["created_at"].replace("Z", "+00:00")),
                thumbnail_url=clip_data.get("thumbnail_url", ""),
                v_score=v_score,
                e_score=e_score,
                u_score=u_score,
                c_score=c_score,
                r_score=r_score,
                composite_score=composite,
            )
            self._collected.append(clip)
            await self.db.record_clip(
                session_id=self._session_id,
                twitch_id=clip.id,
                url=clip.url,
                title=clip.title,
                duration=clip.duration,
                created_at=clip.created_at,
                v_score=clip.v_score,
                e_score=clip.e_score,
                u_score=clip.u_score,
                c_score=clip.c_score,
                r_score=clip.r_score,
                composite_score=clip.composite_score,
            )
            logger.info(
                f"[harvest] clip #{len(self._collected)} : "
                f"{clip.title!r} ({clip.duration:.0f}s) → {clip.url}"
            )

            local_path = await self._download_clip_mp4(clip)
            if local_path is not None:
                clip.local_path = str(local_path)
                await self.db.update_clip_local_path(clip.id, clip.local_path)
                logger.info(f"[download] sauvegardé → {clip.local_path}")

            if self.broadcaster:
                await self.broadcaster.emit_clip(clip)
        finally:
            self._clip_in_progress = False

    async def _download_clip_mp4(self, clip: TwitchClip) -> Path | None:
        """Télécharge le MP4 du clip via streamlink. Retourne le Path ou None si échec."""
        ts = clip.created_at.strftime("%Y%m%d_%H%M%S")
        filename = f"{clip.channel}_{ts}_{clip.id}.mp4"
        output_path = self._raw_dir / filename
        # streamlink exige clips.twitch.tv/<id>, pas twitch.tv/clips/<id>
        stream_url = f"https://clips.twitch.tv/{clip.id}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "streamlink", stream_url, "best", "-o", str(output_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(
                    f"[download] streamlink rc={proc.returncode} : "
                    f"{stderr.decode(errors='replace')[:300]}"
                )
                return None
            if not output_path.exists() or output_path.stat().st_size == 0:
                logger.warning(f"[download] fichier vide ou absent : {output_path}")
                return None
            size_kb = output_path.stat().st_size // 1024
            logger.info(f"[download] {filename} ({size_kb} Ko)")
            return output_path
        except FileNotFoundError:
            logger.warning("[download] streamlink introuvable — vérifier l'installation système")
            return None
        except Exception as exc:
            logger.warning(f"[download] erreur inattendue : {exc!r}")
            return None

    async def _wait_for_clip(self, api: TwitchAPIClient, clip_id: str) -> dict | None:
        await asyncio.sleep(_CLIP_PROCESS_DELAY)
        for attempt in range(1, _CLIP_POLL_RETRIES + 1):
            data = await api.get_clip_by_id(clip_id)
            if data:
                return data
            logger.debug(f"[harvest] clip {clip_id} pas prêt (essai {attempt}/{_CLIP_POLL_RETRIES})")
            await asyncio.sleep(_CLIP_POLL_INTERVAL)
        return None

    async def _liveness_loop(self, api: TwitchAPIClient) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=float(_LIVENESS_CHECK_INTERVAL))
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                break
            stream = await api.get_stream(self.streamer.login)
            if stream is None:
                logger.info(f"[harvest] {self.streamer.login} est passé offline — arrêt")
                self.stop()


def _bare_token(token: str) -> str:
    return token.removeprefix("oauth:")
