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
    let r = 0;
    let g = 0;
    let b = 0;
    const n = data.length / 4;
    for (let i = 0; i < data.length; i += 4) {
      r += data[i];
      g += data[i + 1];
      b += data[i + 2];
    }
    const average = rgbToHex(r / n, g / n, b / n);
    baseColor = boostSaturation(average, 0.5);
    console.log(`Sampled cover base color: ${average} -> ${baseColor}`);
  } catch (err) {
    console.warn(`Could not decode cover.jpg: ${err}`);
  }
} else {
  console.warn(`No cover.jpg in ${songDir}; using default base color`);
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
