import { Buffer } from "node:buffer";
import type { SongQuery, SpotifyTrack } from "./types.js";
import { normalizeText } from "./util.js";

interface SpotifyTokenResponse {
  access_token: string;
  expires_in: number;
}

interface SpotifySearchItem {
  id: string;
  name: string;
  duration_ms: number;
  external_urls?: { spotify?: string };
  artists?: Array<{ name?: string }>;
  album?: {
    name?: string;
    images?: Array<{ url?: string }>;
  };
}

interface SpotifySearchResponse {
  tracks?: {
    items?: SpotifySearchItem[];
  };
}

let cachedToken: { value: string; expiresAt: number } | undefined;

async function getAccessToken(): Promise<string> {
  const clientId = process.env.SPOTIFY_CLIENT_ID;
  const clientSecret = process.env.SPOTIFY_CLIENT_SECRET;
  if (!clientId || !clientSecret) {
    throw new Error("Missing SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET in .env");
  }

  if (cachedToken && cachedToken.expiresAt > Date.now() + 30_000) {
    return cachedToken.value;
  }

  const credentials = Buffer.from(`${clientId}:${clientSecret}`).toString("base64");
  const response = await fetch("https://accounts.spotify.com/api/token", {
    method: "POST",
    headers: {
      Authorization: `Basic ${credentials}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: "grant_type=client_credentials",
  });

  if (!response.ok) {
    throw new Error(`Spotify authentication failed (${response.status} ${response.statusText})`);
  }

  const payload = (await response.json()) as SpotifyTokenResponse;
  cachedToken = {
    value: payload.access_token,
    expiresAt: Date.now() + payload.expires_in * 1000,
  };
  return payload.access_token;
}

function mapTrack(item: SpotifySearchItem): SpotifyTrack | undefined {
  if (!item.id || !item.name || !Number.isFinite(item.duration_ms)) {
    return undefined;
  }

  const artists = (item.artists ?? [])
    .map((artist) => artist.name?.trim())
    .filter((name): name is string => Boolean(name));
  const albumImageUrl = item.album?.images?.[0]?.url;

  return {
    id: item.id,
    title: item.name,
    artists,
    durationMs: item.duration_ms,
    albumName: item.album?.name ?? "",
    ...(albumImageUrl ? { albumImageUrl } : {}),
    spotifyUrl: item.external_urls?.spotify ?? `https://open.spotify.com/track/${item.id}`,
  };
}

function scoreTrack(track: SpotifyTrack, query: SongQuery): number {
  const normalizedTitle = normalizeText(track.title);
  const normalizedQueryTitle = normalizeText(query.title);
  const normalizedArtists = track.artists.map(normalizeText);
  const normalizedQueryArtist = normalizeText(query.artist);
  let score = 0;

  if (normalizedTitle === normalizedQueryTitle) {
    score += 100;
  } else if (normalizedTitle.includes(normalizedQueryTitle) || normalizedQueryTitle.includes(normalizedTitle)) {
    score += 45;
  }

  if (normalizedQueryArtist && normalizedArtists.some((artist) => artist === normalizedQueryArtist)) {
    score += 100;
  } else if (normalizedQueryArtist && normalizedArtists.some((artist) => artist.includes(normalizedQueryArtist))) {
    score += 45;
  }

  return score;
}

export async function searchSpotifyTracks(query: SongQuery, limit = 5): Promise<SpotifyTrack[]> {
  const token = await getAccessToken();
  const spotifyQuery = [
    query.title ? `track:${query.title}` : "",
    query.artist ? `artist:${query.artist}` : "",
  ]
    .filter(Boolean)
    .join(" ") || query.raw;
  const url = new URL("https://api.spotify.com/v1/search");
  url.searchParams.set("q", spotifyQuery);
  url.searchParams.set("type", "track");
  url.searchParams.set("limit", String(limit));

  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new Error(`Spotify search failed (${response.status} ${response.statusText})`);
  }

  const payload = (await response.json()) as SpotifySearchResponse;
  return (payload.tracks?.items ?? [])
    .map(mapTrack)
    .filter((track): track is SpotifyTrack => Boolean(track))
    .sort((left, right) => scoreTrack(right, query) - scoreTrack(left, query));
}

export async function downloadCover(url: string | undefined, filePath: string): Promise<boolean> {
  if (!url) {
    return false;
  }

  const response = await fetch(url);
  if (!response.ok) {
    return false;
  }

  const data = new Uint8Array(await response.arrayBuffer());
  const { writeFile } = await import("node:fs/promises");
  await writeFile(filePath, data);
  return true;
}
