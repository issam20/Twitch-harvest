export type ColorGrade = "viral" | "cinematic" | "raw";

export interface WordSegment {
  word: string;
  start: number;
  end: number;
}

export interface TikTokClipProps {
  videoSrc: string;
  title: string;
  colorGrade: ColorGrade;
  addZoom: boolean;
  words: WordSegment[];
  highlightColor: string;
  // Composition metadata — read by calculateMetadata in Root.tsx
  durationInFrames: number;
  fps: number;
  width: number;
  height: number;
}
