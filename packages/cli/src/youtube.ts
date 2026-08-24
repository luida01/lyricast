import { readdir } from "node:fs/promises";
import path from "node:path";
import type { SpotifyTrack, YoutubeCandidate } from "./types.js";
import { CommandError, runCommand } from "./process.js";
import { findRepositoryRoot, formatDuration, normalizeText } from "./util.js";
import { ask } from "./prompt.js";

interface YtDlpEntry {
  id?: string;
  title?: string;
  uploader?: string;
  channel?: string;
  duration?: number;
  webpage_url?: string;
  thumbnail?: string;
}

interface YtDlpSearchResult {
  entries?: YtDlpEntry[];
}

interface YtDlpCommand {
  command: string;
  prefix: string[];
}

function isMissingExecutable(error: unknown): boolean {
  return error instanceof CommandError && error.spawnError?.code === "ENOENT";
}

async function runYtDlp(args: string[], inheritOutput = false): Promise<{ stdout: string; stderr: string }> {
  const configured = process.env.YT_DLP_BIN;
  const repositoryRoot = findRepositoryRoot();
  const virtualEnvironmentPython = process.platform === "win32"
    ? path.join(repositoryRoot, "pipeline", ".venv", "Scripts", "python.exe")
    : path.join(repositoryRoot, "pipeline", ".venv", "bin", "python");
  const pythonCommands = [
    process.env.PYTHON_BIN,
    virtualEnvironmentPython,
    process.platform === "win32" ? "python" : "python3",
  ].filter((value): value is string => Boolean(value));
  const commands: YtDlpCommand[] = configured
    ? [{ command: configured, prefix: [] }]
    : [
        { command: "yt-dlp", prefix: [] },
        ...pythonCommands.map((command) => ({ command, prefix: ["-m", "yt_dlp"] })),
      ];
  let lastError: unknown;

  for (const candidate of commands) {
    try {
      return await runCommand(candidate.command, [...candidate.prefix, ...args], { inheritOutput });
    } catch (error) {
      lastError = error;
      if (!isMissingExecutable(error) || configured) {
        throw error;
      }
    }
  }

  throw lastError instanceof Error
    ? lastError
    : new Error("yt-dlp was not found. Run pipeline/setup.ps1 first.");
}

function scoreCandidate(candidate: YoutubeCandidate, track: SpotifyTrack): number {
  const normalizedTitle = normalizeText(candidate.title);
  const searchTerms = [track.title, ...track.artists]
    .flatMap((term) => normalizeText(term).split(" "))
    .filter((term) => term.length > 1);
  const badTerms = [
    "karaoke",
    "instrumental",
    "cover",
    "live",
    "remix",
    "slowed",
    "sped up",
    "nightcore",
    "reaction",
    "tutorial",
  ];
  const positiveTerms = ["official", "audio", "music video", "lyrics", "lyric"];
  let score = 0;

  for (const term of searchTerms) {
    if (normalizedTitle.includes(term)) {
      score += 8;
    }
  }
  for (const term of badTerms) {
    if (normalizedTitle.includes(term)) {
      score -= 35;
    }
  }
  for (const term of positiveTerms) {
    if (normalizedTitle.includes(term)) {
      score += 5;
    }
  }

  if (candidate.durationSeconds !== undefined) {
    const delta = Math.abs(candidate.durationSeconds - track.durationMs / 1000);
    score += Math.max(0, 50 - delta * 3);
  }

  return score;
}

export async function searchYoutubeCandidates(track: SpotifyTrack, limit = 5): Promise<YoutubeCandidate[]> {
  const query = `${track.artists[0] ?? ""} ${track.title} official audio`.trim();
  const result = await runYtDlp([
    "--flat-playlist",
    "--dump-single-json",
    "--skip-download",
    "--no-warnings",
    "--quiet",
    `ytsearch${limit}:${query}`,
  ]);

  let payload: YtDlpSearchResult;
  try {
    payload = JSON.parse(result.stdout) as YtDlpSearchResult;
  } catch {
    throw new Error("yt-dlp returned invalid search data. Check the yt-dlp installation and network connection.");
  }

  return (payload.entries ?? [])
    .filter((entry): entry is YtDlpEntry & { id: string; title: string } => Boolean(entry.id && entry.title))
    .map((entry) => {
      const durationSeconds = Number.isFinite(entry.duration) ? entry.duration : undefined;
      const candidate: YoutubeCandidate = {
        id: entry.id,
        title: entry.title,
        channel: entry.channel || entry.uploader || "Unknown channel",
        ...(durationSeconds !== undefined ? { durationSeconds } : {}),
        url: entry.webpage_url || `https://www.youtube.com/watch?v=${entry.id}`,
        ...(entry.thumbnail ? { thumbnailUrl: entry.thumbnail } : {}),
        score: 0,
      };
      return { ...candidate, score: scoreCandidate(candidate, track) };
    })
    .sort((left, right) => right.score - left.score);
}

export async function selectYoutubeCandidate(
  candidates: YoutubeCandidate[],
  autoConfirm: boolean,
): Promise<YoutubeCandidate> {
  if (candidates.length === 0) {
    throw new Error("No YouTube candidates were found for this track.");
  }

  console.log("\nYouTube candidates:");
  candidates.forEach((candidate, index) => {
    console.log(
      `  ${index + 1}. [${formatDuration(candidate.durationSeconds)}] ${candidate.title} - ${candidate.channel}`,
    );
  });

  if (autoConfirm) {
    console.log("Selected candidate 1 (--yes).\n");
    const firstCandidate = candidates[0];
    if (!firstCandidate) {
      throw new Error("No YouTube candidates were found for this track.");
    }
    return firstCandidate;
  }

  while (true) {
    const answer = await ask("Select a candidate (1-5, or q to quit): ");
    if (answer.toLowerCase() === "q") {
      throw new Error("Generation cancelled.");
    }

    const index = Number.parseInt(answer, 10) - 1;
    if (Number.isInteger(index) && index >= 0 && index < candidates.length) {
      const selectedCandidate = candidates[index];
      if (selectedCandidate) {
        return selectedCandidate;
      }
    }
    console.log("Please enter one of the listed candidate numbers.");
  }
}

export async function downloadAudio(candidate: YoutubeCandidate, outputDirectory: string): Promise<string> {
  const template = path.join(outputDirectory, "source.%(ext)s");
  await runYtDlp(
    [
      "--no-playlist",
      "--extract-audio",
      "--audio-format",
      "wav",
      "--audio-quality",
      "0",
      "--output",
      template,
      candidate.url,
    ],
    true,
  );

  const files = await readdir(outputDirectory);
  const source = files.find((file) => file.startsWith("source.") && !file.endsWith(".part"));
  if (!source) {
    throw new Error("yt-dlp completed but no source audio file was found.");
  }
  return path.join(outputDirectory, source);
}
