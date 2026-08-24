import { access } from "node:fs/promises";
import path from "node:path";
import { runCommand } from "./process.js";
import type { SpotifyTrack } from "./types.js";
import { findRepositoryRoot } from "./util.js";

async function fileExists(filePath: string): Promise<boolean> {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function resolvePython(repositoryRoot: string): Promise<string> {
  if (process.env.PYTHON_BIN) {
    return process.env.PYTHON_BIN;
  }

  const virtualEnvironmentPython = process.platform === "win32"
    ? path.join(repositoryRoot, "pipeline", ".venv", "Scripts", "python.exe")
    : path.join(repositoryRoot, "pipeline", ".venv", "bin", "python");
  if (await fileExists(virtualEnvironmentPython)) {
    return virtualEnvironmentPython;
  }
  return process.platform === "win32" ? "python" : "python3";
}

export async function runPythonPipeline(
  outputDirectory: string,
  sourceAudio: string,
  track: SpotifyTrack,
  language: string | undefined,
  whisperModel: string,
): Promise<void> {
  const repositoryRoot = findRepositoryRoot();
  const python = await resolvePython(repositoryRoot);
  const script = path.join(repositoryRoot, "pipeline", "run_pipeline.py");
  const args = [
    script,
    "--input",
    sourceAudio,
    "--output",
    outputDirectory,
    "--artist",
    track.artists.join(", "),
    "--title",
    track.title,
    "--duration",
    String(track.durationMs / 1000),
    "--whisper-model",
    whisperModel,
  ];
  if (language) {
    args.push("--language", language);
  }

  console.log("\nRunning the Python audio and lyric pipeline...\n");
  await runCommand(python, args, { cwd: repositoryRoot, inheritOutput: true });
}
