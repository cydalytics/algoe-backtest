import React from "react";
import { Composition } from "remotion";

import { Film } from "./Film";
import { FPS, TOTAL } from "./theme";

export const Root: React.FC = () => (
  <Composition id="TurnoverFilm" component={Film} durationInFrames={TOTAL} fps={FPS} width={1920} height={1080} />
);
