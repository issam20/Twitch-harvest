import { AbsoluteFill } from "remotion";
import type { FC } from "react";
import { COLORS, FONTS } from "./styles";

interface Props {
  title: string;
  width: number;
  height: number;
}

export const TitleOverlay: FC<Props> = ({ title, width, height }) => {
  if (!title) return null;

  const fontSize = Math.round(width * 0.072);

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-start",
        alignItems: "center",
        paddingTop: Math.round(height * 0.02),
        paddingLeft: 16,
        paddingRight: 16,
      }}
    >
      <div
        style={{
          backgroundColor: COLORS.titleBg,
          borderRadius: 16,
          paddingTop: 8,
          paddingBottom: 8,
          paddingLeft: 20,
          paddingRight: 20,
          fontFamily: FONTS.impact,
          fontSize,
          fontWeight: "normal",
          color: COLORS.titleText,
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
  );
};
