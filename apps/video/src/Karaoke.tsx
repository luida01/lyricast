import {
  AbsoluteFill,
  Audio,
  Img,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { useMemo } from "react";
import { deriveKaraokeColors, rgbToHex } from "./colors";

export interface Word {
  text: string;
  start: number | null;
  end: number | null;
  estimated?: boolean;
  line?: number;
  index?: number;
}

export interface Line {
  text: string;
  words: Word[];
  start: number | null;
  end: number | null;
}

export interface Sync {
  language?: string;
  lyricsProvider?: string;
  timingSource?: string;
  duration: number;
  words: Word[];
  lines: Line[];
  schemaVersion?: number;
  [key: string]: unknown;
}

export type BackgroundKind = "cover-blur" | "gradient" | "solid";

export interface Style {
  fontFamily: string;
  color: string;
  highlightColor: string;
  background: BackgroundKind;
  backgroundColor: string;
  textShadow: boolean;
  autoColor: boolean;
}

export interface KaraokeProps {
  audioSrc: string;
  coverSrc: string;
  sync: Sync;
  style: Style;
  baseColor: string;
}

const DEFAULT_STYLE: Style = {
  fontFamily: "Inter, system-ui, sans-serif",
  color: "#ffffff",
  highlightColor: "#ffd54a",
  background: "cover-blur",
  backgroundColor: "#0b1020",
  textShadow: true,
  autoColor: true,
};

const SLOT_HEIGHT = 170;
const WINDOW_BEFORE = 1;
const WINDOW_AFTER = 3;
const TRANSITION = 0.45;

function easeInOut(x: number): number {
  return x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2;
}

function WordView({
  word,
  frame,
  fps,
  color,
  highlight,
  size,
  active,
  textShadow,
}: {
  word: Word;
  frame: number;
  fps: number;
  color: string;
  highlight: string;
  size: number;
  active: boolean;
  textShadow: boolean;
}) {
  const start = word.start ?? 0;
  const end = word.end ?? start + 0.4;
  const startFrame = start * fps;
  const endFrame = end * fps;
  const isActive = active && frame >= startFrame && frame < endFrame;
  const enter = frame - startFrame;
  const pop = spring({
    frame: Math.max(0, enter),
    fps,
    config: { damping: 13, stiffness: 120, mass: 0.6 },
  });
  const scale = isActive ? interpolate(pop, [0, 1], [1.18, 1]) : 1;

  return (
    <span
      style={{
        color: isActive ? highlight : color,
        fontSize: size,
        margin: "0 0.28em",
        display: "inline-block",
        transform: `scale(${scale})`,
        fontWeight: isActive ? 800 : 500,
        textShadow: textShadow ? "0 2px 10px rgba(0,0,0,0.65)" : undefined,
        whiteSpace: "pre",
      }}
    >
      {word.text}
    </span>
  );
}

function LineView({
  line,
  frame,
  fps,
  color,
  highlight,
  textShadow,
  size,
  active,
}: {
  line: Line;
  frame: number;
  fps: number;
  color: string;
  highlight: string;
  textShadow: boolean;
  size: number;
  active: boolean;
}) {
  if (line.words.length === 0) {
    return <div style={{ fontSize: size, color, textAlign: "center" }}>{line.text}</div>;
  }
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        justifyContent: "center",
        maxWidth: 1600,
        textAlign: "center",
      }}
    >
      {line.words.map((word, index) => (
        <WordView
          key={index}
          word={word}
          frame={frame}
          fps={fps}
          color={color}
          highlight={highlight}
          textShadow={textShadow}
          size={size}
          active={active}
        />
      ))}
    </div>
  );
}

function Background({
  style,
  coverSrc,
  bgColor,
}: {
  style: Style;
  coverSrc: string;
  bgColor: string;
}) {
if (style.background === "cover-blur") {
    return (
      <AbsoluteFill style={{ overflow: "hidden" }}>
        <Img
          src={coverSrc}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            filter: "blur(44px)",
            transform: "scale(1.25)",
          }}
        />
        <AbsoluteFill style={{ backgroundColor: bgColor, opacity: 0.5 }} />
      </AbsoluteFill>
    );
  }
  if (style.background === "gradient") {
    return (
      <AbsoluteFill
        style={{ backgroundImage: `linear-gradient(135deg, ${bgColor}, #000000)` }}
      />
    );
  }
  return <AbsoluteFill style={{ backgroundColor: bgColor }} />;
}

export const Karaoke: React.FC<KaraokeProps> = ({ audioSrc, coverSrc, sync, style, baseColor }) => {
  const frame = useCurrentFrame();
  const { fps, height, durationInFrames } = useVideoConfig();
  const resolved: Style = { ...DEFAULT_STYLE, ...style };

  const colors = useMemo(() => {
    if (resolved.autoColor) {
      return deriveKaraokeColors(baseColor);
    }
    return {
      background: resolved.backgroundColor,
      text: resolved.color,
      highlight: resolved.highlightColor,
    };
  }, [resolved.autoColor, resolved.backgroundColor, resolved.color, resolved.highlightColor, baseColor]);

  const t = frame / fps;
  const duration = sync.duration || durationInFrames / fps;

  const lineTimings = useMemo(
    () =>
      sync.lines.map((line, index) => {
        const start = line.start ?? (sync.lines[index - 1]?.end ?? 0);
        const end = line.end ?? (sync.lines[index + 1]?.start ?? start + 2);
        return { ...line, start, end };
      }),
    [sync.lines],
  );

  const activeNow = lineTimings.findIndex((line) => t >= line.start && t < line.end);

  // Effective per-word timings: keep real WhisperX timings, but fill any word still
  // marked estimated with a proportional slot across its line so the karaoke still
  // highlights word-by-word instead of all at once.
  const effectiveLines = useMemo(
    () =>
      lineTimings.map((line) => {
        const start = line.start ?? 0;
        const end = line.end ?? start + Math.max(1.0, line.words.length * 0.4);
        const span = Math.max(0.3, end - start);
        const n = Math.max(1, line.words.length);
        const words = line.words.map((word, index) => {
          if (word.start != null && !word.estimated) {
            return word;
          }
          const wordStart = start + (index / n) * span;
          const wordEnd = start + ((index + 1) / n) * span;
          return { ...word, start: wordStart, end: Math.max(wordEnd, wordStart + 0.08) };
        });
        return { ...line, words };
      }),
    [lineTimings],
  );

  // Pure-function scroll position (no per-frame state): glides smoothly between
  // lines and, crucially, keeps moving across instrumental gaps instead of freezing.
  const idxFloat = useMemo(() => {
    if (activeNow >= 0) {
      const line = lineTimings[activeNow];
      const from = activeNow > 0 ? activeNow - 1 : activeNow;
      const frac = Math.min(1, Math.max(0, (t - line.start) / TRANSITION));
      return from + (activeNow - from) * easeInOut(frac);
    }
    const upcoming = lineTimings.findIndex((line) => t < line.start);
    if (upcoming < 0) {
      return Math.max(0, lineTimings.length - 1);
    }
    const prev = Math.max(0, upcoming - 1);
    const gapStart = lineTimings[prev].end;
    const gapEnd = lineTimings[upcoming].start;
    const frac = gapEnd > gapStart ? Math.min(1, Math.max(0, (t - gapStart) / (gapEnd - gapStart))) : 1;
    return prev + (upcoming - prev) * frac;
  }, [activeNow, lineTimings, t]);

  const translateY = height / 2 - idxFloat * SLOT_HEIGHT - SLOT_HEIGHT / 2;

  const windowStart = Math.max(0, Math.floor(idxFloat) - WINDOW_BEFORE);
  const windowEnd = Math.min(lineTimings.length, windowStart + WINDOW_BEFORE + WINDOW_AFTER + 1);

  const progress = Math.min(1, Math.max(0, t / duration));

return (
    <AbsoluteFill style={{ backgroundColor: colors.background }}>
      <Background style={resolved} coverSrc={coverSrc} bgColor={colors.background} />
      <Audio src={audioSrc} />
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
        <div style={{ position: "relative", width: "100%", height, overflow: "hidden" }}>
          <div
            style={{
              position: "absolute",
              left: 0,
              right: 0,
              top: 0,
              transform: `translateY(${translateY}px)`,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
            }}
          >
            {effectiveLines.slice(windowStart, windowEnd).map((line, i) => {
              const actualIndex = windowStart + i;
              const isActive = actualIndex === activeNow && activeNow >= 0;
              const dist = Math.abs(actualIndex - idxFloat);
              const size = isActive ? 92 : Math.max(46, 74 - dist * 10);
              const opacity = isActive ? 1 : Math.max(0.3, 0.62 - dist * 0.14);
              return (
                <div
                  key={actualIndex}
                  style={{
                    height: SLOT_HEIGHT,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    width: "100%",
                    opacity,
                  }}
                >
                  <LineView
                    line={line}
                    frame={frame}
                    fps={fps}
                    color={colors.text}
                    highlight={colors.highlight}
                    textShadow={resolved.textShadow}
                    size={size}
                    active={isActive}
                  />
                </div>
              );
            })}
          </div>
        </div>
      </AbsoluteFill>
      {activeNow < 0 ? (
        <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: 70 }}>
          <div style={{ fontSize: 44, color: colors.highlight, opacity: 0.85 }}>♪</div>
        </AbsoluteFill>
      ) : null}
      <AbsoluteFill style={{ justifyContent: "flex-end" }}>
        <div style={{ height: 6, width: "100%", backgroundColor: "rgba(255,255,255,0.18)" }}>
          <div
            style={{
              height: "100%",
              width: `${progress * 100}%`,
              backgroundColor: colors.highlight,
            }}
          />
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
