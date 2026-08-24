from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen


@dataclass
class LyricWord:
    text: str
    start: Optional[float] = None
    end: Optional[float] = None
    estimated: bool = False


@dataclass
class LyricLine:
    text: str
    words: list[LyricWord]
    start: Optional[float] = None
    end: Optional[float] = None


@dataclass
class LyricsResult:
    provider: str
    plain_lyrics: str
    lines: list[LyricLine]
    has_word_timing: bool


LINE_TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
WORD_TIMESTAMP_RE = re.compile(r"<(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?>")
METADATA_RE = re.compile(r"^\[(ar|al|ti|by|offset|re|ve):", re.IGNORECASE)


def timestamp_seconds(minutes: str, seconds: str, fraction: str | None) -> float:
    value = int(minutes) * 60 + int(seconds)
    if fraction:
        value += int(fraction) / (100 if len(fraction) <= 2 else 1000)
    return float(value)


def tokenize(text: str) -> list[str]:
    return re.findall(r"\S+", text.strip())


def _word_chunks(payload: str) -> list[tuple[float, list[str], Optional[float]]]:
    tags = list(WORD_TIMESTAMP_RE.finditer(payload))
    chunks: list[tuple[float, list[str], Optional[float]]] = []
    for index, tag in enumerate(tags):
        next_tag = tags[index + 1] if index + 1 < len(tags) else None
        end_position = next_tag.start() if next_tag else len(payload)
        text = payload[tag.end():end_position].strip()
        words = tokenize(text)
        if not words:
            continue
        start = timestamp_seconds(tag.group(1), tag.group(2), tag.group(3))
        next_start = None
        if next_tag:
            next_start = timestamp_seconds(next_tag.group(1), next_tag.group(2), next_tag.group(3))
        chunks.append((start, words, next_start))
    return chunks


def parse_lrc(content: str, duration: float | None = None) -> tuple[list[LyricLine], bool]:
    lines: list[LyricLine] = []
    has_word_timing = False

    for raw_line in content.splitlines():
        if METADATA_RE.match(raw_line.strip()):
            continue
        line_tags = list(LINE_TIMESTAMP_RE.finditer(raw_line))
        if not line_tags:
            continue

        line_start = timestamp_seconds(
            line_tags[0].group(1), line_tags[0].group(2), line_tags[0].group(3)
        )
        payload = raw_line[line_tags[-1].end():].strip()
        word_chunks = _word_chunks(payload)
        if word_chunks:
            has_word_timing = True
            words: list[LyricWord] = []
            for start, chunk_words, next_start in word_chunks:
                step = (next_start - start) / len(chunk_words) if next_start and next_start > start else 0.0
                for index, word in enumerate(chunk_words):
                    word_start = start + step * index
                    word_end = start + step * (index + 1) if step else None
                    words.append(LyricWord(word, word_start, word_end))
            lines.append(LyricLine(payload, words, line_start))
        elif payload:
            lines.append(LyricLine(payload, [LyricWord(word) for word in tokenize(payload)], line_start))

    lines.sort(key=lambda line: line.start if line.start is not None else 0.0)
    for index, line in enumerate(lines):
        next_start = lines[index + 1].start if index + 1 < len(lines) else duration
        if next_start is not None and line.start is not None and next_start > line.start:
            line.end = next_start
        elif line.start is not None:
            line.end = line.start + max(1.0, len(line.words) * 0.4)

        if has_word_timing:
            for word_index, word in enumerate(line.words):
                if word.start is None:
                    continue
                next_word_start = (
                    line.words[word_index + 1].start
                    if word_index + 1 < len(line.words)
                    else line.end
                )
                if word.end is None:
                    word.end = next_word_start if next_word_start and next_word_start > word.start else word.start + 0.35

    return lines, has_word_timing


def plain_text_lines(content: str) -> list[LyricLine]:
    lines: list[LyricLine] = []
    for raw_line in content.splitlines():
        text = raw_line.strip()
        if not text or METADATA_RE.match(text):
            continue
        words = [LyricWord(word) for word in tokenize(text)]
        if words:
            lines.append(LyricLine(text, words))
    return lines


BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def _request_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = Request(url, headers={"User-Agent": BROWSER_UA, **(headers or {})})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _lyrics_result_from_record(record: dict, duration: float | None) -> LyricsResult | None:
    plain_lyrics = record.get("plainLyrics")
    synced_lyrics = record.get("syncedLyrics")
    if not isinstance(plain_lyrics, str) and not isinstance(synced_lyrics, str):
        return None
    synced_lyrics = synced_lyrics if isinstance(synced_lyrics, str) else ""
    plain_lyrics = plain_lyrics if isinstance(plain_lyrics, str) else synced_lyrics
    lines, has_word_timing = parse_lrc(synced_lyrics, duration) if synced_lyrics else (plain_text_lines(plain_lyrics), False)
    if not lines:
        lines = plain_text_lines(plain_lyrics)
    if not lines:
        return None
    return LyricsResult("lrclib", plain_lyrics, lines, has_word_timing)


def fetch_lrclib(artist: str, title: str, duration: float | None) -> LyricsResult | None:
    params = {"artist_name": artist, "track_name": title}
    if duration is not None:
        params["duration"] = str(round(duration))
    url = f"https://lrclib.net/api/get?{urlencode(params)}"
    try:
        payload = _request_json(url)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None
    return _lyrics_result_from_record(payload, duration)


def fetch_lrclib_search(artist: str, title: str, duration: float | None) -> LyricsResult | None:
    """Best-effort LRCLIB search when the exact get endpoint misses (new or niche releases)."""
    params = {"track_name": title, "artist_name": artist}
    url = f"https://lrclib.net/api/search?{urlencode(params)}"
    try:
        payload = _request_json(url)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None

    if not isinstance(payload, list):
        return None

    def record_score(record: object) -> float:
        if not isinstance(record, dict):
            return 0.0
        score = 0.0
        synced = record.get("syncedLyrics")
        if isinstance(synced, str) and synced.strip():
            score += 3.0
        record_duration = record.get("duration")
        if duration is not None and isinstance(record_duration, (int, float)):
            score += max(0.0, 2.0 - abs(float(record_duration) - duration))
        return score

    candidates = [record for record in payload if isinstance(record, dict)]
    if not candidates:
        return None
    best = max(candidates, key=record_score)
    return _lyrics_result_from_record(best, duration)


class _GeniusLyricsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._current: list[str] = []
        self.blocks: list[str] = []
        self._void_tags = {"br", "img", "input", "meta", "link", "hr"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs)
        is_container = tag == "div" and attributes.get("data-lyrics-container") == "true"
        if is_container:
            self._depth = 1
            self._current = []
            return
        if self._depth:
            if tag == "br":
                self._current.append("\n")
            if tag not in self._void_tags:
                self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._depth and tag not in self._void_tags:
            self._depth -= 1
            if self._depth == 0:
                self.blocks.append("".join(self._current))
                self._current = []

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._current.append(data)


def _normalize_match(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _hit_score(result: dict, artist: str, title: str) -> int:
    raw_title = re.sub(
        r"\((?:romanized|romanised|english translation|translation)\)",
        "",
        str(result.get("title", "")),
        flags=re.IGNORECASE,
    )
    hit_title = _normalize_match(raw_title)
    primary_artist = result.get("primary_artist")
    hit_artist = ""
    if isinstance(primary_artist, dict):
        hit_artist = _normalize_match(str(primary_artist.get("name", "")))
    wanted_title = _normalize_match(title)
    wanted_artist = _normalize_match(artist)
    score = 0

    if wanted_title:
        if hit_title == wanted_title:
            score += 6
        elif wanted_title in hit_title:
            score += 3
    if wanted_artist:
        if hit_artist == wanted_artist:
            score += 6
        elif wanted_artist and wanted_artist in hit_artist:
            score += 2
        # Prefer romanized pages for Asian releases: they are singable by
        # non-native speakers and match how fans search these tracks.
        # The bonus outweighs the original page's exact artist match.
        if hit_artist == "geniusromanizations":
            score += 10
    return score


def _genius_hit_results(artist: str, title: str) -> list[dict]:
    token = os.getenv("GENIUS_ACCESS_TOKEN")
    query = quote_plus(f"{artist} {title}")
    if token:
        payload = _request_json(
            f"https://api.genius.com/search?q={query}",
            {"Authorization": f"Bearer {token}"},
        )
        hits = payload.get("response", {}).get("hits", []) if isinstance(payload, dict) else []
        return [
            hit.get("result", {})
            for hit in hits
            if isinstance(hit, dict) and isinstance(hit.get("result"), dict)
        ]

    # Public web endpoint used by genius.com itself; no token required.
    payload = _request_json(f"https://genius.com/api/search?q={query}")
    if not isinstance(payload, dict):
        return []
    response = payload.get("response")
    if not isinstance(response, dict):
        return []

    # The public search returns a flat hits list; older variants used sections.
    flat_hits = response.get("hits")
    if isinstance(flat_hits, list):
        return [
            hit.get("result", {})
            for hit in flat_hits
            if isinstance(hit, dict)
            and hit.get("type") in (None, "song")
            and isinstance(hit.get("result"), dict)
        ]

    results: list[dict] = []
    sections = response.get("sections")
    for section in sections if isinstance(sections, list) else []:
        if isinstance(section, dict) and section.get("type") == "song":
            for hit in section.get("hits", []):
                if isinstance(hit, dict) and isinstance(hit.get("result"), dict):
                    results.append(hit["result"])
    return results


def _clean_genius_page(text: str) -> str:
    """Drop page chrome (contributors header, translation menu, title heading)."""
    heading = re.search(r"\bLyrics\s*", text)
    if heading:
        text = text[heading.end():]

    cleaned_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        lowered = stripped.lower()
        if "contributor" in lowered or "translation" in lowered:
            continue
        cleaned_lines.append(stripped)
    return "\n".join(cleaned_lines).strip()


def fetch_genius(artist: str, title: str, duration: float | None = None) -> LyricsResult | None:
    del duration
    try:
        hit_results = _genius_hit_results(artist, title)
        if not hit_results:
            return None

        scored = sorted(
            ((_hit_score(result, artist, title), result) for result in hit_results),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score, best_result = scored[0]
        selected_result = best_result if best_score > 0 else hit_results[0]
        selected_url = str(selected_result.get("url") or "")
        if not selected_url:
            return None

        request = Request(selected_url, headers={"User-Agent": BROWSER_UA})
        with urlopen(request, timeout=20) as response:
            page = response.read().decode("utf-8", errors="replace")
        parser = _GeniusLyricsParser()
        parser.feed(page)
        raw_lyrics = "\n".join(block.strip() for block in parser.blocks if block.strip())
        plain_lyrics = _clean_genius_page(raw_lyrics)
        if not plain_lyrics:
            return None

        primary_artist = selected_result.get("primary_artist")
        is_romanized = (
            isinstance(primary_artist, dict)
            and _normalize_match(str(primary_artist.get("name", ""))) == "geniusromanizations"
        )
        provider = "genius-romanized" if is_romanized else "genius"
        return LyricsResult(provider, plain_lyrics, plain_text_lines(plain_lyrics), False)
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError, AttributeError):
        return None


def fetch_lyrics(artist: str, title: str, duration: float | None) -> LyricsResult:
    lrclib_exact = fetch_lrclib(artist, title, duration)
    if lrclib_exact:
        return lrclib_exact

    lrclib_search = fetch_lrclib_search(artist, title, duration)
    if lrclib_search:
        return lrclib_search

    genius = fetch_genius(artist, title, duration)
    if genius:
        return genius

    raise RuntimeError("No lyrics were found on LRCLIB or Genius (including romanized pages).")
