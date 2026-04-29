import { Composition } from "remotion";
import type { CalculateMetadataFunction } from "remotion";
import type { ComponentType, FC } from "react";
import { TikTokClip } from "./TikTokClip";
import type { TikTokClipProps } from "./types";

type AnyProps = Record<string, unknown>;

const DEFAULT_PROPS: TikTokClipProps = {
  videoSrc: "step1.mp4",
  title: "TITRE DU CLIP",
  colorGrade: "viral",
  addZoom: false,
  words: [],
  highlightColor: "#E8003C",
  durationInFrames: 450,
  fps: 30,
  width: 405,
  height: 720,
};

// calculateMetadata lit durationInFrames/fps/width/height depuis les props
// pour configurer dynamiquement la composition au moment du rendu.
// Le double-cast (via unknown) est nécessaire car Composition<T> exige
// T extends Record<string,unknown> avec signature d'index, et les interfaces
// TypeScript n'en ont pas par défaut.
const calculateMetadata: CalculateMetadataFunction<AnyProps> = async ({
  props,
}) => {
  const p = props as unknown as TikTokClipProps;
  return {
    durationInFrames: Math.max(1, Math.round(p.durationInFrames)),
    fps: Math.max(1, Math.round(p.fps)),
    width: p.width,
    height: p.height,
  };
};

export const Root: FC = () => {
  return (
    <Composition
      id="TikTokClip"
      component={TikTokClip as unknown as ComponentType<AnyProps>}
      durationInFrames={DEFAULT_PROPS.durationInFrames}
      fps={DEFAULT_PROPS.fps}
      width={DEFAULT_PROPS.width}
      height={DEFAULT_PROPS.height}
      defaultProps={DEFAULT_PROPS as unknown as AnyProps}
      calculateMetadata={calculateMetadata}
    />
  );
};
