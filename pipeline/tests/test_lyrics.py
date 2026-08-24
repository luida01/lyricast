import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lyricast_pipeline.lyrics import parse_lrc, plain_text_lines
from lyricast_pipeline.alignment import apply_transcript_timings
from lyricast_pipeline.lyrics import LyricLine, LyricWord


class LyricsParsingTests(unittest.TestCase):
    def test_parses_standard_line_timestamps(self) -> None:
        lines, has_word_timing = parse_lrc("[00:01.00]Hello world\n[00:03.50]Again", duration=5.0)

        self.assertFalse(has_word_timing)
        self.assertEqual([line.text for line in lines], ["Hello world", "Again"])
        self.assertEqual(lines[0].start, 1.0)
        self.assertEqual(lines[0].end, 3.5)

    def test_parses_enhanced_word_timestamps(self) -> None:
        content = "[00:01.00]<00:01.00>Hello <00:01.50>world"
        lines, has_word_timing = parse_lrc(content, duration=4.0)

        self.assertTrue(has_word_timing)
        self.assertEqual([word.text for word in lines[0].words], ["Hello", "world"])
        self.assertEqual(lines[0].words[0].start, 1.0)
        self.assertEqual(lines[0].words[1].start, 1.5)

    def test_parses_plain_lyrics(self) -> None:
        lines = plain_text_lines("First line\n\nSecond line")

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1].words[0].text, "Second")

    def test_applies_word_timestamps_without_estimation(self) -> None:
        lines = [LyricLine("Hello world", [LyricWord("Hello"), LyricWord("world")])]
        transcript = [
            {"word": "Hello", "start": 1.0, "end": 1.4},
            {"word": "world", "start": 1.5, "end": 1.9},
        ]

        apply_transcript_timings(lines, transcript, duration=3.0)

        self.assertEqual(lines[0].words[0].start, 1.0)
        self.assertEqual(lines[0].words[1].start, 1.5)
        self.assertFalse(lines[0].words[0].estimated)


if __name__ == "__main__":
    unittest.main()
