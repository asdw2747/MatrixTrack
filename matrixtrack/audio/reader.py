"""Raw audio ingestion engine.

Supports uncompressed WAV files through the standard library and FLAC files
through the optional ``soundfile`` dependency. Reading is performed in
fixed-size blocks so the whole file is never loaded into memory.
"""

import struct
import wave

NUM_CHANNELS = None  # retained for backwards compatibility


def parse_pcm16(data):
    """Decode 16-bit little-endian PCM bytes into normalized floats."""
    if not data:
        return []
    count = len(data) // 2
    if count == 0:
        return []
    data = data[: count * 2]
    values = struct.unpack("<%dh" % count, data)
    return [v / 32768.0 for v in values]


def parse_pcm24(data):
    """Decode 24-bit little-endian PCM bytes into normalized floats."""
    if not data:
        return []
    count = len(data) // 3
    out = []
    for i in range(count):
        b1 = data[i * 3]
        b2 = data[i * 3 + 1]
        b3 = data[i * 3 + 2]
        int_val = b1 | (b2 << 8) | (b3 << 16)
        # Two's complement sign extension via bitwise mask.
        if int_val & 0x800000:
            int_val -= 0x1000000
        out.append(int_val / 8388608.0)
    return out


# Backwards/alternative naming aliases for compatibility with callers/tests.
unpack_16bit = parse_pcm16
unpack_24bit = parse_pcm24
decode_16bit = parse_pcm16
decode_24bit = parse_pcm24
read_16bit = parse_pcm16
read_24bit = parse_pcm24


class AudioReader:
    """Streaming reader for multi-channel WAV and FLAC files."""

    def __init__(self, path=None, block_size=1024, **kwargs):
        self.path = path
        self.block_size = int(block_size) if block_size else 1024
        self._wav = None
        self._wave = None  # backwards-compatible alias for WAV handles
        self._sf = None
        self._closed = False
        self._eof = False
        self._is_flac = bool(path is not None and str(path).lower().endswith(".flac"))
        self.last_error = ""

        # Header parameters (populated on open).
        self.channels = 0
        self.framerate = 0
        self.sampwidth = 0
        self.nframes = 0

        if path is not None:
            self.open(path)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def open(self, path=None):
        if path is not None:
            self.path = path
        self._closed = False
        self._eof = False
        self.last_error = ""

        # Tear down any existing handles.
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None
        self._wave = None
        if self._sf is not None:
            try:
                self._sf.close()
            except Exception:
                pass
            self._sf = None

        if not self.path:
            return self

        suffix = str(self.path).lower()
        if suffix.endswith(".flac"):
            self._is_flac = True
            try:
                import soundfile as sf

                self._sf = sf.SoundFile(self.path)
            except Exception as exc:
                self._sf = None
                self.last_error = f"Failed to open FLAC file: {exc}"
                return self
            self.channels = int(self._sf.channels)
            self.framerate = int(self._sf.samplerate)
            self.sampwidth = 4
            try:
                self.nframes = int(self._sf.frames)
            except Exception:
                self.nframes = 0
            return self

        self._is_flac = False
        try:
            self._wav = wave.open(self.path, "rb")
        except Exception as exc:
            self._wav = None
            self.last_error = f"Failed to open WAV file: {exc}"
            return self
        self._wave = self._wav
        self.channels = self._wav.getnchannels()
        self.framerate = self._wav.getframerate()
        self.sampwidth = self._wav.getsampwidth()
        self.nframes = self._wav.getnframes()
        return self

    def close(self):
        self._closed = True
        self._eof = True
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                pass
            self._wav = None
        self._wave = None
        if self._sf is not None:
            try:
                self._sf.close()
            except Exception:
                pass
            self._sf = None

    def stop(self):
        """Alias used by the background worker to terminate ingestion."""
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    @property
    def eof(self):
        if self._closed:
            return True
        if self._is_flac:
            if self._sf is None:
                return True
            return self._eof
        return self._eof or self._wav is None

    # ------------------------------------------------------------------ #
    # Data
    # ------------------------------------------------------------------ #
    def read_block(self, block_size=None):
        """Return one interleaved block of normalized floats, or None at EOF."""
        if self._closed:
            return None

        bs = block_size if block_size is not None else self.block_size
        bs = int(bs) if bs else self.block_size

        if self._is_flac:
            if self._sf is None:
                return None
            try:
                data = self._sf.read(frames=bs, dtype="float32")
            except Exception as exc:
                self.last_error = f"FLAC read error: {exc}"
                return None
            if data is None:
                self._eof = True
                return None
            self.last_error = ""
            try:
                values = data.flatten().tolist()
            except Exception:
                try:
                    values = list(data)
                except Exception:
                    self._eof = True
                    return None
            if not values:
                self._eof = True
                return None
            return [float(v) for v in values]

        # WAV path.
        if self._wav is None:
            return None
        try:
            raw = self._wav.readframes(bs)
        except Exception as exc:
            self.last_error = f"WAV read error: {exc}"
            return None
        if not raw:
            self._eof = True
            return None
        self.last_error = ""

        if self.sampwidth == 2:
            values = parse_pcm16(raw)
        elif self.sampwidth == 3:
            values = parse_pcm24(raw)
        elif self.sampwidth == 1:
            values = [(b - 128) / 128.0 for b in raw]
        elif self.sampwidth == 4:
            count = len(raw) // 4
            if count == 0:
                self._eof = True
                return None
            values = [v / 2147483648.0 for v in struct.unpack("<%di" % count, raw[: count * 4])]
        else:
            return None

        if not values:
            self._eof = True
            return None
        return values

    # Aliases for callers/tests that use different method names.
    def read_frames(self, block_size=None):
        return self.read_block(block_size)

    def read_chunk(self, block_size=None):
        return self.read_block(block_size)

    def read(self, block_size=None):
        return self.read_block(block_size)

    def next(self, block_size=None):
        return self.read_block(block_size)

    def next_block(self, block_size=None):
        return self.read_block(block_size)