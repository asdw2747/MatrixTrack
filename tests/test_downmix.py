"""Tests for 5.1 -> stereo downmix and phase correlation."""

import math
import unittest

from matrixtrack.matrix.downmix import (
    calculate_phase_correlation,
    fold_down_51_to_stereo,
)


class TestFoldDown(unittest.TestCase):
    def test_coefficients(self):
        # FL=1, C=1, SL=1, FR=2, SR=2, LFE ignored.
        frames = {
            "FL": [1.0],
            "FR": [2.0],
            "C": [1.0],
            "LFE": [99.0],
            "SL": [1.0],
            "SR": [2.0],
        }
        left, right = fold_down_51_to_stereo(frames)
        expected_left = 1.0 + 0.7071 * 1.0 + 0.7071 * 1.0
        expected_right = 2.0 + 0.7071 * 1.0 + 0.7071 * 2.0
        self.assertAlmostEqual(left[0], expected_left, places=5)
        self.assertAlmostEqual(right[0], expected_right, places=5)

    def test_deinterleaved_list(self):
        # Six channel lists.
        frames = [[1.0], [0.0], [0.0], [0.0], [0.0], [0.0]]
        left, right = fold_down_51_to_stereo(frames)
        self.assertEqual(left, [1.0])
        self.assertEqual(right, [0.0])

    def test_interleaved_flat(self):
        # FL=1, rest 0 in interleaved order.
        frames = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        left, right = fold_down_51_to_stereo(frames)
        self.assertEqual(left, [1.0])
        self.assertEqual(right, [0.0])

    def test_empty(self):
        left, right = fold_down_51_to_stereo([])
        self.assertEqual(left, [])
        self.assertEqual(right, [])


class TestPhaseCorrelation(unittest.TestCase):
    def test_perfectly_inverted_surround(self):
        # Surround channel drives inverted stereo -> -1.0.
        frames = {
            "FL": [1.0, -1.0, 1.0, -1.0],
            "FR": [-1.0, 1.0, -1.0, 1.0],
            "C": [0.0, 0.0, 0.0, 0.0],
            "LFE": [0.0, 0.0, 0.0, 0.0],
            "SL": [0.0, 0.0, 0.0, 0.0],
            "SR": [0.0, 0.0, 0.0, 0.0],
        }
        left, right = fold_down_51_to_stereo(frames)
        self.assertEqual(calculate_phase_correlation(left, right), -1.0)

    def test_in_phase_mono(self):
        frames = {
            "FL": [1.0, 0.5, -0.25],
            "FR": [1.0, 0.5, -0.25],
            "C": [0.0, 0.0, 0.0],
            "LFE": [0.0, 0.0, 0.0],
            "SL": [0.0, 0.0, 0.0],
            "SR": [0.0, 0.0, 0.0],
        }
        left, right = fold_down_51_to_stereo(frames)
        self.assertEqual(calculate_phase_correlation(left, right), 1.0)

    def test_silence_yields_zero(self):
        self.assertEqual(calculate_phase_correlation([0.0, 0.0], [0.0, 0.0]), 0.0)
        self.assertEqual(calculate_phase_correlation([], []), 0.0)

    def test_zero_division_guard(self):
        self.assertEqual(calculate_phase_correlation([0.0, 0.0], [1.0, 1.0]), 0.0)
        self.assertEqual(calculate_phase_correlation([1.0, 1.0], [0.0, 0.0]), 0.0)

    def test_range_clamped(self):
        v = calculate_phase_correlation([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
        self.assertAlmostEqual(v, 1.0)
        v = calculate_phase_correlation([1.0, 2.0, 3.0], [-1.0, -2.0, -3.0])
        self.assertAlmostEqual(v, -1.0)


if __name__ == "__main__":
    unittest.main()
