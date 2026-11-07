"""Headless unit tests verifying thread-safe queue handling."""

import os
import queue
import struct
import tempfile
import time
import unittest
import wave

from matrixtrack.gui.monitor import drain_latest, AudioWorker, QueueMonitor
from matrixtrack.matrix.analyzer import CHANNEL_LABELS


def _make_wav(path, channels=6, frames=512, sampwidth=2, amplitude=0.5):
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(44100)
        if sampwidth == 2:
            value = int(amplitude * 32767)
            data = [value if (i % channels == 0) else 0 for i in range(frames * channels)]
            w.writeframes(struct.pack("<%dh" % len(data), *data))
        else:
            raise ValueError("unsupported sampwidth for test helper")


class TestDrainLatest(unittest.TestCase):
    def test_returns_none_when_empty(self):
        q = queue.Queue()
        self.assertIsNone(drain_latest(q))

    def test_returns_last_frame(self):
        q = queue.Queue()
        for i in range(5):
            q.put({"FL": {"peak": float(i), "rms": 0.0}})
        last = drain_latest(q)
        self.assertEqual(last["FL"]["peak"], 4.0)
        self.assertTrue(q.empty())

    def test_drains_all_pending(self):
        q = queue.Queue()
        for i in range(3):
            q.put(i)
        drain_latest(q)
        self.assertTrue(q.empty())


class TestAudioWorker(unittest.TestCase):
    def setUp(self):
        fd, self.wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        _make_wav(self.wav_path, channels=6, frames=512, amplitude=0.5)

    def tearDown(self):
        try:
            os.remove(self.wav_path)
        except OSError:
            pass

    def test_produces_frames_with_expected_keys(self):
        q = queue.Queue(maxsize=4)
        worker = AudioWorker(q, path=self.wav_path, block_size=1024)
        worker.start()
        time.sleep(0.3)
        worker.stop()
        self.assertFalse(q.empty())
        msg = None
        while not q.empty():
            msg = q.get_nowait()
        self.assertIsNotNone(msg)
        for label in CHANNEL_LABELS:
            self.assertIn(label, msg)
            self.assertIn("peak", msg[label])
            self.assertIn("rms", msg[label])
            self.assertGreaterEqual(msg[label]["peak"], 0.0)
            self.assertLessEqual(msg[label]["peak"], 1.0)
        self.assertGreater(msg["FL"]["peak"], 0.0)
        self.assertEqual(msg["FR"]["peak"], 0.0)

    def test_stop_terminates_thread(self):
        q = queue.Queue(maxsize=4)
        worker = AudioWorker(q, path=self.wav_path, block_size=1024)
        worker.start()
        time.sleep(0.1)
        worker.stop()
        self.assertFalse(worker.running)
        self.assertIsNone(worker._thread)

    def test_natural_eof_stops_thread_and_allows_restart(self):
        q = queue.Queue(maxsize=4)
        worker = AudioWorker(q, path=self.wav_path, block_size=1024)
        worker.start()
        worker._thread.join(timeout=3.0)
        self.assertFalse(worker._thread.is_alive())
        self.assertFalse(worker.running)
        # Restart after natural EOF must work without manual stop().
        worker.start()
        self.assertTrue(worker.running)
        worker.stop()
        self.assertIsNone(worker.reader)
        self.assertIsNone(worker._thread)


class TestDropOldFrames(unittest.TestCase):
    def test_drop_policy_when_full(self):
        q = queue.Queue(maxsize=2)
        q.put({"FL": {"peak": 0.1, "rms": 0.0}})
        q.put({"FL": {"peak": 0.2, "rms": 0.0}})
        new_frame = {"FL": {"peak": 0.9, "rms": 0.0}}
        try:
            q.put_nowait(new_frame)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            q.put_nowait(new_frame)
        last = drain_latest(q)
        self.assertEqual(last["FL"]["peak"], 0.9)

    def test_worker_does_not_block_when_queue_full(self):
        q = queue.Queue(maxsize=1)
        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        _make_wav(wav_path, channels=6, frames=4096, amplitude=0.25)
        try:
            worker = AudioWorker(q, path=wav_path, block_size=256)
            worker.start()
            time.sleep(0.2)
            worker.stop()
        finally:
            os.remove(wav_path)
        self.assertTrue(True)


class TestQueueMonitor(unittest.TestCase):
    def test_poll_updates_canvas(self):
        try:
            import tkinter as tk
        except Exception:
            self.skipTest("tkinter unavailable")
        try:
            root = tk.Tk()
        except Exception as e:
            self.skipTest(f"no display: {e}")
        root.withdraw()

        class FakeCanvas(tk.Canvas):
            def __init__(self, master):
                super().__init__(master, width=10, height=10)
                self.levels = None

            def update_levels(self, levels):
                self.levels = levels

        canvas = FakeCanvas(root)
        q = queue.Queue(maxsize=4)
        q.put({"FL": {"peak": 0.7, "rms": 0.3}})
        monitor = QueueMonitor(canvas, q, interval_ms=10)
        monitor.start()
        root.update()
        time.sleep(0.05)
        root.update()
        monitor.stop()
        self.assertEqual(canvas.levels["FL"]["peak"], 0.7)
        root.destroy()


if __name__ == "__main__":
    unittest.main()
