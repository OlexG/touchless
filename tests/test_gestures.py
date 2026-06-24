from __future__ import annotations

from dataclasses import dataclass

import pytest

from gestures import (
    DEFAULT_MIN_PINCHED_FINGERS,
    DEFAULT_PINCH_THRESHOLD,
    cursor_point,
    detect_index_pointing,
    detect_hand_shape,
    detect_pinch,
    palm_center,
    palm_facing_score,
)


@dataclass(frozen=True)
class FakeLandmark:
    x: float
    y: float
    z: float = 0.0


def make_landmarks(
    thumb: FakeLandmark,
    index: FakeLandmark,
    middle: FakeLandmark,
    ring: FakeLandmark,
    pinky: FakeLandmark,
    wrist: FakeLandmark = FakeLandmark(0.50, 0.70),
    index_mcp: FakeLandmark = FakeLandmark(0.44, 0.52),
    middle_mcp: FakeLandmark = FakeLandmark(0.50, 0.50),
    ring_mcp: FakeLandmark = FakeLandmark(0.56, 0.52),
    pinky_mcp: FakeLandmark = FakeLandmark(0.60, 0.55),
) -> list[FakeLandmark]:
    landmarks = [FakeLandmark(0.0, 0.0) for _ in range(21)]
    landmarks[0] = wrist
    landmarks[4] = thumb
    landmarks[5] = index_mcp
    landmarks[8] = index
    landmarks[9] = middle_mcp
    landmarks[13] = ring_mcp
    landmarks[12] = middle
    landmarks[17] = pinky_mcp
    landmarks[16] = ring
    landmarks[20] = pinky
    return landmarks


def test_detect_pinch_active_when_at_least_three_fingertips_are_close_to_thumb() -> None:
    landmarks = make_landmarks(
        FakeLandmark(0.50, 0.50),
        FakeLandmark(0.53, 0.50),
        FakeLandmark(0.50, 0.54),
        FakeLandmark(0.47, 0.50),
        FakeLandmark(0.80, 0.80),
    )

    result = detect_pinch(
        landmarks,
        threshold=DEFAULT_PINCH_THRESHOLD,
        min_pinched_fingers=DEFAULT_MIN_PINCHED_FINGERS,
    )

    assert result.active is True
    assert result.palm_facing is True
    assert result.midpoint.x == pytest.approx(0.50)
    assert result.midpoint.y == pytest.approx(0.51)
    assert result.confidence > 0.0


def test_detect_pinch_inactive_when_fewer_than_three_fingertips_are_close() -> None:
    landmarks = make_landmarks(
        FakeLandmark(0.50, 0.50),
        FakeLandmark(0.53, 0.50),
        FakeLandmark(0.50, 0.54),
        FakeLandmark(0.75, 0.75),
        FakeLandmark(0.80, 0.80),
    )

    result = detect_pinch(
        landmarks,
        threshold=DEFAULT_PINCH_THRESHOLD,
        min_pinched_fingers=DEFAULT_MIN_PINCHED_FINGERS,
    )

    assert result.active is False
    assert result.confidence == 0.0


def test_palm_facing_score_drops_when_palm_is_side_facing() -> None:
    landmarks = make_landmarks(
        FakeLandmark(0.50, 0.50),
        FakeLandmark(0.53, 0.50),
        FakeLandmark(0.50, 0.54),
        FakeLandmark(0.47, 0.50),
        FakeLandmark(0.50, 0.46),
        index_mcp=FakeLandmark(0.49, 0.52),
        middle_mcp=FakeLandmark(0.50, 0.50),
        ring_mcp=FakeLandmark(0.51, 0.52),
        pinky_mcp=FakeLandmark(0.52, 0.55),
    )

    assert palm_facing_score(landmarks) < 0.65


def test_detect_pinch_rejects_incomplete_landmarks() -> None:
    with pytest.raises(ValueError):
        detect_pinch([FakeLandmark(0.0, 0.0) for _ in range(20)])


def test_detect_pinch_rejects_invalid_min_pinched_fingers() -> None:
    landmarks = make_landmarks(
        FakeLandmark(0.50, 0.50),
        FakeLandmark(0.53, 0.50),
        FakeLandmark(0.50, 0.54),
        FakeLandmark(0.47, 0.50),
        FakeLandmark(0.50, 0.46),
    )

    with pytest.raises(ValueError):
        detect_pinch(landmarks, min_pinched_fingers=5)


def test_detect_pinch_is_relative_to_hand_size() -> None:
    far_hand = make_landmarks(
        FakeLandmark(0.50, 0.50),
        FakeLandmark(0.53, 0.50),
        FakeLandmark(0.50, 0.54),
        FakeLandmark(0.47, 0.50),
        FakeLandmark(0.80, 0.80),
        wrist=FakeLandmark(0.50, 0.70),
        middle_mcp=FakeLandmark(0.50, 0.50),
    )
    close_hand = make_landmarks(
        FakeLandmark(0.50, 0.50),
        FakeLandmark(0.56, 0.50),
        FakeLandmark(0.50, 0.58),
        FakeLandmark(0.44, 0.50),
        FakeLandmark(0.80, 0.80),
        wrist=FakeLandmark(0.50, 0.90),
        index_mcp=FakeLandmark(0.32, 0.54),
        middle_mcp=FakeLandmark(0.50, 0.50),
        ring_mcp=FakeLandmark(0.60, 0.54),
        pinky_mcp=FakeLandmark(0.68, 0.58),
    )

    assert detect_pinch(far_hand).active is True
    assert detect_pinch(close_hand).active is True


def test_palm_center_uses_stable_palm_landmarks() -> None:
    landmarks = make_landmarks(
        FakeLandmark(0.50, 0.50),
        FakeLandmark(0.53, 0.50),
        FakeLandmark(0.50, 0.54),
        FakeLandmark(0.47, 0.50),
        FakeLandmark(0.80, 0.80),
        wrist=FakeLandmark(0.50, 0.70),
        index_mcp=FakeLandmark(0.40, 0.52),
        middle_mcp=FakeLandmark(0.50, 0.50),
        ring_mcp=FakeLandmark(0.60, 0.52),
        pinky_mcp=FakeLandmark(0.70, 0.56),
    )

    center = palm_center(landmarks)

    assert center.x == pytest.approx(0.54)
    assert center.y == pytest.approx(0.56)


def test_cursor_point_blends_index_tip_with_pip() -> None:
    landmarks = make_index_pointing_landmarks(
        index_tip=FakeLandmark(0.20, 0.40),
        index_pip=FakeLandmark(0.50, 0.70),
    )

    point = cursor_point(landmarks, tip_weight=0.70)

    assert point.x == pytest.approx(0.29)
    assert point.y == pytest.approx(0.49)


def test_cursor_point_rejects_invalid_weight() -> None:
    landmarks = make_index_pointing_landmarks()

    with pytest.raises(ValueError):
        cursor_point(landmarks, tip_weight=1.2)


def test_detect_index_pointing_active_for_index_extended_sideways() -> None:
    landmarks = make_index_pointing_landmarks(
        index_tip=FakeLandmark(0.20, 0.52),
        index_pip=FakeLandmark(0.34, 0.52),
        index_mcp=FakeLandmark(0.48, 0.52),
    )

    result = detect_index_pointing(landmarks)

    assert result.active is True
    assert result.point.x == pytest.approx(0.20)
    assert result.confidence > 0.0


def test_detect_index_pointing_rejects_open_palm() -> None:
    landmarks = make_index_pointing_landmarks(
        middle_tip=FakeLandmark(0.50, 0.16),
        ring_tip=FakeLandmark(0.58, 0.18),
        pinky_tip=FakeLandmark(0.66, 0.22),
    )

    result = detect_index_pointing(landmarks)

    assert result.active is False


def make_hand_shape_landmarks(
    *,
    tip_distance: float,
    pip_distance: float,
) -> list[FakeLandmark]:
    landmarks = [FakeLandmark(0.0, 0.0) for _ in range(21)]
    landmarks[0] = FakeLandmark(0.50, 0.80)

    for tip_index in (8, 12, 16, 20):
        landmarks[tip_index] = FakeLandmark(0.50, 0.80 - tip_distance)

    for pip_index in (6, 10, 14, 18):
        landmarks[pip_index] = FakeLandmark(0.50, 0.80 - pip_distance)

    return landmarks


def make_index_pointing_landmarks(
    *,
    index_tip: FakeLandmark = FakeLandmark(0.50, 0.16),
    index_pip: FakeLandmark = FakeLandmark(0.50, 0.34),
    index_mcp: FakeLandmark = FakeLandmark(0.50, 0.52),
    middle_tip: FakeLandmark = FakeLandmark(0.56, 0.58),
    ring_tip: FakeLandmark = FakeLandmark(0.62, 0.60),
    pinky_tip: FakeLandmark = FakeLandmark(0.68, 0.62),
) -> list[FakeLandmark]:
    landmarks = [FakeLandmark(0.0, 0.0) for _ in range(21)]
    landmarks[0] = FakeLandmark(0.50, 0.80)
    landmarks[5] = index_mcp
    landmarks[6] = index_pip
    landmarks[8] = index_tip
    landmarks[9] = FakeLandmark(0.56, 0.54)
    landmarks[10] = FakeLandmark(0.56, 0.56)
    landmarks[12] = middle_tip
    landmarks[13] = FakeLandmark(0.62, 0.56)
    landmarks[14] = FakeLandmark(0.62, 0.58)
    landmarks[16] = ring_tip
    landmarks[17] = FakeLandmark(0.68, 0.58)
    landmarks[18] = FakeLandmark(0.68, 0.60)
    landmarks[20] = pinky_tip
    return landmarks


def test_detect_hand_shape_open_when_fingertips_extend_past_pips() -> None:
    result = detect_hand_shape(
        make_hand_shape_landmarks(tip_distance=0.50, pip_distance=0.30)
    )

    assert result.shape == "open"
    assert result.extended_fingers == 4


def test_detect_hand_shape_closed_when_fingertips_are_not_extended() -> None:
    result = detect_hand_shape(
        make_hand_shape_landmarks(tip_distance=0.20, pip_distance=0.30)
    )

    assert result.shape == "closed"
    assert result.extended_fingers == 0
