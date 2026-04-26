"""Post-processing vidéo : smart cut → 9:16 → sous-titres TikTok."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from loguru import logger

from ..core.config import EditorConfig, WebcamZone
from ..core.events import MomentCategory


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
        tmp_cut = self.output_dir / f"tmp_{stem}_cut.mp4"
        tmp_916 = self.output_dir / f"tmp_{stem}_916.mp4"
        final   = self.output_dir / f"{stem}_processed.mp4"

        try:
            if not await self._smart_cut(clip_path, tmp_cut, clip_duration_total):
                logger.error(f"[editor] smart cut échoué : {clip_path.name}")
                return None

            if not await self._build_916(tmp_cut, tmp_916, webcam_zone):
                logger.error(f"[editor] conversion 9:16 échouée : {clip_path.name}")
                return None

            if not await self._add_subtitles(tmp_916, final):
                logger.warning("[editor] sous-titres échoués — copie brute")
                shutil.copy2(tmp_916, final)

            return final

        finally:
            for tmp in (tmp_cut, tmp_916):
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

    async def _add_subtitles(self, input: Path, output: Path) -> bool:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.warning("[editor] faster-whisper non installé — pip install faster-whisper")
            return False

        ass_path = input.with_suffix(".ass")
        try:
            logger.info(f"[editor] transcription Whisper ({self.settings.whisper_model})...")
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
                n_chunks = max(1, -(-len(words) // chunk_size))
                seg_dur = seg.end - seg.start
                chunk_dur = seg_dur / n_chunks
                for i in range(n_chunks):
                    chunk_words = words[i * chunk_size: (i + 1) * chunk_size]
                    t_start = seg.start + i * chunk_dur
                    t_end = t_start + chunk_dur
                    entries.append((t_start, t_end, " ".join(chunk_words).upper()))

            def _fmt_ass(secs: float) -> str:
                h = int(secs // 3600)
                m = int((secs % 3600) // 60)
                s = int(secs % 60)
                cs = int((secs % 1) * 100)
                return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

            cfg = self.settings
            ass_content = (
                "[Script Info]\n"
                "ScriptType: v4.00+\n"
                "PlayResX: 1080\n"
                "PlayResY: 1920\n\n"
                "[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                "Alignment, MarginL, MarginR, MarginV, Encoding\n"
                f"Style: Default,Impact,{cfg.subtitle_fontsize},"
                f"&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
                f"0,0,0,0,100,100,0,0,1,{cfg.subtitle_outline},1,2,10,10,120,1\n\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            )
            dialogues = [
                f"Dialogue: 0,{_fmt_ass(t0)},{_fmt_ass(t1)},Default,,0,0,0,,{text}"
                for t0, t1, text in entries
            ]
            ass_path.write_text(ass_content + "\n".join(dialogues), encoding="utf-8")

            # Sur Windows, le chemin ASS doit avoir les backslashes échappés pour libass
            ass_str = str(ass_path).replace("\\", "/").replace(":", "\\:")
            vf = f"ass={ass_str}:fontsdir='C\\:/Windows/Fonts'"

            rc, stderr = await _run_ffmpeg([
                "-i", str(input),
                "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "160k",
                str(output),
            ])
            if rc != 0:
                logger.error(f"[editor] ffmpeg subtitles rc={rc}: {stderr[:400]}")
            return rc == 0

        finally:
            try:
                if ass_path.exists():
                    ass_path.unlink()
            except Exception:
                pass
