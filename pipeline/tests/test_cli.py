import unittest

from lyricast_pipeline.cli import _romanized_transcript


class RomanizedTranscriptTest(unittest.TestCase):
    def test_romanizes_word_and_keeps_timings(self) -> None:
        words = [
            {"word": chr(0xC0AC), "start": 1.0, "end": 1.4},
            {"word": "still", "start": 1.4, "end": 1.8},
        ]

        romanized = _romanized_transcript(words)

        self.assertEqual(romanized[0]["word"], "sa")
        self.assertEqual(romanized[0]["start"], 1.0)
        self.assertEqual(romanized[0]["end"], 1.4)
        self.assertEqual(romanized[1]["word"], "still")

    def test_non_hangul_words_pass_through(self) -> None:
        words = [{"word": "hello", "start": 0.0}]
        romanized = _romanized_transcript(words)
        self.assertEqual(romanized[0]["word"], "hello")


if __name__ == "__main__":
    unittest.main()