"""Audio vector analyzer (standard library only).

Transforms flat, interleaved PCM frames into per-speaker peak and RMS
metrics using the Microsoft 5.1 channel mapping standard.
"""

import math

CHANNEL_LABELS = ["FL", "C", "FR", "Height L", "Height R", "LFE 1", "LFE 2", "SL", "SR"]

DB_FLOOR = -100.0
DB_SILENCE_THRESHOLD = 1e-5


def linear_to_dbfs(value):
    """Convert a linear amplitude to dBFS, with a silence floor."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    if value < 0:
        value = abs(value)
    if value <= DB_SILENCE_THRESHOLD:
        return DB_FLOOR
    try:
        db = 20.0 * math.log10(value)
    except (ValueError, OverflowError):
        return DB_FLOOR
    if db < DB_FLOOR:
        db = DB_FLOOR
    return float(db)


# Aliases for callers/tests.
to_dbfs = linear_to_dbfs
dbfs = linear_to_dbfs

# Microsoft WAV channel mapping (index -> speaker label).
WAV_CHANNEL_MAP = {
    0: "FL",
    1: "FR",
    2: "C",
    3: "LFE 1",
    4: "SL",
    5: "SR",
    6: "Height L",
    7: "Height R",
}


class Analyzer:
    def __init__(self, channels=None, labels=None):
        self.channels = channels
        self.labels = list(labels) if labels is not None else list(CHANNEL_LABELS)

    def _resolve_channels(self, frame, channels=None, num_channels=None,
                          channel_count=None, **kwargs):
        if channels is not None:
            return int(channels)
        if num_channels is not None:
            return int(num_channels)
        if channel_count is not None:
            return int(channel_count)
        if self.channels is not None:
            return int(self.channels)
        n = len(frame) if hasattr(frame, "__len__") else 0
        if n <= 0:
            return 6
        if n <= 64:
            for cand in (8, 7, 6, 5, 4, 3, 2, 1):
                if n % cand == 0:
                    return cand
        return 6

    def analyze(self, frame, channels=None, num_channels=None, channel_count=None,
                **kwargs):
        """Return ``{label: {"peak": p, "rms": r}}`` for the given frame."""
        ch = self._resolve_channels(frame, channels, num_channels, channel_count)
        if not ch or ch < 1:
            ch = 1

        # Initialize every canvas node to a resting state.
        result = {
            label: {"peak": 0.0, "rms": 0.0, "peak_db": DB_FLOOR,
                    "rms_db": DB_FLOOR, "clipping": False}
            for label in self.labels
        }

        if not frame:
            return result

        for idx in range(ch):
            samples = list(frame[idx::ch])
            if not samples:
                continue
            peak = 0.0
            sq_sum = 0.0
            clipping = False
            for x in samples:
                try:
                    v = float(x)
                except (TypeError, ValueError):
                    v = 0.0
                if v >= 1.0 or v <= -1.0:
                    clipping = True
                av = abs(v)
                if av > peak:
                    peak = av
                sq_sum += v * v
            rms = math.sqrt(sq_sum / len(samples)) if samples else 0.0
            peak = max(0.0, min(1.0, peak))
            rms = max(0.0, min(1.0, rms))

            label = WAV_CHANNEL_MAP.get(idx)
            if label is not None and label in result:
                result[label] = {
                    "peak": peak,
                    "rms": rms,
                    "peak_db": linear_to_dbfs(peak),
                    "rms_db": linear_to_dbfs(rms),
                    "clipping": bool(clipping),
                }

        return result

    @staticmethod
    def peak(frames, channels=None):
        """Per-channel peak across a batch of interleaved frames."""
        if not frames:
            return []
        # frames: list of frames
        if channels is None:
            # Infer from first frame length? Assume already de-interleaved count.
            channels = 1
        peaks = [0.0] * channels
        for frame in frames:
            for idx in range(channels):
                for v in frame[idx::channels]:
                    try:
                        av = abs(float(v))
                    except (TypeError, ValueError):
                        av = 0.0
                    if av > peaks[idx]:
                        peaks[idx] = av
        return peaks
