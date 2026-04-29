"""Génère les fichiers ASS (Advanced SubStation Alpha) pour les sous-titres TikTok."""
from __future__ import annotations

from pathlib import Path

from ..core.logging import logger


class CaptionRenderer:
    # Couleurs ASS (format &HAABBGGRR)
    WHITE = "&H00FFFFFF"
    BLACK = "&H00000000"

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
        # Fix 2 : taille basée sur render_width (post-crop), min 48px
        self.font_size = max(int(video_width * 0.148), 48)
        self.margin_v = round(video_height * 0.22)

    def build_ass(
        self,
        segments: list[dict],   # [{"start": 0.0, "end": 1.2, "text": "ET PAR DES"}]
        title: str,             # conservé pour compatibilité API — titre rendu par drawtext
        output_path: Path,
    ) -> Path:
        """Génère le fichier .ass et le retourne."""
        lines: list[str] = []
        lines += self._header()
        lines += self._styles()
        lines += self._events(segments)
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
        fs = self.font_size
        fn = self.font_name

        # Normal : texte blanc, outline noir 4px, aligné haut-centre
        normal = (
            f"Style: Normal,{fn},{fs},"
            f"{self.WHITE},{self.WHITE},{self.BLACK},{self.BLACK},"
            f"-1,0,0,0,100,100,0,0,1,4,0,8,10,10,{mv},1"
        )
        # Highlight : conservé pour compatibilité ; le vrai highlight passe par des tags inline
        highlight = (
            f"Style: Highlight,{fn},{fs},"
            f"{self.WHITE},{self.WHITE},{self.BLACK},{self.BLACK},"
            f"-1,0,0,0,100,100,0,0,1,4,0,8,10,10,{mv},1"
        )
        return ["[V4+ Styles]", fmt, normal, highlight, ""]

    def _events(self, segments: list[dict]) -> list[str]:
        fmt = "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
        lines = ["[Events]", fmt]

        # Fix 4 : highlight via tags inline ASS (pas de style swap qui casse le positionnement)
        _HL_OPEN  = r"{\c&H002400E8&\3c&H002400E8&\bord0\shad0\be1\blur0}"
        _HL_CLOSE = r"{\r}"

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
                    parts.append(f"{_HL_OPEN}{word}{_HL_CLOSE}")
                else:
                    parts.append(word)
            text = " ".join(parts)
            lines.append(f"Dialogue: 0,{t0},{t1},Normal,,0,0,0,,{text}")

        return lines

    def _fallback_segments(self, caption: str, duration: float) -> list[dict]:
        """Fix 3 : découpe caption en chunks de 3 mots max distribués sur la durée."""
        words = caption.upper().split()
        if not words:
            return [{"start": 0.0, "end": duration, "text": ""}]
        chunks = [" ".join(words[i:i + 3]) for i in range(0, len(words), 3)]
        time_per_chunk = duration / len(chunks)
        return [
            {
                "start": i * time_per_chunk,
                "end": (i + 1) * time_per_chunk,
                "text": chunk,
            }
            for i, chunk in enumerate(chunks)
        ]

    def _split_into_segments(
        self,
        words: list[dict],  # [{"word": "ET", "start": 0.0, "end": 0.3}]
        max_words: int = 3,
    ) -> list[dict]:
        """Fix 6 : regroupe les mots Whisper en segments de max_words, coupe sur pauses > 0.4s."""
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
        1. Mot en majuscules dans le texte original (mixed case uniquement)
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
