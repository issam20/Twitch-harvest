"""Génère les fichiers ASS (Advanced SubStation Alpha) pour les sous-titres TikTok."""
from __future__ import annotations

from pathlib import Path

from ..core.logging import logger


class CaptionRenderer:
    # Couleurs ASS (format &HAABBGGRR)
    WHITE  = "&H00FFFFFF"
    BLACK  = "&H00000000"
    RED    = "&H002400E8"   # #E8003C en BGR

    def __init__(
        self,
        video_width: int,
        video_height: int,
        font_path: str,
        font_name: str,
    ) -> None:
        self.video_width = video_width
        self.video_height = video_height
        self.font_path = font_path
        self.font_name = font_name
        self.font_size = round(video_width * 0.148)
        self.title_font_size = round(video_width * 0.055)
        self.margin_v = round(video_height * 0.22)
        self.title_margin_v = round(video_height * 0.02)

    def build_ass(
        self,
        segments: list[dict],   # [{"start": 0.0, "end": 1.2, "text": "ET PAR DES"}]
        title: str,
        output_path: Path,
    ) -> Path:
        """Génère le fichier .ass et le retourne."""
        lines: list[str] = []
        lines += self._header()
        lines += self._styles()
        lines += self._events(segments, title)
        output_path.write_text("\n".join(lines), encoding="utf-8")
        logger.debug(f"[captions] ASS généré : {output_path} ({len(segments)} segments)")
        return output_path

    def _header(self) -> list[str]:
        return [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {self.video_width}",
            f"PlayResY: {self.video_height}",
            "WrapStyle: 0",
            "",
        ]

    def _styles(self) -> list[str]:
        fmt = (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding"
        )
        mv = self.margin_v
        tmv = self.title_margin_v
        fs = self.font_size
        tfs = self.title_font_size
        fn = self.font_name

        # BorderStyle 1 = outline+shadow, 3 = opaque box
        normal = (
            f"Style: Normal,{fn},{fs},"
            f"{self.WHITE},{self.WHITE},{self.BLACK},{self.BLACK},"
            f"-1,0,0,0,100,100,0,0,1,4,0,8,10,10,{mv},1"
        )
        highlight = (
            f"Style: Highlight,{fn},{fs},"
            f"{self.WHITE},{self.WHITE},{self.BLACK},{self.RED},"
            f"-1,0,0,0,100,100,0,0,3,0,0,8,10,10,{mv},1"
        )
        title_style = (
            f"Style: Title,{fn},{tfs},"
            f"{self.BLACK},{self.BLACK},{self.BLACK},{self.WHITE},"
            f"-1,0,0,0,100,100,0,0,3,0,0,8,10,10,{tmv},1"
        )
        return ["[V4+ Styles]", fmt, normal, highlight, title_style, ""]

    def _events(self, segments: list[dict], title: str) -> list[str]:
        fmt = "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
        lines = ["[Events]", fmt]

        total_start = segments[0]["start"] if segments else 0.0
        total_end = segments[-1]["end"] if segments else 1.0

        if title:
            t0 = self._format_ass_time(total_start)
            t1 = self._format_ass_time(total_end)
            lines.append(f"Dialogue: 0,{t0},{t1},Title,,0,0,0,,{title.upper()}")

        for seg in segments:
            t0 = self._format_ass_time(seg["start"])
            t1 = self._format_ass_time(seg["end"])
            words = seg["text"].split()
            if not words:
                continue
            strong_idx = self._detect_strong_word(words)
            parts: list[str] = []
            for i, word in enumerate(words):
                if i == strong_idx:
                    parts.append(r"{\rHighlight}" + word + r"{\r}")
                else:
                    parts.append(word)
            text = " ".join(parts)
            lines.append(f"Dialogue: 0,{t0},{t1},Normal,,0,0,0,,{text}")

        return lines

    def _split_into_segments(
        self,
        words: list[dict],  # [{"word": "ET", "start": 0.0, "end": 0.3}]
        max_words: int = 3,
    ) -> list[dict]:
        """Regroupe les mots en segments de max_words, coupe sur les pauses > 0.4s."""
        if not words:
            return []

        segments: list[dict] = []
        current: list[dict] = []

        for word in words:
            if current:
                gap = word["start"] - current[-1]["end"]
                if gap > 0.4 or len(current) >= max_words:
                    segments.append({
                        "start": current[0]["start"],
                        "end": current[-1]["end"],
                        "text": " ".join(w["word"].upper() for w in current),
                    })
                    current = []
            current.append(word)

        if current:
            segments.append({
                "start": current[0]["start"],
                "end": current[-1]["end"],
                "text": " ".join(w["word"].upper() for w in current),
            })

        return segments

    def _detect_strong_word(self, words: list[str]) -> int:
        """Retourne l'index du mot fort.

        Priorité :
        1. Mot en majuscules dans le texte original
        2. Dernier mot du segment
        """
        for i, w in enumerate(words):
            if len(w) > 1 and w.isupper():
                return i
        return len(words) - 1

    @staticmethod
    def _format_ass_time(seconds: float) -> str:
        """Convertit des secondes en format ASS : H:MM:SS.cc"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        cs = round((seconds % 1) * 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
