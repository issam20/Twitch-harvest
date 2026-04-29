import {
  AbsoluteFill,
  interpolate,
  OffthreadVideo,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { FC } from "react";
import type { TikTokClipProps, WordSegment } from "../types";
import { colorGradeFilter } from "./styles";
import { TitleOverlay } from "./TitleOverlay";
import { CaptionOverlay } from "./CaptionOverlay";

// Convertit les mots Whisper [{word, start, end}] en contenu SRT
// pour @remotion/captions parseSrt()
function wordsToSrt(words: WordSegment[]): string {
  return words
    .map((w, i) => {
      const start = msToSrtTimestamp(Math.round(w.start * 1000));
      const end = msToSrtTimestamp(Math.round(w.end * 1000));
      return `${i + 1}\n${start} --> ${end}\n${w.word}`;
    })
    .join("\n\n");
}

function msToSrtTimestamp(ms: number): string {
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1_000);
  const rest = ms % 1_000;
  return (
    String(h).padStart(2, "0") +
    ":" +
    String(m).padStart(2, "0") +
    ":" +
    String(s).padStart(2, "0") +
    "," +
    String(rest).padStart(3, "0")
  );
}

export const TwitchClip: FC<TikTokClipProps> = ({
  videoSrc,
  title,
  colorGrade,
  addZoom,
  words,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  const cssFilter = colorGradeFilter(colorGrade);
  const scale = addZoom
    ? interpolate(frame, [0, durationInFrames], [1.0, 1.08], {
        extrapolateRight: "clamp",
      })
    : 1.0;

  const srtContent = wordsToSrt(words);

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {/* Vidéo de base avec color grade + zoom */}
      <AbsoluteFill
        style={{
          transform: `scale(${scale})`,
          filter: cssFilter,
          transformOrigin: "center center",
        }}
      >
        <OffthreadVideo src={staticFile(videoSrc)} />
      </AbsoluteFill>

      <TitleOverlay title={title} width={width} height={height} />
      <CaptionOverlay srtContent={srtContent} width={width} height={height} />
    </AbsoluteFill>
  );
};
