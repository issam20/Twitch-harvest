"""Gestion de la police Impact (ou Anton comme fallback) pour les sous-titres."""
from __future__ import annotations

from pathlib import Path

from ..core.logging import logger


class FontManager:
    CANDIDATES = [
        "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
        "/usr/share/fonts/truetype/impact.ttf",
        "/System/Library/Fonts/Impact.ttf",
        "C:/Windows/Fonts/impact.ttf",
    ]

    _ANTON_URL = "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf"
    _CACHE_DIR = Path.home() / ".cache" / "twitch_harvest" / "fonts"
    _ANTON_PATH = _CACHE_DIR / "Anton-Regular.ttf"

    @classmethod
    def get_impact_path(cls) -> str:
        """Retourne le chemin vers Impact ou Anton.

        Si aucune police trouvée localement, télécharge Anton depuis
        GitHub dans ~/.cache/twitch_harvest/fonts/ et retourne ce chemin.
        """
        for candidate in cls.CANDIDATES:
            p = Path(candidate)
            if p.exists():
                logger.debug(f"[fonts] Impact trouvé : {p}")
                return str(p)

        if cls._ANTON_PATH.exists():
            logger.debug(f"[fonts] Anton (cache) : {cls._ANTON_PATH}")
            return str(cls._ANTON_PATH)

        logger.info("[fonts] Impact introuvable — téléchargement de Anton depuis GitHub")
        cls._CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            import httpx
            resp = httpx.get(cls._ANTON_URL, follow_redirects=True, timeout=30)
            resp.raise_for_status()
            cls._ANTON_PATH.write_bytes(resp.content)
            logger.info(f"[fonts] Anton téléchargé → {cls._ANTON_PATH}")
            return str(cls._ANTON_PATH)
        except Exception as exc:
            raise RuntimeError(f"Impossible de télécharger Anton : {exc}") from exc

    @classmethod
    def get_font_name(cls) -> str:
        """Retourne 'Impact' ou 'Anton' selon ce qui est disponible."""
        for candidate in cls.CANDIDATES:
            if Path(candidate).exists():
                return "Impact"
        return "Anton"
