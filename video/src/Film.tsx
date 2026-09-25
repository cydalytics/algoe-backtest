import React from "react";
import { AbsoluteFill, Audio, Series, staticFile } from "remotion";

import { Baseline } from "./scenes/Baseline";
import { Clock } from "./scenes/Clock";
import { End } from "./scenes/End";
import { Features } from "./scenes/Features";
import { Judgement } from "./scenes/Judgement";
import { Model } from "./scenes/Model";
import { Title } from "./scenes/Title";
import { Unit } from "./scenes/Unit";
import { C, SCENES } from "./theme";

export const Film: React.FC = () => (
  <AbsoluteFill style={{ background: C.bg }}>
    {/* peaks at −20 dBFS; see scripts/make_pulse.py */}
    <Audio src={staticFile("pulse.wav")} />
    <Series>
      <Series.Sequence durationInFrames={SCENES.title}>
        <Title />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENES.unit}>
        <Unit />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENES.baseline}>
        <Baseline />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENES.model}>
        <Model />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENES.features}>
        <Features />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENES.clock}>
        <Clock />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENES.judgement}>
        <Judgement />
      </Series.Sequence>
      <Series.Sequence durationInFrames={SCENES.end}>
        <End />
      </Series.Sequence>
    </Series>
  </AbsoluteFill>
);
