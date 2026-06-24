from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np

from cursor_controller import RelativeCursorController
from gestures import index_fingertip
from hand_detector import make_detection
from sidecar import (
    cursor_motion_to_dict,
    encode_preview_frame,
    select_cursor_hand,
)
from tracker import GestureModeTracker


@dataclass
class Landmark:
    x: float
    y: float
    z: float = 0.0


class FakePyAutoGui:
    PAUSE = 0

    def __init__(self) -> None:
        self.moves: list[tuple[int, int, int]] = []
        self.clicks = 0

    def size(self) -> tuple[int, int]:
        return (1000, 500)

    def moveRel(self, dx: int, dy: int, duration: int = 0) -> None:
        self.moves.append((dx, dy, duration))

    def click(self) -> None:
        self.clicks += 1


def test_pointing_tracking_moves_cursor(
    monkeypatch,
) -> None:
    fake_pyautogui = FakePyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)

    tracker = GestureModeTracker(
        toggle_frames=1,
        release_frames=1,
        smoothing_alpha=1.0,
    )
    cursor = RelativeCursorController(
        gain=1.0,
        max_step=200,
        smoothing_alpha=1.0,
        deadzone_px=0.0,
    )

    first_output = tracker.update(
        hand_visible=True,
        thumb_up=True,
        open_palm=False,
        index_point=index_fingertip(pointing_hand_at(0.40, 0.40)),
        palm_point=index_fingertip(pointing_hand_at(0.40, 0.40)),
        confidence=0.9,
    )
    assert first_output["tracking"] is True
    assert first_output["point"] is None

    first_output = tracker.update(
        hand_visible=True,
        thumb_up=False,
        open_palm=False,
        index_point=index_fingertip(pointing_hand_at(0.40, 0.40)),
        palm_point=index_fingertip(pointing_hand_at(0.40, 0.40)),
        confidence=0.0,
    )
    first_motion = cursor.update(first_output["point"])
    assert first_motion is not None
    assert first_motion.dx == 0
    assert first_motion.dy == 0

    second_output = tracker.update(
        hand_visible=True,
        thumb_up=False,
        open_palm=False,
        index_point=index_fingertip(pointing_hand_at(0.50, 0.52)),
        palm_point=index_fingertip(pointing_hand_at(0.50, 0.52)),
        confidence=0.0,
    )
    second_motion = cursor.update(second_output["point"])

    assert second_output["tracking"] is True
    assert second_motion is not None
    assert second_motion.dx > 0
    assert second_motion.dy > 0
    assert fake_pyautogui.moves
    assert fake_pyautogui.clicks == 0


def test_cursor_motion_to_dict_exposes_diagnostics(
    monkeypatch,
) -> None:
    fake_pyautogui = FakePyAutoGui()
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    cursor = RelativeCursorController(
        gain=1.0,
        max_step=200,
        smoothing_alpha=1.0,
        deadzone_px=0.0,
    )

    cursor.update({"x": 0.2, "y": 0.2})
    motion = cursor.update({"x": 0.3, "y": 0.4})

    assert cursor_motion_to_dict(motion) == {"dx": 99, "dy": 100}


def test_encode_preview_frame_returns_jpeg_data_url() -> None:
    frame = np.zeros((80, 120, 3), dtype=np.uint8)
    frame[:, :] = (20, 80, 160)

    preview = encode_preview_frame(frame)

    assert preview is not None
    assert preview["width"] == 120
    assert preview["height"] == 80
    assert str(preview["image"]).startswith("data:image/jpeg;base64,")


def test_cursor_hand_selection_prefers_open_palm_command() -> None:
    cursor_hand = make_detection(
        index=0,
        landmarks=pointing_hand_at(0.40, 0.40),
        handedness="Right",
        gesture="None",
        gesture_score=0.8,
    )
    other_hand = make_detection(
        index=1,
        landmarks=open_hand_at(0.70, 0.40),
        handedness="Left",
        gesture="Open_Palm",
        gesture_score=0.9,
    )
    cursor_detection = select_cursor_hand([other_hand, cursor_hand])

    assert cursor_detection == other_hand


def test_tracker_does_not_enter_without_thumb_up_toggle() -> None:
    tracker = GestureModeTracker(toggle_frames=1, release_frames=1, smoothing_alpha=1.0)

    output = tracker.update(
        hand_visible=True,
        thumb_up=False,
        open_palm=False,
        index_point=index_fingertip(pointing_hand_at(0.40, 0.40)),
        palm_point=index_fingertip(pointing_hand_at(0.40, 0.40)),
        confidence=0.0,
    )

    assert output["tracking"] is False
    assert output["point"] is None


def pointing_hand_at(x: float, y: float) -> list[Landmark]:
    landmarks = [Landmark(x, y) for _ in range(21)]
    landmarks[0] = Landmark(x, y + 0.30)
    landmarks[4] = Landmark(x - 0.13, y + 0.08)
    landmarks[5] = Landmark(x - 0.12, y + 0.12)
    landmarks[6] = Landmark(x - 0.06, y + 0.06)
    landmarks[8] = Landmark(x, y)
    landmarks[9] = Landmark(x, y + 0.10)
    landmarks[10] = Landmark(x + 0.05, y + 0.14)
    landmarks[13] = Landmark(x + 0.07, y + 0.12)
    landmarks[14] = Landmark(x + 0.10, y + 0.16)
    landmarks[12] = Landmark(x + 0.07, y + 0.16)
    landmarks[17] = Landmark(x + 0.13, y + 0.15)
    landmarks[18] = Landmark(x + 0.15, y + 0.18)
    landmarks[16] = Landmark(x + 0.12, y + 0.18)
    landmarks[20] = Landmark(x + 0.16, y + 0.18)
    return landmarks


def open_hand_at(x: float, y: float) -> list[Landmark]:
    landmarks = [Landmark(x, y) for _ in range(21)]
    landmarks[0] = Landmark(x, y + 0.30)
    landmarks[4] = Landmark(x - 0.18, y + 0.08)
    landmarks[5] = Landmark(x - 0.12, y + 0.05)
    landmarks[6] = Landmark(x - 0.12, y - 0.12)
    landmarks[8] = Landmark(x - 0.12, y - 0.28)
    landmarks[9] = Landmark(x, y + 0.10)
    landmarks[10] = Landmark(x, y - 0.10)
    landmarks[12] = Landmark(x, y - 0.30)
    landmarks[13] = Landmark(x + 0.12, y + 0.05)
    landmarks[14] = Landmark(x + 0.12, y - 0.10)
    landmarks[16] = Landmark(x + 0.12, y - 0.28)
    landmarks[17] = Landmark(x + 0.20, y + 0.08)
    landmarks[18] = Landmark(x + 0.20, y - 0.06)
    landmarks[20] = Landmark(x + 0.20, y - 0.22)
    return landmarks
