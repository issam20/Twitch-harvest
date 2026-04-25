"""Entrypoint CLI.

Usage:
    python -m src.main harvest --streamer <login>
    python -m src.main watch   --streamer <login>
"""
from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import typer

from .api.twitch import TwitchAPIClient
from .core.config import Env, load_settings, load_streamer
from .core.errors import StreamerOfflineError
from .core.logging import logger, setup_logging
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


if __name__ == "__main__":
    app()
