"""Analyse un clip Twitch via DeepSeek et produit un EditPlan structuré."""
from __future__ import annotations

import json

from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator

from ..core.events import ClipCandidate, TwitchClip
from ..core.logging import logger

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment,misc]


class EditPlan(BaseModel):
    worth_editing: bool
    confidence: float
    category: Literal["funny", "hype", "shock", "unknown"]
    trim_start: float
    trim_end: float
    highlight_moment: float
    title: str
    caption: str
    hashtags: list[str]
    caption_style: Literal["impact", "subtitles", "none"]
    caption_position: Literal["top", "bottom", "dynamic"]
    color_grade: Literal["viral", "cinematic", "raw"]
    add_zoom: bool

    @field_validator("title")
    @classmethod
    def title_must_be_short(cls, v: str) -> str:
        words = v.split()
        if len(words) > 10:
            return " ".join(words[:8])
        return v


_SYSTEM_PROMPT = """\
Tu es un expert en contenu viral TikTok/YouTube Shorts spécialisé dans les clips \
Twitch gaming. Tu connais parfaitement le ton de la gen Z francophone et anglophone \
sur TikTok et X (Twitter).

Tu reçois les métadonnées d'un moment viral détecté sur un stream Twitch.
Tu dois produire UNIQUEMENT un objet JSON valide selon le schéma fourni.

=== RÈGLES DE TITRE (CRITIQUE) ===

Le titre est un TEXT OVERLAY de 3-8 mots MAX affiché sur la vidéo.
Il doit stopper le scroll en < 1.7 seconde.

PATTERNS AUTORISÉS (choisis-en un) :
- Skull reaction : "frère a dit QUOI 💀" / "nah 💀" / "bro 😭"
- Cliffhanger : "il s'attendait pas à ça..." / "personne a vu venir..."
- Chat comme perso : "le chat a pété un câble" / "chat went insane"
- Hyperbole brute : "CLIP DE L'ANNÉE" / "GAME OVER 😭"
- Réaction minimale : "nan mais 💀" / "c'est fini frère"
- POV : "POV: t'es dans le chat quand ça arrive"

INTERDIT :
- Titres descriptifs ("Le fou rire incontrôlable")
- Titres qui EXPLIQUENT le clip (ça tue la curiosité)
- Phrases complètes avec sujet-verbe-complément
- Ton formel / journalistique / blog
- Plus de 8 mots
- Le mot "incontrôlable", "incroyable", "hilarant", "épique"

EXEMPLES CORRECTS :
  funny + chat spam → "le chat était en PLS 💀"
  hype + velocity spike → "TOUT LE MONDE A PERDU LA TÊTE"
  shock + caps → "nan mais c'est quoi ça 😭"
  funny + copypasta → "ils spamment tous la même chose mdr"
  hype + unique chatters → "même les lurkers sont sortis"

=== RÈGLES DE CAPTION ===
La caption est le sous-titre court affiché en overlay.
- 10 mots MAX
- Reprend la phrase/réaction clé du moment
- Peut être un message chat viral du moment
- Style : parler comme quelqu'un qui envoie un message à son pote

=== RÈGLES DE HASHTAGS ===
- 8 à 12 hashtags
- Structure : 5-8 niche (#twitchfr #kekw #[nomdustreamer] #[jeu]) \
+ 2-4 génériques (#fyp #viral #gaming)
- JAMAIS de hashtags morts (#france #funny #mdr seuls)

=== FORMAT ===
Aucun texte avant le JSON. Aucun texte après. Pas de ```json.
Le JSON doit être parseable par json.loads().\
"""

_USER_TEMPLATE = """\
Clip Twitch détecté — produis le EditPlan JSON.

SCHÉMA ATTENDU:
{edit_plan_schema}

EXEMPLES DE BONS EDIT PLANS :

Input: score=82, category=funny, reason="velocity+copypasta",
       chat_sample=["KEKW KEKW KEKW", "DEAD 💀", "OMEGALUL"]
Output: {{"title": "le chat en PLS 💀", "caption": "quand tout le monde spam KEKW en même temps", ...}}

Input: score=91, category=hype, reason="velocity+unique_chatters+caps",
       chat_sample=["LETS GOOO", "NO WAY", "POGGERS POGGERS"]
Output: {{"title": "MÊME LES LURKERS SONT SORTIS", "caption": "le chat x10 en 3 secondes", ...}}

Input: score=75, category=shock, reason="velocity+caps",
       chat_sample=["WTF", "NOOOO", "monkaS", "il a pas fait ça"]
Output: {{"title": "il a PAS fait ça 😭", "caption": "tout le monde a freeze", ...}}

Input: score=88, category=funny, reason="velocity+emote+repetition",
       chat_sample=["ICANT", "JE SUIS MORT", "AHAHAHAH", "ICANT ICANT"]
Output: {{"title": "c'est fini frère 💀", "caption": "0 survivant dans le chat", ...}}

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

    # Reasoning models (deepseek-reasoner, o-series) do NOT support temperature
    _REASONING_MODELS = frozenset({"deepseek-reasoner", "deepseek-v4-flash"})

    def _is_reasoning_model(self) -> bool:
        """Return True if the current model is a reasoning model that doesn't support temperature."""
        return self._model in self._REASONING_MODELS

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
                kwargs: dict = {
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 800,
                }
                # Reasoning models (deepseek-reasoner, o1, o3, etc.) don't support temperature
                if not self._is_reasoning_model():
                    kwargs["temperature"] = 0.3

                response = await self._client.chat.completions.create(**kwargs)
                choice = response.choices[0]
                raw = choice.message.content or ""
                finish = choice.finish_reason
                logger.debug(f"[analyzer] finish_reason={finish!r} raw={raw[:200]!r}")
                # Extrait le premier bloc JSON valide meme si le modele ajoute du texte
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start == -1 or end == 0:
                    raise json.JSONDecodeError("aucun JSON trouve", raw, 0)
                plan = EditPlan.model_validate(json.loads(raw[start:end]))
                if plan.worth_editing:
                    logger.info(
                        f"[analyzer] clip {clip.id} -> worth_editing=True | "
                        f"title={plan.title!r} | confidence={plan.confidence:.2f}"
                    )
                else:
                    logger.info(f"[analyzer] clip {clip.id} -> worth_editing=False (score trop bas)")
                return plan
            except (json.JSONDecodeError, ValidationError) as exc:
                logger.warning(f"[analyzer] tentative {attempt + 1}/3 echouee : {exc!r}")
            except Exception as exc:
                logger.warning(f"[analyzer] erreur API tentative {attempt + 1}/3 : {exc!r}")

        logger.warning(
            f"[analyzer] clip {clip.id} -> worth_editing=False (parse error apres 3 essais)"
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
