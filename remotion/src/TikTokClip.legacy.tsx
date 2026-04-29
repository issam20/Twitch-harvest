import {
  AbsoluteFill,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
  Video,
} from "remotion";
import type { FC } from "react";
import type { TikTokClipProps, WordSegment } from "./types";

// CSS filters appliqués sur la vidéo brute
const COLOR_GRADE_FILTERS: Record<string, string> = {
  viral: "saturate(1.3) contrast(1.1) brightness(1.03)",
  cinematic: "saturate(0.85) contrast(1.05)",
  raw: "none",
};

interface CaptionGroup {
  words: WordSegment[];
  start: number;
  end: number;
  highlightIdx: number;
}

// Regroupe les mots en segments de 3 mots max avec coupure sur pause > 0.4 s
function groupWords(words: WordSegment[]): CaptionGroup[] {
  const groups: CaptionGroup[] = [];
  let current: WordSegment[] = [];

  for (const word of words) {
    if (current.length > 0) {
      const gap = word.start - current[current.length - 1].end;
      if (gap > 0.4 || current.length >= 3) {
        groups.push(toGroup(current));
        current = [];
      }
    }
    current.push(word);
  }
  if (current.length > 0) groups.push(toGroup(current));
  return groups;
}

function toGroup(words: WordSegment[]): CaptionGroup {
  // Priorité : premier mot en majuscules → highlight ; sinon dernier mot
  let highlightIdx = words.length - 1;
  for (let i = 0; i < words.length; i++) {
    const w = words[i].word.trim();
    if (w.length > 1 && w === w.toUpperCase() && /[A-Z]/.test(w)) {
      highlightIdx = i;
      break;
    }
  }
  return {
    words,
    start: words[0].start,
    end: words[words.length - 1].end,
    highlightIdx,
  };
}

export const TikTokClip: FC<TikTokClipProps> = ({
  videoSrc,
  title,
  colorGrade,
  addZoom,
  words,
  highlightColor,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();

  const currentTime = frame / fps;
  const cssFilter = COLOR_GRADE_FILTERS[colorGrade] ?? "none";

  const scale = addZoom
    ? interpolate(frame, [0, durationInFrames], [1.0, 1.08], {
        extrapolateRight: "clamp",
      })
    : 1.0;

  const groups = groupWords(words);
  const activeGroup =
    groups.find((g) => currentTime >= g.start && currentTime <= g.end) ?? null;

  const titleFontSize = Math.round(width * 0.072);
  const captionFontSize = Math.round(width * 0.148);
  const outlineWidth = Math.round(captionFontSize * 0.07);

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
        <Video src={staticFile(videoSrc)} />
      </AbsoluteFill>

      {/* Titre en overlay fond blanc arrondi */}
      {title ? (
        <AbsoluteFill
          style={{
            justifyContent: "flex-start",
            alignItems: "center",
            paddingTop: Math.round(height * 0.018),
            paddingLeft: 16,
            paddingRight: 16,
          }}
        >
          <div
            style={{
              backgroundColor: "rgba(255, 255, 255, 0.92)",
              borderRadius: 16,
              paddingTop: 8,
              paddingBottom: 8,
              paddingLeft: 20,
              paddingRight: 20,
              fontFamily: "Impact, 'Arial Black', sans-serif",
              fontSize: titleFontSize,
              fontWeight: "normal",
              color: "black",
              textAlign: "center",
              textTransform: "uppercase",
              letterSpacing: "0.02em",
              maxWidth: "92%",
              lineHeight: 1.15,
            }}
          >
            {title}
          </div>
        </AbsoluteFill>
      ) : null}

      {/* Sous-titres mot/mot style TikTok */}
      {activeGroup ? (
        <AbsoluteFill
          style={{
            justifyContent: "flex-end",
            alignItems: "center",
            paddingBottom: Math.round(height * 0.22),
            paddingLeft: 12,
            paddingRight: 12,
          }}
        >
          <div
            style={{
              fontFamily: "Impact, 'Arial Black', sans-serif",
              fontSize: captionFontSize,
              fontWeight: "normal",
              textAlign: "center",
              lineHeight: 1.1,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {activeGroup.words.map((w, i) => (
              <span key={i}>
                {i > 0 ? " " : ""}
                <span
                  style={
                    i === activeGroup.highlightIdx
                      ? {
                          color: highlightColor,
                          WebkitTextStroke: `${outlineWidth}px rgba(100,0,0,0.7)`,
                        }
                      : {
                          color: "white",
                          WebkitTextStroke: `${outlineWidth}px black`,
                        }
                  }
                >
                  {w.word.toUpperCase()}
                </span>
              </span>
            ))}
          </div>
        </AbsoluteFill>
      ) : null}
    </AbsoluteFill>
  );
};
