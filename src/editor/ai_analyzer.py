"""Analyse un clip Twitch via DeepSeek et produit un EditPlan structuré."""
from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from ..core.events import ClipCandidate, TwitchClip
from ..core.logging import logger

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment,misc]


class EditPlan(BaseModel):
    worth_editing: bool
    confidence: float
    category: str
    trim_start: float
    trim_end: float
    highlight_moment: float
    title: str
    caption: str
    hashtags: list[str]
    caption_style: str
    caption_position: str
    color_grade: str
    add_zoom: bool


_SYSTEM_PROMPT = """\
Tu es un expert monteur vidéo TikTok/YouTube Shorts spécialisé dans les clips \
Twitch viraux francophones et anglophones.
Tu reçois les métadonnées d'un moment détecté comme viral sur un stream Twitch.
Tu dois produire UNIQUEMENT un objet JSON valide correspondant exactement au \
schéma EditPlan fourni dans le prompt utilisateur.
Règles absolues :
- Aucun texte avant le JSON
- Aucun texte après le JSON
- Aucun bloc markdown (pas de ```json)
- Aucune explication
- Le JSON doit être parseable directement par json.loads()\
"""

_USER_TEMPLATE = """\
Clip Twitch détecté — produis le EditPlan JSON.

SCHÉMA ATTENDU:
{edit_plan_schema}

DONNÉES DU MOMENT:
SIGNAL: {reason}
SCORE: {score}/100
CATÉGORIE: {category}
DURÉE CLIP: {duration}s
LANGUE STREAMER: {language}

MESSAGES CHAT (moment du pic):
{sample_messages}

TRANSCRIPT:
{transcript}\
"""


class DeepSeekAnalyzer:
    def __init__(self, api_key: str, model: str = "deepseek-v4-flash") -> None:
        if AsyncOpenAI is None:
            raise ImportError("openai>=1.0 requis : pip install openai")
        self._client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        self._model = model
        self._schema = json.dumps(EditPlan.model_json_schema(), indent=2)

    async def analyze(
        self,
        clip: TwitchClip,
        candidate: ClipCandidate,
        transcript: str | None = None,
    ) -> EditPlan:
        user_prompt = _USER_TEMPLATE.format(
            edit_plan_schema=self._schema,
            reason=candidate.reason,
            score=round(candidate.score, 1),
            category=candidate.category.value,
            duration=clip.duration,
            language="fr",
            sample_messages="\n".join(candidate.sample_messages) or "Aucun message disponible",
            transcript=transcript or "Non disponible",
        )

        for attempt in range(3):
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,
                    max_tokens=800,
                )
                raw = response.choices[0].message.content or ""
                plan = EditPlan.model_validate(json.loads(raw))
                if plan.worth_editing:
                    logger.info(
                        f"[analyzer] clip {clip.id} → worth_editing=True | "
                        f"title={plan.title!r} | confidence={plan.confidence:.2f}"
                    )
                else:
                    logger.info(f"[analyzer] clip {clip.id} → worth_editing=False (score trop bas)")
                return plan
            except (json.JSONDecodeError, ValidationError) as exc:
                logger.warning(f"[analyzer] tentative {attempt + 1}/3 échouée : {exc!r}")

        logger.warning(
            f"[analyzer] clip {clip.id} → worth_editing=False (parse error après 3 essais)"
        )
        return EditPlan(
            worth_editing=False,
            confidence=0.0,
            category="unknown",
            trim_start=0.0,
            trim_end=clip.duration,
            highlight_moment=clip.duration / 2,
            title="",
            caption="",
            hashtags=[],
            caption_style="none",
            caption_position="bottom",
            color_grade="raw",
            add_zoom=False,
        )
