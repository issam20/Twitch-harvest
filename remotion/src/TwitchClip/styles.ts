export const COLORS = {
  white: "#FFFFFF",
  black: "#000000",
  red: "#E8003C",
  titleBg: "rgba(255, 255, 255, 0.92)",
  titleText: "#000000",
} as const;

export const FONTS = {
  impact: 'Impact, "Arial Narrow", Arial, sans-serif',
} as const;

const GRADE_FILTERS: Record<string, string> = {
  viral: "saturate(1.3) contrast(1.1) brightness(1.03)",
  cinematic: "saturate(0.85) contrast(1.05)",
  raw: "none",
};

export function colorGradeFilter(grade: string): string {
  return GRADE_FILTERS[grade] ?? "none";
}
