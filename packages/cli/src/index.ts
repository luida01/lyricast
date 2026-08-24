import "dotenv/config";
import { access, mkdir, rm } from "node:fs/promises";
import path from "node:path";
import { downloadCover, scoreSpotifyTrack, searchSpotifyTracks } from "./spotify.js";
import { downloadAudio, searchYoutubeCandidates, selectYoutubeCandidate } from "./youtube.js";
import { getSongQueryVariants, parseSongQuery, jobSlug } from "./song.js";
import { ensureDirectory, writeJson } from "./util.js";
import type { GenerateOptions, SpotifyTrack } from "./types.js";
import { runPythonPipeline } from "./pipeline.js";

const VERSION = "0.1.0";

function printHelp(): void {
  console.log(`Lyricast ${VERSION}

Generate customizable karaoke assets from an artist and song title.

Usage:
  lyricast generate "Artist - Song Title" [options]
  lyricast generate "Song Title - Artist" [options]
  lyricast generate --artist "Artist" --title "Song Title" [options]

Options:
  --output <dir>       Output root directory (default: out)
  --artist <name>      Artist name when not using a separated query
  --title <name>       Song title when not using a separated query
  --language <code>    Optional WhisperX hint; does not translate lyrics
  --whisper-model <n>  WhisperX model (default: small)
  --yes                Automatically select the highest-ranked YouTube result
  --force              Replace the existing output directory
  --help               Show this help
  --version            Show the version
`);
}

function requireValue(args: string[], index: number, option: string): string {
  const value = args[index + 1];
  if (!value || value.startsWith("--")) {
    throw new Error(`${option} requires a value`);
  }
  return value;
}

function parseGenerateOptions(args: string[]): GenerateOptions {
  const options: GenerateOptions = {
    outputRoot: "out",
    autoConfirm: false,
    force: false,
    whisperModel: "small",
  };
  const queryParts: string[] = [];

  for (let index = 0; index < args.length; index += 1) {
    const argument = args[index];
    if (!argument) {
      continue;
    }
    switch (argument) {
      case "--output":
        options.outputRoot = requireValue(args, index, argument);
        index += 1;
        break;
      case "--artist":
        options.artist = requireValue(args, index, argument);
        index += 1;
        break;
      case "--title":
        options.title = requireValue(args, index, argument);
        index += 1;
        break;
      case "--language":
        options.language = requireValue(args, index, argument);
        index += 1;
        break;
      case "--whisper-model":
        options.whisperModel = requireValue(args, index, argument);
        index += 1;
        break;
      case "--yes":
        options.autoConfirm = true;
        break;
      case "--force":
        options.force = true;
        break;
      case "--help":
        printHelp();
        process.exit(0);
        break;
      default:
        if (argument.startsWith("--")) {
          throw new Error(`Unknown option: ${argument}`);
        }
        queryParts.push(argument);
    }
  }

  if (queryParts.length > 0) {
    options.query = queryParts.join(" ");
  }
  return options;
}

async function directoryExists(directory: string): Promise<boolean> {
  try {
    await access(directory);
    return true;
  } catch {
    return false;
  }
}

function displayTrack(track: SpotifyTrack): void {
  console.log(`Spotify match: ${track.artists.join(", ")} - ${track.title}`);
  if (track.albumName) {
    console.log(`Release: ${track.albumName}${track.albumType ? ` (${track.albumType})` : ""}`);
  }
  console.log(`Duration: ${(track.durationMs / 1000).toFixed(1)}s`);
  console.log(`URL: ${track.spotifyUrl}`);
}

async function generate(options: GenerateOptions): Promise<void> {
  const song = parseSongQuery(options.query, options.artist, options.title);
  console.log(`Searching Spotify for: ${song.raw}`);

  const queryVariants = options.artist && options.title ? [song] : getSongQueryVariants(song);
  const rankedTracks = new Map<string, { track: SpotifyTrack; score: number; query: typeof song }>();
  for (const queryVariant of queryVariants) {
    const spotifyTracks = await searchSpotifyTracks(queryVariant);
    for (const candidate of spotifyTracks) {
      const score = scoreSpotifyTrack(candidate, queryVariant);
      const current = rankedTracks.get(candidate.id);
      if (!current || score > current.score) {
        rankedTracks.set(candidate.id, { track: candidate, score, query: queryVariant });
      }
    }
  }

  const bestMatch = [...rankedTracks.values()].sort((left, right) => right.score - left.score)[0];
  if (!bestMatch) {
    throw new Error("Spotify did not return a matching track.");
  }
  const track = bestMatch.track;
  if (bestMatch.query.artist !== song.artist || bestMatch.query.title !== song.title) {
    console.log(`Interpreted input as: ${track.title} - ${track.artists.join(", ")}`);
  }
  displayTrack(track);

  const candidates = await searchYoutubeCandidates(track);
  const selectedCandidate = await selectYoutubeCandidate(candidates, options.autoConfirm);
  console.log(`Selected: ${selectedCandidate.title}`);

  const outputRoot = path.resolve(options.outputRoot);
  const outputDirectory = path.join(outputRoot, jobSlug({
    raw: song.raw,
    artist: track.artists[0] ?? song.artist,
    title: track.title,
  }));
  if (await directoryExists(outputDirectory)) {
    if (!options.force) {
      throw new Error(`Output already exists: ${outputDirectory}. Use --force to replace it.`);
    }
    await rm(outputDirectory, { recursive: true, force: true });
  }
  await mkdir(outputDirectory, { recursive: true });

  const metadata = {
    schemaVersion: 1,
    status: "downloading",
    query: song.raw,
    track,
    youtube: selectedCandidate,
    generatedAt: new Date().toISOString(),
  };
  await writeJson(path.join(outputDirectory, "meta.json"), metadata);

  const sourceAudio = await downloadAudio(selectedCandidate, outputDirectory);
  const coverDownloaded = await downloadCover(track.albumImageUrl, path.join(outputDirectory, "cover.jpg"));
  await writeJson(path.join(outputDirectory, "meta.json"), {
    ...metadata,
    status: "processing",
    files: {
      sourceAudio: path.basename(sourceAudio),
      cover: coverDownloaded ? "cover.jpg" : null,
    },
  });

  await runPythonPipeline(outputDirectory, sourceAudio, track, options.language, options.whisperModel);
  await writeJson(path.join(outputDirectory, "meta.json"), {
    ...metadata,
    status: "complete",
    files: {
      sourceAudio: path.basename(sourceAudio),
      cover: coverDownloaded ? "cover.jpg" : null,
      vocals: "vocals.wav",
      instrumental: "instrumental.wav",
      lyrics: "lyrics.txt",
      sync: "sync.json",
    },
  });

  console.log(`\nGeneration complete: ${outputDirectory}`);
}

async function main(): Promise<void> {
  const [command, ...args] = process.argv.slice(2);
  if (command === "--version" || command === "-v") {
    console.log(VERSION);
    return;
  }
  if (!command || command === "--help" || command === "-h") {
    printHelp();
    return;
  }
  if (command !== "generate") {
    throw new Error(`Unknown command: ${command}. Use --help for usage.`);
  }

  await generate(parseGenerateOptions(args));
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`\nError: ${message}`);
  process.exitCode = 1;
});
