"""Entrypoint CLI.

Usage:
    python -m src.main harvest --streamer <login>
    python -m src.main watch   --streamer <login>
"""
from __future__ import annotations

import asyncio
import signal
from datetime import datetime
from pathlib import Path

import typer

from .api.twitch import TwitchAPIClient
from .core.config import Env, load_settings, load_streamer
from .core.db import Database
from .core.errors import StreamerOfflineError
from .core.events import ClipCandidate, MomentCategory, TwitchClip
from .core.logging import logger, setup_logging
from .editor.processor import ClipProcessor
from .orchestrator.harvest import HarvestPipeline
from .orchestrator.pipeline import Pipeline

app = typer.Typer(add_completion=False, help="Twitch Viral Clipper - MVP Etage 1")


@app.command()
def auth(
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
):
    """Génère un token user (clips:edit + chat:read) via Device Flow et l'écrit dans .env."""
    setup_logging(level=log_level)
    env = Env()

    _PLACEHOLDERS = {"your_client_id", "your_client_secret", ""}
    if env.twitch_client_id in _PLACEHOLDERS or env.twitch_client_secret in _PLACEHOLDERS:
        logger.error("TWITCH_CLIENT_ID et TWITCH_CLIENT_SECRET doivent être remplis dans .env")
        raise typer.Exit(1)

    scopes = ["clips:edit", "chat:read"]
    logger.info(f"Démarrage du Device Flow (scopes: {scopes})")

    async def _run() -> str:
        token_data = await TwitchAPIClient.device_flow(env.twitch_client_id, scopes)
        return token_data["access_token"]

    try:
        access_token = asyncio.new_event_loop().run_until_complete(_run())
    except (RuntimeError, TimeoutError) as exc:
        logger.error(str(exc))
        raise typer.Exit(1)

    # Écrire le token dans .env
    env_path = Path(".env")
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        if "TWITCH_USER_TOKEN=" in content:
            lines = [
                f"TWITCH_USER_TOKEN={access_token}" if l.startswith("TWITCH_USER_TOKEN=") else l
                for l in content.splitlines()
            ]
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            with env_path.open("a", encoding="utf-8") as f:
                f.write(f"\nTWITCH_USER_TOKEN={access_token}\n")
    else:
        env_path.write_text(f"TWITCH_USER_TOKEN={access_token}\n", encoding="utf-8")

    logger.info(f"Token sauvegardé dans .env (TWITCH_USER_TOKEN)")
    logger.info("Tu peux maintenant lancer : python -m src.main harvest --streamer <login>")


@app.command()
def watch(
    streamer: str = typer.Option(..., "--streamer", "-s", help="Login Twitch (twitch.tv/<login>)"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
    config_path: Path = typer.Option(Path("config/settings.yaml"), "--config", "-c"),
):
    """Lance le pipeline de surveillance sur un streamer."""
    setup_logging(level=log_level)
    env = Env()  # charge .env
    settings = load_settings(config_path)
    streamer_cfg = load_streamer(streamer)

    # sanity checks
    missing = []
    if not env.twitch_irc_token:
        missing.append("TWITCH_IRC_TOKEN")
    if not env.twitch_irc_nick:
        missing.append("TWITCH_IRC_NICK")
    if missing:
        logger.error(f"Config manquante dans .env: {missing}")
        raise typer.Exit(1)

    pipeline = Pipeline(env, settings, streamer_cfg)

    loop = asyncio.new_event_loop()

    def _sig_handler():
        logger.info("signal recu, arret demande")
        pipeline.stop()

    try:
        loop.add_signal_handler(signal.SIGINT, _sig_handler)
        loop.add_signal_handler(signal.SIGTERM, _sig_handler)
    except NotImplementedError:
        # Windows: add_signal_handler n'est pas supporte sur ProactorEventLoop
        # -> on laisse Ctrl+C lever KeyboardInterrupt
        pass

    try:
        loop.run_until_complete(pipeline.run())
    except KeyboardInterrupt:
        pipeline.stop()
        loop.run_until_complete(asyncio.sleep(0.5))
    finally:
        loop.close()


@app.command()
def harvest(
    streamer: str = typer.Option(..., "--streamer", "-s", help="Login Twitch (twitch.tv/<login>)"),
    cooldown: int = typer.Option(120, "--cooldown", "-c", help="Cooldown entre deux clips (secondes)"),
    config_path: Path = typer.Option(Path("config/settings.yaml"), "--config"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
    dashboard: bool = typer.Option(False, "--dashboard", "-d", help="Ouvrir le dashboard web"),
    port: int = typer.Option(8000, "--port", help="Port du dashboard (défaut 8000)"),
):
    """Écoute un live et crée automatiquement des clips Twitch lors des spikes de chat."""
    setup_logging(level=log_level)
    env = Env()

    _PLACEHOLDERS = {"your_client_id", "your_client_secret", "your_bot_username", "", "xxx"}

    missing = []
    if env.twitch_client_id in _PLACEHOLDERS:
        missing.append("TWITCH_CLIENT_ID")
    if env.twitch_client_secret in _PLACEHOLDERS:
        missing.append("TWITCH_CLIENT_SECRET")
    if not env.twitch_irc_token or env.twitch_irc_token in _PLACEHOLDERS:
        missing.append("TWITCH_IRC_TOKEN (token OAuth pour le chat)")
    if not env.twitch_irc_nick or env.twitch_irc_nick in _PLACEHOLDERS:
        missing.append("TWITCH_IRC_NICK (login du compte bot)")
    if missing:
        logger.error(f"Credentials manquantes dans .env :\n  " + "\n  ".join(f"- {m}" for m in missing))
        raise typer.Exit(1)

    settings = load_settings(config_path)
    streamer_cfg = load_streamer(streamer)

    broadcaster = None
    if dashboard:
        try:
            import uvicorn
            from .api.dashboard import SignalBroadcaster, create_app
            broadcaster = SignalBroadcaster(channel=streamer)
        except ImportError:
            logger.error("fastapi et uvicorn requis : pip install fastapi uvicorn")
            raise typer.Exit(1)

    pipeline = HarvestPipeline(
        env, settings, streamer_cfg,
        cooldown_seconds=cooldown,
        broadcaster=broadcaster,
    )

    loop = asyncio.new_event_loop()

    def _sig_handler() -> None:
        logger.info("signal reçu, arrêt demandé")
        pipeline.stop()

    try:
        loop.add_signal_handler(signal.SIGINT, _sig_handler)
        loop.add_signal_handler(signal.SIGTERM, _sig_handler)
    except NotImplementedError:
        pass

    async def _run() -> list:
        if broadcaster is None:
            return await pipeline.run()
        dash_app = create_app(broadcaster, db_path=str(env.data_dir / "state.db"))
        server = uvicorn.Server(
            uvicorn.Config(dash_app, host="0.0.0.0", port=port, log_level="warning")
        )
        logger.info(f"Dashboard → http://localhost:{port}")
        harvest_task = asyncio.create_task(pipeline.run())
        server_task  = asyncio.create_task(server.serve())
        try:
            result = await harvest_task
        finally:
            server.should_exit = True
            await server_task
        return result

    try:
        clips = loop.run_until_complete(_run())
        if clips:
            logger.info(f"[harvest] session terminée — {len(clips)} clip(s) créé(s)")
            for c in clips:
                logger.info(f"  - {c.title!r} ({c.duration:.0f}s) → {c.url}")
        else:
            logger.warning("[harvest] session terminée sans clip détecté")
    except StreamerOfflineError as exc:
        logger.error(str(exc))
        raise typer.Exit(1)
    except KeyboardInterrupt:
        pipeline.stop()
        loop.run_until_complete(asyncio.sleep(0.3))
    finally:
        loop.close()


@app.command()
def process(
    session: int = typer.Option(..., "--session", "-s", help="ID de la session à traiter"),
    config_path: Path = typer.Option(Path("config/settings.yaml"), "--config"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
):
    """Post-traite les clips bruts d'une session : smart cut, 9:16, hook, sous-titres."""
    setup_logging(level=log_level)
    env = Env()
    settings = load_settings(config_path)

    async def _run() -> None:
        db = Database(env.data_dir / "state.db")
        await db.init()

        session_data = await db.get_session(session)
        if session_data is None:
            logger.error(f"[process] session #{session} introuvable en base")
            raise typer.Exit(1)

        clips = await db.get_unprocessed_clips(session)
        if not clips:
            logger.info(f"[process] aucun clip à traiter pour la session #{session}")
            return

        streamer_login = session_data["streamer"]
        streamer_cfg = load_streamer(streamer_login)
        output_dir = env.data_dir / "clips" / "processed" / streamer_login
        processor = ClipProcessor(output_dir=output_dir, settings=settings.editor)

        success = 0
        for i, clip in enumerate(clips, 1):
            logger.info(f"[process] clip {i}/{len(clips)} : {clip['title']!r}")
            try:
                category = MomentCategory(clip.get("category", "unknown"))
            except ValueError:
                category = MomentCategory.UNKNOWN

            result = await processor.process(
                Path(clip["local_path"]),
                streamer_cfg.webcam_zone,
                category=category,
                clip_duration_total=float(clip["duration"]),
            )
            if result is not None:
                await db.update_clip_processed_path(clip["twitch_id"], str(result))
                success += 1
                logger.info(f"[process] → {result.name}")

        logger.info(f"[process] terminé — {success}/{len(clips)} clips traités")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    except (SystemExit, typer.Exit):
        raise
    except KeyboardInterrupt:
        logger.info("[process] interrompu")
    finally:
        loop.close()


@app.command()
def edit(
    session: int | None = typer.Option(None, "--session", "-s", help="ID de la session à éditer"),
    clip: str | None = typer.Option(None, "--clip", "-c", help="Twitch clip ID à éditer"),
    last: bool = typer.Option(False, "--last", help="Utiliser la dernière session"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Liste les clips sans appeler l'API"),
    config_path: Path = typer.Option(Path("config/settings.yaml"), "--config"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
):
    """Analyse les clips d'une session avec DeepSeek et génère les Edit Plans."""
    setup_logging(level=log_level)
    env = Env()

    async def _run() -> None:
        db = Database(env.data_dir / "state.db")
        await db.init()

        if last:
            sess = await db.get_last_session()
            if sess is None:
                logger.error("[edit] aucune session en base")
                raise typer.Exit(1)
            clips = await db.get_clips_by_session(sess["id"])
        elif session is not None:
            sess = await db.get_session(session)
            if sess is None:
                logger.error(f"[edit] session #{session} introuvable")
                raise typer.Exit(1)
            clips = await db.get_clips_by_session(session)
        elif clip is not None:
            row = await db.get_clip_by_twitch_id(clip)
            if row is None:
                logger.error(f"[edit] clip {clip!r} introuvable")
                raise typer.Exit(1)
            clips = [row]
        else:
            logger.error("[edit] précise --session, --clip ou --last")
            raise typer.Exit(1)

        if not clips:
            logger.info("[edit] aucun clip à traiter")
            return

        if dry_run:
            logger.info(f"[edit] dry-run — {len(clips)} clip(s) trouvé(s), aucune analyse lancée")
            for c in clips:
                logger.info(f"  - {c['twitch_id']} | score={c['composite_score']:.1f}")
            return

        if not env.deepseek_api_key:
            logger.error("[edit] DEEPSEEK_API_KEY manquant dans .env")
            raise typer.Exit(1)

        from .editor.ai_analyzer import DeepSeekAnalyzer
        from .editor.video_editor import VideoEditor

        analyzer = DeepSeekAnalyzer(env.deepseek_api_key)
        editor = VideoEditor(output_dir=env.data_dir / "edited")

        for i, row in enumerate(clips, 1):
            logger.info(f"[edit] {i}/{len(clips)} — {row['twitch_id']}")
            twitch_clip = TwitchClip(
                id=row["twitch_id"],
                url=row["url"],
                title=row["title"],
                channel=row.get("channel", ""),
                creator_name="auto",
                view_count=0,
                duration=float(row["duration"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                thumbnail_url=row.get("thumbnail_url") or "",
            )
            candidate = ClipCandidate(
                timestamp=datetime.fromisoformat(row["created_at"]),
                channel=row.get("channel", ""),
                score=float(row["composite_score"]),
                category=MomentCategory(row.get("category") or "unknown"),
                reason="from DB",
                chat_velocity=float(row.get("v_score") or 0),
                emote_density=float(row.get("e_score") or 0) / 100.0,
                sample_messages=[],
            )

            plan = await analyzer.analyze(twitch_clip, candidate)

            edited_path = None
            if plan.worth_editing and row.get("local_path"):
                twitch_clip.local_path = row["local_path"]
                edited_path = await editor.render(twitch_clip, plan)

            if edited_path:
                await db.update_clip_edit_result(
                    row["id"],
                    plan.model_dump_json(),
                    category=plan.category,
                    processed_path=str(edited_path),
                )
                logger.info(f"[edit] ✓ render → {edited_path.name}")
            else:
                await db.update_clip_edit_result(row["id"], plan.model_dump_json())

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    except (SystemExit, typer.Exit):
        raise
    except KeyboardInterrupt:
        logger.info("[edit] interrompu")
    finally:
        loop.close()


if __name__ == "__main__":
    app()
