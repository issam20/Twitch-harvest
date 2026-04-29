"""Génère CONTEXT.md à la racine du repo avec tous les fichiers source."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows : la console cp1252 ne sait pas encoder ✓ et les emojis
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Racine du repo
if "GITHUB_WORKSPACE" in os.environ:
    ROOT = Path(os.environ["GITHUB_WORKSPACE"])
else:
    ROOT = Path(__file__).parent.parent.parent

# Fichiers à inclure — (chemin relatif à ROOT, limite de lignes ou None)
FILES: list[tuple[str, int | None]] = [
    ("src/core/config.py",                    None),
    ("src/core/db.py",                        None),
    ("src/core/events.py",                    None),
    ("src/detector/scorer.py",                None),
    ("src/detector/chat_velocity.py",         None),
    ("src/detector/chat_signals.py",          None),
    ("src/detector/emote_spam.py",            None),
    ("src/orchestrator/harvest.py",           300),
    ("src/editor/ai_analyzer.py",             None),
    ("src/editor/video_editor.py",            None),
    ("src/editor/ffmpeg_preprocessor.py",     None),
    ("src/editor/whisper_transcriber.py",     None),
    ("remotion/src/types.ts",                 None),
    ("remotion/src/TwitchClip/index.tsx",     None),
    ("remotion/src/TwitchClip/CaptionOverlay.tsx", None),
    ("remotion/src/TwitchClip/TitleOverlay.tsx",   None),
    ("remotion/src/TwitchClip/styles.ts",    None),
    ("remotion/render.mjs",                   None),
    ("config/settings.yaml",                  None),
    ("pyproject.toml",                        None),
    ("src/main.py",                           300),
]

# Extension → langage pour les blocs de code markdown
EXT_LANG: dict[str, str] = {
    ".py":   "python",
    ".ts":   "typescript",
    ".tsx":  "tsx",
    ".mjs":  "javascript",
    ".yaml": "yaml",
    ".yml":  "yaml",
    ".toml": "toml",
    ".sql":  "sql",
    ".json": "json",
    ".md":   "markdown",
    ".sh":   "bash",
}

# Tableau d'état des modules du projet
MODULES: list[tuple[str, str, str]] = [
    ("core/config.py",              "Config Pydantic — Env + Settings + StreamerConfig",          "✅"),
    ("core/db.py",                  "SQLite async — sessions + clips + scores",                   "✅"),
    ("core/events.py",              "Dataclasses partagées — TwitchClip, ClipCandidate, etc.",    "✅"),
    ("detector/scorer.py",          "ViralScorer — fusion composite des signaux",                 "✅"),
    ("detector/chat_velocity.py",   "Velocity Z-score adaptatif",                                 "✅"),
    ("detector/chat_signals.py",    "Pipeline de signaux IRC",                                    "✅"),
    ("detector/emote_spam.py",      "Détection spam d'emotes funny/hype/shock",                  "✅"),
    ("orchestrator/harvest.py",     "HarvestPipeline — orchestration clip + edit",               "✅"),
    ("editor/ai_analyzer.py",       "DeepSeek V4 Flash → EditPlan JSON structuré",               "✅"),
    ("editor/video_editor.py",      "Pipeline render : ffmpeg + Whisper + Remotion",             "✅"),
    ("editor/ffmpeg_preprocessor.py", "FFmpeg trim + crop 9:16 faststart",                       "🔧"),
    ("editor/whisper_transcriber.py", "Transcription mot/mot via faster-whisper",                "🔧"),
    ("remotion/TwitchClip/",        "Composition React — captions + titre + grade + zoom",       "✅"),
]


def lang_for(path: str) -> str:
    return EXT_LANG.get(Path(path).suffix.lower(), "text")


def extract_db_schema() -> str:
    """Extrait le bloc SCHEMA SQL depuis src/core/db.py."""
    db_path = ROOT / "src" / "core" / "db.py"
    if not db_path.exists():
        return "-- src/core/db.py introuvable"
    content = db_path.read_text(encoding="utf-8")
    for quote in ('"""', "'''"):
        marker = f"SCHEMA = {quote}"
        start = content.find(marker)
        if start != -1:
            start = content.find(quote, start) + 3
            end = content.find(quote, start)
            if end != -1:
                return content[start:end].strip()
    return "-- Schéma SCHEMA non trouvé dans db.py"


def main() -> None:
    now = datetime.now(timezone.utc)
    out: list[str] = []
    missing: list[str] = []
    included = 0

    # ── En-tête ──────────────────────────────────────────────────────────────
    out += [
        "# CONTEXT.md — Twitch Harvest",
        "",
        f"> Généré automatiquement le **{now.strftime('%Y-%m-%d %H:%M:%S UTC')}**",
        "> Ne pas éditer manuellement — mis à jour automatiquement à chaque push sur `main`.",
        "",
    ]

    # ── Tableau d'état ────────────────────────────────────────────────────────
    out += [
        "## État du projet",
        "",
        "| Module | Description | Statut |",
        "|--------|-------------|--------|",
    ]
    for module, desc, status in MODULES:
        out.append(f"| `src/{module}` | {desc} | {status} |")
    out.append("")

    # ── Schéma DB ─────────────────────────────────────────────────────────────
    out += [
        "## Schéma base de données",
        "",
        "```sql",
        extract_db_schema(),
        "```",
        "",
    ]

    # ── Fichiers source ───────────────────────────────────────────────────────
    out.append("## Fichiers source")
    out.append("")

    for rel_path, max_lines in FILES:
        full_path = ROOT / rel_path
        if not full_path.exists():
            missing.append(rel_path)
            continue

        raw = full_path.read_text(encoding="utf-8", errors="replace")
        file_lines = raw.splitlines()
        total = len(file_lines)
        truncated = max_lines is not None and total > max_lines
        if truncated:
            file_lines = file_lines[:max_lines]

        out.append(f"### `{rel_path}`")
        if truncated:
            out.append(f"_(tronqué à {max_lines} lignes sur {total})_")
        out.append("")
        out.append(f"```{lang_for(rel_path)}")
        out.extend(file_lines)
        out.append("```")
        out.append("")
        included += 1

    # ── Fichiers non trouvés ──────────────────────────────────────────────────
    if missing:
        out += ["## Fichiers non trouvés", ""]
        for m in missing:
            out.append(f"- `{m}`")
        out.append("")

    # ── Footer ────────────────────────────────────────────────────────────────
    out += [
        "---",
        f"_{included} fichier(s) inclus — {len(missing)} non trouvé(s)_",
    ]

    output_path = ROOT / "CONTEXT.md"
    output_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"✓ CONTEXT.md généré ({included} fichiers inclus, {len(missing)} manquants)")
    if missing:
        print("Fichiers manquants :")
        for m in missing:
            print(f"  - {m}")


if __name__ == "__main__":
    main()
