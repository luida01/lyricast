from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .alignment import (
    ALIGNMENT_VERSION,
    _segments_from_words,
    apply_line_timestamp_timings,
    apply_transcript_timings,
    classify_gaps,
    detect_vocal_activity,
    estimate_line_timings,
    force_align_lyrics,
    sanitize_word_timings,
    transcribe_words,
    validate_line_timings,
    validate_semantics,
    validate_word_timings,
)
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
    word_timing_source: str = "distributed",
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
            "timingQuality": line.timing_quality,
            "words": words,
        })

    payload: dict[str, object] = {
        "schemaVersion": 1,
        "alignmentVersion": ALIGNMENT_VERSION,
        "track": {"artist": artist, "title": title},
        "duration": duration,
        "lyricsProvider": lyrics.provider,
        "timingSource": timing_source,
        "wordTimingSource": word_timing_source,
        "language": language,
        "lines": serialized_lines,
        "words": serialized_words,
    }
    if alignment_error:
        payload["alignmentError"] = alignment_error
    if alignment_stats:
        payload["alignment"] = alignment_stats
    return payload


def _alignment_stats(
    lines: list[LyricLine],
    timing_source: str,
    word_timing_source: str,
    duration: float | None,
) -> dict[str, object]:
    words = [word for line in lines for word in line.words]
    total = len(words)
    matched = sum(1 for word in words if not word.estimated)
    estimated = total - matched
    short_lines: list[int] = []
    long_lines: list[int] = []
    for index, line in enumerate(lines):
        start = line.start or 0.0
        end = line.end or start
        line_duration = end - start
        if line_duration < 0.5 and len(line.words) > 3:
            short_lines.append(index)
        if line_duration > 20 and len(line.words) > 0:
            long_lines.append(index)
    return {
        "alignmentVersion": ALIGNMENT_VERSION,
        "totalWords": total,
        "matchedWords": matched,
        "estimatedWords": estimated,
        "alignmentConfidence": round(matched / total, 2) if total else 0.0,
        "timingSource": timing_source,
        "wordTimingSource": word_timing_source,
        "validation": {
            "lineWarnings": validate_line_timings(lines, duration),
            "wordWarnings": validate_word_timings(lines, duration),
            "semanticWarnings": validate_semantics(lines, duration),
        },
        "shortLines": short_lines,
        "longLines": long_lines,
    }


def _romanized_transcript(transcript_words: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {**word, "word": romanize_korean(str(word.get("word", "")))}
        for word in transcript_words
    ]


def _probe_duration(audio_path: Path) -> float | None:
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(result.stdout.strip())
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def _audit_audio_durations(output_directory: Path) -> dict[str, object]:
    durations: dict[str, float | None] = {}
    for name in ("source.wav", "vocals.wav", "instrumental.wav"):
        path = output_directory / name
        durations[name] = _probe_duration(path) if path.exists() else None
    warnings: list[str] = []
    base = durations.get("source.wav")
    if base is not None:
        for name in ("vocals.wav", "instrumental.wav"):
            value = durations.get(name)
            if value is not None and abs(value - base) > 0.3:
                warnings.append(
                    f"{name} duration ({value:.2f}s) differs from source ({base:.2f}s)"
                )
    return {"durations": durations, "warnings": warnings}


def _lines_from_aligned_cache(
    lines: list[LyricLine], payload: dict[str, object]
) -> list[LyricLine] | None:
    cached_lines = payload.get("lines")
    if not isinstance(cached_lines, list) or len(cached_lines) != len(lines):
        return None
    for line, cached in zip(lines, cached_lines):
        cached_words = cached.get("words")
        if not isinstance(cached_words, list) or len(cached_words) != len(line.words):
            return None
        line.start = cached.get("start")
        line.end = cached.get("end")
        line.timing_quality = cached.get("timingQuality") or "anchored"
        for word, cached_word in zip(line.words, cached_words):
            word.start = cached_word.get("start")
            word.end = cached_word.get("end")
            word.estimated = bool(cached_word.get("estimated", False))
    return lines


def _aligned_cache_payload(lines: list[LyricLine]) -> list[dict[str, object]]:
    return [
        {
            "start": line.start,
            "end": line.end,
            "timingQuality": line.timing_quality,
            "words": [
                {
                    "text": word.text,
                    "start": word.start,
                    "end": word.end,
                    "estimated": word.estimated,
                }
                for word in line.words
            ],
        }
        for line in lines
    ]


def _write_alignment_debug(
    output_directory: Path,
    lines: list[LyricLine],
    duration: float | None,
    vocal_regions: list[tuple[float, float]],
    audio_audit: dict[str, object],
) -> None:
    anomalies: list[str] = []
    for index, line in enumerate(lines):
        start = line.start or 0.0
        end = line.end or start
        span = end - start
        if len(line.words) > 3 and span < 0.5:
            anomalies.append(f"line {index}: very short ({span:.2f}s)")
        if span > 8:
            anomalies.append(f"line {index}: very long ({span:.2f}s)")
    payload: dict[str, object] = {
        "duration": duration,
        "audio": audio_audit,
        "regions": classify_gaps(lines, vocal_regions),
        "anomalies": anomalies,
        "lines": [
            {
                "index": index,
                "text": line.text,
                "start": line.start,
                "end": line.end,
                "duration": round((line.end or line.start or 0.0) - (line.start or 0.0), 3),
                "timingQuality": line.timing_quality,
                "wordCount": len(line.words),
            }
            for index, line in enumerate(lines)
        ],
    }
    (output_directory / "alignment-debug.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


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
    word_timing_source: str
    alignment_error: str | None = None
    detected_language = args.language
    transcript_path = output_directory / "transcript.json"
    transcript_words: list[dict[str, object]] = []
    transcript_segments: list[dict[str, object]] = []
    vocal_regions: list[tuple[float, float]] = []
    timed_lines: list[LyricLine]
    if lyrics.has_word_timing:
        timed_lines = sanitize_word_timings(lyrics.lines, duration)
        timing_source = "lrclib-word"
        word_timing_source = "lrc"
    elif any(line.start is not None for line in lyrics.lines):
        timed_lines = apply_line_timestamp_timings(lyrics.lines, duration)
        timing_source = "lrclib-line"
        word_timing_source = "distributed"
    else:
        if transcript_path.exists() and not force_align:
            print(f"Reusing transcript cache {transcript_path.name} (use --force-align to redo).")
            cached = json.loads(transcript_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict):
                transcript_words = cached.get("words") or []
                transcript_segments = cached.get("segments") or _segments_from_words(transcript_words)
                cached_language = cached.get("language")
                if cached_language:
                    detected_language = str(cached_language)
            else:
                transcript_words = cached
                transcript_segments = _segments_from_words(transcript_words)
        else:
            try:
                transcript_words, detected_language, transcript_segments = transcribe_words(
                    vocals_path, args.language, args.whisper_model
                )
                transcript_path.write_text(
                    json.dumps(
                        {
                            "language": detected_language,
                            "alignmentVersion": ALIGNMENT_VERSION,
                            "segments": transcript_segments,
                            "words": transcript_words,
                        },
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
                word_timing_source = "distributed"
                stats = _alignment_stats(timed_lines, timing_source, word_timing_source, duration)
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
                    word_timing_source,
                )
                _write_json(output_directory / "sync.json", sync_payload)
                (output_directory / "alignment-report.json").write_text(
                    json.dumps(stats, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
                )
                if not args.keep_stems:
                    shutil.rmtree(stems_directory, ignore_errors=True)
                print(f"Wrote {output_directory / 'sync.json'}")
                return 0

        vocal_path = output_directory / "vocal-activity.json"
        if vocal_path.exists() and not force_align:
            try:
                cached_vocal = json.loads(vocal_path.read_text(encoding="utf-8"))
                vocal_regions = [tuple(region) for region in cached_vocal.get("regions", [])]
            except (ValueError, OSError):
                pass
        if not vocal_regions:
            vocal_regions = detect_vocal_activity(vocals_path)
            vocal_path.write_text(
                json.dumps(
                    {"regions": [[round(start, 2), round(end, 2)] for start, end in vocal_regions]},
                    indent=2,
                ),
                encoding="utf-8",
            )

        forced_confidence = 0.0
        timed_lines = None
        aligned_path = output_directory / "aligned-lyrics.json"
        if aligned_path.exists() and not force_align:
            try:
                cached_align = json.loads(aligned_path.read_text(encoding="utf-8"))
                forced_confidence = float(cached_align.get("confidence", 0.0))
                timed_lines = _lines_from_aligned_cache(lyrics.lines, cached_align)
            except (ValueError, OSError, TypeError):
                timed_lines = None
        if timed_lines is None:
            print("Forced-aligning the known lyrics against the vocal audio...")
            try:
                result = force_align_lyrics(
                    lyrics.lines,
                    transcript_segments,
                    vocals_path,
                    detected_language,
                )
                if result is not None:
                    timed_lines, forced_confidence = result
            except Exception as error:
                print(f"Warning: forced alignment failed: {error}")
                timed_lines = None
            if timed_lines is not None:
                aligned_path.write_text(
                    json.dumps(
                        {
                            "language": detected_language,
                            "alignmentVersion": ALIGNMENT_VERSION,
                            "confidence": forced_confidence,
                            "lines": _aligned_cache_payload(timed_lines),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print(f"Forced alignment confidence: {forced_confidence:.2f}")
        if timed_lines is not None:
            timing_source = "whisperx-forced"
            word_timing_source = "forced"
        else:
            try:
                timed_lines = apply_transcript_timings(
                    lyrics.lines,
                    {"segments": transcript_segments, "words": transcript_words},
                    duration,
                    vocal_regions,
                )
                timing_source = "whisperx-heuristic"
                word_timing_source = "distributed"
            except Exception as error:
                alignment_error = str(error)
                print(f"Warning: alignment step failed: {alignment_error}")
                timed_lines = estimate_line_timings(lyrics.lines, duration)
                timing_source = "line-estimated"
                word_timing_source = "distributed"

        (output_directory / "transcript-romanized.json").write_text(
            json.dumps(_romanized_transcript(transcript_words), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    stats = _alignment_stats(timed_lines, timing_source, word_timing_source, duration)
    if timing_source == "whisperx-forced":
        stats["alignmentConfidence"] = round(forced_confidence, 2)
        stats["lineDistribution"] = "forced"
    elif timing_source == "whisperx-heuristic":
        anchored = sum(1 for line in timed_lines if line.timing_quality == "anchored")
        stats["anchoredLines"] = anchored
        stats["alignmentConfidence"] = round(anchored / len(timed_lines), 2) if timed_lines else 0.0
        stats["lineDistribution"] = "heuristic"
        stats["regions"] = classify_gaps(timed_lines, vocal_regions)
    elif timing_source == "lrclib-line":
        stats["alignmentConfidence"] = 1.0
    if alignment_error:
        stats["error"] = alignment_error

    audio_audit = _audit_audio_durations(output_directory)
    if audio_audit["warnings"]:
        print("Warning: " + "; ".join(audio_audit["warnings"]))
    _write_alignment_debug(
        output_directory, timed_lines, duration, vocal_regions, audio_audit
    )
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
        word_timing_source,
    )
    _write_json(output_directory / "sync.json", sync_payload)
    (output_directory / "alignment-report.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    if not args.keep_stems:
        shutil.rmtree(stems_directory, ignore_errors=True)

    confidence = stats["alignmentConfidence"]
    if timing_source == "whisperx-heuristic" and confidence < 0.5:
        print(
            f"Warning: heuristic alignment confidence ({confidence}). "
            "Forced alignment was not reliable for this track."
        )
    print(
        f"Wrote {output_directory / 'sync.json'} "
        f"(timingSource={timing_source}, wordTimingSource={word_timing_source}, "
        f"confidence={confidence}, estimated={stats['estimatedWords']})"
    )
    return 0