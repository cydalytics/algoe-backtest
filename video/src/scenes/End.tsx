import React from "react";
import { AbsoluteFill } from "remotion";

import { C } from "../theme";
import { Ground, Rise, SceneFade } from "../ui";

export const End: React.FC = () => (
  <SceneFade>
    <Ground>
      <AbsoluteFill style={{ justifyContent: "center", paddingLeft: 180 }}>
        <Rise at={4}>
          <div style={{ fontSize: 64, fontWeight: 600, letterSpacing: "-0.02em" }}>Scored in the backtest.</div>
        </Rise>
        <Rise at={14}>
          <div style={{ fontSize: 64, fontWeight: 600, letterSpacing: "-0.02em", color: C.muted, marginTop: 8 }}>
            Same rows. <span style={{ color: C.gold }}>A</span> against <span style={{ color: C.iris }}>B</span>.
          </div>
        </Rise>
      </AbsoluteFill>
    </Ground>
  </SceneFade>
);
