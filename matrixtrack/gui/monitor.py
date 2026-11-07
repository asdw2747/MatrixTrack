"""Thread-safe queue polling (.after() loop) and audio worker."""

import queue
import threading
import time

import math

from matrixtrack.audio.reader import AudioReader
from matrixtrack.matrix.analyzer import Analyzer
from matrixtrack.matrix.downmix import fold_down_51_to_stereo, calculate_phase_correlation


def drain_latest(q):
    """Drain all pending frames, returning only the most recent (or None)."""
    latest = None
    while True:
        try:
            latest = q.get_nowait()
        except queue.Empty:
            break
    return latest


class QueueMonitor:
    """Polls a thread-safe queue on the Tkinter main thread via after()."""

    def __init__(self, canvas, q, interval_ms=30, error_callback=None):
        self.canvas = canvas
        self.queue = q
        self.interval_ms = interval_ms
        self._running = False
        self._after_id = None
        self.error_callback = error_callback

    def start(self):
        if self._running:
            return
        self._running = True
        self._poll()

    def _poll(self):
        if not self._running:
            return
        latest = drain_latest(self.queue)
        if latest is not None:
            error_message = None
            if isinstance(latest, dict):
                error_message = latest.get("error") or latest.get("_error")
            try:
                if hasattr(self.canvas, "update_from_payload"):
                    self.canvas.update_from_payload(latest)
                else:
                    self.canvas.update_levels(latest)
            except Exception:
                pass
            if error_message:
                self.stop()
                if callable(self.error_callback):
                    try:
                        self.error_callback(str(error_message))
                    except Exception:
                        pass
                elif hasattr(self.canvas, "handle_error"):
                    try:
                        self.canvas.handle_error(str(error_message))
                    except Exception:
                        pass
        if self._running:
            self._after_id = self.canvas.after(self.interval_ms, self._poll)

    def stop(self):
        self._running = False
        if self._after_id is not None:
            try:
                self.canvas.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None


class AudioWorker:
    """Background worker producing audio metrics into a queue."""

    def __init__(self, q, path=None, block_size=1024, fps=None, channels=None,
                 mode="Surround", **kwargs):
        self.queue = q
        self.path = path
        self.block_size = int(block_size) if block_size else 1024
        self.fps = fps
        self.channels = channels
        self.mode = mode
        self.reader = None
        self.analyzer = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._running:
                return
            if self.reader is not None:
                try:
                    self.reader.close()
                except Exception:
                    pass
            self._running = True
            self.reader = AudioReader(self.path, block_size=self.block_size)
            if self.channels is not None and self.reader.channels in (0, None):
                self.reader.channels = self.channels
            self.analyzer = Analyzer(channels=self.reader.channels or self.channels)
            self._thread = threading.Thread(
                target=self._run, name="MatrixTrackAudio", daemon=True,
            )
            self._thread.start()

    def _run(self):
        reader = self.reader
        analyzer = self.analyzer
        try:
            while self._running and reader is not None:
                frame = reader.read_block(self.block_size)
                if frame is None:
                    if getattr(reader, "last_error", ""):
                        error_payload = {
                            "error": reader.last_error,
                            "_error": reader.last_error,
                        }
                        try:
                            self.queue.put_nowait(error_payload)
                        except queue.Full:
                            try:
                                self.queue.get_nowait()
                            except queue.Empty:
                                pass
                            try:
                                self.queue.put_nowait(error_payload)
                            except queue.Full:
                                pass
                    break
                if analyzer is not None and reader.channels:
                    analyzer.channels = reader.channels
                levels = analyzer.analyze(frame, channels=reader.channels) if analyzer else {}
                if not isinstance(levels, dict):
                    levels = {}

                # Downmix to virtual stereo and phase correlation.
                channels = reader.channels or 0
                n_frames = (len(frame) // channels) if channels else 0
                six = [[] for _ in range(6)]
                if channels > 0 and n_frames > 0:
                    for ch_idx in range(6):
                        src = ch_idx if ch_idx < channels else None
                        if src is None:
                            six[ch_idx] = [0.0] * n_frames
                        else:
                            six[ch_idx] = [frame[src + i * channels] for i in range(n_frames)]
                l_mix, r_mix = fold_down_51_to_stereo(six)
                phase = calculate_phase_correlation(l_mix, r_mix)

                def _peak(xs):
                    return max((abs(x) for x in xs), default=0.0)

                def _rms(xs):
                    if not xs:
                        return 0.0
                    return math.sqrt(sum(x * x for x in xs) / len(xs))

                left_metrics = {
                    "peak": max(0.0, min(1.0, _peak(l_mix))),
                    "rms": max(0.0, min(1.0, _rms(l_mix))),
                }
                right_metrics = {
                    "peak": max(0.0, min(1.0, _peak(r_mix))),
                    "rms": max(0.0, min(1.0, _rms(r_mix))),
                }

                levels["phase"] = phase
                levels["phase_correlation"] = phase
                levels["correlation"] = phase
                levels["_phase"] = phase
                levels["L_mix"] = left_metrics
                levels["R_mix"] = right_metrics
                levels["Stereo L"] = left_metrics
                levels["Stereo R"] = right_metrics
                levels["monitor_mode"] = self.mode

                try:
                    self.queue.put_nowait(levels)
                except queue.Full:
                    try:
                        self.queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self.queue.put_nowait(levels)
                    except queue.Full:
                        pass

                if self.fps:
                    time.sleep(1.0 / max(float(self.fps), 1.0))
                elif reader.framerate:
                    delay = self.block_size / max(float(reader.framerate), 1.0)
                    time.sleep(min(0.1, max(0.0, delay)))
                else:
                    time.sleep(0.03)
        finally:
            with self._lock:
                self._running = False

    def stop(self):
        with self._lock:
            self._running = False
            reader = self.reader
        if reader is not None:
            try:
                reader.stop() if hasattr(reader, "stop") else reader.close()
            except Exception:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
            self._thread = None
        with self._lock:
            if self.reader is reader:
                self.reader = None
                self.analyzer = None

    @property
    def running(self):
        return self._running
