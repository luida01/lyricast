import { slugify } from "./util.js";
import type { SongQuery } from "./types.js";

export function parseSongQuery(rawQuery: string | undefined, artist?: string, title?: string): SongQuery {
  const raw = rawQuery?.trim() ?? "";
  const parsedArtist = artist?.trim() ?? "";
  const parsedTitle = title?.trim() ?? "";

  if (parsedArtist && parsedTitle) {
    return { raw: raw || `${parsedArtist} - ${parsedTitle}`, artist: parsedArtist, title: parsedTitle };
  }

  if (!raw) {
    throw new Error("Provide a query in the format: Artist - Song Title");
  }

  const separator = raw.match(/\s+-\s+/);
  if (separator?.index !== undefined) {
    const left = raw.slice(0, separator.index).trim();
    const right = raw.slice(separator.index + separator[0].length).trim();
    if (left && right) {
      return { raw, artist: left, title: right };
    }
  }

  if (parsedArtist || parsedTitle) {
    return {
      raw,
      artist: parsedArtist,
      title: parsedTitle || raw,
    };
  }

  return { raw, artist: "", title: raw };
}

export function jobSlug(song: SongQuery): string {
  return slugify(`${song.artist || "unknown-artist"}-${song.title}`);
}
