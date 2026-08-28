from __future__ import annotations

import difflib
import gc
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

from .lyrics import LyricLine, LyricWord
from .romanize import romanize_korean

ALIGNMENT_VERSION = 5

BLOCK_MERGE_GAP = 0.5
BLOCK_MIN_DURATION = 1.0

MODE_A_BLOCK_RATIO = 0.4
MODE_A_MAX_GAP = 12.0

ANCHOR_MIN_SCORE = 0.35
ANCHOR_EST_DENOMINATOR = 0.75
ANCHOR_MAX_RUN = 16
ANCHOR_RUN_PENALTY = 0.015
FUZZY_MATCH_RATIO = 0.6
GAP_MIN_SPAN = 1.0
TRIM_OVERLAP_THRESHOLD = 0.25

ALIGN_MARGIN = 1.0
ALIGN_WINDOW_MERGE_GAP = 0.25
FORCED_MIN_CONFIDENCE = 0.35
FORCED_SKIP_LANGUAGES = frozenset("""
    ko ja zh th km my lo bo hi bn ta te mr gu kn ml pa ne si ur fa ar he am ti
    ka hy kk ky ug yi
""".split())

SPANISH_STOPWORDS = frozenset("""
    y a de la el los las que en con lo un una al del por no se su sus mi tu me
    te es e o u le les ya como mas pero esta este esto para entre si cuando muy
    sin sobre tambien hasta hay donde quien todo todos desde nos durante uno ni
    contra ese eso ante ellos que unos yo otro otra otras otros mucho muchos
    nada cual poco ella estar estas algo nosotros ahi adonde cualquiera dale asi
""".split())

VOCAL_WINDOW_SECONDS = 0.5
VOCAL_SILENCE_THRESHOLD = 0.02
VOCAL_MIN_ACTIVE_SECONDS = 0.8


def normalize_word(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", without_accents.lower())


def _number(value: object, default: float) -> float:
    try:
        result = float(value)
        return result if result >= 0 else default
    except (TypeError, ValueError):
        return default


def _word_interval(word: dict[str, object]) -> tuple[float, float] | None:
    start = _number(word.get("start"), -1.0)
    end = _number(word.get("end"), -1.0)
    if start < 0 or end <= start:
        return None
    return (start, end)


def _group_intervals(transcript_words: list[dict[str, object]]) -> list[tuple[float, float]]:
    """Group transcript words into intervals, preferring WhisperX segment boundaries."""
    by_segment: dict[int | None, list[tuple[float, float]]] = {}
    for word in transcript_words:
        interval = _word_interval(word)
        if interval is None:
            continue
        segment = word.get("segment")
        key = int(segment) if isinstance(segment, (int, float)) else None
        by_segment.setdefault(key, []).append(interval)

    intervals: list[tuple[float, float]] = []
    for key, items in by_segment.items():
        if key is None:
            intervals.extend(items)
        else:
            starts = [start for start, _end in items]
            ends = [end for _start, end in items]
            intervals.append((min(starts), max(ends)))
    return sorted(intervals)


def extract_vocal_blocks(
    transcript_words: list[dict[str, object]],
    merge_gap: float = BLOCK_MERGE_GAP,
    min_duration: float = BLOCK_MIN_DURATION,
) -> list[tuple[float, float]]:
    """Merge WhisperX intervals into contiguous vocal blocks separated by real pauses.

    The transcribed text is ignored: only the timestamps matter, so a wrong word
    can never shift the alignment.
    """
    blocks: list[tuple[float, float]] = []
    intervals = _group_intervals(transcript_words)
    if not intervals:
        return blocks
    start, end = intervals[0]
    for interval_start, interval_end in intervals[1:]:
        if interval_start - end <= merge_gap:
            end = max(end, interval_end)
        else:
            if end - start >= min_duration:
                blocks.append((start, end))
            start, end = interval_start, interval_end
    if end - start >= min_duration:
        blocks.append((start, end))
    return blocks


def _merge_blocks(
    blocks: list[tuple[float, float]], maximum: int
) -> list[tuple[float, float]]:
    """Merge the adjacent pair with the smallest pause until block count fits."""
    merged = list(blocks)
    while len(merged) > max(1, maximum):
        best_index = min(
            range(len(merged) - 1),
            key=lambda index: merged[index + 1][0] - merged[index][1],
        )
        left, right = merged[best_index], merged[best_index + 1]
        merged[best_index : best_index + 2] = [(left[0], right[1])]
    return merged


def _lines_per_block(line_count: int, blocks: list[tuple[float, float]]) -> list[int]:
    """Distribute lyric lines across vocal blocks proportionally to block duration."""
    total = sum(end - start for start, end in blocks)
    if total <= 0:
        return [0] * len(blocks)
    counts = [0] * len(blocks)
    remaining = line_count
    for index, (start, end) in enumerate(blocks):
        count = round(line_count * (end - start) / total)
        counts[index] = min(count, remaining)
        remaining -= counts[index]
    for index in range(len(counts)):
        if remaining <= 0:
            break
        counts[index] += 1
        remaining -= 1
    if line_count >= len(blocks):
        for index in range(len(counts)):
            if counts[index] == 0:
                largest = max(
                    range(len(counts)),
                    key=lambda i: counts[i] * 1000 + blocks[i][1] - blocks[i][0],
                )
                counts[largest] -= 1
                counts[index] = 1
    return counts


def _line_weight(line: LyricLine) -> int:
    return max(1, sum(len(normalize_word(word.text)) for word in line.words))


def _use_uniform_distribution(
    blocks: list[tuple[float, float]], line_count: int
) -> bool:
    """Transcription is too holey for block mapping when blocks are few relative
    to lines or a long pause separates them. Uniform distribution then spreads
    lines across the whole vocal timeline instead of cramming them into the
    transcribed regions."""
    if len(blocks) < line_count * MODE_A_BLOCK_RATIO:
        return True
    for index in range(len(blocks) - 1):
        if blocks[index + 1][0] - blocks[index][1] > MODE_A_MAX_GAP:
            return True
    return False


def _uniform_line_bounds(
    lines: list[LyricLine], blocks: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Even line distribution across the vocal timeline, snapped to block edges."""
    start_time = blocks[0][0]
    end_time = blocks[-1][1]
    if end_time <= start_time:
        return []
    total_span = end_time - start_time
    weights = [_line_weight(line) for line in lines]
    total_weight = sum(weights)
    positions: list[float] = [start_time]
    accumulated = 0.0
    for weight in weights:
        accumulated += weight
        positions.append(start_time + total_span * accumulated / total_weight)
    spacing = total_span / len(lines)
    max_snap = min(1.5, spacing / 2.0)
    used: set[int] = set()
    for block_start, _block_end in blocks[1:]:
        if block_start <= start_time or block_start >= end_time:
            continue
        best: int | None = None
        best_distance = max_snap + 1.0
        for index in range(1, len(positions) - 1):
            if index in used:
                continue
            distance = abs(positions[index] - block_start)
            if distance < best_distance:
                best_distance = distance
                best = index
        if best is None:
            continue
        previous = positions[best - 1]
        following = positions[best + 1]
        if block_start - previous < 0.05 or following - block_start < 0.05:
            continue
        positions[best] = block_start
        used.add(best)
    bounds: list[tuple[float, float]] = []
    for index in range(len(lines)):
        start = positions[index]
        end = positions[index + 1]
        bounds.append((start, max(end, start)))
    return bounds


def compute_line_bounds(
    lines: list[LyricLine],
    blocks: list[tuple[float, float]],
    duration: float | None,
) -> list[tuple[float, float]]:
    """Derive contiguous, monotonic line bounds from vocal activity.

    Fallback distribution used when no text anchors are available:
    - Blocks mode when the transcript is dense (pauses fall on line boundaries).
    - Uniform mode when the transcript has holes (a missing section never
      displaces the rest of the song).
    """
    del duration
    if not lines or not blocks:
        return []
    if _use_uniform_distribution(blocks, len(lines)):
        return _uniform_line_bounds(lines, blocks)
    blocks = _merge_blocks(blocks, len(lines))
    counts = _lines_per_block(len(lines), blocks)
    bounds: list[tuple[float, float]] = []
    line_index = 0
    for (block_start, block_end), count in zip(blocks, counts):
        if count <= 0:
            continue
        span = block_end - block_start
        weights = [_line_weight(lines[line_index + offset]) for offset in range(count)]
        total_weight = sum(weights)
        accumulated = 0.0
        for offset in range(count):
            start = block_start + span * accumulated / total_weight
            accumulated += weights[offset]
            end = block_start + span * accumulated / total_weight
            bounds.append((start, end))
            line_index += 1
    return bounds


def _text_tokens(text: object) -> list[str]:
    tokens: list[str] = []
    for raw in re.findall(r"\S+", str(text)):
        token = normalize_word(romanize_korean(raw))
        if token and token not in SPANISH_STOPWORDS:
            tokens.append(token)
    return tokens


def _average_line_tokens(lines: list[LyricLine]) -> float:
    counts = [len(_text_tokens(line.text)) for line in lines]
    counts = [count for count in counts if count > 0]
    if not counts:
        return 1.0
    return sum(counts) / len(counts)


def _token_overlap(segment_tokens: list[str], line_tokens: list[str]) -> float:
    """Fraction of the segment tokens found (exact or fuzzy) in the line run."""
    if not segment_tokens or not line_tokens:
        return 0.0
    pool = Counter(line_tokens)
    exact = 0
    for token in segment_tokens:
        if pool.get(token, 0) > 0:
            pool[token] -= 1
            exact += 1
    fuzzy = 0
    leftovers = list(pool.elements())
    for token in segment_tokens:
        best: str | None = None
        best_ratio = FUZZY_MATCH_RATIO
        for candidate in leftovers:
            ratio = difflib.SequenceMatcher(None, token, candidate).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best = candidate
        if best is not None:
            leftovers.remove(best)
            fuzzy += 1
    return (exact + 0.6 * fuzzy) / max(1, min(len(segment_tokens), len(line_tokens)))


def _segments_from_words(transcript_words: list[dict[str, object]]) -> list[dict[str, object]]:
    """Rebuild segment records from a flat word list (old transcript caches)."""
    by_segment: dict[int | None, list[dict[str, object]]] = {}
    for word in transcript_words:
        segment = word.get("segment")
        key = int(segment) if isinstance(segment, (int, float)) else None
        by_segment.setdefault(key, []).append(word)

    segments: list[dict[str, object]] = []
    for key in sorted(by_segment, key=lambda k: (k is None, k if k is not None else -1)):
        words = by_segment[key]
        if key is None:
            for word in words:
                segments.append({
                    "start": _number(word.get("start"), 0.0),
                    "end": _number(word.get("end"), 0.0),
                    "text": str(word.get("word", "")),
                    "words": [word],
                })
        else:
            segments.append({
                "start": _number(min((w.get("start") for w in words), default=0.0), 0.0),
                "end": _number(max((w.get("end") for w in words), default=0.0), 0.0),
                "text": " ".join(str(w.get("word", "")) for w in words),
                "words": words,
            })
    return segments


def _blocks_with_text(
    segments: list[dict[str, object]],
) -> list[tuple[float, float, str]]:
    """Merge segments into vocal blocks and attach their concatenated text."""
    merged: list[list[float | str]] = []
    for segment in segments:
        start = _number(segment.get("start"), -1.0)
        end = _number(segment.get("end"), -1.0)
        if start < 0 or end <= start:
            continue
        text = str(segment.get("text") or "")
        if not text.strip():
            text = " ".join(str(word.get("word", "")) for word in segment.get("words", []))
        if merged and start - float(merged[-1][1]) <= BLOCK_MERGE_GAP:
            merged[-1][1] = max(float(merged[-1][1]), end)
            if text.strip():
                merged[-1][2] = f"{merged[-1][2]} {text}".strip()
        else:
            merged.append([start, end, text.strip()])
    return [
        (float(start), float(end), str(text))
        for start, end, text in merged
        if float(end) - float(start) >= BLOCK_MIN_DURATION
    ]


def _anchor_ranges(
    lines: list[LyricLine],
    blocks_text: list[tuple[float, float, str]],
    duration: float | None,
) -> list[tuple[int, int, int]]:
    """Globally assign each vocal block a range of lyric lines.

    A dynamic program over (block, line) choices maximizes the total text
    overlap while keeping ranges monotonic and non-overlapping. Unlike a greedy
    cursor, a repeated refrain sung twice is matched to both occurrences
    instead of leaving the second one anchorless.

    The run length per block is estimated from its duration relative to the
    average line duration, so a long verse block anchors many lines instead of
    only its single best-matching line.
    """
    line_count = len(lines)
    if duration and duration > 0:
        average_line_duration = max(0.1, duration / max(1, line_count))
    else:
        average_line_duration = None
    average_tokens = _average_line_tokens(lines)
    block_options: list[list[tuple[int, int, float]]] = []
    for block_start, block_end, text in blocks_text:
        tokens = _text_tokens(text)
        if not tokens:
            block_options.append([])
            continue
        if average_line_duration is not None:
            estimated = max(1, min(ANCHOR_MAX_RUN, round((block_end - block_start) / average_line_duration)))
        else:
            estimated = max(1, min(ANCHOR_MAX_RUN, round(len(tokens) / average_tokens / ANCHOR_EST_DENOMINATOR)))
        run_lengths = sorted({estimated - 1, estimated, estimated + 1, estimated + 2} & set(range(1, ANCHOR_MAX_RUN + 1)))
        if not run_lengths:
            run_lengths = [estimated]
        options: list[tuple[int, int, float]] = []
        for run_len in run_lengths:
            for start in range(0, line_count - run_len + 1):
                line_tokens: list[str] = []
                for line_index in range(start, start + run_len):
                    line_tokens.extend(_text_tokens(lines[line_index].text))
                score = _token_overlap(tokens, line_tokens)
                score *= 1.0 - ANCHOR_RUN_PENALTY * abs(run_len - estimated)
                if score >= ANCHOR_MIN_SCORE:
                    options.append((start, run_len, score))
        block_options.append(options)

    dynamic = [-1.0] * line_count
    parent = [None] * line_count
    chosen = [None] * line_count
    prefix_best = [-1.0] * line_count
    prefix_idx = [-1] * line_count
    for block_index, options in enumerate(block_options):
        new_dynamic = dynamic[:]
        new_parent = parent[:]
        new_chosen = chosen[:]
        for start, run_len, score in options:
            # Chain only from states created by strictly earlier blocks; the
            # prefix is frozen from the previous iteration.
            if start > 0 and prefix_best[start - 1] >= 0:
                previous = prefix_best[start - 1]
                previous_index = prefix_idx[start - 1]
            else:
                previous = 0.0
                previous_index = -1
            end_line = start + run_len - 1
            total = previous + score
            if total > new_dynamic[end_line]:
                new_dynamic[end_line] = total
                new_parent[end_line] = previous_index
                new_chosen[end_line] = (block_index, start, run_len)
        running = -1.0
        running_index = -1
        for index in range(line_count):
            if new_dynamic[index] > running:
                running = new_dynamic[index]
                running_index = index
            prefix_best[index] = running
            prefix_idx[index] = running_index
        dynamic, parent, chosen = new_dynamic, new_parent, new_chosen

    best_end = max(range(line_count), key=lambda index: dynamic[index])
    if dynamic[best_end] <= 0:
        return []
    ranges: list[tuple[int, int, int]] = []
    while best_end >= 0 and chosen[best_end] is not None:
        block_index, start, run_len = chosen[best_end]
        ranges.append((block_index, start, start + run_len - 1))
        best_end = parent[best_end]
    ranges.reverse()

    # Trim range edges with no per-line content overlap: those lines belong to
    # the neighboring gap (e.g. a repeated hook at the start of a verse block),
    # not to this block. Trimming lets the vocal-gap slot place them correctly.
    trimmed: list[tuple[int, int, int]] = []
    for block_index, start, end in ranges:
        tokens = _text_tokens(blocks_text[block_index][2])
        if not tokens:
            trimmed.append((block_index, start, end))
            continue
        while start < end:
            line_tokens = _text_tokens(lines[start].text)
            if line_tokens and _token_overlap(tokens, line_tokens) >= TRIM_OVERLAP_THRESHOLD:
                break
            start += 1
        while end > start:
            line_tokens = _text_tokens(lines[end].text)
            if line_tokens and _token_overlap(tokens, line_tokens) >= TRIM_OVERLAP_THRESHOLD:
                break
            end -= 1
        if start <= end:
            trimmed.append((block_index, start, end))
    return trimmed


def _has_vocal(start: float, end: float, vocal_regions: list[tuple[float, float]] | None) -> bool:
    return any(region_start < end and region_end > start for region_start, region_end in (vocal_regions or []))


def _bounds_from_anchors(
    lines: list[LyricLine],
    blocks: list[tuple[float, float]],
    ranges: list[tuple[int, int, int]],
    vocal_regions: list[tuple[float, float]] | None,
    duration: float | None,
) -> tuple[list[tuple[float, float]], list[str]]:
    """Distribute lines across block spans, respecting anchored ranges.

    Anchored lines live in their block's span. Unanchored lines between two
    anchored ranges go into a transcription-gap slot when the inter-block span
    is large and the audio has vocal energy there; otherwise they stay in the
    previous block's span. Real instrumental pauses (no vocals, no lines)
    remain as gaps between lines.
    """
    line_count = len(lines)
    slot: list[object] = [None] * line_count
    qualities = ["distributed"] * line_count
    for block_index, start, end in ranges:
        for line_index in range(start, end + 1):
            slot[line_index] = block_index
            qualities[line_index] = "anchored"

    ordered = sorted(ranges, key=lambda item: item[1])
    for index in range(len(ordered) - 1):
        block_previous, _start_previous, range_end = ordered[index]
        block_next, range_start, _end_next = ordered[index + 1]
        unanchored = [line_index for line_index in range(range_end + 1, range_start) if slot[line_index] is None]
        if not unanchored:
            continue
        span_start = blocks[block_previous][1]
        span_end = blocks[block_next][0]
        if span_end - span_start > GAP_MIN_SPAN and _has_vocal(span_start, span_end, vocal_regions):
            for line_index in unanchored:
                slot[line_index] = ("gap", span_start, span_end)
        else:
            for line_index in unanchored:
                slot[line_index] = block_previous

    if ordered:
        first_block = ordered[0][0]
        last_block = ordered[-1][0]
        for line_index in range(0, ordered[0][1]):
            if slot[line_index] is None:
                slot[line_index] = first_block
        for line_index in range(ordered[-1][2] + 1, line_count):
            if slot[line_index] is None:
                slot[line_index] = last_block

    groups: list[tuple[object, list[int]]] = []
    for line_index in range(line_count):
        current = slot[line_index]
        if current is None:
            current = ("gap", blocks[0][0], duration if duration else blocks[-1][1])
        if groups and groups[-1][0] == current:
            groups[-1][1].append(line_index)
        else:
            groups.append((current, [line_index]))

    bounds: list[tuple[float, float]] = []
    for slot_key, line_indices in groups:
        if isinstance(slot_key, tuple):
            start, end = float(slot_key[1]), float(slot_key[2])
        else:
            start, end = blocks[int(slot_key)]
        span = max(0.0, end - start)
        weights = [_line_weight(lines[line_index]) for line_index in line_indices]
        total_weight = sum(weights)
        accumulated = 0.0
        for offset, line_index in enumerate(line_indices):
            line_start = start + span * accumulated / total_weight
            accumulated += weights[offset]
            line_end = start + span * accumulated / total_weight
            bounds.append((line_start, max(line_end, line_start)))
    return bounds, qualities


def _build_alignment_windows(
    segments: list[dict[str, object]],
) -> list[list[float]]:
    """Merge pass-1 segments into contiguous windows and insert transcription holes.

    Holes between segments larger than one second get their own window so the
    lines sung there (missed by WhisperX) still receive forced alignment.
    """
    windows: list[list[float]] = []
    for segment in segments:
        start = _number(segment.get("start"), -1.0)
        end = _number(segment.get("end"), -1.0)
        if start < 0 or end <= start:
            continue
        windows.append([start, end])
    windows.sort(key=lambda item: item[0])
    merged: list[list[float]] = []
    for start, end in windows:
        if merged and start - merged[-1][1] <= ALIGN_WINDOW_MERGE_GAP:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    with_holes: list[list[float]] = []
    for start, end in merged:
        if with_holes and start - with_holes[-1][1] > GAP_MIN_SPAN:
            with_holes.append([with_holes[-1][1], start])
        with_holes.append([start, end])
    return with_holes


def _assign_lines_to_windows(
    lines: list[LyricLine], windows: list[list[float]]
) -> list[tuple[int, list[int]]]:
    """Partition lyric lines across windows proportionally to line weight."""
    if not lines or not windows:
        return []
    start_time = windows[0][0]
    end_time = windows[-1][1]
    span = max(0.0, end_time - start_time)
    weights = [_line_weight(line) for line in lines]
    total_weight = sum(weights)
    positions: list[float] = [start_time]
    accumulated = 0.0
    for weight in weights:
        accumulated += weight
        positions.append(start_time + span * accumulated / total_weight)

    assignments: list[list[int]] = [[] for _ in windows]
    for line_index in range(len(lines)):
        line_start = positions[line_index]
        window_index = 0
        for index, (start, end) in enumerate(windows):
            if line_start >= start and line_start < end:
                window_index = index
                break
            if line_start < start:
                window_index = index
                break
        assignments[window_index].append(line_index)
    return [(index, lines_index) for index, lines_index in enumerate(assignments) if lines_index]


def _map_aligned_words_to_lines(
    lines: list[LyricLine],
    segments: list[dict[str, object]],
    aligned_words: list[dict[str, object]],
) -> bool:
    """Slice the flattened aligned words back onto their lyric lines.

    Every window contributes exactly the number of words its text has, so the
    flattened sequence maps 1:1 onto the lyric words in order.
    """
    word_index = 0
    for segment in segments:
        expected = sum(len(lines[line_index].words) for line_index in segment["lines"])
        window_words = aligned_words[word_index : word_index + expected]
        word_index += expected
        if len(window_words) != expected:
            return False
        cursor = 0
        for line_index in segment["lines"]:
            line = lines[line_index]
            count = len(line.words)
            for word, aligned in zip(line.words, window_words[cursor : cursor + count]):
                start = _number(aligned.get("start"), -1.0)
                end = _number(aligned.get("end"), -1.0)
                if start >= 0 and end > start:
                    word.start = start
                    word.end = end
                    word.estimated = False
            cursor += count
    return word_index == len(aligned_words)


def _finalize_line_bounds(lines: list[LyricLine]) -> None:
    """Derive line bounds from aligned words and enforce global monotonicity."""
    previous_end = 0.0
    for line in lines:
        for word in line.words:
            if word.start is None:
                continue
            if word.start < previous_end:
                word.start = previous_end
                word.end = max(word.end or word.start, word.start + 0.05)
            previous_end = max(previous_end, word.end or word.start)
        starts = [word.start for word in line.words if word.start is not None]
        ends = [word.end for word in line.words if word.end is not None]
        if starts and ends:
            line.start = min(starts)
            line.end = max(ends)
            line.timing_quality = "anchored"

    for index, line in enumerate(lines):
        if line.start is not None:
            continue
        previous_end = lines[index - 1].end if index > 0 and lines[index - 1].end is not None else 0.0
        next_start = (
            lines[index + 1].start
            if index + 1 < len(lines) and lines[index + 1].start is not None
            else previous_end + max(1.0, len(line.words) * 0.4)
        )
        start = previous_end
        end = max(next_start, previous_end + max(0.5, len(line.words) * 0.2))
        line.start = start
        line.end = end
        span = max(0.0, end - start)
        total = sum(max(1.0, len(word.text)) for word in line.words)
        accumulated = 0.0
        for word in line.words:
            word_start = start + span * accumulated / total
            accumulated += max(1.0, len(word.text))
            word.end = start + span * accumulated / total
            word.start = word_start
            word.estimated = True


def force_align_lyrics(
    lines: list[LyricLine],
    transcript_segments: list[dict[str, object]],
    audio_path: Path,
    language: str | None,
) -> tuple[list[LyricLine], float] | None:
    """Forced-align the known lyrics against the vocal audio with WhisperX.

    The lyrics are grouped into windows derived from the pass-1 transcript and
    re-aligned phonetically, so the word timestamps belong to the displayed
    text. Returns None when the language is unsupported, the audio cannot be
    loaded, or the average CTC confidence is below the threshold.
    """
    try:
        import numpy as np
        import torch
        import whisperx
    except ImportError:
        return None

    detected = (language or "en").lower()
    if detected in FORCED_SKIP_LANGUAGES:
        return None

    windows = _build_alignment_windows(transcript_segments)
    assignments = _assign_lines_to_windows(lines, windows)
    if not assignments:
        return None

    start_time = windows[0][0]
    end_time = windows[-1][1]
    segments: list[dict[str, object]] = []
    for window_index, line_indices in assignments:
        window_start, window_end = windows[window_index]
        text = " ".join(
            word.text for line_index in line_indices for word in lines[line_index].words
        )
        if not text.strip():
            continue
        segments.append({
            "start": max(start_time, window_start - ALIGN_MARGIN),
            "end": min(end_time, window_end + ALIGN_MARGIN),
            "text": text,
            "lines": line_indices,
        })

    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        align_model, metadata = whisperx.load_align_model(
            language_code=detected,
            device=device,
        )
        audio = whisperx.load_audio(str(audio_path))
        result = whisperx.align(segments, align_model, metadata, audio, device)
    except Exception:
        return None

    aligned_words = [
        dict(word) for segment in result.get("segments", []) for word in segment.get("words", [])
    ]
    if not aligned_words:
        return None
    scores = [float(_number(word.get("score"), 0.0)) for word in aligned_words]
    confidence = float(np.mean(scores)) if scores else 0.0
    if confidence < FORCED_MIN_CONFIDENCE:
        return None

    for line in lines:
        for word in line.words:
            word.start = None
            word.end = None
            word.estimated = True

    if not _map_aligned_words_to_lines(lines, segments, aligned_words):
        return None
    _finalize_line_bounds(lines)
    return lines, confidence


def apply_transcript_timings(
    lines: list[LyricLine],
    transcript: dict[str, object] | list[dict[str, object]],
    duration: float | None,
    vocal_regions: list[tuple[float, float]] | None = None,
) -> list[LyricLine]:
    """Align lyric lines to the transcript by text-anchored regions.

    WhisperX text is used only to locate which lyric lines fall in each vocal
    block; it never replaces the displayed lyrics. Line timings inside a block
    are distributed proportionally, and transcription gaps with vocal energy
    get their own slot so a missing section never displaces the song.
    """
    if isinstance(transcript, list):
        transcript = {"words": transcript}
    words = transcript.get("words") or []
    segments = transcript.get("segments") or _segments_from_words(words)
    blocks_text = _blocks_with_text(segments)
    if not blocks_text:
        return estimate_line_timings(lines, duration)
    blocks = [(start, end) for start, end, _text in blocks_text]
    ranges = _anchor_ranges(lines, blocks_text, duration)
    if not ranges:
        bounds = compute_line_bounds(lines, blocks, duration)
        if not bounds:
            return estimate_line_timings(lines, duration)
        distribute_word_timings(lines, bounds)
        for line in lines:
            line.timing_quality = "fallback"
        return lines
    bounds, qualities = _bounds_from_anchors(lines, blocks, ranges, vocal_regions, duration)
    distribute_word_timings(lines, bounds)
    for line, quality in zip(lines, qualities):
        line.timing_quality = quality
    return lines


def _fill_missing_starts(
    lines: list[LyricLine], duration: float | None
) -> None:
    index = 0
    while index < len(lines):
        if lines[index].start is not None:
            index += 1
            continue
        run_begin = index
        while index < len(lines) and lines[index].start is None:
            index += 1
        run_end = index
        previous_time = lines[run_begin - 1].start if run_begin > 0 else 0.0
        if run_end < len(lines):
            next_time = lines[run_end].start
        elif duration is not None:
            next_time = duration
        else:
            next_time = (previous_time or 0.0) + max(2.0, (run_end - run_begin) * 2.0)
        previous_time = previous_time if previous_time is not None else 0.0
        next_time = next_time if next_time is not None else previous_time
        span = max(next_time - previous_time, (run_end - run_begin) * 0.5)
        for offset in range(run_end - run_begin):
            fraction = (offset + 1) / (run_end - run_begin + 1)
            lines[run_begin + offset].start = previous_time + span * fraction


def apply_line_timestamp_timings(
    lines: list[LyricLine], duration: float | None
) -> list[LyricLine]:
    """Use LRCLIB line timestamps as the timing structure.

    Line starts come from the published LRC; ends are the next line's start (or
    the song duration for the last line). Words are distributed inside each line.
    WhisperX is not involved, so the lyric structure and tempo are preserved as
    published and can never be displaced by a transcription hole.
    """
    if not lines:
        return lines
    if not any(line.start is not None for line in lines):
        return estimate_line_timings(lines, duration)
    _fill_missing_starts(lines, duration)
    bounds: list[tuple[float, float]] = []
    for index, line in enumerate(lines):
        start = line.start if line.start is not None else 0.0
        end = line.end if line.end is not None and line.end > start else None
        if end is None:
            next_start = lines[index + 1].start if index + 1 < len(lines) else None
            if next_start is not None and next_start > start:
                end = next_start
            elif duration is not None and duration > start:
                end = duration
            else:
                end = start + max(1.0, len(line.words) * 0.4)
        if duration is not None and end > duration:
            end = duration
        if end <= start:
            end = start + max(0.5, len(line.words) * 0.1)
        bounds.append((start, end))
    distribute_word_timings(lines, bounds)
    for line in lines:
        line.timing_quality = "lrclib"
    return lines


def validate_semantics(
    lines: list[LyricLine], duration: float | None = None
) -> list[str]:
    """Heuristic warnings about timing plausibility (not math violations)."""
    warnings: list[str] = []
    previous_end: float | None = None
    for index, line in enumerate(lines):
        start = line.start if line.start is not None else 0.0
        end = line.end if line.end is not None else start
        if len(line.words) > 3 and end - start < 0.3:
            warnings.append(
                f"line {index}: very short ({end - start:.2f}s) for {len(line.words)} words"
            )
        if previous_end is not None and duration is not None:
            gap = start - previous_end
            if gap > 20:
                warnings.append(f"line {index}: instrumental gap of {gap:.1f}s")
        previous_end = end
    return warnings


def classify_gaps(
    lines: list[LyricLine], vocal_regions: list[tuple[float, float]] | None
) -> list[dict[str, object]]:
    """Report pauses longer than 3s between lines.

    Pauses with vocal energy are transcription gaps (WhisperX missed content);
    pauses without energy are real instrumental breaks.
    """
    regions: list[dict[str, object]] = []
    for index in range(len(lines) - 1):
        gap_start = lines[index].end if lines[index].end is not None else (lines[index].start or 0.0)
        gap_end = lines[index + 1].start if lines[index + 1].start is not None else gap_start
        if gap_end - gap_start <= 3.0:
            continue
        regions.append({
            "start": round(gap_start, 2),
            "end": round(gap_end, 2),
            "type": "transcription-gap" if _has_vocal(gap_start, gap_end, vocal_regions) else "instrumental",
        })
    return regions


def _active_regions_from_rms(
    rms: list[float] | object,
    window_seconds: float,
    min_active_seconds: float,
) -> list[tuple[float, float]]:
    regions: list[tuple[float, float]] = []
    start: float | None = None
    for index, value in enumerate(rms):
        if value >= VOCAL_SILENCE_THRESHOLD and start is None:
            start = index * window_seconds
        elif value < VOCAL_SILENCE_THRESHOLD and start is not None:
            end = index * window_seconds
            if end - start >= min_active_seconds:
                regions.append((start, end))
            start = None
    if start is not None:
        end = len(rms) * window_seconds
        if end - start >= min_active_seconds:
            regions.append((start, end))
    return regions


def detect_vocal_activity(
    audio_path: Path,
    window_seconds: float = VOCAL_WINDOW_SECONDS,
) -> list[tuple[float, float]]:
    """Detect vocal-energy regions from the separated vocal stem via RMS."""
    try:
        import numpy as np
        from whisperx.audio import load_audio
    except ImportError:
        return []
    audio = load_audio(str(audio_path))
    sample_rate = 16000
    step = int(sample_rate * window_seconds)
    count = len(audio) // step
    if count == 0:
        return []
    frames = audio[: count * step].reshape(count, step)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    return _active_regions_from_rms(rms, window_seconds, VOCAL_MIN_ACTIVE_SECONDS)


def distribute_word_timings(
    lines: list[LyricLine], bounds: list[tuple[float, float]]
) -> list[LyricLine]:
    """Spread each line's words across its bounds, weighted by visual length.

    Words keep the lyric text untouched; only the timing is assigned. Every word
    is marked estimated because the timing was distributed, not measured.
    """
    for line, (start, end) in zip(lines, bounds):
        word_count = len(line.words)
        if word_count == 0:
            line.start = start
            line.end = end
            continue
        weights = [max(1.0, float(len(word.text))) for word in line.words]
        total_weight = sum(weights)
        span = max(0.0, end - start)
        accumulated = 0.0
        for word, weight in zip(line.words, weights):
            word_start = start + span * accumulated / total_weight
            accumulated += weight
            word_end = start + span * accumulated / total_weight
            if word_end <= word_start:
                word_end = word_start + 0.001
            word.start = word_start
            word.end = word_end
            word.estimated = True
        line.start = start
        line.end = end
    return lines


def validate_line_timings(
    lines: list[LyricLine], duration: float | None = None
) -> list[str]:
    warnings: list[str] = []
    previous_end: float | None = None
    for index, line in enumerate(lines):
        start = line.start if line.start is not None else 0.0
        end = line.end if line.end is not None else start
        if end < start:
            warnings.append(f"line {index}: end before start")
        if previous_end is not None and start < previous_end - 0.001:
            warnings.append(f"line {index}: overlaps previous line")
        if duration is not None and end > duration + 0.001:
            warnings.append(f"line {index}: exceeds song duration")
        previous_end = end
    return warnings


def validate_word_timings(
    lines: list[LyricLine], duration: float | None = None
) -> list[str]:
    warnings: list[str] = []
    for line_index, line in enumerate(lines):
        line_start = line.start if line.start is not None else 0.0
        line_end = line.end if line.end is not None else line_start
        previous_end: float | None = None
        for word_index, word in enumerate(line.words):
            start = word.start if word.start is not None else 0.0
            end = word.end if word.end is not None else start
            label = f"word {line_index}.{word_index}"
            if end < start:
                warnings.append(f"{label}: end before start")
            if previous_end is not None and start < previous_end - 0.001:
                warnings.append(f"{label}: overlaps previous word")
            if start < line_start - 0.001 or end > line_end + 0.001:
                warnings.append(f"{label}: outside line bounds")
            previous_end = end
    return warnings


def sanitize_word_timings(
    lines: list[LyricLine], duration: float | None = None
) -> list[LyricLine]:
    """Make timestamped LRC word timings monotonic and contained in their lines."""
    for line in lines:
        line_start = line.start if line.start is not None else 0.0
        line_end = line.end if line.end is not None else (
            duration if duration else line_start + 1.0
        )
        if line_end <= line_start:
            line_end = line_start + max(0.5, len(line.words) * 0.3)
        for word in line.words:
            if word.start is None:
                continue
            start = min(max(word.start, line_start), line_end)
            end = word.end if word.end is not None else start + 0.3
            end = min(max(end, start + 0.05), line_end)
            if end <= start:
                end = line_end if line_end > start else start + 0.05
            word.start = start
            word.end = end
            word.estimated = False
        previous_end = line_start
        for word in line.words:
            if word.start is None:
                continue
            if word.start < previous_end:
                word.start = min(previous_end, line_end)
                word.end = min(line_end, max(word.end or word.start, word.start + 0.05))
                word.estimated = True
            previous_end = max(previous_end, word.end or word.start)
    for line in lines:
        line.timing_quality = "lrclib"
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
        line.timing_quality = "fallback"
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
) -> tuple[list[dict[str, object]], str, list[dict[str, object]]]:
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

    segments: list[dict[str, object]] = []
    for segment_index, segment in enumerate(aligned.get("segments", [])):
        segment_words: list[dict[str, object]] = []
        for word in segment.get("words", []):
            if word.get("word") and word.get("start") is not None and word.get("end") is not None:
                cloned = dict(word)
                cloned["segment"] = segment_index
                segment_words.append(cloned)
        segments.append({
            "start": _number(segment.get("start"), 0.0),
            "end": _number(segment.get("end"), 0.0),
            "text": str(segment.get("text") or ""),
            "words": segment_words,
        })
    words = [word for segment in segments for word in segment["words"]]
    if not words:
        raise RuntimeError("WhisperX did not return any word-level timestamps.")
    return words, detected_language, segments