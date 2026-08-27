from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .alignment import apply_transcript_timings, estimate_line_timings, transcribe_words
from .lyrics import LyricLine, LyricsResult, fetch_lyrics
from .romanize import romanize_korean
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
    alignment_stats: dict[str, object] | None = None,
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
    if alignment_stats:
        payload["alignment"] = alignment_stats
    return payload


def _alignment_stats(lines: list[LyricLine], timing_source: str) -> dict[str, object]:
    words = [word for line in lines for word in line.words]
    total = len(words)
    matched = sum(1 for word in words if not word.estimated)
    estimated = total - matched
    short_lines: list[int] = []
    long_lines: list[int] = []
    for index, line in enumerate(lines):
        start = line.start or 0.0
        end = line.end or start
        duration = end - start
        if duration < 0.5 and len(line.words) > 3:
            short_lines.append(index)
        if duration > 20 and len(line.words) > 0:
            long_lines.append(index)
    return {
        "totalWords": total,
        "matchedWords": matched,
        "estimatedWords": estimated,
        "alignmentConfidence": round(matched / total, 2) if total else 0.0,
        "timingSource": timing_source,
        "shortLines": short_lines,
        "longLines": long_lines,
    }


def _romanized_transcript(transcript_words: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {**word, "word": romanize_korean(str(word.get("word", "")))}
        for word in transcript_words
    ]


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
    parser.add_argument("--force-separate", action="store_true", help="Re-run Demucs even if stems exist")
    parser.add_argument("--force-align", action="store_true", help="Re-run WhisperX even if transcript cache exists")
    parser.add_argument("--force", action="store_true", help="Re-run both separation and alignment")
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

    force_separate = args.force_separate or args.force
    force_align = args.force_align or args.force

    duration: float | None = args.duration
    if duration is None:
        meta_path = output_directory / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                track = meta.get("track") or {}
                duration_ms = track.get("durationMs")
                if duration_ms:
                    duration = float(duration_ms) / 1000.0
            except (ValueError, OSError):
                pass

    print(f"Fetching lyrics for {args.artist} - {args.title}...")
    lyrics = fetch_lyrics(args.artist, args.title, duration)
    (output_directory / "lyrics.txt").write_text(lyrics.plain_lyrics, encoding="utf-8")
    print(f"Lyrics provider: {lyrics.provider}")

    vocals_path = output_directory / "vocals.wav"
    instrumental_path = output_directory / "instrumental.wav"
    stems_directory = output_directory / ".stems"
    if vocals_path.exists() and instrumental_path.exists() and not force_separate:
        print("Reusing existing vocals.wav / instrumental.wav (use --force-separate to redo).")
    else:
        print("Separating vocals and instrumental with Demucs...")
        vocals_path, instrumental_path, stems_directory = separate_audio(
            input_path, output_directory, args.demucs_model
        )

    timing_source: str
    alignment_error: str | None = None
    detected_language = args.language
    transcript_path = output_directory / "transcript.json"
    timed_lines: list[LyricLine]
    if lyrics.has_word_timing:
        timed_lines = lyrics.lines
        timing_source = "lrclib-word"
    else:
        transcript_words: list[dict[str, object]]
        if transcript_path.exists() and not force_align:
            print(f"Reusing transcript cache {transcript_path.name} (use --force-align to redo).")
            cached = json.loads(transcript_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and "words" in cached:
                transcript_words = cached["words"]
                cached_language = cached.get("language")
                if cached_language:
                    detected_language = str(cached_language)
            else:
                transcript_words = cached
        else:
            try:
                transcript_words, detected_language = transcribe_words(
                    vocals_path, args.language, args.whisper_model
                )
                transcript_path.write_text(
                    json.dumps(
                        {"language": detected_language, "words": transcript_words},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception as error:  # Keep a usable line-timing artifact for diagnostics.
                alignment_error = str(error)
                print(f"Warning: WhisperX alignment failed: {alignment_error}")
                timed_lines = estimate_line_timings(lyrics.lines, duration)
                timing_source = "line-estimated"
                stats = _alignment_stats(timed_lines, timing_source)
                if alignment_error:
                    stats["error"] = alignment_error
                sync_payload = build_sync_payload(
                    args.artist,
                    args.title,
                    duration,
                    lyrics,
                    timed_lines,
                    timing_source,
                    detected_language,
                    alignment_error,
                    stats,
                )
                _write_json(output_directory / "sync.json", sync_payload)
                (output_directory / "alignment-report.json").write_text(
                    json.dumps(stats, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
                )
                if not args.keep_stems:
                    shutil.rmtree(stems_directory, ignore_errors=True)
                print(f"Wrote {output_directory / 'sync.json'}")
                return 0
        try:
            timed_lines = apply_transcript_timings(lyrics.lines, transcript_words, duration)
            timing_source = "whisperx-aligned"
        except Exception as error:
            alignment_error = str(error)
            print(f"Warning: alignment step failed: {alignment_error}")
            timed_lines = estimate_line_timings(lyrics.lines, duration)
            timing_source = "line-estimated"

        (output_directory / "transcript-romanized.json").write_text(
            json.dumps(_romanized_transcript(transcript_words), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    stats = _alignment_stats(timed_lines, timing_source)
    if alignment_error:
        stats["error"] = alignment_error
    sync_payload = build_sync_payload(
        args.artist,
        args.title,
        duration,
        lyrics,
        timed_lines,
        timing_source,
        detected_language,
        alignment_error,
        stats,
    )
    _write_json(output_directory / "sync.json", sync_payload)
    (output_directory / "alignment-report.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    if not args.keep_stems:
        shutil.rmtree(stems_directory, ignore_errors=True)

    confidence = stats["alignmentConfidence"]
    if confidence < 0.5:
        print(
            f"Warning: low alignment confidence ({confidence}). "
            "WhisperX may have misdetected the language; consider passing --language."
        )
    print(
        f"Wrote {output_directory / 'sync.json'} "
        f"(confidence={confidence}, estimated={stats['estimatedWords']})"
    )
    return 0