"""Exceptions métier du projet."""
from __future__ import annotations


class StreamerOfflineError(Exception):
    """Levée quand le streamer n'est pas en live au moment du lancement."""

    def __init__(self, login: str) -> None:
        super().__init__(f"Le streamer '{login}' n'est pas en live")
        self.login = login
