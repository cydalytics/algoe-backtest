import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";

import { C, FPS, MONO } from "../theme";
import { Caption, Chip, Ground, ramp, Rise, SceneFade, SceneTitle } from "../ui";

type Item = { name: string; v: number };
type Col = { n: string; title: string; items: Item[]; note?: string; extra?: "bestline" };

// Bar lengths are drawing only. They are one row's shape, not values to read.
const COLS: Col[] = [
  {
    n: "1",
    title: "Memory",
    items: [
      { name: "log(1+lag₁)", v: 0.7 },
      { name: "lag₂", v: 0.55 },
      { name: "lag₃", v: 0.45 },
      { name: "1st difference", v: 0.3 },
      { name: "2nd difference", v: 0.2 },
      { name: "lag₁ / lag₂", v: 0.5 },
    ],
  },
  {
    n: "2",
    title: "Mix",
    items: [
      { name: "share of line", v: 0.6 },
      { name: "share of pool", v: 0.42 },
      { name: "share of match", v: 0.22 },
      { name: "Δ share", v: 0.18 },
    ],
  },
  {
    n: "3",
    title: "The line",
    items: [
      { name: "is best line", v: 1 },
      { name: "distance from best", v: 0.25 },
      { name: "line − TG or SUP", v: 0.35 },
      { name: "best line moved", v: 0 },
    ],
    extra: "bestline",
    note: "A line that disappeared has no row.",
  },
  {
    n: "4",
    title: "The board",
    items: [
      { name: "other matches live", v: 0.55 },
      { name: "match share of book", v: 0.3 },
      { name: "hour", v: 0.65 },
      { name: "weekday", v: 0.5 },
    ],
  },
  {
    n: "5",
    title: "History",
    items: [
      { name: "pool mean", v: 0.5 },
      { name: "hour mean", v: 0.45 },
      { name: "league mean", v: 0.6 },
      { name: "hot vs mean", v: 0.35 },
    ],
    note: "Finished days only. Walk-forward.",
  },
  {
    n: "6",
    title: "Shock & ticket",
    items: [
      { name: "since last goal", v: 0.2 },
      { name: "since last corner", v: 0.35 },
      { name: "since last yellow", v: 0.6 },
      { name: "tickets last bucket", v: 0.4 },
      { name: "turnover / tickets", v: 0.55 },
    ],
    note: "No customer id in the feed.",
  },
];

const INTRO = 2 * FPS;
const BEAT = 1.5 * FPS;
const COL_W = 262;
// frames into the line column's beat when the reference line changes
const FLIP = 30;

/** Three lines, each with its two sides' probabilities. Closest pair = best. */
const BestLine: React.FC<{ at: number; flip: number }> = ({ at, flip }) => {
  const frame = useCurrentFrame();
  const t = ramp(frame, at, at + 16);
  const f = ramp(frame, flip, flip + 10);
  const lines = [
    { l: "ℓ₁", a: 0.66 + (0.5 - 0.66) * f },
    { l: "ℓ₂", a: 0.51 + (0.62 - 0.51) * f },
    { l: "ℓ₃", a: 0.36 + (0.44 - 0.36) * f },
  ];
  const bestL = lines.reduce((b, x) => (Math.abs(x.a - 0.5) < Math.abs(b.a - 0.5) ? x : b)).l;
  return (
    <div style={{ marginTop: 16, opacity: t }}>
      {lines.map((x) => {
        const best = x.l === bestL;
        return (
          <div key={x.l} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <span style={{ fontFamily: MONO, fontSize: 18, width: 28, color: best ? C.gold : C.muted }}>{x.l}</span>
            <div style={{ display: "flex", width: 170, height: 10, border: `1px solid ${best ? C.gold : C.hair}` }}>
              <div style={{ width: `${x.a * 100 * t}%`, background: best ? C.gold : C.faint }} />
              <div style={{ flex: 1, background: best ? "rgba(212,168,75,0.35)" : "transparent" }} />
            </div>
            {best && <span style={{ fontSize: 16, color: C.gold }}>best</span>}
          </div>
        );
      })}
    </div>
  );
};

const Column: React.FC<{ col: Col; start: number; active: boolean }> = ({ col, start, active }) => {
  const frame = useCurrentFrame();
  const t = ramp(frame, start, start + 14);
  return (
    <div
      style={{
        width: COL_W,
        opacity: t * (active ? 1 : 0.55),
        transform: `translateY(${(1 - t) * 18}px)`,
        borderTop: `2px solid ${active ? C.gold : C.hair}`,
        paddingTop: 18,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
        <span style={{ fontFamily: MONO, fontSize: 20, color: C.gold }}>{col.n}</span>
        <span style={{ fontSize: 30, fontWeight: 600 }}>{col.title}</span>
      </div>
      <div style={{ marginTop: 18, display: "flex", flexDirection: "column", gap: 14 }}>
        {col.items.map((it, i) => {
          const d = ramp(frame, start + 6 + i * 3, start + 20 + i * 3);
          const moved = it.name === "best line moved";
          const v = moved ? ramp(frame, start + FLIP, start + FLIP + 10) : it.v;
          return (
            <div key={it.name}>
              <div style={{ fontFamily: MONO, fontSize: 19, color: active ? C.ink : C.muted }}>
                {it.name}
                {moved && (
                  <span style={{ color: v > 0.5 ? C.gold : C.muted }}>{v > 0.5 ? "  → 1" : "  = 0"}</span>
                )}
              </div>
              <div style={{ height: 6, width: 220, background: C.faint, marginTop: 6, borderRadius: 1 }}>
                <div
                  style={{
                    height: 6,
                    width: 220 * v * d,
                    background: moved && v > 0.5 ? C.gold : active ? C.iris : C.muted,
                    borderRadius: 1,
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
      {col.extra === "bestline" && <BestLine at={start + 12} flip={start + FLIP} />}
      {col.note && (
        <div style={{ marginTop: 16, fontSize: 18, color: C.muted, opacity: ramp(frame, start + 18, start + 30) }}>{col.note}</div>
      )}
    </div>
  );
};

export const Features: React.FC<{ beats?: number[] }> = ({ beats = [0, 1, 2, 3, 4, 5] }) => {
  const frame = useCurrentFrame();
  const shown = beats.map((i) => COLS[i]);
  const end = INTRO + shown.length * BEAT;
  const activeIdx = Math.min(shown.length - 1, Math.floor((frame - INTRO) / BEAT));
  const allUp = ramp(frame, end + 6, end + 24);

  return (
    <SceneFade>
      <Ground>
        <SceneTitle index="04" title="The features" />
        <Rise at={10} style={{ position: "absolute", left: 120, top: 150 }}>
          <div style={{ fontSize: 24, color: C.muted }}>One row. Every value is known at T.</div>
        </Rise>

        <AbsoluteFill style={{ paddingLeft: 120, paddingTop: 240 }}>
          <div style={{ display: "flex", gap: 28 }}>
            {shown.map((col, i) => (
              <Column key={col.n} col={col} start={INTRO + i * BEAT} active={frame < end ? i === activeIdx : true} />
            ))}
          </div>
        </AbsoluteFill>

        <div style={{ position: "absolute", left: 120, bottom: 190, opacity: allUp, display: "flex", alignItems: "center", gap: 22 }}>
          <Chip tone="dim" style={{ textDecoration: "line-through", fontSize: 24 }}>
            all-up · in-play
          </Chip>
          <span style={{ fontSize: 24, color: C.muted }}>dimmed — out of the model</span>
        </div>

        <Caption at={end + 30}>Features describe the row. The label is never one of them.</Caption>
      </Ground>
    </SceneFade>
  );
};
