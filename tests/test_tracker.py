from __future__ import annotations

import pytest

from gestures import NormalizedPoint
from tracker import GestureModeTracker, OpenPalmCursorTracker, PinchLocationTracker


def test_tracker_enters_tracking_after_debounced_pinch() -> None:
    tracker = PinchLocationTracker(enter_frames=3)
    point = NormalizedPoint(0.4, 0.5)

    assert tracker.update(
        hand_visible=True,
        pinch_active=True,
        point=point,
        confidence=0.8,
    )["tracking"] is False
    assert tracker.update(
        hand_visible=True,
        pinch_active=True,
        point=point,
        confidence=0.8,
    )["tracking"] is False

    output = tracker.update(
        hand_visible=True,
        pinch_active=True,
        point=point,
        confidence=0.8,
    )

    assert output["tracking"] is True
    assert output["point"] == {"x": 0.4, "y": 0.5}


def test_tracker_exits_tracking_after_debounced_release() -> None:
    tracker = PinchLocationTracker(enter_frames=1, exit_frames=2)
    point = NormalizedPoint(0.4, 0.5)

    assert tracker.update(
        hand_visible=True,
        pinch_active=True,
        point=point,
    )["tracking"] is True
    assert tracker.update(
        hand_visible=True,
        pinch_active=False,
        point=None,
    )["tracking"] is True

    output = tracker.update(
        hand_visible=True,
        pinch_active=False,
        point=None,
    )

    assert output["tracking"] is False
    assert output["point"] is None


def test_tracker_freezes_point_during_weak_pinch_release() -> None:
    tracker = PinchLocationTracker(
        enter_frames=1,
        exit_frames=2,
        smoothing_alpha=1.0,
        motion_confidence_threshold=0.20,
    )

    assert tracker.update(
        hand_visible=True,
        pinch_active=True,
        point=NormalizedPoint(0.4, 0.5),
        confidence=0.8,
    )["point"] == {"x": 0.4, "y": 0.5}

    output = tracker.update(
        hand_visible=True,
        pinch_active=True,
        point=NormalizedPoint(0.9, 0.9),
        confidence=0.05,
    )

    assert output["tracking"] is True
    assert output["gesture"] == "pinch"
    assert output["point"] is None


def test_tracker_exits_tracking_after_lost_hand_timeout() -> None:
    tracker = PinchLocationTracker(enter_frames=1, lost_frames=2)

    assert tracker.update(
        hand_visible=True,
        pinch_active=True,
        point=NormalizedPoint(0.4, 0.5),
    )["tracking"] is True
    assert tracker.update(
        hand_visible=False,
        pinch_active=False,
        point=None,
    )["tracking"] is True

    output = tracker.update(
        hand_visible=False,
        pinch_active=False,
        point=None,
    )

    assert output["tracking"] is False


def test_tracker_smooths_location_toward_new_point() -> None:
    tracker = PinchLocationTracker(enter_frames=1, smoothing_alpha=0.5)

    tracker.update(
        hand_visible=True,
        pinch_active=True,
        point=NormalizedPoint(0.0, 0.0),
    )
    output = tracker.update(
        hand_visible=True,
        pinch_active=True,
        point=NormalizedPoint(1.0, 1.0),
    )

    assert output["point"] is not None
    assert output["point"]["x"] == pytest.approx(0.5)
    assert output["point"]["y"] == pytest.approx(0.5)


def test_open_palm_tracker_toggles_on_after_debounce() -> None:
    tracker = OpenPalmCursorTracker(toggle_frames=3)

    assert tracker.update(
        hand_visible=True,
        open_palm=True,
        point=NormalizedPoint(0.2, 0.3),
        confidence=0.9,
    )["tracking"] is False
    assert tracker.update(
        hand_visible=True,
        open_palm=True,
        point=NormalizedPoint(0.2, 0.3),
        confidence=0.9,
    )["tracking"] is False

    output = tracker.update(
        hand_visible=True,
        open_palm=True,
        point=NormalizedPoint(0.2, 0.3),
        confidence=0.9,
    )

    assert output["tracking"] is True
    assert output["gesture"] == "open_palm"
    assert output["point"] is None


def test_open_palm_tracker_latches_held_palm_until_released() -> None:
    tracker = OpenPalmCursorTracker(toggle_frames=1, release_frames=2)

    assert tracker.update(
        hand_visible=True,
        open_palm=True,
        point=NormalizedPoint(0.2, 0.3),
        confidence=0.9,
    )["tracking"] is True
    assert tracker.update(
        hand_visible=True,
        open_palm=True,
        point=NormalizedPoint(0.2, 0.3),
        confidence=0.9,
    )["tracking"] is True

    tracker.update(
        hand_visible=True,
        open_palm=False,
        point=NormalizedPoint(0.4, 0.5),
    )
    tracker.update(
        hand_visible=True,
        open_palm=False,
        point=NormalizedPoint(0.4, 0.5),
    )

    output = tracker.update(
        hand_visible=True,
        open_palm=True,
        point=NormalizedPoint(0.2, 0.3),
        confidence=0.9,
    )

    assert output["tracking"] is False
    assert output["gesture"] == "open_palm"


def test_open_palm_tracker_tracks_index_point_while_enabled() -> None:
    tracker = OpenPalmCursorTracker(
        toggle_frames=1,
        release_frames=1,
        smoothing_alpha=0.5,
    )

    tracker.update(
        hand_visible=True,
        open_palm=True,
        point=NormalizedPoint(0.0, 0.0),
        confidence=0.9,
    )
    tracker.update(
        hand_visible=True,
        open_palm=False,
        point=NormalizedPoint(0.0, 0.0),
    )
    output = tracker.update(
        hand_visible=True,
        open_palm=False,
        point=NormalizedPoint(1.0, 1.0),
    )

    assert output["tracking"] is True
    assert output["gesture"] == "point"
    assert output["point"] is not None
    assert output["point"]["x"] == pytest.approx(0.5)
    assert output["point"]["y"] == pytest.approx(0.5)


def test_open_palm_tracker_exits_after_lost_hand_timeout() -> None:
    tracker = OpenPalmCursorTracker(toggle_frames=1, lost_frames=2)

    assert tracker.update(
        hand_visible=True,
        open_palm=True,
        point=NormalizedPoint(0.2, 0.3),
        confidence=0.9,
    )["tracking"] is True
    assert tracker.update(
        hand_visible=False,
        open_palm=False,
        point=None,
    )["tracking"] is True

    output = tracker.update(
        hand_visible=False,
        open_palm=False,
        point=None,
    )

    assert output["tracking"] is False


def test_gesture_mode_tracker_thumb_up_toggles_cursor() -> None:
    tracker = GestureModeTracker(toggle_frames=2)

    assert tracker.update(
        hand_visible=True,
        thumb_up=True,
        open_palm=False,
        index_point=NormalizedPoint(0.2, 0.3),
        palm_point=NormalizedPoint(0.2, 0.3),
        confidence=0.9,
    )["tracking"] is False

    output = tracker.update(
        hand_visible=True,
        thumb_up=True,
        open_palm=False,
        index_point=NormalizedPoint(0.2, 0.3),
        palm_point=NormalizedPoint(0.2, 0.3),
        confidence=0.9,
    )

    assert output["tracking"] is True
    assert output["gesture"] == "thumb_up"
    assert output["point"] is None


def test_gesture_mode_tracker_tracks_index_after_thumb_toggle() -> None:
    tracker = GestureModeTracker(toggle_frames=1, release_frames=1, smoothing_alpha=1.0)

    tracker.update(
        hand_visible=True,
        thumb_up=True,
        open_palm=False,
        index_point=NormalizedPoint(0.2, 0.3),
        palm_point=NormalizedPoint(0.2, 0.3),
        confidence=0.9,
    )
    output = tracker.update(
        hand_visible=True,
        thumb_up=False,
        open_palm=False,
        index_point=NormalizedPoint(0.4, 0.5),
        palm_point=NormalizedPoint(0.4, 0.5),
        confidence=0.0,
    )

    assert output["tracking"] is True
    assert output["gesture"] == "point"
    assert output["point"] == {"x": 0.4, "y": 0.5}


def test_gesture_mode_tracker_open_palm_scrolls_by_vertical_motion() -> None:
    tracker = GestureModeTracker(scroll_threshold=0.005, scroll_gain=100.0)

    tracker.update(
        hand_visible=True,
        thumb_up=False,
        open_palm=True,
        index_point=NormalizedPoint(0.5, 0.5),
        palm_point=NormalizedPoint(0.5, 0.5),
        confidence=0.9,
    )
    output = tracker.update(
        hand_visible=True,
        thumb_up=False,
        open_palm=True,
        index_point=NormalizedPoint(0.5, 0.45),
        palm_point=NormalizedPoint(0.5, 0.45),
        confidence=0.9,
    )

    assert output["gesture"] == "open_palm"
    assert output["point"] is None
    assert output["command"] == {"kind": "scroll", "amount": 5}


def test_gesture_mode_tracker_open_palm_swipe_switches_spaces() -> None:
    tracker = GestureModeTracker(
        space_threshold=0.10,
        space_dominance_ratio=1.2,
        space_cooldown_frames=2,
    )

    tracker.update(
        hand_visible=True,
        thumb_up=False,
        open_palm=True,
        index_point=NormalizedPoint(0.2, 0.5),
        palm_point=NormalizedPoint(0.2, 0.5),
        confidence=0.9,
    )
    output = tracker.update(
        hand_visible=True,
        thumb_up=False,
        open_palm=True,
        index_point=NormalizedPoint(0.35, 0.51),
        palm_point=NormalizedPoint(0.35, 0.51),
        confidence=0.9,
    )

    assert output["gesture"] == "open_palm"
    assert output["command"] == {"kind": "space", "direction": "right"}
