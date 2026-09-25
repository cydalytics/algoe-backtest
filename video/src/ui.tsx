import React from "react";
import { AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig } from "remotion";

import { C, MONO, NUM, SANS } from "./theme";

const EASE = Easing.bezier(0.22, 1, 0.36, 1);

/** 0 → 1 between two frames, eased. */
export function useRamp(from: number, to: number): number {
  const frame = useCurrentFrame();
  return interpolate(frame, [from, to], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASE,
  });
}

export function ramp(frame: number, from: number, to: number): number {
  return interpolate(frame, [from, to], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: EASE,
  });
}

export function mix(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Hairline grid on the navy ground. Every scene sits on it. */
export const Ground: React.FC<{ children?: React.ReactNode }> = ({ children }) => (
  <AbsoluteFill
    style={{
      background: C.bg,
      backgroundImage: `linear-gradient(${C.grid} 1px, transparent 1px), linear-gradient(90deg, ${C.grid} 1px, transparent 1px)`,
      backgroundSize: "80px 80px",
      backgroundPosition: "-1px -1px",
      fontFamily: SANS,
      color: C.ink,
      ...NUM,
    }}
  >
    {children}
  </AbsoluteFill>
);

/** Fade the whole scene in and out at its edges. */
export const SceneFade: React.FC<{ children: React.ReactNode; edge?: number }> = ({ children, edge = 9 }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const opacity = interpolate(
    frame,
    [0, edge, durationInFrames - edge, durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  return <AbsoluteFill style={{ opacity }}>{children}</AbsoluteFill>;
};

/** Appear: fade plus a short rise. */
export const Rise: React.FC<{
  at: number;
  dur?: number;
  dy?: number;
  style?: React.CSSProperties;
  children: React.ReactNode;
}> = ({ at, dur = 14, dy = 14, style, children }) => {
  const t = useRamp(at, at + dur);
  return (
    <div style={{ opacity: t, transform: `translateY(${(1 - t) * dy}px)`, ...style }}>{children}</div>
  );
};

export const Kicker: React.FC<{ children: React.ReactNode; color?: string }> = ({ children, color = C.muted }) => (
  <div
    style={{
      fontSize: 20,
      letterSpacing: "0.22em",
      textTransform: "uppercase",
      color,
      fontWeight: 600,
    }}
  >
    {children}
  </div>
);

export const SceneTitle: React.FC<{ index: string; title: string; at?: number }> = ({ index, title, at = 0 }) => (
  <Rise at={at} style={{ position: "absolute", left: 120, top: 84 }}>
    <div style={{ display: "flex", alignItems: "baseline", gap: 22 }}>
      <span style={{ fontFamily: MONO, fontSize: 22, color: C.gold }}>{index}</span>
      <span style={{ fontSize: 40, fontWeight: 600, letterSpacing: "-0.01em" }}>{title}</span>
    </div>
  </Rise>
);

export const Caption: React.FC<{ at: number; children: React.ReactNode; bottom?: number }> = ({
  at,
  children,
  bottom = 96,
}) => (
  <Rise
    at={at}
    style={{
      position: "absolute",
      left: 120,
      right: 120,
      bottom,
      fontSize: 34,
      fontWeight: 500,
      color: C.ink,
      letterSpacing: "-0.005em",
    }}
  >
    <span style={{ borderLeft: `3px solid ${C.gold}`, paddingLeft: 22 }}>{children}</span>
  </Rise>
);

export const Chip: React.FC<{
  children: React.ReactNode;
  tone?: "known" | "unknown" | "plain" | "gold" | "iris" | "dim";
  style?: React.CSSProperties;
}> = ({ children, tone = "plain", style }) => {
  const color =
    tone === "known" ? C.ink : tone === "unknown" ? C.neg : tone === "gold" ? C.gold : tone === "iris" ? C.iris : tone === "dim" ? C.faint : C.muted;
  const border =
    tone === "unknown" ? C.neg : tone === "gold" ? C.gold : tone === "iris" ? C.iris : tone === "dim" ? C.faint : C.hairStrong;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 10,
        border: `1px solid ${border}`,
        borderRadius: 3,
        padding: "8px 14px",
        fontSize: 22,
        color,
        background: "rgba(12,18,32,0.85)",
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      {children}
    </span>
  );
};

export const Stamp: React.FC<{ at: number; tone: "known" | "unknown"; children: React.ReactNode }> = ({
  at,
  tone,
  children,
}) => {
  const frame = useCurrentFrame();
  const t = ramp(frame, at, at + 8);
  const color = tone === "known" ? C.iris : C.neg;
  return (
    <span
      style={{
        display: "inline-block",
        opacity: t,
        transform: `scale(${mix(1.25, 1, t)}) rotate(-3deg)`,
        border: `2px solid ${color}`,
        color,
        fontFamily: MONO,
        fontSize: 16,
        letterSpacing: "0.12em",
        textTransform: "uppercase",
        padding: "3px 8px",
        borderRadius: 2,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
};

export const Formula: React.FC<{ children: React.ReactNode; size?: number; color?: string; style?: React.CSSProperties }> = ({
  children,
  size = 40,
  color = C.ink,
  style,
}) => (
  <div style={{ fontFamily: MONO, fontSize: size, color, whiteSpace: "pre", ...style }}>{children}</div>
);

/** A money bar with no number on it. Height is illustrative only. */
export const MoneyBar: React.FC<{
  h: number;
  color: string;
  width?: number;
  dashed?: boolean;
  label?: React.ReactNode;
  style?: React.CSSProperties;
}> = ({ h, color, width = 64, dashed = false, label, style }) => (
  <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12, ...style }}>
    <div style={{ height: 240, width, display: "flex", alignItems: "flex-end" }}>
      <div
        style={{
          width: "100%",
          height: Math.max(h, 2),
          background: dashed ? "transparent" : color,
          border: dashed ? `2px dashed ${color}` : "none",
          opacity: dashed ? 0.9 : 0.92,
          borderRadius: "2px 2px 0 0",
        }}
      />
    </div>
    {label && <div style={{ fontFamily: MONO, fontSize: 20, color: C.muted, whiteSpace: "nowrap" }}>{label}</div>}
  </div>
);

export const Blank: React.FC<{ label: string; width?: number }> = ({ label, width = 280 }) => (
  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
    <div style={{ fontSize: 18, color: C.muted, letterSpacing: "0.08em", textTransform: "uppercase" }}>{label}</div>
    <div
      style={{
        width,
        height: 64,
        border: `2px dashed ${C.hairStrong}`,
        borderRadius: 3,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontFamily: MONO,
        fontSize: 20,
        color: C.faint,
        letterSpacing: "0.14em",
      }}
    >
      PLACEHOLDER
    </div>
  </div>
);
