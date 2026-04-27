"""VideoEditor — pipeline ffmpeg : trim, color grade, sous-titres ASS TikTok."""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from ..core.events import TwitchClip
from ..core.logging import logger
from .ai_analyzer import EditPlan
from .caption_renderer import CaptionRenderer
from .font_manager import FontManager


class VideoEditor:
    OUTPUT_SUFFIX = "_edited.mp4"

    def __init__(self, output_dir: Path | None = None) -> None:
        self.output_dir = output_dir

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

        output_path = base_dir / (input_path.stem + self.OUTPUT_SUFFIX)
        suffix = 1
        while output_path.exists():
            output_path = base_dir / f"{input_path.stem}{self.OUTPUT_SUFFIX[:-4]}_{suffix}.mp4"
            suffix += 1

        tmp_dir = Path(tempfile.mkdtemp(prefix="harvest_render_"))
        try:
            ok = await self._render_pipeline(input_path, output_path, plan, transcript_words, tmp_dir)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if ok:
            logger.info(f"[editor] ✓ render terminé → {output_path.name}")
            return output_path
        logger.warning(f"[editor] render échoué pour {clip.id!r}")
        return None

    async def _render_pipeline(
        self,
        input_path: Path,
        output_path: Path,
        plan: EditPlan,
        transcript_words: list[dict] | None,
        tmp_dir: Path,
    ) -> bool:
        video_info = await self._ffprobe_video_info(input_path)
        if not video_info:
            return False

        step1 = tmp_dir / "step1_trim_crop.mp4"
        step2 = tmp_dir / "step2_grade.mp4"
        ass_path = tmp_dir / "captions.ass"

        logger.info(f"[editor] step 1/3 — trim + crop : {input_path.name}")
        if not await self._step_trim_and_crop(input_path, step1, plan, video_info):
            return False

        logger.info(f"[editor] step 2/3 — color grade : {plan.color_grade}")
        if not await self._step_color_grade(step1, step2, plan.color_grade):
            return False

        graded_info = await self._ffprobe_video_info(step2)
        w = graded_info.get("width", video_info["width"])
        h = graded_info.get("height", video_info["height"])

        font_path = FontManager.get_impact_path()
        font_name = FontManager.get_font_name()
        renderer = CaptionRenderer(w, h, font_path, font_name)

        trim_start = plan.trim_start
        trim_end = plan.trim_end if plan.trim_end > plan.trim_start else video_info.get("duration", 30.0)
        trim_duration = trim_end - trim_start

        if transcript_words:
            segments = renderer._split_into_segments(transcript_words)
        else:
            caption_text = plan.caption.upper() if plan.caption else plan.title.upper()
            segments = [{"start": 0.0, "end": trim_duration, "text": caption_text}]

        renderer.build_ass(segments, plan.title, ass_path)
        logger.info(f"[editor] step 3/3 — burn captions : {len(segments)} segments ASS")

        return await self._step_burn_captions(step2, output_path, ass_path, Path(font_path).parent)

    async def _step_trim_and_crop(
        self,
        input_path: Path,
        temp_path: Path,
        plan: EditPlan,
        video_info: dict,
    ) -> bool:
        w = video_info["width"]
        h = video_info["height"]

        trim_start = plan.trim_start
        trim_end = plan.trim_end if plan.trim_end > plan.trim_start else video_info.get("duration", 30.0)
        duration = trim_end - trim_start

        filters: list[str] = []

        # Recadrage 9:16 si la source n'est pas déjà 9:16
        aspect = w / h if h else 1.0
        if abs(aspect - 9 / 16) > 0.01:
            target_w = h * 9 // 16
            crop_x = (w - target_w) // 2
            filters.append(f"crop={target_w}:{h}:{crop_x}:0")

        if plan.add_zoom:
            filters.append(
                "zoompan=z='min(zoom+0.0015,1.08)':d=1"
                ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':fps=30"
            )

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
            str(temp_path),
        ]

        if filters:
            args = base_args + ["-vf", ",".join(filters)] + encode_args
        else:
            args = base_args + encode_args

        return await self._run_ffmpeg(args, "trim+crop")

    async def _step_color_grade(
        self,
        input_path: Path,
        temp_path: Path,
        color_grade: str,
    ) -> bool:
        if color_grade == "raw":
            args = ["ffmpeg", "-y", "-i", str(input_path), "-c", "copy", str(temp_path)]
        elif color_grade == "cinematic":
            vf = (
                "eq=saturation=0.85:contrast=1.05,"
                "curves=r='0/0 0.5/0.45 1/0.9':"
                "g='0/0 0.5/0.48 1/0.92':"
                "b='0/0 0.5/0.52 1/1.0'"
            )
            args = [
                "ffmpeg", "-y", "-i", str(input_path),
                "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-c:a", "copy", "-movflags", "+faststart",
                str(temp_path),
            ]
        else:  # "viral"
            vf = "eq=saturation=1.3:contrast=1.1:brightness=0.03"
            args = [
                "ffmpeg", "-y", "-i", str(input_path),
                "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-c:a", "copy", "-movflags", "+faststart",
                str(temp_path),
            ]

        return await self._run_ffmpeg(args, f"color_grade:{color_grade}")

    async def _step_burn_captions(
        self,
        input_path: Path,
        output_path: Path,
        ass_path: Path,
        fonts_dir: Path,
    ) -> bool:
        # Escaping du chemin pour le filtre ffmpeg (Windows : antislash → slash, colon → \:)
        def _esc(p: Path) -> str:
            s = str(p).replace("\\", "/")
            # Escape le deux-points du lecteur Windows (ex: C:/ → C\:/)
            if len(s) >= 2 and s[1] == ":":
                s = s[0] + "\\:" + s[2:]
            return s

        ass_esc = _esc(ass_path)
        fonts_esc = _esc(fonts_dir)
        vf = f"ass={ass_esc}:fontsdir='{fonts_esc}'"

        args = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]
        return await self._run_ffmpeg(args, "burn_captions")

    async def _ffprobe_video_info(self, path: Path) -> dict:
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
        """Lance une commande ffmpeg. Retourne True si succès."""
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
                    f"[editor] ffmpeg [{step_name}] rc={proc.returncode}\n"
                    f"{stderr.decode(errors='replace')[-600:]}"
                )
                return False
            return True
        except Exception as exc:
            logger.warning(f"[editor] ffmpeg [{step_name}] erreur : {exc!r}")
            return False
