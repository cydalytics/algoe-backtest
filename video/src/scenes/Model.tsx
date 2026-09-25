import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";

import { C, MONO } from "../theme";
import { Caption, Chip, Formula, Ground, mix, MoneyBar, ramp, SceneFade, SceneTitle } from "../ui";

const ALPHAS = ["0", "0.25", "0.5", "0.75", "1"];

const Block: React.FC<{ at: number; tone: string; name: string; body: string; how: string }> = ({ at, tone, name, body, how }) => {
  const frame = useCurrentFrame();
  const t = ramp(frame, at, at + 18);
  return (
    <div
      style={{
        opacity: t,
        transform: `translateX(${(1 - t) * -24}px)`,
        borderLeft: `3px solid ${tone}`,
        background: C.panel,
        padding: "20px 28px",
        width: 980,
      }}
    >
      <Formula size={40}>
        <span style={{ color: tone }}>{name}</span>
        {"  =  "}
        {body}
      </Formula>
      <div style={{ fontFamily: MONO, fontSize: 22, color: C.muted, marginTop: 10 }}>{how}</div>
    </div>
  );
};

export const Model: React.FC = () => {
  const frame = useCurrentFrame();
  const blend = ramp(frame, 120, 146);
  // α walks across its grid once, then settles
  const walk = Math.min(4, Math.max(0, Math.floor((frame - 160) / 14)));
  const alphaIdx = frame < 160 ? -1 : walk;
  const demo = ramp(frame, 236, 262);
  const pFill = ramp(frame, 262, 300);
  const yGrow = ramp(frame, 296, 330);

  return (
    <SceneFade>
      <Ground>
        <SceneTitle index="03" title="The model" />

        <AbsoluteFill style={{ paddingLeft: 120, paddingTop: 196 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            <Block
              at={8}
              tone={C.iris}
              name="p "
              body="P(money in the next bucket)"
              how="ridge-logistic · Newton · CPU"
            />
            <Block
              at={50}
              tone={C.iris}
              name="μ "
              body="E[ log(1 + turnover) | money ]"
              how="ridge · CPU"
            />
            <div
              style={{
                opacity: blend,
                transform: `translateY(${(1 - blend) * 12}px)`,
                borderLeft: `3px solid ${C.gold}`,
                padding: "22px 28px",
                width: 980,
                background: "rgba(212,168,75,0.06)",
              }}
            >
              <Formula size={34}>
                {"ŷ  =  "}
                <span style={{ color: C.gold }}>α</span>
                {" · p · (exp(μ) − 1)  +  (1 − "}
                <span style={{ color: C.gold }}>α</span>
                {") · lag₁"}
              </Formula>
              <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 22 }}>
                <span style={{ fontFamily: MONO, fontSize: 24, color: C.muted, marginRight: 8 }}>α ∈</span>
                {ALPHAS.map((a, i) => (
                  <Chip key={a} tone={i === alphaIdx ? "gold" : "plain"} style={{ fontFamily: MONO }}>
                    {a}
                  </Chip>
                ))}
                <span style={{ fontSize: 22, color: C.muted, marginLeft: 16 }}>
                  <span style={{ fontFamily: MONO, color: C.gold }}>α = 0</span> is persistence
                </span>
              </div>
            </div>
          </div>
        </AbsoluteFill>

        {/* a quiet row: persistence stays at zero, the model does not */}
        <div
          style={{
            position: "absolute",
            right: 120,
            top: 196,
            width: 560,
            opacity: demo,
            border: `1px solid ${C.hair}`,
            borderRadius: 4,
            padding: "22px 28px",
            background: C.panel,
          }}
        >
          <div style={{ fontSize: 20, color: C.muted, letterSpacing: "0.14em", textTransform: "uppercase" }}>a quiet row</div>
          <div style={{ fontFamily: MONO, fontSize: 30, marginTop: 10 }}>
            lag₁ = <span style={{ color: C.gold }}>0</span>
          </div>
          <div style={{ marginTop: 22 }}>
            <div style={{ fontFamily: MONO, fontSize: 20, color: C.muted, marginBottom: 8 }}>p</div>
            <div style={{ height: 12, width: 480, background: C.faint, borderRadius: 1 }}>
              <div style={{ height: 12, width: 480 * mix(0, 0.64, pFill), background: C.iris, borderRadius: 1 }} />
            </div>
            <div style={{ fontFamily: MONO, fontSize: 20, color: C.muted, margin: "18px 0 8px" }}>exp(μ) − 1 · from the line’s own level</div>
            <div style={{ height: 12, width: 480, background: C.faint, borderRadius: 1 }}>
              <div style={{ height: 12, width: 480 * mix(0, 0.72, pFill), background: C.iris, borderRadius: 1 }} />
            </div>
          </div>
          <div style={{ display: "flex", gap: 60, marginTop: 10, justifyContent: "center" }}>
            <MoneyBar h={0} color={C.gold} width={90} label="persistence · 0" />
            <MoneyBar h={170 * yGrow} color={C.iris} width={90} label="ŷ" />
          </div>
        </div>

        <Caption at={300}>NumPy only. No GPU. The parquet never leaves the machine.</Caption>
      </Ground>
    </SceneFade>
  );
};
