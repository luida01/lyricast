export interface RawLineTiming {
  start: number | null;
  end: number | null;
}

export interface LineTiming {
  start: number;
  end: number;
}

export function normalizeLineTimings<T extends RawLineTiming>(
  lines: T[],
): (T & LineTiming)[] {
  const normalized = lines.map((line) => {
    const start =
      typeof line.start === "number" && Number.isFinite(line.start) ? line.start : 0;
    const end =
      typeof line.end === "number" && Number.isFinite(line.end) ? line.end : start;
    return { ...line, start, end };
  });
  let previousEnd = 0;
  for (const item of normalized) {
    if (item.start < previousEnd) {
      item.start = previousEnd;
    }
    if (item.end < item.start + 0.01) {
      item.end = item.start + 0.01;
    }
    previousEnd = item.end;
  }
  return normalized;
}

export function findActiveLine(lines: LineTiming[], time: number): number {
  for (let index = 0; index < lines.length; index += 1) {
    if (time >= lines[index].start && time < lines[index].end) {
      return index;
    }
  }
  return -1;
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

export function easeInOut(x: number): number {
  return x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2;
}

export function getScrollIndex(
  lines: LineTiming[],
  time: number,
  transition: number,
): number {
  if (lines.length === 0) {
    return 0;
  }
  const active = findActiveLine(lines, time);
  if (active >= 0) {
    const from = active > 0 ? active - 1 : active;
    const frac = clamp01((time - lines[active].start) / Math.max(0.01, transition));
    return from + (active - from) * easeInOut(frac);
  }
  const upcoming = lines.findIndex((line) => time < line.start);
  if (upcoming < 0) {
    return lines.length - 1;
  }
  const prev = Math.max(0, upcoming - 1);
  const gapStart = upcoming === 0 ? 0 : lines[prev].end;
  const gapEnd = upcoming === 0 ? lines[0].start : lines[upcoming].start;
  const frac = gapEnd > gapStart ? clamp01((time - gapStart) / Math.max(0.01, transition)) : 1;
  return Math.min(lines.length - 1, prev + (upcoming - prev) * frac);
}