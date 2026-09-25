import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";

import { C, MONO } from "../theme";
import { Ground, ramp, SceneFade } from "../ui";

export const Title: React.FC = () => {
  const frame = useCurrentFrame();
  const a = ramp(frame, 6, 30);
  const b = ramp(frame, 22, 44);
  const c = ramp(frame, 40, 60);
  const rule = ramp(frame, 12, 50);
  return (
    <SceneFade>
      <Ground>
        <AbsoluteFill style={{ justifyContent: "center", paddingLeft: 180 }}>
          <div style={{ width: 520 * rule, height: 2, background: C.gold, marginBottom: 44 }} />
          <div
            style={{
              fontSize: 132,
              fontWeight: 600,
              letterSpacing: "-0.03em",
              opacity: a,
              transform: `translateY(${(1 - a) * 18}px)`,
            }}
          >
            Next 5 minutes.
          </div>
          <div
            style={{
              fontSize: 54,
              color: C.muted,
              marginTop: 18,
              opacity: b,
              transform: `translateY(${(1 - b) * 14}px)`,
            }}
          >
            Turnover, per selection.
          </div>
          <div
            style={{
              fontFamily: MONO,
              fontSize: 24,
              color: C.muted,
              marginTop: 64,
              letterSpacing: "0.08em",
              opacity: c,
            }}
          >
            Algo E · HKJC soccer · 5-minute book
          </div>
        </AbsoluteFill>
      </Ground>
    </SceneFade>
  );
};
