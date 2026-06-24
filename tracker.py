from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

from gestures import NormalizedPoint


GestureLabel = Literal["point", "pinch", "open_palm", "thumb_up", "none"]
CommandKind = Literal["scroll", "space"]
SpaceDirection = Literal["left", "right"]


class PointDict(TypedDict):
    x: float
    y: float


class TrackingOutput(TypedDict):
    tracking: bool
    gesture: GestureLabel
    point: PointDict | None
    confidence: float
    command: NotRequired[dict[str, object] | None]


@dataclass
class PinchLocationTracker:
    enter_frames: int = 2
    exit_frames: int = 4
    lost_frames: int = 14
    smoothing_alpha: float = 0.55
    motion_confidence_threshold: float = 0.20

    def __post_init__(self) -> None:
        if self.enter_frames < 1:
            raise ValueError("enter_frames must be >= 1")
        if self.exit_frames < 1:
            raise ValueError("exit_frames must be >= 1")
        if self.lost_frames < 1:
            raise ValueError("lost_frames must be >= 1")
        if not 0.0 < self.smoothing_alpha <= 1.0:
            raise ValueError("smoothing_alpha must be in (0.0, 1.0]")
        if not 0.0 <= self.motion_confidence_threshold <= 1.0:
            raise ValueError("motion_confidence_threshold must be in [0.0, 1.0]")

        self._tracking = False
        self._pinch_count = 0
        self._release_count = 0
        self._lost_count = 0
        self._smoothed_point: NormalizedPoint | None = None

    def update(
        self,
        *,
        hand_visible: bool,
        pinch_active: bool,
        point: NormalizedPoint | None,
        confidence: float = 1.0,
        gesture_label: GestureLabel = "pinch",
    ) -> TrackingOutput:
        if not hand_visible:
            self._handle_lost_hand()
            return self._output("none", 0.0)

        self._lost_count = 0
        gesture: GestureLabel = gesture_label if pinch_active else "none"
        motion_active = (
            pinch_active
            and point is not None
            and confidence >= self.motion_confidence_threshold
        )

        if motion_active and point is not None:
            self._release_count = 0
            self._pinch_count += 1

            if not self._tracking and self._pinch_count >= self.enter_frames:
                self._tracking = True
                self._smoothed_point = point

            if self._tracking:
                self._smoothed_point = self._smooth(point)
        else:
            self._pinch_count = 0

            if self._tracking:
                self._release_count += 1
                self._smoothed_point = None
                if self._release_count >= self.exit_frames:
                    self._reset_tracking()
            else:
                self._release_count = 0

        return self._output(gesture, confidence if pinch_active else 0.0)

    def _handle_lost_hand(self) -> None:
        self._pinch_count = 0
        self._release_count = 0

        if self._tracking:
            self._lost_count += 1
            if self._lost_count >= self.lost_frames:
                self._reset_tracking()
                self._lost_count = 0
        else:
            self._lost_count = 0

    def _smooth(self, point: NormalizedPoint) -> NormalizedPoint:
        if self._smoothed_point is None:
            return point

        alpha = self.smoothing_alpha
        return NormalizedPoint(
            x=self._smoothed_point.x + alpha * (point.x - self._smoothed_point.x),
            y=self._smoothed_point.y + alpha * (point.y - self._smoothed_point.y),
            z=self._smoothed_point.z + alpha * (point.z - self._smoothed_point.z),
        )

    def _reset_tracking(self) -> None:
        self._tracking = False
        self._smoothed_point = None
        self._pinch_count = 0
        self._release_count = 0

    def _output(self, gesture: GestureLabel, confidence: float) -> TrackingOutput:
        point: PointDict | None = None
        if self._tracking and self._smoothed_point is not None:
            point = {
                "x": _clamp01(self._smoothed_point.x),
                "y": _clamp01(self._smoothed_point.y),
            }

        return {
            "tracking": self._tracking,
            "gesture": gesture,
            "point": point,
            "confidence": max(0.0, min(1.0, confidence)),
        }


@dataclass
class OpenPalmCursorTracker:
    toggle_frames: int = 4
    release_frames: int = 3
    lost_frames: int = 14
    smoothing_alpha: float = 0.55
    open_palm_confidence_threshold: float = 0.50

    def __post_init__(self) -> None:
        if self.toggle_frames < 1:
            raise ValueError("toggle_frames must be >= 1")
        if self.release_frames < 1:
            raise ValueError("release_frames must be >= 1")
        if self.lost_frames < 1:
            raise ValueError("lost_frames must be >= 1")
        if not 0.0 < self.smoothing_alpha <= 1.0:
            raise ValueError("smoothing_alpha must be in (0.0, 1.0]")
        if not 0.0 <= self.open_palm_confidence_threshold <= 1.0:
            raise ValueError("open_palm_confidence_threshold must be in [0.0, 1.0]")

        self._enabled = False
        self._open_count = 0
        self._release_count = 0
        self._lost_count = 0
        self._toggle_latched = False
        self._smoothed_point: NormalizedPoint | None = None

    def update(
        self,
        *,
        hand_visible: bool,
        open_palm: bool,
        point: NormalizedPoint | None,
        confidence: float = 1.0,
    ) -> TrackingOutput:
        if not hand_visible:
            self._handle_lost_hand()
            return self._output("none", 0.0)

        self._lost_count = 0
        palm_confidence = max(0.0, min(1.0, confidence))
        palm_active = (
            open_palm
            and palm_confidence >= self.open_palm_confidence_threshold
        )

        if palm_active:
            self._open_count += 1
            self._release_count = 0
            self._smoothed_point = None

            if self._open_count >= self.toggle_frames and not self._toggle_latched:
                self._enabled = not self._enabled
                self._toggle_latched = True

            return self._output("open_palm", palm_confidence)

        self._open_count = 0
        self._release_count += 1
        if self._release_count >= self.release_frames:
            self._toggle_latched = False

        if self._enabled and point is not None:
            self._smoothed_point = self._smooth(point)
            return self._output("point", 1.0)

        self._smoothed_point = None
        return self._output("none", 0.0)

    def _handle_lost_hand(self) -> None:
        self._open_count = 0
        self._release_count = 0
        self._toggle_latched = False
        self._smoothed_point = None

        if self._enabled:
            self._lost_count += 1
            if self._lost_count >= self.lost_frames:
                self._enabled = False
                self._lost_count = 0
        else:
            self._lost_count = 0

    def _smooth(self, point: NormalizedPoint) -> NormalizedPoint:
        if self._smoothed_point is None:
            return point

        alpha = self.smoothing_alpha
        return NormalizedPoint(
            x=self._smoothed_point.x + alpha * (point.x - self._smoothed_point.x),
            y=self._smoothed_point.y + alpha * (point.y - self._smoothed_point.y),
            z=self._smoothed_point.z + alpha * (point.z - self._smoothed_point.z),
        )

    def _output(self, gesture: GestureLabel, confidence: float) -> TrackingOutput:
        point: PointDict | None = None
        if self._enabled and self._smoothed_point is not None:
            point = {
                "x": _clamp01(self._smoothed_point.x),
                "y": _clamp01(self._smoothed_point.y),
            }

        return {
            "tracking": self._enabled,
            "gesture": gesture,
            "point": point,
            "confidence": max(0.0, min(1.0, confidence)),
        }


@dataclass
class GestureModeTracker:
    toggle_frames: int = 3
    release_frames: int = 3
    lost_frames: int = 14
    smoothing_alpha: float = 0.55
    thumb_confidence_threshold: float = 0.50
    palm_confidence_threshold: float = 0.50
    scroll_threshold: float = 0.010
    scroll_gain: float = 150.0
    max_scroll_amount: int = 8
    space_threshold: float = 0.16
    space_dominance_ratio: float = 1.6
    space_cooldown_frames: int = 22

    def __post_init__(self) -> None:
        if self.toggle_frames < 1:
            raise ValueError("toggle_frames must be >= 1")
        if self.release_frames < 1:
            raise ValueError("release_frames must be >= 1")
        if self.lost_frames < 1:
            raise ValueError("lost_frames must be >= 1")
        if not 0.0 <= self.thumb_confidence_threshold <= 1.0:
            raise ValueError("thumb_confidence_threshold must be in [0.0, 1.0]")
        if not 0.0 <= self.palm_confidence_threshold <= 1.0:
            raise ValueError("palm_confidence_threshold must be in [0.0, 1.0]")
        if self.scroll_threshold < 0.0:
            raise ValueError("scroll_threshold must be >= 0")
        if self.scroll_gain <= 0.0:
            raise ValueError("scroll_gain must be > 0")
        if self.max_scroll_amount < 1:
            raise ValueError("max_scroll_amount must be >= 1")
        if self.space_threshold <= 0.0:
            raise ValueError("space_threshold must be > 0")
        if self.space_dominance_ratio <= 1.0:
            raise ValueError("space_dominance_ratio must be > 1")
        if self.space_cooldown_frames < 0:
            raise ValueError("space_cooldown_frames must be >= 0")

        self._cursor_enabled = False
        self._thumb_count = 0
        self._release_count = 0
        self._lost_count = 0
        self._toggle_latched = False
        self._cursor_point: NormalizedPoint | None = None
        self._previous_palm: NormalizedPoint | None = None
        self._palm_anchor: NormalizedPoint | None = None
        self._space_cooldown = 0

    def update(
        self,
        *,
        hand_visible: bool,
        thumb_up: bool,
        open_palm: bool,
        index_point: NormalizedPoint | None,
        palm_point: NormalizedPoint | None,
        confidence: float = 1.0,
    ) -> TrackingOutput:
        if self._space_cooldown > 0:
            self._space_cooldown -= 1

        if not hand_visible:
            self._handle_lost_hand()
            return self._output("none", 0.0)

        self._lost_count = 0
        clamped_confidence = max(0.0, min(1.0, confidence))
        thumb_active = thumb_up and clamped_confidence >= self.thumb_confidence_threshold
        palm_active = open_palm and clamped_confidence >= self.palm_confidence_threshold

        if thumb_active:
            self._thumb_count += 1
            self._release_count = 0
            self._cursor_point = None
            self._reset_palm_motion()

            if self._thumb_count >= self.toggle_frames and not self._toggle_latched:
                self._cursor_enabled = not self._cursor_enabled
                self._toggle_latched = True

            return self._output("thumb_up", clamped_confidence)

        self._thumb_count = 0
        self._release_count += 1
        if self._release_count >= self.release_frames:
            self._toggle_latched = False

        if palm_active and palm_point is not None:
            self._cursor_point = None
            command = self._open_palm_command(palm_point)
            return self._output("open_palm", clamped_confidence, command=command)

        self._reset_palm_motion()
        if self._cursor_enabled and index_point is not None:
            self._cursor_point = index_point
            return self._output("point", 1.0)

        self._cursor_point = None
        return self._output("none", 0.0)

    def _open_palm_command(
        self,
        palm_point: NormalizedPoint,
    ) -> dict[str, object] | None:
        if self._previous_palm is None or self._palm_anchor is None:
            self._previous_palm = palm_point
            self._palm_anchor = palm_point
            return None

        previous = self._previous_palm
        anchor = self._palm_anchor
        self._previous_palm = palm_point

        frame_dx = palm_point.x - previous.x
        frame_dy = palm_point.y - previous.y
        anchor_dx = palm_point.x - anchor.x
        anchor_dy = palm_point.y - anchor.y

        if (
            self._space_cooldown == 0
            and abs(anchor_dx) >= self.space_threshold
            and abs(anchor_dx) >= abs(anchor_dy) * self.space_dominance_ratio
        ):
            self._space_cooldown = self.space_cooldown_frames
            self._palm_anchor = palm_point
            return {
                "kind": "space",
                "direction": "right" if anchor_dx > 0.0 else "left",
            }

        if abs(frame_dy) >= self.scroll_threshold and abs(frame_dy) > abs(frame_dx):
            amount = int(round(-frame_dy * self.scroll_gain))
            if amount != 0:
                return {
                    "kind": "scroll",
                    "amount": max(
                        -self.max_scroll_amount,
                        min(self.max_scroll_amount, amount),
                    ),
                }

        return None

    def _handle_lost_hand(self) -> None:
        self._thumb_count = 0
        self._release_count = 0
        self._toggle_latched = False
        self._cursor_point = None
        self._reset_palm_motion()

        if self._cursor_enabled:
            self._lost_count += 1
            if self._lost_count >= self.lost_frames:
                self._cursor_enabled = False
                self._lost_count = 0
        else:
            self._lost_count = 0

    def _reset_palm_motion(self) -> None:
        self._previous_palm = None
        self._palm_anchor = None

    def _output(
        self,
        gesture: GestureLabel,
        confidence: float,
        *,
        command: dict[str, object] | None = None,
    ) -> TrackingOutput:
        point: PointDict | None = None
        if self._cursor_enabled and self._cursor_point is not None:
            point = {
                "x": _clamp01(self._cursor_point.x),
                "y": _clamp01(self._cursor_point.y),
            }

        return {
            "tracking": self._cursor_enabled,
            "gesture": gesture,
            "point": point,
            "confidence": max(0.0, min(1.0, confidence)),
            "command": command,
        }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
