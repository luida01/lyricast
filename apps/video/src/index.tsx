import { Composition, registerRoot } from "remotion";
import { z } from "zod";
import { Karaoke } from "./Karaoke";
import { currentSong } from "./current-song";

const FPS = 30;
const WIDTH = 1920;
const HEIGHT = 1080;

const styleSchema = z.object({
  fontFamily: z.string(),
  color: z.string(),
  highlightColor: z.string(),
  background: z.enum(["cover-blur", "gradient", "solid"]),
  backgroundColor: z.string(),
  textShadow: z.boolean(),
  autoColor: z.boolean(),
});

const propsSchema = z.object({
  audioSrc: z.string(),
  coverSrc: z.string(),
  sync: z.any(),
  style: styleSchema,
  baseColor: z.string(),
});

const durationInFrames = Math.max(1, Math.ceil((currentSong.sync.duration || 1) * FPS));

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Karaoke"
      component={Karaoke as React.ComponentType<unknown>}
      durationInFrames={durationInFrames}
      fps={FPS}
      width={WIDTH}
      height={HEIGHT}
      defaultProps={currentSong}
      schema={propsSchema}
    />
  );
};

registerRoot(RemotionRoot);
