import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";

import { C, MONO } from "../theme";
import { Caption, Chip, Ground, mix, MoneyBar, ramp, Rise, SceneFade, SceneTitle } from "../ui";

const LAGS = [
  { label: "lag₃", h: 120 },
  { label: "lag₂", h: 150 },
  { label: "lag₁", h: 170 },
];

/** One blind spot: persistence bar (gold) against what the bucket then took (outline). */
const Blind: React.FC<{
  at: number;
  title: string;
  why: string;
  persist: number;
  actual: number;
  zero: boolean;
}> = ({ at, title, why, persist, actual, zero }) => {
  const frame = useCurrentFrame();
  const t = ramp(frame, at, at + 16);
  return (
    <div
      style={{
        opacity: t,
        transform: `translateY(${(1 - t) * 14}px)`,
        border: `1px solid ${C.hair}`,
        borderRadius: 4,
        padding: "22px 26px",
        width: 470,
        background: C.panel,
      }}
    >
      <div style={{ fontSize: 30, fontWeight: 600 }}>{title}</div>
      <div style={{ fontSize: 21, color: C.muted, marginTop: 6, minHeight: 58 }}>{why}</div>
      <div style={{ display: "flex", gap: 34, alignItems: "flex-end", marginTop: 8, transform: "scale(0.7)", transformOrigin: "left bottom", height: 200 }}>
        <MoneyBar h={persist * t} color={C.gold} label="persistence" />
        <MoneyBar h={actual * t} color={C.ink} dashed label="next bucket" />
      </div>
      {zero && (
        <div style={{ marginTop: 6 }}>
          <Chip tone="unknown" style={{ fontFamily: MONO, fontSize: 19 }}>forecast = 0</Chip>
        </div>
      )}
    </div>
  );
};

export const Baseline: React.FC = () => {
  const frame = useCurrentFrame();
  const copy = ramp(frame, 26, 62);
  const strip = 1 - ramp(frame, 104, 122);
  const nextX = mix(0, 1, copy);

  return (
    <SceneFade>
      <Ground>
        <SceneTitle index="02" title="The baseline" />

        <AbsoluteFill style={{ opacity: strip, justifyContent: "center", paddingLeft: 260 }}>
          <div style={{ display: "flex", gap: 70, alignItems: "flex-end" }}>
            {LAGS.map((b, i) => (
              <Rise key={b.label} at={4 + i * 4}>
                <MoneyBar h={b.h} color={i === 2 ? C.gold : C.faint} width={110} label={b.label} />
              </Rise>
            ))}
            <div style={{ width: 2, height: 280, background: C.hairStrong, marginBottom: 36 }} />
            <div style={{ position: "relative" }}>
              <MoneyBar h={170 * nextX} color={C.gold} width={110} dashed label="next · (T, T+5min]" />
              <div
                style={{
                  position: "absolute",
                  top: 150,
                  left: -62,
                  fontFamily: MONO,
                  fontSize: 34,
                  color: C.gold,
                  opacity: copy,
                  transform: `translateX(${(copy - 1) * 20}px)`,
                }}
              >
                →
              </div>
            </div>
          </div>
        </AbsoluteFill>

        <AbsoluteFill style={{ opacity: strip }}>
          <Caption at={40} bottom={150}>
            Persistence. Next 5 min = last 5 min.
          </Caption>
        </AbsoluteFill>
        <Caption at={176}>Quiet selections and new lines are scored as zero.</Caption>

        <AbsoluteFill style={{ paddingLeft: 120, paddingTop: 210, opacity: 1 - strip }}>
          <Rise at={110}>
            <div style={{ fontSize: 22, color: C.muted, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 22 }}>
              where it is blind
            </div>
          </Rise>
          <div style={{ display: "flex", gap: 34 }}>
            <Blind
              at={116}
              title="Quiet last bucket"
              why="lag₁ = 0. Money arrives anyway."
              persist={0}
              actual={150}
              zero
            />
            <Blind
              at={132}
              title="A goal"
              why="lag₁ is pre-goal money. The jump after it is missed."
              persist={90}
              actual={220}
              zero={false}
            />
            <Blind
              at={148}
              title="The line just moved"
              why="The new line has no last bucket of its own."
              persist={0}
              actual={170}
              zero
            />
          </div>
        </AbsoluteFill>
      </Ground>
    </SceneFade>
  );
};
