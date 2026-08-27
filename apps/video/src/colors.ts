export type RGB = [number, number, number];

export function hexToRgb(hex: string): RGB {
  const h = hex.replace("#", "").trim();
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const num = parseInt(full, 16);
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255];
}

export function rgbToHex(r: number, g: number, b: number): string {
  const clamp = (v: number) => Math.max(0, Math.min(255, Math.round(v)));
  const c = (v: number) => clamp(v).toString(16).padStart(2, "0");
  return `#${c(r)}${c(g)}${c(b)}`;
}

export function relativeLuminance([r, g, b]: RGB): number {
  const channel = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrastRatio(a: string, b: string): number {
  const la = relativeLuminance(hexToRgb(a));
  const lb = relativeLuminance(hexToRgb(b));
  const light = Math.max(la, lb);
  const dark = Math.min(la, lb);
  return (light + 0.05) / (dark + 0.05);
}

function hsvToHex(h: number, s: number, v: number): string {
  const c = v * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - c;
  let r = 0;
  let g = 0;
  let b = 0;
  if (h < 60) [r, g, b] = [c, x, 0];
  else if (h < 120) [r, g, b] = [x, c, 0];
  else if (h < 180) [r, g, b] = [0, c, x];
  else if (h < 240) [r, g, b] = [0, x, c];
  else if (h < 300) [r, g, b] = [x, 0, c];
  else [r, g, b] = [c, 0, x];
  return rgbToHex((r + m) * 255, (g + m) * 255, (b + m) * 255);
}

function hueOf(hex: string): number {
  const [r, g, b] = hexToRgb(hex);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  if (d === 0) return 0;
  let h: number;
  if (max === r) h = ((g - b) / d) % 6;
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return (h * 60 + 360) % 360;
}

export function harmonize(baseHex: string, mode: "complementary" | "analogous" = "complementary"): string {
  const [r, g, b] = hexToRgb(baseHex);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  const h = hueOf(baseHex);
  const s = max === 0 ? 0 : d / max;
  const v = max / 255;
  const h2 = mode === "complementary" ? (h + 180) % 360 : (h + 30) % 360;
  return hsvToHex(h2, Math.max(0.55, s), Math.min(1, v * 1.1));
}

export function pickReadableText(bgHex: string): string {
  return relativeLuminance(hexToRgb(bgHex)) > 0.5 ? "#0a0a0a" : "#ffffff";
}

export function darken(hex: string, amount: number): string {
  const [r, g, b] = hexToRgb(hex);
  return rgbToHex(r * (1 - amount), g * (1 - amount), b * (1 - amount));
}

function hslToHex(h: number, s: number, l: number): string {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  let r = 0;
  let g = 0;
  let b = 0;
  if (h < 60) [r, g, b] = [c, x, 0];
  else if (h < 120) [r, g, b] = [x, c, 0];
  else if (h < 180) [r, g, b] = [0, c, x];
  else if (h < 240) [r, g, b] = [0, x, c];
  else if (h < 300) [r, g, b] = [x, 0, c];
  else [r, g, b] = [c, 0, x];
  return rgbToHex((r + m) * 255, (g + m) * 255, (b + m) * 255);
}

export function boostSaturation(hex: string, target: number): string {
  const [r, g, b] = hexToRgb(hex);
  const max = Math.max(r, g, b) / 255;
  const min = Math.min(r, g, b) / 255;
  const l = (max + min) / 2;
  const delta = max - min;
  let h = 0;
  if (delta !== 0) {
    const rr = (max - r / 255) / delta;
    const gg = (max - g / 255) / delta;
    const bb = (max - b / 255) / delta;
    if (max === r / 255) h = bb - gg;
    else if (max === g / 255) h = 2 + rr - bb;
    else h = 4 + gg - rr;
    h = (h * 60 + 360) % 360;
  }
  const s = delta === 0 ? 0 : delta / (1 - Math.abs(2 * l - 1));
  const clampedL = Math.min(0.55, Math.max(0.3, l));
  return hslToHex(h, Math.max(s, target), clampedL);
}

export interface KaraokeColors {
  background: string;
  text: string;
  highlight: string;
}

export function deriveKaraokeColors(baseHex: string): KaraokeColors {
  const background = darken(baseHex, 0.45);
  const text = pickReadableText(background);
  const candidate = harmonize(baseHex, "complementary");
  let highlight = candidate;
  if (contrastRatio(highlight, background) < 3 || contrastRatio(highlight, text) < 2) {
    highlight = text === "#ffffff" ? "#ffd54a" : "#4fc3f7";
  }
  return { background, text, highlight };
}
