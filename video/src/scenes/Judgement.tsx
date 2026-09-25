import React from "react";
import { useCurrentFrame } from "remotion";

import { C, MONO } from "../theme";
import { Blank, Formula, Ground, MoneyBar, ramp, Rise, SceneFade, SceneTitle } from "../ui";

const Read: React.FC<{ at: number; n: string; head: React.ReactNode; sub: string }> = ({ at, n, head, sub }) => (
  <Rise at={at}>
    <div style={{ display: "flex", gap: 22, alignItems: "baseline" }}>
      <span style={{ fontFamily: MONO, fontSize: 20, color: C.gold, width: 26 }}>{n}</span>
      <div>
        <Formula size={32}>{head}</Formula>
        <div style={{ fontSize: 22, color: C.muted, marginTop: 6 }}>{sub}</div>
      </div>
    </div>
  </Rise>
);

export const Judgement: React.FC = () => {
  const frame = useCurrentFrame();
  const cut = ramp(frame, 236, 262);
  return (
    <SceneFade>
      <Ground>
        <SceneTitle index="06" title="The judgement" />
        <Rise at={8} style={{ position: "absolute", left: 120, top: 150, display: "flex", gap: 36, fontSize: 24 }}>
          <span style={{ color: C.gold }}>A · persistence</span>
          <span style={{ color: C.iris }}>B · hurdle</span>
          <span style={{ color: C.muted }}>same rows</span>
        </Rise>

        {/* left: the three reads, in order */}
        <div style={{ position: "absolute", left: 120, top: 240, width: 900, display: "flex", flexDirection: "column", gap: 40 }}>
          <Read at={16} n="1" head="WAPE = Σ|ŷ − y| / Σy" sub="Money missed, not average error per row." />
          <Read at={70} n="2" head="Skill = 1 − WAPE_model / WAPE_persist" sub="How much of persistence’s miss B removes." />
          <Read
            at={124}
            n="3"
            head="Days, not rows, are the sample."
            sub="Paired daily difference · Diebold–Mariano · Newey–West, one lag."
          />
          <Rise at={170}>
            <div
              style={{
                marginLeft: 48,
                border: `1px solid ${C.neg}`,
                color: C.ink,
                padding: "14px 20px",
                fontSize: 24,
                width: "fit-content",
              }}
            >
              Under 10 scored days, the window is too short to call.
            </div>
          </Rise>
        </div>

        {/* right: the numbers this film does not have yet */}
        <div style={{ position: "absolute", left: 1100, top: 240, display: "flex", flexDirection: "column", gap: 24 }}>
          <Rise at={40}>
            <div style={{ fontSize: 20, color: C.muted, letterSpacing: "0.14em", textTransform: "uppercase" }}>hold-out</div>
          </Rise>
          <Rise at={46}>
            <div style={{ display: "flex", gap: 24 }}>
              <Blank label="WAPE · persistence" width={320} />
              <Blank label="WAPE · model" width={320} />
            </div>
          </Rise>
          <Rise at={96}>
            <Blank label="skill" width={320} />
          </Rise>
          <Rise at={130}>
            <div style={{ display: "flex", alignItems: "flex-end", gap: 16 }}>
              <Blank label="days the model won" width={250} />
              <span style={{ fontSize: 24, color: C.muted, paddingBottom: 18 }}>of</span>
              <Blank label="scored days" width={250} />
            </div>
          </Rise>

          {/* one cut: quiet rows vs the rest */}
          <div style={{ opacity: cut, marginTop: 8 }}>
            <div style={{ fontSize: 20, color: C.muted, letterSpacing: "0.14em", textTransform: "uppercase", marginBottom: 6 }}>
              one cut · WAPE
            </div>
            <div style={{ display: "flex", gap: 80, alignItems: "flex-end", transform: "scale(0.8)", transformOrigin: "left bottom" }}>
              <div style={{ display: "flex", gap: 16, alignItems: "flex-end" }}>
                <MoneyBar h={220 * cut} color={C.gold} width={80} label="100%" />
                <MoneyBar h={220} color={C.iris} width={80} dashed label="PLACEHOLDER" />
              </div>
              <div style={{ display: "flex", gap: 16, alignItems: "flex-end" }}>
                <MoneyBar h={220} color={C.gold} width={80} dashed label="PLACEHOLDER" />
                <MoneyBar h={220} color={C.iris} width={80} dashed label="PLACEHOLDER" />
              </div>
            </div>
            <div style={{ display: "flex", gap: 80, fontFamily: MONO, fontSize: 22, marginTop: 4 }}>
              <span style={{ width: 176 * 0.8 + 16, color: C.ink }}>lag₁ = 0</span>
              <span style={{ color: C.ink }}>lag₁ &gt; 0</span>
            </div>
            <div style={{ fontSize: 22, color: C.muted, marginTop: 14 }}>
              Persistence is 100% WAPE on the quiet rows. That is the bar.
            </div>
          </div>
        </div>
      </Ground>
    </SceneFade>
  );
};
