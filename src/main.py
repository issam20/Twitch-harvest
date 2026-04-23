"""Entrypoint CLI.

Usage:
    python -m src.main watch --streamer <login>
"""
from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import typer

from .core.config import Env, load_settings, load_streamer
from .core.logging import logger, setup_logging
from .orchestrator.pipeline import Pipeline

app = typer.Typer(add_completion=False, help="Twitch Viral Clipper - MVP Etage 1")


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


if __name__ == "__main__":
    app()
