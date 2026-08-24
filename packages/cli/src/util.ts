import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

export function normalizeText(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

export function slugify(value: string): string {
  const slug = normalizeText(value).replace(/\s+/g, "-");
  return slug || "untitled";
}

export function formatDuration(durationSeconds: number | undefined): string {
  if (durationSeconds === undefined || !Number.isFinite(durationSeconds)) {
    return "unknown";
  }

  const minutes = Math.floor(durationSeconds / 60);
  const seconds = Math.round(durationSeconds % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

export async function ensureDirectory(directory: string): Promise<void> {
  await mkdir(directory, { recursive: true });
}

export async function writeJson(filePath: string, value: unknown): Promise<void> {
  await writeFile(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

export function findRepositoryRoot(start = process.cwd()): string {
  let current = path.resolve(start);

  while (true) {
    if (current.endsWith(`${path.sep}lyricast`) || path.basename(current) === "lyricast") {
      return current;
    }

    const parent = path.dirname(current);
    if (parent === current) {
      return path.resolve(start);
    }
    current = parent;
  }
}
