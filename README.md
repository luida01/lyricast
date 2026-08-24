# Lyricast

> Turn any track into a customizable karaoke video with automatic instrumental separation and word-level lyric synchronization.

Lyricast is a local-first karaoke generation pipeline. Given an artist and song title, it searches for the source track, downloads the audio, separates vocals from the instrumental, obtains lyrics, and produces timing data that can later be rendered as a custom video.

The project is currently in Phase 1: validating the audio and lyric synchronization pipeline before building the video editor.

## Current Pipeline

```text
Artist + song title
        |
        v
Spotify metadata and duration
        |
        v
YouTube search with manual candidate confirmation
        |
        v
Original audio download through yt-dlp
        |
        v
Demucs -> vocals.wav + instrumental.wav
        |
        v
LRCLIB word-level lyrics, or Genius/WhisperX fallback
        |
        v
sync.json
```

## Monorepo Layout

```text
packages/cli/       TypeScript CLI and pipeline orchestrator
pipeline/           Python audio separation and lyric alignment
apps/video/         Remotion app planned for Phase 2
apps/web/           Next.js app planned for Phase 3
```

## Prerequisites

- Node.js 20 or newer
- Python 3.10 or newer
- FFmpeg available on `PATH`
- A Spotify developer app with client credentials
- A machine with enough storage for audio files and ML models

An NVIDIA GPU is strongly recommended for faster Demucs and WhisperX processing, but CPU processing is supported for local experimentation. On Windows, `setup.ps1` detects `nvidia-smi` and installs the CUDA-enabled PyTorch build automatically. The current pipeline uses NVIDIA CUDA; integrated Intel or AMD graphics are not automatically supported by Demucs and WhisperX.

## Setup

Install the JavaScript workspace dependencies:

```powershell
npm install
```

Create the Python environment and install the media/ML dependencies:

```powershell
.\pipeline\setup.ps1
```

Copy `.env.example` to `.env` and fill in the Spotify credentials. `dotenv` loads this file automatically when the CLI starts.

Keep `SPOTIFY_MARKET` as a valid two-letter Spotify market such as `US` or change it to the market you want to search. It is required because the CLI uses the Client Credentials flow rather than a user access token.

## Usage

The input format is `Artist - Song Title`:

```powershell
npm run build
npm run lyricast -- generate "Rick Astley - Never Gonna Give You Up"
```

The CLI searches YouTube and shows the top candidates. Select one before downloading it. To accept the highest-ranked candidate without prompting:

```powershell
npm run lyricast -- generate "Rick Astley - Never Gonna Give You Up" --yes
```

Useful options:

```text
--output <dir>       Output root directory (default: out)
--artist <name>      Use with --title instead of the Artist - Title format
--title <name>       Use with --artist instead of the Artist - Title format
--language <code>    Hint WhisperX, for example es or en
--force              Replace an existing generated job
--yes                Accept the first YouTube candidate
```

Each generated song is written to `out/<artist>-<title>/`:

```text
source.wav           Downloaded source audio
vocals.wav           Separated vocal stem
instrumental.wav     Separated accompaniment
lyrics.txt           Lyrics used by the pipeline
sync.json            Word and line timing data
cover.jpg            Spotify album artwork when available
meta.json            Source and generation metadata
```

## Roadmap

1. Build and validate the local audio/lyrics pipeline.
2. Add a Remotion composition that renders `sync.json` to an MP4.
3. Add a Next.js editor with live preview and style controls.
4. Add a job queue and batch generation.

## Content and Platform Responsibility

This tool can access third-party media and lyrics. You are responsible for having the necessary rights and for complying with YouTube, Spotify, lyrics-provider, and other applicable terms before downloading, generating, or distributing content.

## License

MIT. See [LICENSE](LICENSE).
