from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .alignment import apply_transcript_timings, estimate_line_timings, transcribe_words
from .lyrics import LyricLine, LyricsResult, fetch_lyrics
from .stems import separate_audio


def build_sync_payload(
    artist: str,
    title: str,
    duration: float | None,
    lyrics: LyricsResult,
    lines: list[LyricLine],
    timing_source: str,
    language: str | None,
    alignment_error: str | None = None,
) -> dict[str, object]:
    serialized_lines: list[dict[str, object]] = []
    serialized_words: list[dict[str, object]] = []
    for line_index, line in enumerate(lines):
        words: list[dict[str, object]] = []
        for word_index, word in enumerate(line.words):
            item = {
                "text": word.text,
                "start": round(word.start or 0.0, 3),
                "end": round(word.end or (word.start or 0.0) + 0.08, 3),
                "estimated": word.estimated,
            }
            words.append(item)
            serialized_words.append({"line": line_index, "index": word_index, **item})
        serialized_lines.append({
            "text": line.text,
            "start": round(line.start or 0.0, 3),
            "end": round(line.end or line.start or 0.0, 3),
            "words": words,
        })

    payload: dict[str, object] = {
        "schemaVersion": 1,
        "track": {"artist": artist, "title": title},
        "duration": duration,
        "lyricsProvider": lyrics.provider,
        "timingSource": timing_source,
        "language": language,
        "lines": serialized_lines,
        "words": serialized_words,
    }
    if alignment_error:
        payload["alignmentError"] = alignment_error
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create separated stems and synchronized lyrics for Lyricast.")
    parser.add_argument("--input", required=True, type=Path, help="Downloaded source audio file")
    parser.add_argument("--output", required=True, type=Path, help="Generated song directory")
    parser.add_argument("--artist", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--language", default=None)
    parser.add_argument("--whisper-model", default="small")
    parser.add_argument("--demucs-model", default="htdemucs")
    parser.add_argument("--keep-stems", action="store_true")
    return parser.parse_args()


def _write_json(file_path: Path, payload: object) -> None:
    file_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    input_path: Path = args.input.resolve()
    output_directory: Path = args.output.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    if not input_path.exists():
        raise RuntimeError(f"Input audio does not exist: {input_path}")

    print(f"Fetching lyrics for {args.artist} - {args.title}...")
    lyrics = fetch_lyrics(args.artist, args.title, args.duration)
    (output_directory / "lyrics.txt").write_text(lyrics.plain_lyrics, encoding="utf-8")
    print(f"Lyrics provider: {lyrics.provider}")

    print("Separating vocals and instrumental with Demucs...")
    vocals_path, _instrumental_path, stems_directory = separate_audio(
        input_path, output_directory, args.demucs_model
    )

    timing_source: str
    alignment_error: str | None = None
    detected_language = args.language
    if lyrics.has_word_timing:
        timed_lines = lyrics.lines
        timing_source = "lrclib-word"
    else:
        try:
            transcript_words, detected_language = transcribe_words(
                vocals_path, args.language, args.whisper_model
            )
            timed_lines = apply_transcript_timings(lyrics.lines, transcript_words, args.duration)
            timing_source = "whisperx-aligned"
        except Exception as error:  # Keep a usable line-timing artifact for diagnostics.
            alignment_error = str(error)
            print(f"Warning: WhisperX alignment failed: {alignment_error}")
            timed_lines = estimate_line_timings(lyrics.lines, args.duration)
            timing_source = "line-estimated"

    sync_payload = build_sync_payload(
        args.artist,
        args.title,
        args.duration,
        lyrics,
        timed_lines,
        timing_source,
        detected_language,
        alignment_error,
    )
    _write_json(output_directory / "sync.json", sync_payload)
    if not args.keep_stems:
        shutil.rmtree(stems_directory, ignore_errors=True)

    print(f"Wrote {output_directory / 'sync.json'}")
    return 0
