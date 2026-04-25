"""Client Twitch Helix API.

Authentification :
  - App Access Token (client_credentials) pour les lectures publiques
  - User Token (clips:edit) pour créer des clips
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import httpx

from ..core.errors import StreamerOfflineError
from ..core.logging import logger


class TwitchAPIClient:
    _BASE = "https://api.twitch.tv/helix"
    _TOKEN_URL = "https://id.twitch.tv/oauth2/token"

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str = ""
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "TwitchAPIClient":
        self._http = httpx.AsyncClient(timeout=15.0)
        await self._fetch_token()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http:
            await self._http.aclose()

    async def _fetch_token(self) -> None:
        resp = await self._http.post(  # type: ignore[union-attr]
            self._TOKEN_URL,
            params={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]

    def _headers(self) -> dict[str, str]:
        return {
            "Client-Id": self._client_id,
            "Authorization": f"Bearer {self._token}",
        }

    async def get_stream(self, login: str) -> dict[str, Any] | None:
        """Retourne les infos du stream live, ou None si le streamer est offline."""
        resp = await self._http.get(  # type: ignore[union-attr]
            f"{self._BASE}/streams",
            params={"user_login": login},
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        return data[0] if data else None

    async def require_live(self, login: str) -> dict[str, Any]:
        """Comme get_stream mais lève StreamerOfflineError si le streamer n'est pas live."""
        stream = await self.get_stream(login)
        if stream is None:
            raise StreamerOfflineError(login)
        return stream

    async def get_clips(
        self,
        broadcaster_id: str,
        started_at: datetime,
        first: int = 20,
    ) -> list[dict[str, Any]]:
        """Clips créés depuis `started_at` pour ce broadcaster, triés par date desc."""
        resp = await self._http.get(  # type: ignore[union-attr]
            f"{self._BASE}/clips",
            params={
                "broadcaster_id": broadcaster_id,
                "started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "first": first,
            },
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()["data"]

    @staticmethod
    async def device_flow(client_id: str, scopes: list[str]) -> dict:
        """OAuth Device Code Flow — retourne le token dict après autorisation utilisateur.

        Affiche le code à entrer sur https://www.twitch.tv/activate puis poll
        jusqu'à autorisation ou expiration.
        """
        async with httpx.AsyncClient(timeout=15.0) as http:
            # Étape 1 : demande de device code
            r = await http.post(
                "https://id.twitch.tv/oauth2/device",
                data={"client_id": client_id, "scopes": " ".join(scopes)},
            )
            r.raise_for_status()
            device = r.json()

            print(f"\n  → Ouvre cette URL : {device['verification_uri']}")
            print(f"  → Entre le code   : {device['user_code']}\n")

            interval = device.get("interval", 5)
            expires_in = device.get("expires_in", 1800)
            elapsed = 0

            while elapsed < expires_in:
                await asyncio.sleep(interval)
                elapsed += interval

                t = await http.post(
                    "https://id.twitch.tv/oauth2/token",
                    data={
                        "client_id": client_id,
                        "device_code": device["device_code"],
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
                if t.status_code == 200:
                    return t.json()
                body = t.json()
                if body.get("message") == "authorization_pending":
                    print("  En attente d'autorisation…", end="\r")
                    continue
                # Autre erreur (expired, denied…)
                raise RuntimeError(f"Device flow échoué : {body}")

        raise TimeoutError("Device flow expiré sans autorisation")

    async def create_clip(self, broadcaster_id: str, user_token: str) -> str | None:
        """Crée un clip Twitch sur le stream en cours. Retourne le clip_id ou None.

        Nécessite un user token avec scope clips:edit.
        Twitch retourne 202 Accepted — le clip est traité en asynchrone.
        """
        resp = await self._http.post(  # type: ignore[union-attr]
            f"{self._BASE}/clips",
            params={"broadcaster_id": broadcaster_id, "has_delay": "false"},
            headers={
                "Client-Id": self._client_id,
                "Authorization": f"Bearer {user_token}",
            },
        )
        if resp.status_code not in (200, 202):
            logger.error(f"[twitch] create_clip HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        data = resp.json().get("data", [])
        if not data:
            logger.error("[twitch] create_clip: réponse vide")
            return None
        clip_id: str = data[0]["id"]
        logger.debug(f"[twitch] clip créé (id={clip_id}), en attente de traitement Twitch…")
        return clip_id

    async def get_clip_by_id(self, clip_id: str) -> dict[str, Any] | None:
        """Récupère les métadonnées complètes d'un clip par son ID."""
        resp = await self._http.get(  # type: ignore[union-attr]
            f"{self._BASE}/clips",
            params={"id": clip_id},
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return data[0] if data else None
