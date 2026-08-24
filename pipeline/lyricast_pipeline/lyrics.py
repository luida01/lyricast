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


def _request_json(url: str, headers: dict[str, str] | None = None) -> object:
    request = Request(url, headers={"User-Agent": "Lyricast/0.1", **(headers or {})})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


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
    plain_lyrics = payload.get("plainLyrics")
    synced_lyrics = payload.get("syncedLyrics")
    if not isinstance(plain_lyrics, str) and not isinstance(synced_lyrics, str):
        return None
    synced_lyrics = synced_lyrics if isinstance(synced_lyrics, str) else ""
    plain_lyrics = plain_lyrics if isinstance(plain_lyrics, str) else synced_lyrics
    lines, has_word_timing = parse_lrc(synced_lyrics, duration) if synced_lyrics else (plain_text_lines(plain_lyrics), False)
    if not lines:
        lines = plain_text_lines(plain_lyrics)
    return LyricsResult("lrclib", plain_lyrics, lines, has_word_timing)


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


def fetch_genius(artist: str, title: str, duration: float | None = None) -> LyricsResult | None:
    del duration
    token = os.getenv("GENIUS_ACCESS_TOKEN")
    if not token:
        return None

    try:
        search_url = f"https://api.genius.com/search?q={quote_plus(f'{artist} {title}')}"
        payload = _request_json(search_url, {"Authorization": f"Bearer {token}"})
        hits = payload.get("response", {}).get("hits", []) if isinstance(payload, dict) else []
        selected_url: str | None = None
        for hit in hits:
            result = hit.get("result", {}) if isinstance(hit, dict) else {}
            hit_title = result.get("title", "")
            primary_artist = result.get("primary_artist", {}).get("name", "")
            if _normalize_match(title) in _normalize_match(hit_title) and _normalize_match(artist) in _normalize_match(primary_artist):
                selected_url = result.get("url")
                break
        if not selected_url and hits:
            selected_url = hits[0].get("result", {}).get("url")
        if not selected_url:
            return None

        request = Request(selected_url, headers={"User-Agent": "Lyricast/0.1"})
        with urlopen(request, timeout=20) as response:
            page = response.read().decode("utf-8", errors="replace")
        parser = _GeniusLyricsParser()
        parser.feed(page)
        plain_lyrics = "\n".join(block.strip() for block in parser.blocks if block.strip())
        if not plain_lyrics:
            return None
        return LyricsResult("genius", plain_lyrics, plain_text_lines(plain_lyrics), False)
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError, AttributeError):
        return None


def fetch_lyrics(artist: str, title: str, duration: float | None) -> LyricsResult:
    lrclib = fetch_lrclib(artist, title, duration)
    if lrclib:
        return lrclib

    genius = fetch_genius(artist, title, duration)
    if genius:
        return genius

    raise RuntimeError(
        "No lyrics were found. LRCLIB returned no result; set GENIUS_ACCESS_TOKEN "
        "to enable the Genius fallback."
    )
