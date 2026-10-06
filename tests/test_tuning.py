"""Kiosk tuning: choosing the anti-spoof threshold from score histograms."""
import pytest

from backend.central.tuning import choose_object_threshold, choose_threshold


def hist(**bins):
    h = [0] * 50
    for k, v in bins.items():
        h[int(k[1:])] = v
    return h


def test_well_separated_scores_are_good_and_the_threshold_sits_in_the_gap():
    result = choose_threshold(hist(b40=200, b45=100), hist(b2=200, b6=100))
    assert result["quality"] == "Good"
    assert 0.14 < result["threshold"] < 0.8
    assert result["spoof_caught"] >= 0.99 and result["real_rejected"] <= 0.01


def test_some_overlap_is_fair():
    result = choose_threshold(hist(b10=20, b30=280), hist(b2=200, b14=100))
    assert result["quality"] == "Fair"


def test_heavy_overlap_is_poor():
    result = choose_threshold(hist(b10=150, b30=150), hist(b10=200, b12=100))
    assert result["quality"] == "Poor"


def test_too_few_samples_is_refused():
    with pytest.raises(ValueError):
        choose_threshold(hist(b40=10), hist(b2=300))


# ---- phone training ---------------------------------------------------------------------

def test_phones_clearly_seen_give_a_good_threshold_above_the_noise():
    result = choose_object_threshold(hist(b35=280, b40=20), hist(b0=250, b10=48, b12=2))
    assert result["quality"] == "Good"
    assert 0.24 <= result["threshold"] <= 0.7          # above every no-phone frame
    assert result["phone_caught"] >= 0.9 and result["false_alarms"] <= 0.01


def test_threshold_never_goes_below_the_floor():
    result = choose_object_threshold(hist(b30=300), hist(b0=300))
    assert result["threshold"] == 0.3


def test_phones_barely_seen_are_poor():
    result = choose_object_threshold(hist(b5=250, b30=50), hist(b0=200, b8=100))
    assert result["quality"] == "Poor"


def test_phone_training_needs_samples_in_both_steps():
    with pytest.raises(ValueError):
        choose_object_threshold(hist(b30=300), hist(b0=5))


def test_real_face_frames_in_the_photo_step_do_not_drag_the_threshold_up():
    # The photo step also caught the person's own face behind the phone (scores ~0.98).
    result = choose_threshold(hist(b49=450), hist(b0=220, b3=10, b49=110))
    assert 0.3 <= result["threshold"] <= 0.7
    assert result["quality"] == "Good" and result["real_rejected"] == 0
