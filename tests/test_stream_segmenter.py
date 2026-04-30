from __future__ import annotations

import unittest

from app.stream_segmenter import SentenceSegmenter


class SentenceSegmenterTests(unittest.TestCase):
    def test_default_mode_waits_for_hard_boundary(self) -> None:
        segmenter = SentenceSegmenter(split_on_soft_boundaries=False)

        self.assertEqual(segmenter.push("こんにちは、"), [])
        self.assertEqual(segmenter.push("元気です。"), ["こんにちは、元気です。"])

    def test_soft_boundary_mode_emits_at_comma(self) -> None:
        segmenter = SentenceSegmenter(split_on_soft_boundaries=True)

        self.assertEqual(segmenter.push("こんにちは、"), ["こんにちは、"])
        self.assertEqual(segmenter.push("元気です。"), ["元気です。"])

    def test_soft_boundary_mode_splits_even_when_period_is_in_same_chunk(self) -> None:
        segmenter = SentenceSegmenter(split_on_soft_boundaries=True)

        self.assertEqual(segmenter.push("こんにちは、元気です。"), ["こんにちは、", "元気です。"])


if __name__ == "__main__":
    unittest.main()