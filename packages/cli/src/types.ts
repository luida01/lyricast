export interface SongQuery {
  raw: string;
  artist: string;
  title: string;
}

export interface SpotifyTrack {
  id: string;
  title: string;
  artists: string[];
  durationMs: number;
  albumName: string;
  albumType?: string;
  albumImageUrl?: string;
  spotifyUrl: string;
}

export interface YoutubeCandidate {
  id: string;
  title: string;
  channel: string;
  durationSeconds?: number;
  url: string;
  thumbnailUrl?: string;
  score: number;
}

export interface GenerateOptions {
  query?: string;
  artist?: string;
  title?: string;
  outputRoot: string;
  autoConfirm: boolean;
  force: boolean;
  language?: string;
  whisperModel: string;
}
