import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import jpeg from "jpeg-js";
import { boostSaturation, rgbToHex } from "../src/colors";

const videoDir = path.resolve(fileURLToPath(import.meta.url), "..", "..");
const repoRoot = path.resolve(videoDir, "..", "..");

const slug = process.argv[2] || "kendrick-lamar-humble";
const songDir = path.join(repoRoot, "out", slug);
const publicDir = path.join(videoDir, "public");

if (!fs.existsSync(songDir)) {
  console.error(`No song directory found at ${songDir}`);
  process.exit(1);
}

fs.mkdirSync(publicDir, { recursive: true });

for (const file of ["instrumental.wav", "cover.jpg"]) {
  const source = path.join(songDir, file);
  const target = path.join(publicDir, file);
  if (fs.existsSync(source)) {
    fs.copyFileSync(source, target);
  } else {
    console.warn(`Missing ${file} in ${songDir}; skipping copy`);
  }
}

const syncPath = path.join(songDir, "sync.json");
if (!fs.existsSync(syncPath)) {
  console.error(`Missing sync.json in ${songDir}`);
  process.exit(1);
}
const sync = JSON.parse(fs.readFileSync(syncPath, "utf-8"));

const coverPath = path.join(songDir, "cover.jpg");
let baseColor = "#0b1020";
if (fs.existsSync(coverPath)) {
  try {
    const raw = jpeg.decode(fs.readFileSync(coverPath), { useTArray: true });
    const data = raw.data;
    const dominant = dominantColor(data);
    baseColor = boostSaturation(dominant, 0.55);
    console.log(`Sampled cover dominant color: ${dominant} -> ${baseColor}`);
  } catch (err) {
    console.warn(`Could not decode cover.jpg: ${err}`);
  }
} else {
  console.warn(`No cover.jpg in ${songDir}; using default base color`);
}

function dominantColor(data: Uint8Array): string {
  const step = 6;
  const buckets = new Map<number, { r: number; g: number; b: number; n: number }>();
  for (let i = 0; i < data.length; i += 4 * step) {
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    if (max < 25 || min > 235) continue;
    const key = ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4);
    const bucket = buckets.get(key) ?? { r: 0, g: 0, b: 0, n: 0 };
    bucket.r += r;
    bucket.g += g;
    bucket.b += b;
    bucket.n += 1;
    buckets.set(key, bucket);
  }
  let best: { r: number; g: number; b: number; n: number } | null = null;
  let bestScore = -1;
  for (const bucket of buckets.values()) {
    const r = bucket.r / bucket.n;
    const g = bucket.g / bucket.n;
    const b = bucket.b / bucket.n;
    const mx = Math.max(r, g, b);
    const mn = Math.min(r, g, b);
    const chroma = (mx - mn) / 255;
    const score = bucket.n + chroma * 200;
    if (score > bestScore) {
      bestScore = score;
      best = bucket;
    }
  }
  if (!best) return "#333333";
  return rgbToHex(best.r / best.n, best.g / best.n, best.b / best.n);
}

const defaultStyle = {
  fontFamily: "Inter, system-ui, sans-serif",
  color: "#ffffff",
  highlightColor: "#ffd54a",
  background: "cover-blur",
  backgroundColor: "#0b1020",
  textShadow: true,
  autoColor: true,
};

const contents = `import { staticFile } from "remotion";
import type { KaraokeProps } from "./Karaoke";

export const currentSong: KaraokeProps = {
  audioSrc: staticFile("instrumental.wav"),
  coverSrc: staticFile("cover.jpg"),
  sync: ${JSON.stringify(sync)},
  style: ${JSON.stringify(defaultStyle, null, 2)},
  baseColor: ${JSON.stringify(baseColor)},
};
`;

fs.writeFileSync(path.join(videoDir, "src", "current-song.ts"), contents);
console.log(`Prepared "${slug}" for preview/render (${sync.lines.length} lines).`);
