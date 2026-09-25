import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";

import { C, MONO } from "../theme";
import { Caption, Ground, ramp, Rise, SceneFade, SceneTitle } from "../ui";

const DAYS = 35;
const WARMUP = 14;
const REFIT = 7;
const INNER = 7;
const CELL = 42;
const GAP = 6;

/** The last refit at or before day n: after the warm-up, then every REFIT days. */
function lastRefit(n: number): number | null {
  if (n < WARMUP) return null;
  return WARMUP + Math.floor((n - WARMUP) / REFIT) * REFIT;
}

export const Clock: React.FC = () => {
  const frame = useCurrentFrame();
  const strip = ramp(frame, 6, 24);
  const cursor = Math.floor(
    interpolate(frame, [30, 236], [0, DAYS - 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
  );
  const r = lastRefit(cursor);

  return (
    <SceneFade>
      <Ground>
        <SceneTitle index="05" title="The clock" />
        <Rise at={10} style={{ position: "absolute", left: 120, top: 150 }}>
          <div style={{ fontSize: 24, color: C.muted }}>Walk-forward. Day N is predicted, then observed.</div>
        </Rise>

        <AbsoluteFill style={{ paddingLeft: 120, paddingTop: 420, opacity: strip }}>
          <div style={{ display: "flex", gap: GAP }}>
            {Array.from({ length: DAYS }).map((_, d) => {
              const future = d > cursor;
              const warm = d < WARMUP;
              const predictNow = d === cursor;
              let bg = "transparent";
              let border = C.hair;
              if (!future && !predictNow && r !== null) {
                if (d < r - INNER) bg = "rgba(212,168,75,0.55)";
                else if (d < r) {
                  bg = "rgba(139,140,240,0.28)";
                  border = C.iris;
                } else bg = "rgba(230,233,239,0.10)";
              } else if (!future && !predictNow) {
                bg = "rgba(230,233,239,0.10)";
              }
              if (predictNow) {
                bg = "transparent";
                border = C.ink;
              }
              return (
                <div
                  key={d}
                  style={{
                    width: CELL,
                    height: CELL * 1.6,
                    background: bg,
                    border: `${predictNow ? 2 : 1}px solid ${border}`,
                    borderRadius: 2,
                    backgroundImage: warm && !predictNow
                      ? `repeating-linear-gradient(135deg, transparent 0 6px, rgba(255,255,255,0.06) 6px 7px)`
                      : undefined,
                    position: "relative",
                  }}
                >
                  {predictNow && (
                    <div
                      style={{
                        position: "absolute",
                        top: -44,
                        left: "50%",
                        transform: "translateX(-50%)",
                        fontFamily: MONO,
                        fontSize: 20,
                        color: C.ink,
                        whiteSpace: "nowrap",
                      }}
                    >
                      N
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* warm-up bracket and refit ticks */}
          <div style={{ position: "relative", height: 120, marginTop: 18 }}>
            <div
              style={{
                position: "absolute",
                left: 0,
                width: WARMUP * (CELL + GAP) - GAP,
                borderTop: `1px solid ${C.muted}`,
                paddingTop: 10,
                fontSize: 22,
                color: C.muted,
              }}
            >
              warm-up · 14 days · unscored
            </div>
            {[WARMUP, WARMUP + REFIT, WARMUP + 2 * REFIT].map((d, i) => (
              <div
                key={d}
                style={{
                  position: "absolute",
                  left: d * (CELL + GAP) - GAP / 2,
                  top: -CELL * 1.6 - 34,
                  height: CELL * 1.6 + 30,
                  borderLeft: `2px solid ${cursor >= d ? C.gold : C.faint}`,
                }}
              >
                <div
                  style={{
                    position: "absolute",
                    top: CELL * 1.6 + 36,
                    left: 8,
                    fontFamily: MONO,
                    fontSize: 18,
                    color: cursor >= d ? C.gold : C.faint,
                    whiteSpace: "nowrap",
                  }}
                >
                  {i === 0 ? "first fit" : "refit"}
                </div>
              </div>
            ))}
          </div>

          <div style={{ display: "flex", gap: 44, marginTop: 30, fontSize: 22, color: C.muted }}>
            <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span style={{ width: 22, height: 22, background: "rgba(212,168,75,0.55)" }} /> fits the coefficients
            </span>
            <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span style={{ width: 22, height: 22, background: "rgba(139,140,240,0.28)", border: `1px solid ${C.iris}` }} />
              last 7 days of the fit · only choose α
            </span>
            <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span style={{ width: 22, height: 22, border: `2px solid ${C.ink}` }} /> day N · predicted, then observed
            </span>
            <span style={{ fontFamily: MONO, color: C.gold }}>refit every 7 days</span>
          </div>
        </AbsoluteFill>

        <Caption at={214}>A day never trains on itself.</Caption>
      </Ground>
    </SceneFade>
  );
};
