import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { createTikTokStyleCaptions, parseSrt } from "@remotion/captions";
import type { TikTokPage } from "@remotion/captions";
import type { FC } from "react";
import { COLORS, FONTS } from "./styles";

const MAX_WORDS_PER_PAGE = 3;

function splitToMaxWords(pages: TikTokPage[]): TikTokPage[] {
  const result: TikTokPage[] = [];
  for (const page of pages) {
    if (page.tokens.length <= MAX_WORDS_PER_PAGE) {
      result.push(page);
      continue;
    }
    for (let i = 0; i < page.tokens.length; i += MAX_WORDS_PER_PAGE) {
      const chunk = page.tokens.slice(i, i + MAX_WORDS_PER_PAGE);
      result.push({
        text: chunk.map((t) => t.text).join(" "),
        startMs: chunk[0].fromMs,
        tokens: chunk,
        durationMs: chunk[chunk.length - 1].toMs - chunk[0].fromMs,
      });
    }
  }
  return result;
}

interface Props {
  srtContent: string;
  width: number;
  height: number;
}

export const CaptionOverlay: FC<Props> = ({ srtContent, width, height }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const timeMs = (frame / fps) * 1000;

  const { captions } = parseSrt({ input: srtContent });
  const { pages: rawPages } = createTikTokStyleCaptions({
    captions,
    combineTokensWithinMilliseconds: 500,
  });
  const pages = splitToMaxWords(rawPages);

  const currentPage =
    pages.find(
      (p) => timeMs >= p.startMs && timeMs < p.startMs + p.durationMs
    ) ?? null;

  if (!currentPage || currentPage.tokens.length === 0) return null;

  // FIX 1 — taille de police basée sur la hauteur, pas la largeur
  const fontSize = Math.round(height * 0.055);
  const outlineWidth = Math.round(fontSize * 0.07);

  // FIX 3 — highlight sur le token actuellement prononcé
  const activeTokenIndex = currentPage.tokens.findIndex(
    (token) => timeMs >= token.fromMs && timeMs < token.toMs
  );
  const strongIndex =
    activeTokenIndex >= 0
      ? activeTokenIndex
      : currentPage.tokens.length - 1;

  return (
    <AbsoluteFill>
      {/* FIX 2 — position : top 38% au lieu du bas */}
      <div
        style={{
          position: "absolute",
          top: height * 0.38,
          left: 0,
          right: 0,
          display: "flex",
          justifyContent: "center",
          paddingLeft: 12,
          paddingRight: 12,
        }}
      >
        <div
          style={{
            fontFamily: FONTS.impact,
            fontSize,
            fontWeight: "normal",
            textAlign: "center",
            lineHeight: 1.1,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {currentPage.tokens.map((token, i) => (
            <span key={i}>
              {i > 0 ? " " : ""}
              <span
                style={
                  i === strongIndex
                    ? {
                        color: COLORS.red,
                        WebkitTextStroke: `${outlineWidth}px rgba(100,0,0,0.7)`,
                      }
                    : {
                        color: COLORS.white,
                        WebkitTextStroke: `${outlineWidth}px ${COLORS.black}`,
                      }
                }
              >
                {token.text.toUpperCase()}
              </span>
            </span>
          ))}
        </div>
      </div>
    </AbsoluteFill>
  );
};
