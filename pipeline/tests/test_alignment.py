import unittest

from lyricast_pipeline.alignment import apply_transcript_timings
from lyricast_pipeline.lyrics import LyricLine, LyricWord
from lyricast_pipeline.romanize import romanize_korean

# Codepoints verified to romanize to the expected tokens (avoids source-encoding issues).
SA = chr(0xC0AC)  # sa
NEO = chr(0xB108)  # neo
GEU = chr(0xADF8)  # geu
DAE = chr(0xB300)  # dae


class RomanizeTest(unittest.TestCase):
    def test_hangul_romanizes(self) -> None:
        self.assertEqual(romanize_korean(SA), "sa")
        self.assertEqual(romanize_korean(NEO), "neo")
        self.assertEqual(romanize_korean(GEU + DAE), "geudae")

    def test_non_hangul_passthrough(self) -> None:
        self.assertEqual(romanize_korean("still with you"), "still with you")

    def test_mixed_text(self) -> None:
        self.assertEqual(romanize_korean("still " + NEO), "still neo")


class AlignmentTest(unittest.TestCase):
    def test_korean_transcript_matches_romanized_lyrics(self) -> None:
        lyric = romanize_korean(SA) + " " + romanize_korean(NEO)
        lines = [
            LyricLine(
                lyric,
                [LyricWord(romanize_korean(SA)), LyricWord(romanize_korean(NEO))],
                None,
                None,
            )
        ]
        transcript = [
            {"word": SA, "start": 1.0, "end": 1.4},
            {"word": NEO, "start": 1.4, "end": 1.7},
        ]

        timed = apply_transcript_timings(lines, transcript, 3.0)

        self.assertEqual(timed[0].words[0].start, 1.0)
        self.assertEqual(timed[0].words[0].end, 1.4)
        self.assertFalse(timed[0].words[0].estimated)
        self.assertEqual(timed[0].words[1].start, 1.4)
        self.assertFalse(timed[0].words[1].estimated)

    def test_korean_transcript_matches_romanized_phrase(self) -> None:
        lyric = romanize_korean(GEU) + " " + romanize_korean(DAE)
        lines = [
            LyricLine(
                lyric,
                [LyricWord(romanize_korean(GEU)), LyricWord(romanize_korean(DAE))],
                None,
                None,
            )
        ]
        transcript = [
            {"word": GEU, "start": 2.0, "end": 2.5},
            {"word": DAE, "start": 2.5, "end": 3.0},
        ]
        timed = apply_transcript_timings(lines, transcript, 4.0)
        self.assertFalse(timed[0].words[0].estimated)
        self.assertFalse(timed[0].words[1].estimated)

    def test_unmatched_words_remain_estimated(self) -> None:
        lines = [
            LyricLine(
                "alpha beta gamma",
                [LyricWord("alpha"), LyricWord("beta"), LyricWord("gamma")],
                None,
                None,
            )
        ]
        transcript = [{"word": "unknown", "start": 0.5, "end": 0.9}]
        timed = apply_transcript_timings(lines, transcript, 3.0)
        self.assertTrue(all(w.estimated for w in timed[0].words))


if __name__ == "__main__":
    unittest.main()
