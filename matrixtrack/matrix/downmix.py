"""5.1 -> stereo downmix and phase correlation utilities.

Standard-library only. Operates on blocks of de-interleaved 5.1 channels.
"""

import math

ITU_C = 0.7071


def _extract_channels(float_frames):
    """Return six channel lists in Microsoft order: FL, FR, C, LFE, SL, SR."""
    if isinstance(float_frames, dict):
        key_sets = [
            ("FL", "FR", "C", "LFE", "SL", "SR"),
            ("FL", "FR", "C", "LFE 1", "SL", "SR"),
            (0, 1, 2, 3, 4, 5),
        ]
        for keys in key_sets:
            try:
                channels = [list(float_frames[k]) for k in keys]
                if len(channels) == 6:
                    return channels
            except (KeyError, TypeError, ValueError):
                continue
        # Fall back to any available ordering.
        channels = []
        for name in ("FL", "FR", "C", "LFE", "SL", "SR"):
            if name in float_frames:
                channels.append(list(float_frames[name]))
        while len(channels) < 6:
            channels.append([])
        return channels[:6]

    if isinstance(float_frames, (list, tuple)):
        if len(float_frames) == 0:
            return [[] for _ in range(6)]

        # De-interleaved: six sequences.
        if all(isinstance(x, (list, tuple)) for x in float_frames):
            if len(float_frames) == 6:
                return [list(x) for x in float_frames]
            # Frames x channels (each row has 6 values).
            if len(float_frames[0]) == 6:
                return [[row[i] for row in float_frames] for i in range(6)]

        # Interleaved flat list: de-interleave by 6.
        flat = [float(x) for x in float_frames]
        n = len(flat) // 6
        return [[flat[i * 6 + ch] for i in range(n)] for ch in range(6)]

    # Last resort: try to coerce.
    try:
        flat = [float(x) for x in float_frames]
    except (TypeError, ValueError):
        return [[] for _ in range(6)]
    n = len(flat) // 6
    return [[flat[i * 6 + ch] for i in range(n)] for ch in range(6)]


def fold_down_51_to_stereo(float_frames):
    """Fold a 5.1 block into virtual stereo L/R using ITU-R BS.775-3.

    Returns ``(l_mix, r_mix)`` as lists of floats.
    """
    channels = _extract_channels(float_frames)
    # Pad short/missing channels with silence.
    max_len = max((len(c) for c in channels), default=0)
    for c in channels:
        if len(c) < max_len:
            c.extend([0.0] * (max_len - len(c)))

    fl, fr, c, _lfe, sl, sr = channels[:6]
    l_mix = [fl[i] + (ITU_C * c[i]) + (ITU_C * sl[i]) for i in range(max_len)]
    r_mix = [fr[i] + (ITU_C * c[i]) + (ITU_C * sr[i]) for i in range(max_len)]
    return l_mix, r_mix


def calculate_phase_correlation(l_mix, r_mix):
    """Normalized cross-correlation between two virtual stereo channels."""
    try:
        l = [float(x) for x in l_mix]
        r = [float(x) for x in r_mix]
    except (TypeError, ValueError):
        return 0.0

    n = min(len(l), len(r))
    if n == 0:
        return 0.0

    num = 0.0
    den_l = 0.0
    den_r = 0.0
    for i in range(n):
        lv = l[i]
        rv = r[i]
        num += lv * rv
        den_l += lv * lv
        den_r += rv * rv

    if den_l <= 0.0 or den_r <= 0.0:
        return 0.0
    value = num / math.sqrt(den_l * den_r)
    # Guard against tiny floating-point overshoot.
    if value > 1.0:
        value = 1.0
    elif value < -1.0:
        value = -1.0
    return float(value)
