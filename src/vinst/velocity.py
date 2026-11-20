"""Loudness by velocity: one curve for every note and velocity, so moving from one recorded velocity
to the next changes the sound, not the loudness.

The volume follows a power of the velocity: steep at the soft end, so pianissimo stays, and flatter
at the top, so the usual playing range (about 60 to 100) stays even. At NATIVE_RANGE_DB it is
DecentSampler's own velocity tracking at full strength (ampVelTrack 1: volume in proportion to
velocity), and each velocity range plays in one piece. Other ranges fall more or less steeply than
that tracking can (at most 8.7 / velocity dB per velocity step), so each velocity range is played in
slices, each with its own volume and tracking, near enough to the curve that no step is heard.
"""

import math

MAX_DEVIATION_DB = 0.25  # from the curve, anywhere in a slice
NATIVE_RANGE_DB = 20 * math.log10(127)  # 42.1: volume in proportion to velocity


def curve_db(velocity: float, range_db: float) -> float:
    """The loudness at `velocity`, relative to velocity 127: 0 there, -range_db at velocity 1."""
    return range_db * math.log10(velocity / 127) / math.log10(127)


def tracking_db(velocity: float, track: float) -> float:
    """What DecentSampler's ampVelTrack of `track` takes off at `velocity`."""
    return 20 * math.log10(max(1e-6, 1 - track + track * velocity / 127))


def _fit(lo: int, hi: int, range_db: float) -> tuple[float, float]:
    """The tracking that runs from the line's level at `lo` to its level at `hi`, as far as it can
    (it can't fall more steeply than to silence at velocity 0), and how far it then strays from it."""
    ratio = 10 ** ((curve_db(hi, range_db) - curve_db(lo, range_db)) / 20)
    # (1 - t + t*hi/127) / (1 - t + t*lo/127) = ratio
    denominator = (ratio - 1) + (hi - ratio * lo) / 127
    if ratio <= 1:
        track = 0.0
    else:
        track = 1.0 if denominator <= 0 else min(1.0, (ratio - 1) / denominator)
    off = max(abs((tracking_db(v, track) - tracking_db(hi, track)) - (curve_db(v, range_db) - curve_db(hi, range_db)))
              for v in range(lo, hi + 1))
    return track, off


def slices(lo: int, hi: int, range_db: float) -> list[tuple[int, int, float, float]]:
    """(lowest, highest velocity, dB relative to `hi`'s level, ampVelTrack) per slice of lo..hi, from
    the top down. The dB is the slice's volume: with its tracking, it plays the curve's level at its
    highest velocity, and near it below."""
    out = []
    top = hi
    while top >= lo:
        bottom = top
        while bottom > lo and _fit(bottom - 1, top, range_db)[1] <= MAX_DEVIATION_DB:
            bottom -= 1
        track, _ = _fit(bottom, top, range_db)
        out.append((bottom, top, curve_db(top, range_db) - curve_db(hi, range_db) - tracking_db(top, track), track))
        top = bottom - 1
    return out
