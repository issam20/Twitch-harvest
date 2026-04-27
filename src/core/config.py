"""Chargement de la configuration: env + YAML global + YAML par streamer."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------- ENV (secrets) ----------

class Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    twitch_client_id: str = ""
    twitch_client_secret: str = ""
    twitch_irc_token: str = ""         # format "oauth:xxxx" — pour IRC chat
    twitch_irc_nick: str = ""
    twitch_user_token: str = ""        # token avec clips:edit (sans prefix oauth:)

    anthropic_api_key: str = ""
    deepseek_api_key: str = ""

    data_dir: Path = Path("./data")


# ---------- YAML global ----------

class DetectorConfig(BaseModel):
    chat_window_seconds: int = 10
    baseline_window_seconds: int = 60       # utilisé par unique chatters tracker
    emote_density_threshold: float = 0.35
    min_viral_score: float = 60.0
    cooldown_seconds: int = 60
    emotes: dict[str, list[str]] = Field(default_factory=dict)

    # Velocity Z-score (remplace chat_velocity_threshold + velocity_multiplier)
    stats_window_seconds: int = 300         # historique pour μ/σ (5 min)
    z_score_threshold: float = 2.5          # σ au-dessus de μ pour déclencher
    velocity_floor: float = 0.3             # msg/s minimum absolu (anti-bruit)
    warmup_samples: int = 30               # ticks avant activation (~60 s)



class ClipperConfig(BaseModel):
    buffer_seconds: int = 120
    segment_duration: int = 10
    clip_pre_seconds: int = 15
    clip_post_seconds: int = 15
    stream_quality: str = "720p60,720p,best"


class OrchestratorConfig(BaseModel):
    log_level: str = "INFO"


class HookTemplates(BaseModel):
    funny: str = "WAIT FOR IT 😭"
    hype: str = "WATCH THIS 🔥"
    shock: str = "NO WAY 💀"
    unknown: str = "CHAT WENT CRAZY"


class EditorConfig(BaseModel):
    whisper_model: str = "medium"
    subtitle_fontsize: int = 75
    subtitle_color: str = "&H00FFFF"
    subtitle_outline: int = 3
    gameplay_ratio: float = 0.55
    clip_duration: int = 15
    pre_peak_seconds: int = 3
    peak_offset_seconds: int = -3
    hook_duration: float = 2.0
    hook_fontsize: int = 90
    hook_templates: HookTemplates = HookTemplates()


class Settings(BaseModel):
    detector: DetectorConfig = DetectorConfig()
    clipper: ClipperConfig = ClipperConfig()
    orchestrator: OrchestratorConfig = OrchestratorConfig()
    editor: EditorConfig = EditorConfig()


# ---------- YAML par streamer ----------

class WebcamZone(BaseModel):
    x_pct: float = 0.75
    y_pct: float = 0.70
    w_pct: float = 0.24
    h_pct: float = 0.28


class StreamerConfig(BaseModel):
    login: str
    display_name: str = ""
    language: str = "en"
    webcam_zone: WebcamZone = WebcamZone()
    detector_overrides: dict[str, Any] = Field(default_factory=dict)
    default_tags: list[str] = Field(default_factory=list)


# ---------- loaders ----------

def load_settings(path: Path = Path("config/settings.yaml")) -> Settings:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Settings(**data)


def load_streamer(login: str, base: Path = Path("config/streamers")) -> StreamerConfig:
    path = base / f"{login}.yaml"
    if not path.exists():
        # Config par defaut si absente
        return StreamerConfig(login=login, display_name=login)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return StreamerConfig(**data)


def apply_streamer_overrides(settings: Settings, streamer: StreamerConfig) -> Settings:
    """Retourne un nouveau Settings avec les overrides du streamer appliques."""
    if not streamer.detector_overrides:
        return settings
    detector_dict = settings.detector.model_dump()
    detector_dict.update(streamer.detector_overrides)
    return settings.model_copy(update={"detector": DetectorConfig(**detector_dict)})
