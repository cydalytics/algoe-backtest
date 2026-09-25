import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";

import { C, MONO } from "../theme";
import { Caption, Chip, Ground, mix, ramp, Rise, SceneFade, SceneTitle, Stamp } from "../ui";

const ROWS = [
  ["Match A", "HILO", "line ℓ₁", "Over", "T"],
  ["Match A", "HILO", "line ℓ₁", "Under", "T"],
  ["Match A", "HDC", "line ℓ₂", "Home", "T"],
  ["Match A", "HDC", "line ℓ₂", "Away", "T"],
  ["Match B", "CHLO", "line ℓ₃", "Over", "T"],
  ["Match B", "CHLO", "line ℓ₃", "Under", "T"],
  ["Match B", "CHDC", "line ℓ₄", "Home", "T"],
];
const PICK = 2;
const HEAD = ["Match", "Pool", "Line", "Selection", "Bucket"];
const COLS = [220, 170, 190, 220, 150];
const ROW_H = 66;
const BAR = [0.62, 0.48, 0.8, 0.35, 0.55, 0.4, 0.7];

export const Unit: React.FC = () => {
  const frame = useCurrentFrame();
  const board = ramp(frame, 4, 26);
  const push = ramp(frame, 56, 104);
  const others = 1 - ramp(frame, 48, 80);
  const detail = ramp(frame, 100, 124);

  const scale = mix(1, 1.55, push);
  const dy = mix(0, -(PICK - 3) * ROW_H * 1.55, push);

  return (
    <SceneFade>
      <Ground>
        <SceneTitle index="01" title="The unit" />

        {/* the board, then the push into one row */}
        <AbsoluteFill
          style={{
            justifyContent: "center",
            alignItems: "center",
            opacity: board * (1 - detail),
          }}
        >
          <div style={{ transform: `translateY(${dy}px) scale(${scale})`, transformOrigin: "center" }}>
            <div style={{ display: "flex", borderBottom: `1px solid ${C.hairStrong}`, paddingBottom: 12, opacity: others }}>
              {HEAD.map((h, i) => (
                <div key={h} style={{ width: COLS[i], fontSize: 20, color: C.muted, letterSpacing: "0.14em", textTransform: "uppercase" }}>
                  {h}
                </div>
              ))}
              <div style={{ width: 200, fontSize: 20, color: C.muted, letterSpacing: "0.14em", textTransform: "uppercase" }}>Money</div>
            </div>
            {ROWS.map((r, ri) => {
              const on = ri === PICK;
              const rowIn = ramp(frame, 8 + ri * 3, 24 + ri * 3);
              return (
                <div
                  key={ri}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    height: ROW_H,
                    borderBottom: `1px solid ${C.hair}`,
                    opacity: rowIn * (on ? 1 : others),
                    background: on ? `rgba(212,168,75,${0.08 * push})` : "transparent",
                  }}
                >
                  {r.map((cell, ci) => (
                    <div
                      key={ci}
                      style={{
                        width: COLS[ci],
                        fontSize: 28,
                        fontFamily: ci === 2 || ci === 4 ? MONO : undefined,
                        color: on ? C.ink : C.muted,
                      }}
                    >
                      {cell}
                    </div>
                  ))}
                  <div style={{ width: 200 }}>
                    <div style={{ height: 10, width: 170 * BAR[ri], background: on ? C.gold : C.faint, borderRadius: 1 }} />
                  </div>
                </div>
              );
            })}
          </div>
        </AbsoluteFill>

        {/* the row in detail */}
        <AbsoluteFill style={{ opacity: detail, paddingLeft: 120, paddingRight: 120, paddingTop: 200 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 28,
              border: `1px solid ${C.gold}`,
              borderRadius: 4,
              padding: "26px 34px",
              fontSize: 40,
              width: "fit-content",
            }}
          >
            <span>Match A</span>
            <span style={{ color: C.muted }}>·</span>
            <span>HDC</span>
            <span style={{ color: C.muted }}>·</span>
            <span style={{ fontFamily: MONO }}>line ℓ₂</span>
            <span style={{ color: C.muted }}>·</span>
            <span>Home</span>
            <span style={{ color: C.muted }}>·</span>
            <span style={{ fontFamily: MONO, color: C.gold }}>bucket T</span>
          </div>

          <Rise at={118} style={{ marginTop: 40 }}>
            <div style={{ fontFamily: MONO, fontSize: 36 }}>
              <span style={{ color: C.muted }}>target </span>
              y = turnover in <span style={{ color: C.gold }}>(T, T+5min]</span>
              <span style={{ color: C.neg }}>  · unknown at T</span>
            </div>
          </Rise>

          <div style={{ display: "flex", gap: 64, marginTop: 56 }}>
            <div>
              <Rise at={128}>
                <div style={{ fontSize: 20, color: C.muted, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 18 }}>
                  inputs
                </div>
              </Rise>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 16, maxWidth: 1040 }}>
                {["odds", "TG / SUP", "clock", "lag₁  lag₂  lag₃ · last three buckets", "last goal"].map((k, i) => (
                  <Rise key={k} at={132 + i * 6}>
                    <div style={{ display: "flex", flexDirection: "column", gap: 10, alignItems: "flex-start" }}>
                      <Chip tone="known">{k}</Chip>
                      <Stamp at={150 + i * 6} tone="known">known at T</Stamp>
                    </div>
                  </Rise>
                ))}
              </div>
            </div>
            <div>
              <Rise at={160}>
                <div style={{ fontSize: 20, color: C.muted, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 18 }}>
                  label
                </div>
              </Rise>
              <Rise at={164}>
                <div style={{ display: "flex", flexDirection: "column", gap: 10, alignItems: "flex-start" }}>
                  <Chip tone="unknown">this bucket’s money</Chip>
                  <Stamp at={176} tone="unknown">not known</Stamp>
                </div>
              </Rise>
            </div>
          </div>
        </AbsoluteFill>

        <Caption at={186}>The forecast prices the next five minutes. It does not explain the last five.</Caption>
      </Ground>
    </SceneFade>
  );
};
