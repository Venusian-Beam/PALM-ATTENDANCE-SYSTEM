"""Kiosk tuning: pick the anti-spoof threshold for one kiosk's webcam and light.

The kiosk records live-face scores for two 30-second steps (real faces, then photos and
phones held up) and sends only a 50-bin histogram of the scores (no images). The
threshold is placed in the middle of the gap that best separates the two.
"""
from __future__ import annotations

from typing import Any

BINS = 50
MIN_SAMPLES = 30
LOWEST, HIGHEST = 0.1, 0.9
GAIN_TOLERANCE = 0.05


def _cumulative(hist: list[int]) -> list[float]:
    total = sum(hist) or 1
    out, running = [], 0
    for count in hist:
        running += count
        out.append(running / total)
    return out


def choose_threshold(real_hist: list[int], spoof_hist: list[int]) -> dict[str, Any]:
    """Returns {threshold, quality, real_rejected, spoof_caught, message}.

    Picks the cut-off that best separates the two steps (most photo frames below it,
    fewest real frames below it), and puts it in the middle of the widest such gap.
    This copes with the photo step also containing some frames of the real face behind
    the phone, which a "99 % of photo frames below" rule would chase up to the real faces.
    """
    if len(real_hist) != BINS or len(spoof_hist) != BINS:
        raise ValueError("Histograms must have 50 bins.")
    if sum(real_hist) < MIN_SAMPLES or sum(spoof_hist) < MIN_SAMPLES:
        raise ValueError("Not enough samples. Run both steps with a face in view.")
    real_cum, spoof_cum = _cumulative(real_hist), _cumulative(spoof_hist)
    lo, hi = round(LOWEST * BINS), round(HIGHEST * BINS)
    # Cut-off at bin edge k: frames in bins < k score below it.
    gain = {k: spoof_cum[k - 1] - real_cum[k - 1] for k in range(lo, hi + 1)}
    # Near-best counts as best: catching the last few stray photo frames is not worth
    # moving the cut-off up against the real faces.
    good_enough = max(gain.values()) - GAIN_TOLERANCE
    runs, run = [], []
    for k in range(lo, hi + 1):
        if gain[k] >= good_enough:
            run.append(k)
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    widest = max(runs, key=len)
    edge = (widest[0] + widest[-1]) / 2
    threshold = round(edge / BINS, 2)
    k = max(1, min(BINS, round(threshold * BINS)))
    real_rejected, spoof_caught = real_cum[k - 1], spoof_cum[k - 1]
    if real_rejected > 0.2:
        quality, message = "Poor", ("Real faces and photos look alike here. Improve the light "
                                    "in front of the kiosk and run the steps again.")
    elif real_rejected > 0.05:
        quality, message = "Fair", "Some real faces score low. More light in front would help."
    else:
        quality, message = "Good", "Real faces and photos are well apart."
    return {"threshold": threshold, "quality": quality,
            "real_rejected": round(real_rejected, 3), "spoof_caught": round(spoof_caught, 3),
            "message": message}


# ---- phone training ---------------------------------------------------------------------

OBJECT_LOWEST, OBJECT_HIGHEST = 0.3, 0.8
CLEAR_FALSE_ALARMS = 0.01


def choose_object_threshold(phone_hist: list[int], clear_hist: list[int]) -> dict[str, Any]:
    """Phone confidence that blocks: at most 1 % of no-phone frames reach it.
    Returns {threshold, quality, phone_caught, false_alarms, message}."""
    if len(phone_hist) != BINS or len(clear_hist) != BINS:
        raise ValueError("Histograms must have 50 bins.")
    if sum(phone_hist) < MIN_SAMPLES or sum(clear_hist) < MIN_SAMPLES:
        raise ValueError("Not enough samples. Run both steps with a face in view.")
    clear_cum, phone_cum = _cumulative(clear_hist), _cumulative(phone_hist)
    # Lowest bin edge with no more than 1 % of the no-phone frames at or above it.
    edge = next((k for k in range(1, BINS) if 1 - clear_cum[k - 1] <= CLEAR_FALSE_ALARMS), BINS)
    threshold = round(min(OBJECT_HIGHEST, max(OBJECT_LOWEST, edge / BINS)), 2)
    k = round(threshold * BINS)
    phone_caught = 1 - phone_cum[k - 1]
    false_alarms = 1 - clear_cum[k - 1]
    if phone_caught >= 0.9:
        quality, message = "Good", "Phones are seen clearly at this kiosk."
    elif phone_caught >= 0.7:
        quality, message = "Fair", "Most phones are caught. Hold them closer to the face to check."
    else:
        quality, message = "Poor", ("Phones are hard to see here. Hold them closer to the face, "
                                    "or improve the light, and run the steps again.")
    return {"threshold": threshold, "quality": quality, "phone_caught": round(phone_caught, 3),
            "false_alarms": round(false_alarms, 3), "message": message}
