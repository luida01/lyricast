from __future__ import annotations

import difflib
import gc
import re
import unicodedata
from pathlib import Path

from .lyrics import LyricLine, LyricWord


def normalize_word(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", without_accents.lower())


def _flatten(lines: list[LyricLine]) -> list[tuple[int, int, LyricWord]]:
    flattened: list[tuple[int, int, LyricWord]] = []
    for line_index, line in enumerate(lines):
        for word_index, word in enumerate(line.words):
            flattened.append((line_index, word_index, word))
    return flattened


def _map_transcript_words(
    lyric_words: list[LyricWord], transcript_words: list[dict[str, object]]
) -> dict[int, int]:
    lyric_tokens = [normalize_word(word.text) for word in lyric_words]
    transcript_tokens = [normalize_word(str(word.get("word", ""))) for word in transcript_words]
    matcher = difflib.SequenceMatcher(None, lyric_tokens, transcript_tokens, autojunk=False)
    mapping: dict[int, int] = {}

    for tag, lyric_start, lyric_end, transcript_start, transcript_end in matcher.get_opcodes():
        if tag == "equal":
            for lyric_index, transcript_index in zip(
                range(lyric_start, lyric_end), range(transcript_start, transcript_end)
            ):
                mapping[lyric_index] = transcript_index
        elif tag == "replace":
            length = min(lyric_end - lyric_start, transcript_end - transcript_start)
            for offset in range(length):
                lyric_index = lyric_start + offset
                transcript_index = transcript_start + offset
                similarity = difflib.SequenceMatcher(
                    None, lyric_tokens[lyric_index], transcript_tokens[transcript_index]
                ).ratio()
                if similarity >= 0.55:
                    mapping[lyric_index] = transcript_index
    return mapping


def _number(value: object, default: float) -> float:
    try:
        result = float(value)
        return result if result >= 0 else default
    except (TypeError, ValueError):
        return default


def apply_transcript_timings(
    lines: list[LyricLine], transcript_words: list[dict[str, object]], duration: float | None
) -> list[LyricLine]:
    flattened = _flatten(lines)
    mapping = _map_transcript_words([item[2] for item in flattened], transcript_words)
    known: dict[int, tuple[float, float]] = {}
    for lyric_index, transcript_index in mapping.items():
        transcript = transcript_words[transcript_index]
        start = _number(transcript.get("start"), 0.0)
        end = _number(transcript.get("end"), start + 0.35)
        known[lyric_index] = (start, max(end, start + 0.08))

    line_known: list[list[tuple[int, tuple[float, float]]]] = [[] for _ in lines]
    for lyric_index, (line_index, word_index, _word) in enumerate(flattened):
        if lyric_index in known:
            line_known[line_index].append((word_index, known[lyric_index]))

    line_bounds: list[tuple[float, float]] = []
    for line_index, line in enumerate(lines):
        anchors = line_known[line_index]
        start = anchors[0][1][0] if anchors else None
        end = anchors[-1][1][1] if anchors else None
        if start is None:
            start = line.start
        if end is None:
            end = line.end

        if start is None:
            previous_end = line_bounds[-1][1] if line_bounds else 0.0
            start = previous_end
        if end is None:
            next_anchor_start = None
            for future in line_known[line_index + 1:]:
                if future:
                    next_anchor_start = future[0][1][0]
                    break
            end = next_anchor_start if next_anchor_start is not None else duration
        if end is None or end <= start:
            end = start + max(1.0, len(line.words) * 0.4)
        line_bounds.append((start, end))

    for line_index, line in enumerate(lines):
        bounds = line_bounds[line_index]
        anchors = {word_index: timing for word_index, timing in line_known[line_index]}
        missing = [index for index in range(len(line.words)) if index not in anchors]
        runs: list[list[int]] = []
        current: list[int] = []
        for word_index in missing:
            if current and word_index != current[-1] + 1:
                runs.append(current)
                current = []
            current.append(word_index)
        if current:
            runs.append(current)

        for run in runs:
            previous = max((index for index in anchors if index < run[0]), default=None)
            following = min((index for index in anchors if index > run[-1]), default=None)
            left = anchors[previous] if previous is not None else (bounds[0], bounds[0])
            right = anchors[following] if following is not None else (bounds[1], bounds[1])
            available_start = left[1]
            available_end = right[0]
            if available_end <= available_start:
                available_start, available_end = bounds
            step = (available_end - available_start) / len(run)
            for offset, word_index in enumerate(run):
                start = available_start + step * offset
                end = available_start + step * (offset + 1)
                anchors[word_index] = (start, max(end, start + 0.08))

        anchor_indices = {index for index, _timing in line_known[line_index]}
        for word_index, word in enumerate(line.words):
            start, end = anchors.get(word_index, bounds)
            word.start = start
            word.end = max(end, start + 0.08)
            word.estimated = word_index not in anchor_indices
        line.start = bounds[0]
        line.end = bounds[1]

    return lines


def estimate_line_timings(lines: list[LyricLine], duration: float | None) -> list[LyricLine]:
    if not lines:
        return lines

    known_starts = [line.start for line in lines]
    total_duration = duration or max(len(lines) * 2.0, 1.0)
    unknown_count = sum(start is None for start in known_starts)
    if unknown_count:
        step = total_duration / len(lines)
        for index, line in enumerate(lines):
            if line.start is None:
                line.start = index * step

    for index, line in enumerate(lines):
        start = line.start or 0.0
        next_start = lines[index + 1].start if index + 1 < len(lines) else total_duration
        end = next_start if next_start and next_start > start else start + max(1.0, len(line.words) * 0.4)
        line.start = start
        line.end = end
        step = (end - start) / max(len(line.words), 1)
        for word_index, word in enumerate(line.words):
            word.start = start + step * word_index
            word.end = start + step * (word_index + 1)
            word.estimated = True
    return lines


def transcribe_words(
    audio_path: Path,
    language: str | None,
    model_name: str,
) -> tuple[list[dict[str, object]], str]:
    try:
        import torch
        import whisperx
    except ImportError as error:
        raise RuntimeError(
            "WhisperX is not installed. Run pipeline/setup.ps1 or use an Enhanced LRC result."
        ) from error

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    model_kwargs: dict[str, object] = {"device": device, "compute_type": compute_type}
    if language:
        model_kwargs["language"] = language

    print(f"Loading WhisperX model '{model_name}' on {device}...")
    model = whisperx.load_model(model_name, **model_kwargs)
    audio = whisperx.load_audio(str(audio_path))
    transcribe_kwargs: dict[str, object] = {"batch_size": 8}
    if language:
        transcribe_kwargs["language"] = language
    result = model.transcribe(audio, **transcribe_kwargs)
    detected_language = str(result.get("language") or language or "en")
    del model
    gc.collect()

    align_model, metadata = whisperx.load_align_model(
        language_code=detected_language,
        device=device,
    )
    aligned = whisperx.align(
        result["segments"],
        align_model,
        metadata,
        audio,
        device,
        return_char_alignments=False,
    )
    del align_model
    gc.collect()

    words: list[dict[str, object]] = []
    for segment in aligned.get("segments", []):
        for word in segment.get("words", []):
            if word.get("word") and word.get("start") is not None and word.get("end") is not None:
                words.append(word)
    if not words:
        raise RuntimeError("WhisperX did not return any word-level timestamps.")
    return words, detected_language
