"""Post-processing vidéo : smart cut → 9:16 → hook → sous-titres."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from loguru import logger

from ..core.config import EditorConfig, WebcamZone
from ..core.events import MomentCategory


def _ffmpeg_escape(text: str) -> str:
    """Échappe les caractères spéciaux pour le filtre drawtext de ffmpeg."""
    return (
        text
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("%", "\\%")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


async def _run_ffmpeg(args: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    return proc.returncode, stderr.decode(errors="replace")


class ClipProcessor:
    def __init__(self, output_dir: Path, settings: EditorConfig) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.settings = settings

    async def process(
        self,
        clip_path: Path,
        webcam_zone: WebcamZone | None,
        category: MomentCategory = MomentCategory.UNKNOWN,
        clip_duration_total: float = 60.0,
    ) -> Path | None:
        stem = clip_path.stem
        tmp_cut  = self.output_dir / f"tmp_{stem}_cut.mp4"
        tmp_916  = self.output_dir / f"tmp_{stem}_916.mp4"
        tmp_hook = self.output_dir / f"tmp_{stem}_hook.mp4"
        final    = self.output_dir / f"{stem}_processed.mp4"

        try:
            if not await self._smart_cut(clip_path, tmp_cut, clip_duration_total):
                logger.error(f"[editor] smart cut échoué : {clip_path.name}")
                return None

            if not await self._build_916(tmp_cut, tmp_916, webcam_zone):
                logger.error(f"[editor] conversion 9:16 échouée : {clip_path.name}")
                return None

            if not await self._add_hook(tmp_916, tmp_hook, category):
                logger.warning("[editor] hook échoué — on continue sans")
                shutil.copy2(tmp_916, tmp_hook)

            if not await self._add_subtitles(tmp_hook, final):
                logger.warning("[editor] sous-titres échoués — on continue sans")
                shutil.move(str(tmp_hook), str(final))
                tmp_hook = Path("/dev/null")  # ne pas essayer de supprimer à nouveau

            return final

        finally:
            for tmp in (tmp_cut, tmp_916, tmp_hook):
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass

    # ------------------------------------------------------------------

    async def _smart_cut(self, input: Path, output: Path, total_duration: float) -> bool:
        cfg = self.settings
        peak_time = total_duration / 2 + cfg.peak_offset_seconds
        start = max(0.0, peak_time - cfg.pre_peak_seconds)
        end = min(total_duration, start + cfg.clip_duration)
        duration = end - start

        rc, stderr = await _run_ffmpeg([
            "-ss", str(start),
            "-i", str(input),
            "-t", str(duration),
            "-c", "copy",
            str(output),
        ])
        if rc != 0:
            logger.error(f"[editor] ffmpeg smart_cut rc={rc}: {stderr[:300]}")
        return rc == 0

    async def _build_916(
        self, input: Path, output: Path, webcam_zone: WebcamZone | None
    ) -> bool:
        cfg = self.settings

        if webcam_zone:
            top_h = int(1920 * cfg.gameplay_ratio)
            bot_h = 1920 - top_h
            fc = (
                f"[0:v]scale=1920:1080,crop=1080:{top_h}:(1920-1080)/2:0[top];"
                f"[0:v]crop=iw*{webcam_zone.w_pct}:ih*{webcam_zone.h_pct}"
                f":iw*{webcam_zone.x_pct}:ih*{webcam_zone.y_pct}"
                f",scale=1080:{bot_h}[bot];"
                f"[top][bot]vstack=inputs=2[out]"
            )
            extra = ["-filter_complex", fc, "-map", "[out]", "-map", "0:a"]
        else:
            fc = "[0:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920[out]"
            extra = ["-filter_complex", fc, "-map", "[out]", "-map", "0:a"]

        rc, stderr = await _run_ffmpeg([
            "-i", str(input),
            *extra,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "copy",
            str(output),
        ])
        if rc != 0:
            logger.error(f"[editor] ffmpeg build_916 rc={rc}: {stderr[:300]}")
        return rc == 0

    async def _add_hook(
        self, input: Path, output: Path, category: MomentCategory
    ) -> bool:
        cfg = self.settings
        templates = cfg.hook_templates
        text_map = {
            MomentCategory.FUNNY: templates.funny,
            MomentCategory.HYPE:  templates.hype,
            MomentCategory.SHOCK: templates.shock,
            MomentCategory.UNKNOWN: templates.unknown,
        }
        raw_text = text_map.get(category, templates.unknown)
        hook_text = _ffmpeg_escape(raw_text)
        hd = cfg.hook_duration

        alpha = (
            f"if(lt(t,{hd - 0.5}),1,"
            f"if(lt(t,{hd}),({hd}-t)/0.5,0))"
        )
        drawtext = (
            f"drawtext=text='{hook_text}'"
            f":fontsize={cfg.hook_fontsize}"
            f":fontcolor=yellow"
            f":bordercolor=black"
            f":borderw=4"
            f":x=(w-text_w)/2"
            f":y=h/3-text_h/2"
            f":enable='between(t,0,{hd})'"
            f":alpha='{alpha}'"
        )

        rc, stderr = await _run_ffmpeg([
            "-i", str(input),
            "-vf", drawtext,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "copy",
            str(output),
        ])
        if rc != 0:
            logger.warning(f"[editor] ffmpeg add_hook rc={rc}: {stderr[:300]}")
        return rc == 0

    async def _add_subtitles(self, input: Path, output: Path) -> bool:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.warning(
                "[editor] faster-whisper non installé — "
                "pip install 'twitch-viral-clipper[editor]'"
            )
            return False

        srt_path = input.with_suffix(".srt")
        try:
            logger.info(f"[editor] transcription Whisper ({self.settings.whisper_model})…")
            model = WhisperModel(
                self.settings.whisper_model, device="cpu", compute_type="int8"
            )
            segments, _ = model.transcribe(str(input), language=None, word_timestamps=False)

            entries: list[tuple[float, float, str]] = []
            for seg in segments:
                words = seg.text.strip().split()
                if not words:
                    continue
                chunk_size = 3
                n_chunks = max(1, -(-len(words) // chunk_size))  # ceil division
                seg_dur = seg.end - seg.start
                chunk_dur = seg_dur / n_chunks
                for i in range(n_chunks):
                    chunk_words = words[i * chunk_size: (i + 1) * chunk_size]
                    t_start = seg.start + i * chunk_dur
                    t_end = t_start + chunk_dur
                    entries.append((t_start, t_end, " ".join(chunk_words).upper()))

            def _fmt(secs: float) -> str:
                h = int(secs // 3600)
                m = int((secs % 3600) // 60)
                s = int(secs % 60)
                ms = int((secs % 1) * 1000)
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

            srt_lines = []
            for idx, (t0, t1, text) in enumerate(entries, 1):
                srt_lines.append(f"{idx}\n{_fmt(t0)} --> {_fmt(t1)}\n{text}\n")
            srt_path.write_text("\n".join(srt_lines), encoding="utf-8")

            cfg = self.settings
            force_style = (
                f"FontName=Arial,"
                f"FontSize={cfg.subtitle_fontsize},"
                f"PrimaryColour={cfg.subtitle_color},"
                f"OutlineColour=&H00000000,"
                f"Outline={cfg.subtitle_outline},"
                f"Bold=1,"
                f"Alignment=2,"
                f"MarginV=420"
            )
            # Sur Windows, le chemin SRT doit avoir les backslashes échappés
            srt_str = str(srt_path).replace("\\", "/").replace(":", "\\:")
            vf = f"subtitles={srt_str}:force_style='{force_style}'"

            rc, stderr = await _run_ffmpeg([
                "-i", str(input),
                "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "160k",
                str(output),
            ])
            if rc != 0:
                logger.warning(f"[editor] ffmpeg subtitles rc={rc}: {stderr[:300]}")
            return rc == 0

        finally:
            try:
                if srt_path.exists():
                    srt_path.unlink()
            except Exception:
                pass
