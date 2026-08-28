import unittest
from pathlib import Path

from lyricast_pipeline.alignment import (
    _active_regions_from_rms,
    _anchor_ranges,
    _assign_lines_to_windows,
    _build_alignment_windows,
    _finalize_line_bounds,
    _map_aligned_words_to_lines,
    _segments_from_words,
    _token_overlap,
    _use_uniform_distribution,
    apply_line_timestamp_timings,
    apply_transcript_timings,
    classify_gaps,
    compute_line_bounds,
    distribute_word_timings,
    extract_vocal_blocks,
    force_align_lyrics,
    sanitize_word_timings,
    validate_line_timings,
    validate_semantics,
    validate_word_timings,
)
from lyricast_pipeline.lyrics import LyricLine, LyricWord
from lyricast_pipeline.romanize import romanize_korean

# Codepoints verified to romanize to the expected tokens (avoids source-encoding issues).
SA = chr(0xC0AC)  # sa
NEO = chr(0xB108)  # neo
GEU = chr(0xADF8)  # geu
DAE = chr(0xB300)  # dae


def line(text: str) -> LyricLine:
    return LyricLine(text, [LyricWord(word) for word in text.split()])


def rounded(bounds: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(round(start, 6), round(end, 6)) for start, end in bounds]


class RomanizeTest(unittest.TestCase):
    def test_hangul_romanizes(self) -> None:
        self.assertEqual(romanize_korean(SA), "sa")
        self.assertEqual(romanize_korean(NEO), "neo")
        self.assertEqual(romanize_korean(GEU + DAE), "geudae")

    def test_non_hangul_passthrough(self) -> None:
        self.assertEqual(romanize_korean("still with you"), "still with you")

    def test_mixed_text(self) -> None:
        self.assertEqual(romanize_korean("still " + NEO), "still neo")


class VocalBlocksTest(unittest.TestCase):
    def test_merges_close_words_into_single_block(self) -> None:
        words = [
            {"word": "a", "start": 1.0, "end": 1.4},
            {"word": "b", "start": 1.5, "end": 1.9},
            {"word": "c", "start": 2.1, "end": 2.5},
        ]
        blocks = extract_vocal_blocks(words)
        self.assertEqual(blocks, [(1.0, 2.5)])

    def test_splits_on_long_gaps(self) -> None:
        words = [
            {"word": "a", "start": 1.0, "end": 2.0},
            {"word": "b", "start": 4.0, "end": 5.0},
        ]
        blocks = extract_vocal_blocks(words)
        self.assertEqual(blocks, [(1.0, 2.0), (4.0, 5.0)])

    def test_drops_short_blocks(self) -> None:
        words = [
            {"word": "a", "start": 1.0, "end": 1.3},
            {"word": "b", "start": 5.0, "end": 6.0},
        ]
        blocks = extract_vocal_blocks(words)
        self.assertEqual(blocks, [(5.0, 6.0)])

    def test_returns_empty_without_valid_words(self) -> None:
        self.assertEqual(extract_vocal_blocks([]), [])
        self.assertEqual(extract_vocal_blocks([{"word": "x", "start": 2.0, "end": 1.0}]), [])

    def test_groups_by_segment_index(self) -> None:
        words = [
            {"word": "a", "start": 1.0, "end": 1.4, "segment": 0},
            {"word": "b", "start": 1.6, "end": 2.0, "segment": 0},
            {"word": "c", "start": 5.0, "end": 6.0, "segment": 1},
        ]
        blocks = extract_vocal_blocks(words)
        self.assertEqual(blocks, [(1.0, 2.0), (5.0, 6.0)])


class LineBoundsTest(unittest.TestCase):
    def test_one_to_one_line_block_mapping(self) -> None:
        lines = [line("one two"), line("three four"), line("five six")]
        blocks = [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
        bounds = compute_line_bounds(lines, blocks, 7.0)
        self.assertEqual(rounded(bounds), [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)])

    def test_preserves_intro_and_outro_silence(self) -> None:
        lines = [line("one two"), line("three four")]
        blocks = [(4.0, 5.0), (8.0, 9.0)]
        bounds = compute_line_bounds(lines, blocks, 12.0)
        self.assertEqual(bounds, [(4.0, 5.0), (8.0, 9.0)])

    def test_subdivides_long_blocks_when_more_lines(self) -> None:
        lines = [line("a b c"), line("d e"), line("f g"), line("h i j")]
        blocks = [(10.0, 20.0)]
        bounds = compute_line_bounds(lines, blocks, 30.0)
        self.assertEqual(bounds, [(10.0, 13.0), (13.0, 15.0), (15.0, 17.0), (17.0, 20.0)])

    def test_uneven_line_split_within_block(self) -> None:
        lines = [line("a b"), line("c d e f")]
        blocks = [(5.0, 8.0)]
        bounds = compute_line_bounds(lines, blocks, 9.0)
        self.assertEqual(bounds, [(5.0, 6.0), (6.0, 8.0)])

    def test_merges_blocks_when_more_blocks_than_lines(self) -> None:
        lines = [line("one two"), line("three four"), line("five six")]
        blocks = [(1.0, 1.8), (2.0, 2.6), (5.0, 5.8), (7.0, 7.6)]
        bounds = compute_line_bounds(lines, blocks, 9.0)
        self.assertEqual(rounded(bounds), [(1.0, 2.6), (5.0, 5.8), (7.0, 7.6)])

    def test_returns_empty_without_blocks(self) -> None:
        lines = [line("one two")]
        self.assertEqual(compute_line_bounds(lines, [], 5.0), [])


class LineTimestampTimingsTest(unittest.TestCase):
    def test_preserves_lrc_line_starts(self) -> None:
        items = [
            LyricLine("one two", [LyricWord("one"), LyricWord("two")], 2.0, None),
            LyricLine("three four", [LyricWord("three"), LyricWord("four")], 5.0, None),
        ]
        timed = apply_line_timestamp_timings(items, 10.0)
        self.assertEqual(timed[0].start, 2.0)
        self.assertEqual(timed[0].end, 5.0)
        self.assertEqual(timed[1].start, 5.0)
        self.assertEqual(timed[1].end, 10.0)
        self.assertTrue(timed[0].words[0].estimated)
        self.assertGreaterEqual(timed[0].words[0].start, 2.0)
        self.assertLessEqual(timed[0].words[0].end, 5.0)

    def test_interpolates_missing_starts(self) -> None:
        items = [
            LyricLine("a b", [LyricWord("a"), LyricWord("b")], 1.0, None),
            LyricLine("c d", [LyricWord("c"), LyricWord("d")], None, None),
            LyricLine("e f", [LyricWord("e"), LyricWord("f")], 4.0, None),
        ]
        timed = apply_line_timestamp_timings(items, 8.0)
        self.assertEqual(timed[1].start, 2.5)

    def test_falls_back_to_estimate_without_timestamps(self) -> None:
        items = [LyricLine("a b", [LyricWord("a"), LyricWord("b")])]
        timed = apply_line_timestamp_timings(items, 6.0)
        self.assertEqual(timed[0].start, 0.0)
        self.assertTrue(timed[0].words[0].estimated)


class UniformDistributionTest(unittest.TestCase):
    def test_fills_transcription_holes_with_even_lines(self) -> None:
        lines = [line("line one two") for _ in range(10)]
        blocks = [(1.0, 5.0), (20.0, 25.0)]
        bounds = compute_line_bounds(lines, blocks, 30.0)
        self.assertEqual(round(bounds[0][0], 3), 1.0)
        self.assertEqual(round(bounds[-1][1], 3), 25.0)
        for index in range(1, len(bounds)):
            self.assertGreaterEqual(bounds[index][0], bounds[index - 1][1] - 1e-9)
        self.assertAlmostEqual(bounds[8][0], 20.0, places=1)

    def test_mode_selection(self) -> None:
        self.assertTrue(_use_uniform_distribution([(1.0, 2.0), (20.0, 21.0)], 3))
        self.assertTrue(_use_uniform_distribution([(1.0, 5.0)], 10))
        self.assertFalse(_use_uniform_distribution([(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)], 3))


class SemanticValidationTest(unittest.TestCase):
    def test_detects_very_short_lines_and_large_gaps(self) -> None:
        items = [
            LyricLine(
                "a b c d e",
                [LyricWord("a"), LyricWord("b"), LyricWord("c"), LyricWord("d"), LyricWord("e")],
                1.0,
                1.2,
            ),
            LyricLine("x y", [LyricWord("x"), LyricWord("y")], 30.0, 31.0),
        ]
        warnings = validate_semantics(items, 40.0)
        self.assertTrue(any("very short" in warning for warning in warnings))
        self.assertTrue(any("gap" in warning for warning in warnings))


class WordDistributionTest(unittest.TestCase):
    def test_proportional_to_length(self) -> None:
        lines = [LyricLine("ab c", [LyricWord("ab"), LyricWord("c")])]
        distribute_word_timings(lines, [(10.0, 13.0)])
        self.assertEqual(lines[0].words[0].start, 10.0)
        self.assertEqual(lines[0].words[0].end, 12.0)
        self.assertEqual(lines[0].words[1].start, 12.0)
        self.assertEqual(lines[0].words[1].end, 13.0)
        self.assertTrue(lines[0].words[0].estimated)

    def test_single_word_fills_line(self) -> None:
        lines = [LyricLine("hello", [LyricWord("hello")])]
        distribute_word_timings(lines, [(2.0, 3.0)])
        self.assertEqual((lines[0].words[0].start, lines[0].words[0].end), (2.0, 3.0))

    def test_monotonic_contiguous_words(self) -> None:
        lines = [LyricLine("one two six", [LyricWord("one"), LyricWord("two"), LyricWord("six")])]
        distribute_word_timings(lines, [(0.0, 6.0)])
        words = lines[0].words
        self.assertEqual(words[0].start, 0.0)
        self.assertEqual(words[0].end, 2.0)
        self.assertEqual(words[1].start, 2.0)
        self.assertEqual(words[1].end, 4.0)
        self.assertEqual(words[2].start, 4.0)
        self.assertEqual(words[2].end, 6.0)


class ApplyTranscriptTimingsTest(unittest.TestCase):
    def test_korean_transcript_provides_timing_only(self) -> None:
        lyric = romanize_korean(SA) + " " + romanize_korean(NEO)
        lines = [
            LyricLine(
                lyric,
                [LyricWord(romanize_korean(SA)), LyricWord(romanize_korean(NEO))],
            )
        ]
        transcript = [
            {"word": SA, "start": 1.0, "end": 1.5},
            {"word": NEO, "start": 1.5, "end": 2.1},
        ]

        timed = apply_transcript_timings(lines, transcript, 3.0)

        self.assertEqual(timed[0].start, 1.0)
        self.assertEqual(timed[0].end, 2.1)
        self.assertTrue(timed[0].words[0].estimated)
        self.assertEqual(round(timed[0].words[0].start, 3), 1.0)
        self.assertEqual(round(timed[0].words[0].end, 3), 1.44)
        self.assertEqual(round(timed[0].words[1].start, 3), 1.44)
        self.assertEqual(round(timed[0].words[1].end, 3), 2.1)

    def test_transcription_errors_do_not_shift_timing(self) -> None:
        lines = [line("hello world"), line("you are here")]
        transcript = [
            {"word": "gibberish", "start": 2.0, "end": 2.6},
            {"word": "nonsense", "start": 2.6, "end": 3.2},
            {"word": "wrong", "start": 6.0, "end": 6.8},
            {"word": "stuff", "start": 6.8, "end": 7.4},
        ]

        timed = apply_transcript_timings(lines, transcript, 10.0)

        self.assertEqual(timed[0].start, 2.0)
        self.assertEqual(timed[0].end, 3.2)
        self.assertEqual(timed[1].start, 6.0)
        self.assertEqual(timed[1].end, 7.4)
        self.assertTrue(all(word.estimated for word in timed[0].words))

    def test_repeated_lines_keep_sequential_timing(self) -> None:
        lines = [line("hello world"), line("other words"), line("hello world")]
        transcript = [
            {"word": "hello", "start": 1.0, "end": 1.5},
            {"word": "world", "start": 1.5, "end": 2.0},
            {"word": "other", "start": 3.0, "end": 3.5},
            {"word": "words", "start": 3.5, "end": 4.0},
            {"word": "hello", "start": 5.0, "end": 5.5},
            {"word": "world", "start": 5.5, "end": 6.0},
        ]

        timed = apply_transcript_timings(lines, transcript, 7.0)

        self.assertEqual(
            [(item.start, item.end) for item in timed],
            [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)],
        )

    def test_falls_back_to_uniform_when_no_blocks(self) -> None:
        lines = [line("hello world"), line("second line")]
        transcript = [{"word": "x", "start": 0.5, "end": 0.4}]

        timed = apply_transcript_timings(lines, transcript, 10.0)

        self.assertTrue(all(word.estimated for item in timed for word in item.words))
        self.assertEqual(timed[0].start, 0.0)
        self.assertEqual(timed[0].end, 5.0)
        self.assertEqual(timed[1].start, 5.0)
        self.assertEqual(timed[1].end, 10.0)


class SanitizeTest(unittest.TestCase):
    def test_clamps_words_into_line(self) -> None:
        item = LyricLine("a b", [LyricWord("a", 1.0, 1.2), LyricWord("b", 1.2, 9.0)], 1.0, 3.0)
        sanitize_word_timings([item], duration=5.0)
        self.assertEqual(item.words[1].end, 3.0)
        self.assertFalse(item.words[1].estimated)

    def test_enforces_list_order_monotonicity(self) -> None:
        item = LyricLine("a b", [LyricWord("a", 2.0, 2.4), LyricWord("b", 1.0, 1.4)], 0.0, 4.0)
        sanitize_word_timings([item])
        a, b = item.words
        self.assertGreaterEqual(b.start, a.end - 0.001)
        self.assertTrue(b.estimated)


class ValidationTest(unittest.TestCase):
    def test_clean_timings_have_no_warnings(self) -> None:
        items = [
            LyricLine("a b", [LyricWord("a", 1.0, 1.4), LyricWord("b", 1.4, 1.8)], 1.0, 1.8),
            LyricLine("c d", [LyricWord("c", 2.0, 2.4), LyricWord("d", 2.4, 2.8)], 2.0, 2.8),
        ]
        self.assertEqual(validate_line_timings(items, 5.0), [])
        self.assertEqual(validate_word_timings(items, 5.0), [])

    def test_detects_line_overlap(self) -> None:
        items = [
            LyricLine("a b", [LyricWord("a", 1.0, 1.4), LyricWord("b", 1.4, 1.8)], 1.0, 1.8),
            LyricLine("c d", [LyricWord("c", 1.5, 1.9), LyricWord("d", 1.9, 2.3)], 1.5, 2.3),
        ]
        self.assertEqual(len(validate_line_timings(items, 5.0)), 1)

    def test_detects_word_overlap_and_out_of_bounds(self) -> None:
        items = [
            LyricLine("a b", [LyricWord("a", 1.0, 2.0), LyricWord("b", 1.5, 2.5)], 1.0, 1.8),
        ]
        warnings = validate_word_timings(items, 5.0)
        self.assertGreaterEqual(len(warnings), 1)  # overlap
        self.assertGreaterEqual(len(warnings), 2)  # out of bounds


def hook_line() -> LyricLine:
    text = "Pararme a mi lo dudo pues estilo tengo vario"
    return LyricLine(text, [LyricWord(word) for word in text.split()])


class TokenOverlapTest(unittest.TestCase):
    def test_counts_exact_and_fuzzy_matches(self) -> None:
        segment = ["paralname", "lo", "dudo", "tengo", "varios"]
        line = ["pararme", "lo", "dudo", "tengo", "vario"]
        score = _token_overlap(segment, line)
        self.assertGreaterEqual(score, 0.6)

    def test_no_overlap_scores_zero(self) -> None:
        self.assertEqual(_token_overlap(["zzz"], ["aaa"]), 0.0)


class SegmentsFromWordsTest(unittest.TestCase):
    def test_groups_words_by_segment_key(self) -> None:
        words = [
            {"word": "a", "start": 1.0, "end": 1.4, "segment": 0},
            {"word": "b", "start": 1.5, "end": 1.9, "segment": 0},
            {"word": "c", "start": 5.0, "end": 5.6, "segment": 1},
        ]
        segments = _segments_from_words(words)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["start"], 1.0)
        self.assertEqual(segments[0]["end"], 1.9)
        self.assertIn("a b", segments[0]["text"])


class AnchoredTimingsTest(unittest.TestCase):
    def test_hook_lines_fill_transcription_gap(self) -> None:
        lines = [hook_line() for _ in range(8)] + [
            line("To este chorro de mama bi tu y tu combo wannabe"),
            line("Tan fronteandome con culo que hace rato les meti"),
        ]
        transcript = {
            "segments": [
                {"start": 0.0, "end": 3.2, "text": "paralname lo dudo tengo varios", "words": []},
                {"start": 10.0, "end": 13.0, "text": "fue pecho remamabi tu y tu combo wannabe", "words": []},
            ],
            "words": [],
        }
        vocal_regions = [(0.0, 13.0)]
        timed = apply_transcript_timings(lines, transcript, 15.0, vocal_regions)

        self.assertEqual(timed[0].start, 0.0)
        self.assertEqual(timed[7].end, 10.0)
        self.assertEqual(timed[8].start, 10.0)
        for index in range(8):
            self.assertLessEqual(timed[index].end, 10.0)
        self.assertGreater(timed[7].end - timed[7].start, 1.0)
        self.assertEqual(timed[0].timing_quality, "anchored")
        self.assertEqual(timed[7].timing_quality, "distributed")

    def test_gap_only_filled_when_vocal_energy_exists(self) -> None:
        lines = [hook_line() for _ in range(8)] + [
            line("To este chorro de mama bi tu y tu combo wannabe"),
        ]
        transcript = {
            "segments": [
                {"start": 0.0, "end": 3.2, "text": "paralname lo dudo tengo varios", "words": []},
                {"start": 10.0, "end": 13.0, "text": "fue pecho remamabi tu y tu combo wannabe", "words": []},
            ],
            "words": [],
        }
        timed = apply_transcript_timings(lines, transcript, 15.0, [])

        self.assertLessEqual(timed[7].end, 3.2)
        self.assertEqual(timed[8].start, 10.0)

    def test_global_assignment_for_repeated_refrain(self) -> None:
        lines = [
            line("Intro line with its own words"),
            line("Buscame un espejo para inspirarme"),
            line("Esto va a vivir por siempre porque es arte"),
            line("Ya todos quieren que se la cante"),
            line("Pues mi rapero favorito no le meten como antes"),
            line("Te pongo el estudio te pago horas de estudio"),
        ]
        transcript = {
            "segments": [
                {"start": 0.0, "end": 4.0, "text": "intro line with its own words", "words": []},
                {"start": 5.0, "end": 9.0, "text": "busquenme un espejo para inspirarme esto va a vivir por siempre", "words": []},
                {"start": 10.0, "end": 14.0, "text": "ya todos quieren que se la cante pues mi rapero favorito no le meten", "words": []},
                {"start": 15.0, "end": 17.0, "text": "te pongo el estudio te pago horas de estudio", "words": []},
            ],
            "words": [],
        }
        timed = apply_transcript_timings(lines, transcript, 20.0, [])
        self.assertEqual(timed[1].timing_quality, "anchored")
        self.assertEqual(timed[3].timing_quality, "anchored")
        self.assertEqual(timed[5].timing_quality, "anchored")
        self.assertEqual(timed[3].start, 10.0)
        self.assertEqual(timed[5].end, 17.0)

    def test_anchor_ranges_dp_resolves_refrain_conflict(self) -> None:
        lines = [hook_line() for _ in range(4)] + [line("other words here"), line("more lyrics now")]
        blocks_text = [
            (0.0, 3.0, "pararme lo dudo tengo varios"),
            (5.0, 8.0, "other words here more lyrics now"),
        ]
        ranges = _anchor_ranges(lines, blocks_text, 8.0)
        self.assertEqual(len(ranges), 2)
        self.assertEqual(ranges[0][1], 0)
        self.assertEqual(ranges[1][1], 4)

    def test_falls_back_to_blocks_when_no_text_matches(self) -> None:
        lines = [line("hello world"), line("you are here")]
        transcript = {
            "segments": [
                {"start": 2.0, "end": 3.2, "text": "gibberish nonsense", "words": []},
                {"start": 6.0, "end": 7.4, "text": "wrong stuff", "words": []},
            ],
            "words": [],
        }
        timed = apply_transcript_timings(lines, transcript, 10.0)
        self.assertEqual(timed[0].start, 2.0)
        self.assertEqual(timed[0].end, 3.2)
        self.assertEqual(timed[1].start, 6.0)
        self.assertEqual(timed[1].end, 7.4)
        self.assertTrue(all(item.timing_quality == "fallback" for item in timed))


class VocalActivityTest(unittest.TestCase):
    def test_active_regions_from_rms(self) -> None:
        rms = [0.3] * 10 + [0.001] * 5 + [0.3] * 10
        regions = _active_regions_from_rms(rms, 0.5, 0.8)
        self.assertEqual(regions, [(0.0, 5.0), (7.5, 12.5)])

    def test_short_bursts_are_dropped(self) -> None:
        rms = [0.3] * 1 + [0.001] * 5
        self.assertEqual(_active_regions_from_rms(rms, 0.5, 0.8), [])


class GapClassificationTest(unittest.TestCase):
    def test_classifies_transcription_gap_and_instrumental(self) -> None:
        items = [
            LyricLine("a b", [LyricWord("a"), LyricWord("b")], 0.0, 5.0),
            LyricLine("c d", [LyricWord("c"), LyricWord("d")], 10.0, 12.0),
            LyricLine("e f", [LyricWord("e"), LyricWord("f")], 20.0, 22.0),
        ]
        regions = classify_gaps(items, [(5.0, 12.0)])
        self.assertEqual(len(regions), 2)
        self.assertEqual(regions[0]["type"], "transcription-gap")
        self.assertEqual(regions[1]["type"], "instrumental")

    def test_small_gaps_not_reported(self) -> None:
        items = [
            LyricLine("a b", [LyricWord("a"), LyricWord("b")], 0.0, 2.0),
            LyricLine("c d", [LyricWord("c"), LyricWord("d")], 2.5, 4.5),
        ]
        self.assertEqual(classify_gaps(items, []), [])


class ForcedAlignmentWindowsTest(unittest.TestCase):
    def test_merges_close_segments_and_inserts_holes(self) -> None:
        segments = [
            {"start": 0.0, "end": 3.0, "text": "a"},
            {"start": 3.2, "end": 6.0, "text": "b"},
            {"start": 12.0, "end": 15.0, "text": "c"},
        ]
        windows = _build_alignment_windows(segments)
        self.assertEqual(windows, [[0.0, 6.0], [6.0, 12.0], [12.0, 15.0]])

    def test_drops_invalid_segments(self) -> None:
        self.assertEqual(_build_alignment_windows([{"start": 3.0, "end": 2.0, "text": "x"}]), [])

    def test_partitions_lines_exactly_once(self) -> None:
        lines = [line("one two three four five") for _ in range(8)]
        windows = [[0.0, 4.0], [4.0, 12.0], [12.0, 16.0]]
        assignments = _assign_lines_to_windows(lines, windows)
        assigned = [line_index for _window_index, line_indices in assignments for line_index in line_indices]
        self.assertEqual(assigned, list(range(8)))
        self.assertEqual([line_index for line_index, _ in assignments], [0, 1, 2])

    def test_hole_window_receives_lines(self) -> None:
        lines = [line("a b c d e") for _ in range(6)]
        windows = [[0.0, 2.0], [2.0, 8.0], [8.0, 10.0]]
        assignments = _assign_lines_to_windows(lines, windows)
        by_window = {window_index: line_indices for window_index, line_indices in assignments}
        self.assertIn(1, by_window)
        self.assertTrue(by_window[1])


class ForcedAlignmentMappingTest(unittest.TestCase):
    def test_maps_aligned_words_back_onto_lines(self) -> None:
        lines = [
            LyricLine("hola mundo", [LyricWord("hola"), LyricWord("mundo")]),
            LyricLine("adios amigo", [LyricWord("adios"), LyricWord("amigo")]),
        ]
        segments = [
            {"start": 0.0, "end": 2.0, "text": "hola mundo", "lines": [0]},
            {"start": 2.0, "end": 4.0, "text": "adios amigo", "lines": [1]},
        ]
        aligned = [
            {"word": "hola", "start": 0.1, "end": 0.5, "score": 0.8},
            {"word": "mundo", "start": 0.5, "end": 0.9, "score": 0.7},
            {"word": "adios", "start": 2.1, "end": 2.6, "score": 0.9},
            {"word": "amigo", "start": 2.6, "end": 3.1, "score": 0.8},
        ]
        ok = _map_aligned_words_to_lines(lines, segments, aligned)
        self.assertTrue(ok)
        self.assertEqual(lines[0].words[0].start, 0.1)
        self.assertFalse(lines[0].words[0].estimated)
        self.assertEqual(lines[1].words[1].end, 3.1)

    def test_token_count_mismatch_fails(self) -> None:
        lines = [LyricLine("hola mundo", [LyricWord("hola"), LyricWord("mundo")])]
        segments = [{"start": 0.0, "end": 2.0, "text": "hola mundo", "lines": [0]}]
        aligned = [{"word": "hola", "start": 0.1, "end": 0.5}]
        self.assertFalse(_map_aligned_words_to_lines(lines, segments, aligned))

    def test_finalize_bounds_from_words(self) -> None:
        lines = [
            LyricLine("a b", [LyricWord("a", 1.0, 1.4), LyricWord("b", 1.4, 1.8)]),
            LyricLine("c d", [LyricWord("c", 2.0, 2.4), LyricWord("d", 2.4, 2.8)]),
        ]
        _finalize_line_bounds(lines)
        self.assertEqual((lines[0].start, lines[0].end), (1.0, 1.8))
        self.assertEqual((lines[1].start, lines[1].end), (2.0, 2.8))
        self.assertEqual(lines[0].timing_quality, "anchored")

    def test_finalize_clamps_overlaps(self) -> None:
        lines = [
            LyricLine("a b", [LyricWord("a", 1.0, 3.0), LyricWord("b", 3.2, 3.6)]),
            LyricLine("c d", [LyricWord("c", 2.0, 2.5), LyricWord("d", 2.5, 2.9)]),
        ]
        _finalize_line_bounds(lines)
        self.assertGreaterEqual(lines[1].words[0].start, lines[0].words[0].end - 0.001)
        self.assertGreaterEqual(lines[1].start, lines[0].end - 0.001)

    def test_force_align_skips_unsupported_languages(self) -> None:
        self.assertIsNone(force_align_lyrics([], [], Path("missing.wav"), "ko"))


if __name__ == "__main__":
    unittest.main()