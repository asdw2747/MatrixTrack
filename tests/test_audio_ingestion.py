"""Tests for 16-bit and 24-bit PCM ingestion."""

import os
import struct
import tempfile
import unittest
import wave

from matrixtrack.audio.reader import AudioReader, parse_pcm16, parse_pcm24
from matrixtrack.matrix.analyzer import Analyzer, CHANNEL_LABELS, linear_to_dbfs


def _pack24(value):
    if value < 0:
        value += 0x1000000
    return bytes([value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF])


class TestPCM16(unittest.TestCase):
    def test_parse_values(self):
        data = struct.pack("<3h", 0, 16384, -16384)
        vals = parse_pcm16(data)
        self.assertEqual(len(vals), 3)
        self.assertAlmostEqual(vals[0], 0.0)
        self.assertAlmostEqual(vals[1], 16384 / 32768.0)
        self.assertAlmostEqual(vals[2], -16384 / 32768.0)

    def test_reader_16bit(self):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave.open(path, "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(struct.pack("<4h", 32767, -32768, 0, 0))
            reader = AudioReader(path, block_size=10)
            block = reader.read_block()
            self.assertIsNotNone(block)
            self.assertEqual(len(block), 4)
            self.assertAlmostEqual(block[0], 32767 / 32768.0, places=5)
            self.assertAlmostEqual(block[1], -32768 / 32768.0, places=5)
            self.assertEqual(reader.channels, 2)
            self.assertEqual(reader.sampwidth, 2)
            reader.close()
        finally:
            os.remove(path)

    def test_dynamic_block_size(self):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave.open(path, "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(struct.pack("<20h", *([1, 2] * 10)))
            reader = AudioReader(path, block_size=1024)
            block = reader.read_block(4)
            self.assertEqual(len(block), 8)
            block2 = reader.read_block(1000)
            self.assertEqual(len(block2), 12)
            self.assertIsNone(reader.read_block(1000))  # EOF
            reader.close()
        finally:
            os.remove(path)


class TestPCM24(unittest.TestCase):
    def test_parse_values(self):
        # 0.5, -0.5
        pos = int(0.5 * 8388608)
        neg = -int(0.5 * 8388608)
        data = _pack24(pos) + _pack24(neg) + _pack24(0)
        vals = parse_pcm24(data)
        self.assertEqual(len(vals), 3)
        self.assertAlmostEqual(vals[0], 0.5, places=5)
        self.assertAlmostEqual(vals[1], -0.5, places=5)
        self.assertAlmostEqual(vals[2], 0.0)

    def test_reader_24bit(self):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            pos = int(0.25 * 8388608)
            neg = -int(0.25 * 8388608)
            raw = _pack24(pos) + _pack24(neg)
            with wave.open(path, "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(3)
                w.setframerate(44100)
                w.writeframes(raw)
            reader = AudioReader(path, block_size=10)
            block = reader.read_block()
            self.assertIsNotNone(block)
            self.assertEqual(len(block), 2)
            self.assertAlmostEqual(block[0], 0.25, places=5)
            self.assertAlmostEqual(block[1], -0.25, places=5)
            self.assertEqual(reader.sampwidth, 3)
            reader.close()
        finally:
            os.remove(path)

    def test_twos_complement_mask(self):
        # Max negative 24-bit value.
        data = bytes([0x00, 0x00, 0x80])
        vals = parse_pcm24(data)
        self.assertAlmostEqual(vals[0], -1.0, places=5)

    def test_boundary_extremes(self):
        self.assertEqual(parse_pcm24(_pack24(-8388608))[0], -1.0)
        self.assertEqual(parse_pcm24(_pack24(8388607))[0], 8388607 / 8388608.0)
        self.assertEqual(parse_pcm24(_pack24(-1))[0], -1 / 8388608.0)
        self.assertEqual(parse_pcm24(_pack24(0))[0], 0.0)
        self.assertEqual(parse_pcm24(_pack24(1))[0], 1 / 8388608.0)


class TestEOFSafety(unittest.TestCase):
    def _empty_wav(self):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"")
        return path

    def test_empty_file_returns_none_repeatedly(self):
        path = self._empty_wav()
        try:
            reader = AudioReader(path, block_size=1024)
            self.assertIsNone(reader.read_block())
            self.assertTrue(reader.eof)
            self.assertIsNone(reader.read_block())
            self.assertIsNone(reader.read_frames())
            reader.close()
        finally:
            os.remove(path)

    def test_exact_block_boundary_then_eof(self):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave.open(path, "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(struct.pack("<4h", 1, 2, 3, 4))
            reader = AudioReader(path, block_size=2)
            self.assertEqual(len(reader.read_block()), 4)
            self.assertIsNone(reader.read_block())
            self.assertIsNone(reader.read_block())
            reader.close()
        finally:
            os.remove(path)


class TestLinearToDbfs(unittest.TestCase):
    def test_silence_floor(self):
        self.assertEqual(linear_to_dbfs(0.0), -100.0)
        self.assertEqual(linear_to_dbfs(1e-6), -100.0)
        self.assertEqual(linear_to_dbfs(1e-5), -100.0)

    def test_unity_is_zero_db(self):
        self.assertEqual(linear_to_dbfs(1.0), 0.0)

    def test_half_scale(self):
        self.assertAlmostEqual(linear_to_dbfs(0.5), -6.020599913, places=4)

    def test_negative_input_uses_magnitude(self):
        self.assertEqual(linear_to_dbfs(-1.0), 0.0)


class TestAnalyzer(unittest.TestCase):
    def test_deinterleaves_and_metrics(self):
        frame = [0.5, -0.25, 0.0, 0.0, 0.0, 0.0] * 10
        metrics = Analyzer().analyze(frame, channels=6)
        self.assertAlmostEqual(metrics["FL"]["peak"], 0.5)
        self.assertAlmostEqual(metrics["FR"]["peak"], 0.25)
        self.assertAlmostEqual(metrics["C"]["peak"], 0.0)
        self.assertAlmostEqual(metrics["FL"]["rms"], 0.5)
        self.assertIn("LFE 1", metrics)
        self.assertEqual(metrics["LFE 2"]["peak"], 0.0)

    def test_empty_block(self):
        metrics = Analyzer().analyze([], channels=6)
        for label in CHANNEL_LABELS:
            self.assertEqual(metrics[label]["peak"], 0.0)
            self.assertEqual(metrics[label]["rms"], 0.0)

    def test_zero_signal_rms_is_zero(self):
        metrics = Analyzer().analyze([0.0] * 120, channels=6)
        for label in CHANNEL_LABELS:
            self.assertEqual(metrics[label]["peak"], 0.0)
            self.assertEqual(metrics[label]["rms"], 0.0)

    def test_clipping_flag(self):
        frame = [1.0, -1.0, 0.25, 0.0, 0.0, 0.0] * 4
        metrics = Analyzer().analyze(frame, channels=6)
        self.assertTrue(metrics["FL"]["clipping"])
        self.assertTrue(metrics["FR"]["clipping"])
        self.assertFalse(metrics["C"]["clipping"])

    def test_stereo_maps_fl_fr(self):
        frame = [0.8, 0.3] * 5
        metrics = Analyzer().analyze(frame, channels=2)
        self.assertAlmostEqual(metrics["FL"]["peak"], 0.8)
        self.assertAlmostEqual(metrics["FR"]["peak"], 0.3)
        self.assertEqual(metrics["C"]["peak"], 0.0)


if __name__ == "__main__":
    unittest.main()
