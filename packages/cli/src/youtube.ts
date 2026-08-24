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
  channel_id?: string;
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
  const normalizedArtist = normalizeText(track.artists[0] ?? "");
  const normalizedTrackTitle = normalizeText(track.title);
  const normalizedAlbum = normalizeText(track.albumName);
  const normalizedChannel = normalizeText(candidate.channel);
  const rawTitle = candidate.title.toLowerCase();
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
    "color coded",
    "easy lyrics",
  ];
  const weakNegativeTerms = ["lyrics", "가사", "sub espa", "eng sub", "sub eng"];
  const positiveTerms = ["official", "audio", "music video"];
  let score = 0;

  for (const term of searchTerms) {
    if (normalizedTitle.includes(term)) {
      score += 8;
    }
  }
  if (normalizedTrackTitle && normalizedTitle.includes(normalizedTrackTitle)) {
    score += 20;
  }
  if (normalizedAlbum && normalizedAlbum !== normalizedTrackTitle && normalizedTitle.includes(normalizedAlbum)) {
    score += 18;
  }
  for (const term of badTerms) {
    if (normalizedTitle.includes(term) || rawTitle.includes(term)) {
      score -= 35;
    }
  }
  for (const term of weakNegativeTerms) {
    if (rawTitle.includes(term)) {
      score -= 14;
    }
  }
  for (const term of positiveTerms) {
    if (normalizedTitle.includes(term)) {
      score += 5;
    }
  }

  if (normalizedArtist && normalizedChannel === normalizedArtist) {
    score += 45;
  } else if (normalizedArtist && normalizedChannel.includes(`${normalizedArtist} topic`)) {
    score += 65;
  } else if (normalizedArtist && normalizedChannel.includes(normalizedArtist)) {
    score += 25;
  }
  if (normalizedChannel.includes("vevo")) {
    score += 15;
  }

  if (candidate.durationSeconds !== undefined) {
    const delta = Math.abs(candidate.durationSeconds - track.durationMs / 1000);
    score += Math.max(0, 50 - delta * 3);
  }

  return score;
}

function hasUsableAlbum(track: SpotifyTrack): boolean {
  const album = normalizeText(track.albumName);
  const title = normalizeText(track.title);
  const isAlbumRelease = track.albumType === "album" || track.albumType === "compilation";
  return Boolean(isAlbumRelease && album && album !== title);
}

async function searchYoutubeEntries(query: string, limit: number): Promise<YtDlpEntry[]> {
  const result = await runYtDlp([
    "--flat-playlist",
    "--dump-single-json",
    "--skip-download",
    "--no-warnings",
    "--quiet",
    "--js-runtimes",
    "node",
    `ytsearch${limit}:${query}`,
  ]);

  let payload: YtDlpSearchResult;
  try {
    payload = JSON.parse(result.stdout) as YtDlpSearchResult;
  } catch {
    throw new Error("yt-dlp returned invalid search data. Check the yt-dlp installation and network connection.");
  }

  return payload.entries ?? [];
}

function mapYoutubeCandidates(entries: YtDlpEntry[], track: SpotifyTrack): YoutubeCandidate[] {
  return entries
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

export async function searchYoutubeCandidates(track: SpotifyTrack, limit = 5): Promise<YoutubeCandidate[]> {
  const artist = track.artists[0] ?? "";
  const base = `${artist} ${track.title}`.trim();
  const queries = hasUsableAlbum(track)
    ? [`${base} ${track.albumName} official audio`, `${base} official audio`]
    : [`${base} Topic official audio`, `${base} official audio`];
  let entries: YtDlpEntry[] = [];

  for (const query of queries) {
    console.log(`YouTube search: ${query}`);
    entries = await searchYoutubeEntries(query, limit);
    if (entries.length > 0) {
      break;
    }
  }

  return mapYoutubeCandidates(entries, track);
}

async function fetchFlatTabEntries(url: string, limit: number): Promise<YtDlpEntry[]> {
  try {
    const result = await runYtDlp([
      "--flat-playlist",
      "--dump-single-json",
      "--skip-download",
      "--no-warnings",
      "--quiet",
      "--js-runtimes",
      "node",
      "--playlist-end",
      String(limit),
      url,
    ]);

    const payload = JSON.parse(result.stdout) as YtDlpSearchResult;
    return payload.entries ?? [];
  } catch {
    return [];
  }
}

async function resolveArtistChannelId(artist: string): Promise<string | undefined> {
  if (!artist) {
    return undefined;
  }

  const normalizedArtist = normalizeText(artist);
  let fallbackChannelId: string | undefined;

  for (const query of [`${artist} - Topic`, `${artist} official`] as const) {
    let entries: YtDlpEntry[] = [];
    try {
      entries = await searchYoutubeEntries(query, 5);
    } catch {
      continue;
    }

    for (const entry of entries) {
      if (!entry.channel_id) {
        continue;
      }
      const channel = normalizeText(entry.channel || entry.uploader || "");
      if (!channel.includes(normalizedArtist)) {
        continue;
      }
      if (channel.includes("topic")) {
        return entry.channel_id;
      }
      fallbackChannelId ??= entry.channel_id;
    }
  }

  return fallbackChannelId;
}

function pickBestRelease(entries: YtDlpEntry[], albumName: string): YtDlpEntry | undefined {
  const wantedAlbum = normalizeText(albumName);
  if (!wantedAlbum) {
    return undefined;
  }

  let best: { entry: YtDlpEntry; score: number } | undefined;
  for (const entry of entries) {
    if (!entry.id || !entry.title) {
      continue;
    }
    const title = normalizeText(entry.title);
    let score = 0;
    if (title === wantedAlbum) {
      score = 4;
    } else if (title.includes(wantedAlbum) || wantedAlbum.includes(title)) {
      score = 2;
    }
    if (score > 0 && (!best || score > best.score)) {
      best = { entry, score };
    }
  }
  return best?.entry;
}

function pickBestReleaseTrack(entries: YtDlpEntry[], track: SpotifyTrack): YoutubeCandidate | undefined {
  const wantedTitle = normalizeText(track.title);
  const expectedDuration = track.durationMs / 1000;
  let best: { candidate: YoutubeCandidate; score: number } | undefined;

  for (const entry of entries) {
    if (!entry.id || !entry.title) {
      continue;
    }
    const title = normalizeText(entry.title);
    let score = 0;
    if (title === wantedTitle) {
      score = 8;
    } else if (wantedTitle && title.includes(wantedTitle)) {
      score = 4;
    }
    if (score === 0) {
      continue;
    }

    const durationSeconds = Number.isFinite(entry.duration) ? entry.duration : undefined;
    if (durationSeconds !== undefined) {
      score += Math.max(0, 4 - Math.abs(durationSeconds - expectedDuration));
    }

    const candidate: YoutubeCandidate = {
      id: entry.id,
      title: entry.title,
      channel: entry.channel || entry.uploader || "Unknown channel",
      ...(durationSeconds !== undefined ? { durationSeconds } : {}),
      url: entry.webpage_url || `https://www.youtube.com/watch?v=${entry.id}`,
      ...(entry.thumbnail ? { thumbnailUrl: entry.thumbnail } : {}),
      score: 400 + score,
    };
    if (!best || score > best.score) {
      best = { candidate, score };
    }
  }
  return best?.candidate;
}

async function findReleaseTrackCandidate(track: SpotifyTrack): Promise<YoutubeCandidate | undefined> {
  const artist = track.artists[0] ?? "";
  const channelId = await resolveArtistChannelId(artist);
  if (!channelId) {
    return undefined;
  }

  // Artist channels expose their discography under /releases; Topic channels mirror it.
  const releases = await fetchFlatTabEntries(
    `https://www.youtube.com/channel/${channelId}/releases`,
    50,
  );
  const release = pickBestRelease(releases, track.albumName);
  if (!release?.id) {
    return undefined;
  }

  const tracks = await fetchFlatTabEntries(`https://www.youtube.com/playlist?list=${release.id}`, 80);
  const matched = pickBestReleaseTrack(tracks, track);
  if (!matched) {
    return undefined;
  }

  return {
    ...matched,
    channel: release.channel || release.uploader || `${artist} - Topic`,
  };
}

export async function findYoutubeAudio(track: SpotifyTrack, limit = 5): Promise<YoutubeCandidate[]> {
  // Preferred path: the artist's Releases tab -> matching release -> matching track.
  try {
    const releaseCandidate = await findReleaseTrackCandidate(track);
    if (releaseCandidate) {
      return [releaseCandidate];
    }
  } catch {
    // Fall through to keyword searches below.
  }

  return await searchYoutubeCandidates(track, limit);
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
    const answer = await ask(`Select a candidate (1-${candidates.length}, or q to quit): `);
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
      "--js-runtimes",
      "node",
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
