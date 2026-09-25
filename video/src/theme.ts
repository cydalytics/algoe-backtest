import type React from "react";

export const FPS = 30;

export const C = {
  bg: "#0c1220",
  panel: "rgba(255,255,255,0.025)",
  grid: "rgba(255,255,255,0.045)",
  hair: "rgba(255,255,255,0.12)",
  hairStrong: "rgba(255,255,255,0.22)",
  ink: "#e6e9ef",
  muted: "#7c879c",
  faint: "#3a4458",
  gold: "#d4a84b",
  iris: "#8b8cf0",
  neg: "#c46a6a",
};

export const SANS = '"Segoe UI", "Helvetica Neue", Arial, sans-serif';
export const MONO = 'Consolas, "Cascadia Mono", "Courier New", monospace';

export const NUM: React.CSSProperties = {
  fontVariantNumeric: "tabular-nums",
  fontFeatureSettings: '"tnum" 1',
};

// Scene lengths in frames. Sum is 75 s at 30 fps.
export const SCENES = {
  title: 4 * FPS,
  unit: 8 * FPS,
  baseline: 8 * FPS,
  model: 14 * FPS,
  features: 16 * FPS,
  clock: 10 * FPS,
  judgement: 12 * FPS,
  end: 3 * FPS,
};

export const TOTAL = Object.values(SCENES).reduce((a, b) => a + b, 0);
