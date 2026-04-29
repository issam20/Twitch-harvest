"""VideoEditor — pipeline : trim+crop 9:16 (ffmpeg) → transcription (Whisper) → rendu (Remotion)."""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from ..core.config import EditorConfig
from ..core.events import TwitchClip
from ..core.logging import logger
from .ai_analyzer import EditPlan
from .transcriber import Transcriber

# Répertoire racine du projet Remotion (deux niveaux au-dessus de ce fichier)
_REMOTION_DIR = Path(__file__).parent.parent.parent / "remotion"
_RENDER_SCRIPT = _REMOTION_DIR / "scripts" / "render.mjs"

HIGHLIGHT_COLOR = "#E8003C"


class VideoEditor:
    OUTPUT_SUFFIX = "_edited.mp4"

    def __init__(
        self,
        output_dir: Path | None = None,
        settings: EditorConfig | None = None,
    ) -> None:
        self.output_dir = output_dir
        self._whisper_model = settings.whisper_model if settings else "medium"

    async def render(
        self,
        clip: TwitchClip,
        plan: EditPlan,
        transcript_words: list[dict] | None = None,
    ) -> Path | None:
        """Pipeline complet de montage. Retourne le Path du fichier édité ou None si échec."""
        if not clip.local_path:
            logger.warning(f"[editor] clip {clip.id!r} — pas de local_path, skip")
            return None

        input_path = Path(clip.local_path)
        if not input_path.exists():
            logger.warning(f"[editor] fichier introuvable : {input_path}")
            return None

        base_dir = self.output_dir or input_path.parent
        base_dir.mkdir(parents=True, exist_ok=True)
        output_path = self._unique_output(base_dir, input_path.stem)

        tmp_dir = Path(tempfile.mkdtemp(prefix="harvest_render_"))
        try:
            ok = await self._render_pipeline(
                input_path, output_path, plan, transcript_words, tmp_dir
            )
        except asyncio.CancelledError:
            logger.info(f"[editor] render annulé pour {clip.id!r}")
            raise
        except Exception as exc:
            logger.warning(f"[editor] erreur inattendue : {exc!r}")
            return None
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if ok:
            logger.info(f"[editor] ✓ rendu terminé → {output_path.name}")
            return output_path
        logger.warning(f"[editor] render échoué pour {clip.id!r}")
        return None

    # ------------------------------------------------------------------
    # Pipeline interne
    # ------------------------------------------------------------------

    async def _render_pipeline(
        self,
        input_path: Path,
        output_path: Path,
        plan: EditPlan,
        transcript_words: list[dict] | None,
        tmp_dir: Path,
    ) -> bool:
        # --- Step 1: ffmpeg trim + crop 9:16 ---
        video_info = await self._ffprobe(input_path)
        if not video_info:
            return False

        step1 = tmp_dir / "step1.mp4"
        if not await self._step_trim_crop(input_path, step1, plan, video_info):
            return False

        # Dimensions réelles du fichier croppé (source de vérité pour Remotion)
        cropped_info = await self._ffprobe(step1)
        if not cropped_info:
            return False

        w = cropped_info["width"]
        h = cropped_info["height"]
        fps = round(cropped_info["fps"])           # Remotion exige un entier
        duration = cropped_info["duration"]
        duration_in_frames = max(1, round(duration * fps))

        logger.info(
            f"[ffmpeg] crop: {video_info['width']}x{video_info['height']} → {w}x{h} "
            f"| {duration:.2f}s @ {fps}fps ({duration_in_frames} frames)"
        )

        # --- Step 2: transcription Whisper ---
        if transcript_words is not None:
            words = transcript_words
            logger.info(f"[whisper] {len(words)} mots (fournis externalement)")
        else:
            try:
                words = await Transcriber(self._whisper_model).transcribe(step1)
            except RuntimeError as exc:
                logger.warning(f"[whisper] {exc} — fallback segments")
                words = []

        if not words:
            words = self._fallback_words(plan, duration)
            logger.info(f"[whisper] {len(words)} mots synthétiques (fallback)")

        # --- Step 3: rendu Remotion ---
        # output_path peut être relatif si data_dir l'est (Path("./data")) ;
        # Node.js path.resolve() le résoudrait depuis cwd=remotion/ → mauvais endroit.
        config = {
            "publicDir": str(tmp_dir),
            "outputPath": str(output_path.resolve()),
            "inputProps": {
                "videoSrc": "step1.mp4",
                "title": plan.title,
                "colorGrade": plan.color_grade,
                "addZoom": plan.add_zoom,
                "words": words,
                "highlightColor": HIGHLIGHT_COLOR,
                "durationInFrames": duration_in_frames,
                "fps": fps,
                "width": w,
                "height": h,
            },
        }
        config_path = tmp_dir / "render_config.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

        return await self._step_remotion(config_path)

    async def _step_trim_crop(
        self,
        input_path: Path,
        output_path: Path,
        plan: EditPlan,
        video_info: dict,
    ) -> bool:
        w = video_info["width"]
        h = video_info["height"]
        trim_start = plan.trim_start
        trim_end = (
            plan.trim_end
            if plan.trim_end > plan.trim_start
            else video_info.get("duration", 30.0)
        )
        duration = trim_end - trim_start

        filters: list[str] = []
        source_ratio = w / h if h else 1.0
        if abs(source_ratio - 9 / 16) > 0.05:
            target_w = h * 9 // 16
            crop_x = (w - target_w) // 2
            filters.append(f"crop={target_w}:{h}:{crop_x}:0")

        base_args = [
            "ffmpeg", "-y",
            "-ss", str(trim_start),
            "-i", str(input_path),
            "-t", str(duration),
        ]
        encode_args = [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            str(output_path),
        ]
        args = base_args + (["-vf", ",".join(filters)] if filters else []) + encode_args
        return await self._run_ffmpeg(args, "trim+crop")

    async def _step_remotion(self, config_path: Path) -> bool:
        """Lance le rendu Remotion via Node.js."""
        node = shutil.which("node")
        if not node:
            raise RuntimeError(
                "node introuvable dans le PATH — installer Node.js : https://nodejs.org"
            )
        if not _REMOTION_DIR.exists():
            raise RuntimeError(
                f"Projet Remotion introuvable : {_REMOTION_DIR}\n"
                "Lancer depuis la racine du projet : cd remotion && npm install"
            )
        if not (_REMOTION_DIR / "node_modules").exists():
            raise RuntimeError(
                f"node_modules absent dans {_REMOTION_DIR}\n"
                "Lancer : cd remotion && npm install"
            )

        args = [node, str(_RENDER_SCRIPT), str(config_path)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=_REMOTION_DIR,
            )
            stdout, stderr = await proc.communicate()
            for line in stdout.decode(errors="replace").splitlines():
                if line.strip():
                    logger.info(line)
            if proc.returncode != 0:
                logger.warning(
                    f"[remotion] rc={proc.returncode}\n"
                    f"{stderr.decode(errors='replace')[-1200:]}"
                )
                return False
            return True
        except Exception as exc:
            logger.warning(f"[remotion] erreur subprocess : {exc!r}")
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fallback_words(self, plan: EditPlan, duration: float) -> list[dict]:
        """Génère des mots synthétiques depuis plan.caption / plan.title."""
        text = plan.caption if plan.caption else plan.title
        raw_words = text.upper().split()
        if not raw_words:
            return [{"word": "...", "start": 0.0, "end": duration}]
        time_per_word = duration / len(raw_words)
        return [
            {"word": w, "start": round(i * time_per_word, 3), "end": round((i + 1) * time_per_word, 3)}
            for i, w in enumerate(raw_words)
        ]

    def _unique_output(self, base_dir: Path, stem: str) -> Path:
        output = base_dir / (stem + self.OUTPUT_SUFFIX)
        i = 1
        while output.exists():
            output = base_dir / f"{stem}{self.OUTPUT_SUFFIX[:-4]}_{i}.mp4"
            i += 1
        return output

    async def _ffprobe(self, path: Path) -> dict:
        """Retourne width, height, duration, fps via ffprobe JSON."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams", "-show_format",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(f"[editor] ffprobe rc={proc.returncode} pour {path.name}")
                return {}
            data = json.loads(stdout)
            video = next(
                (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
                {},
            )
            fmt = data.get("format", {})
            fps_str = video.get("r_frame_rate", "30/1")
            num, den = fps_str.split("/") if "/" in fps_str else (fps_str, "1")
            fps = float(num) / float(den) if float(den) else 30.0
            return {
                "width": int(video.get("width", 1280)),
                "height": int(video.get("height", 720)),
                "duration": float(fmt.get("duration", 0)),
                "fps": fps,
            }
        except Exception as exc:
            logger.warning(f"[editor] ffprobe erreur : {exc!r}")
            return {}

    async def _run_ffmpeg(self, args: list[str], step_name: str) -> bool:
        if not shutil.which("ffmpeg"):
            raise RuntimeError(
                "ffmpeg introuvable dans le PATH — "
                "installer : https://ffmpeg.org/download.html"
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(
                    f"[ffmpeg] [{step_name}] rc={proc.returncode}\n"
                    f"{stderr.decode(errors='replace')[-600:]}"
                )
                return False
            return True
        except Exception as exc:
            logger.warning(f"[ffmpeg] [{step_name}] erreur : {exc!r}")
            return False
