from __future__ import annotations

import argparse
from typing import NoReturn

import cv2

from cursor_controller import RelativeCursorController
from gestures import (
    DEFAULT_MIN_PINCHED_FINGERS,
    DEFAULT_PINCH_THRESHOLD,
    NormalizedPoint,
    detect_pinch,
    palm_center,
)
from hand_detector import HandDetector
from tracker import PinchLocationTracker, TrackingOutput


WINDOW_NAME = "Touchless Gesture Tracking"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Track a pinch gesture from the webcam using MediaPipe."
    )
    parser.add_argument("--camera", type=int, default=0, help="Camera index to open.")
    parser.add_argument(
        "--pinch-threshold",
        type=float,
        default=DEFAULT_PINCH_THRESHOLD,
        help="Hand-size-relative thumb-to-fingertip distance below which pinch is active.",
    )
    parser.add_argument(
        "--min-pinched-fingers",
        type=int,
        default=DEFAULT_MIN_PINCHED_FINGERS,
        choices=(1, 2, 3, 4),
        help="Number of non-thumb fingertips that must be close to the thumb.",
    )
    parser.add_argument(
        "--no-mirror",
        action="store_true",
        help="Disable mirrored webcam preview.",
    )
    parser.add_argument(
        "--enter-frames",
        type=int,
        default=2,
        help="Consecutive pinch frames required to enter tracking.",
    )
    parser.add_argument(
        "--exit-frames",
        type=int,
        default=4,
        help="Consecutive non-pinch frames required to exit tracking.",
    )
    parser.add_argument(
        "--lost-frames",
        type=int,
        default=14,
        help="Consecutive missing-hand frames required to exit tracking.",
    )
    parser.add_argument(
        "--smoothing-alpha",
        type=float,
        default=0.55,
        help="Smoothing responsiveness in (0, 1]; higher follows fast motion more closely.",
    )
    parser.add_argument(
        "--control-cursor",
        action="store_true",
        help="Move the macOS cursor while tracking is active.",
    )
    parser.add_argument(
        "--cursor-gain",
        type=float,
        default=1.4,
        help="Relative cursor movement multiplier.",
    )
    parser.add_argument(
        "--max-cursor-step",
        type=int,
        default=140,
        help="Maximum cursor pixels moved per camera frame.",
    )
    parser.add_argument(
        "--cursor-smoothing-alpha",
        type=float,
        default=0.35,
        help="Cursor delta smoothing in (0, 1]; lower is smoother.",
    )
    parser.add_argument(
        "--cursor-deadzone-px",
        type=float,
        default=1.5,
        help="Ignore smoothed cursor movement smaller than this many pixels.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cap = cv2.VideoCapture(args.camera)

    if not cap.isOpened():
        raise SystemExit(f"Could not open camera index {args.camera}")

    tracker = PinchLocationTracker(
        enter_frames=args.enter_frames,
        exit_frames=args.exit_frames,
        lost_frames=args.lost_frames,
        smoothing_alpha=args.smoothing_alpha,
    )
    cursor = (
        RelativeCursorController(
            gain=args.cursor_gain,
            max_step=args.max_cursor_step,
            smoothing_alpha=args.cursor_smoothing_alpha,
            deadzone_px=args.cursor_deadzone_px,
        )
        if args.control_cursor
        else None
    )

    with HandDetector() as detector:
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    raise SystemExit("Camera frame read failed")

                if not args.no_mirror:
                    frame = cv2.flip(frame, 1)

                detection, results = detector.detect(frame)
                detector.draw(frame, results)
                recognized_gesture = detection.gesture if detection else "None"

                output = update_tracking(
                    tracker=tracker,
                    detection=detection,
                    pinch_threshold=args.pinch_threshold,
                    min_pinched_fingers=args.min_pinched_fingers,
                )
                if cursor is not None:
                    cursor.update(output["point"])
                clicked = False

                draw_tracking_overlay(
                    frame,
                    output,
                    cursor_enabled=cursor is not None,
                    hand_shape=recognized_gesture,
                    clicked=clicked,
                )
                cv2.imshow(WINDOW_NAME, frame)

                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    break
        finally:
            cap.release()
            cv2.destroyAllWindows()


def update_tracking(
    *,
    tracker: PinchLocationTracker,
    detection: object | None,
    pinch_threshold: float,
    min_pinched_fingers: int,
) -> TrackingOutput:
    if detection is None:
        return tracker.update(
            hand_visible=False,
            pinch_active=False,
            point=None,
            confidence=0.0,
        )

    pinch = detect_pinch(
        detection.landmarks,
        threshold=pinch_threshold,
        min_pinched_fingers=min_pinched_fingers,
    )
    return tracker.update(
        hand_visible=True,
        pinch_active=pinch.active,
        point=palm_center(detection.landmarks),
        confidence=pinch.confidence,
    )


def draw_tracking_overlay(
    frame: object,
    output: TrackingOutput,
    *,
    cursor_enabled: bool = False,
    hand_shape: str = "unknown",
    clicked: bool = False,
) -> None:
    height, width = frame.shape[:2]
    point = output["point"]

    if output["tracking"] and point is not None:
        pixel = normalized_to_pixel(NormalizedPoint(point["x"], point["y"]), width, height)
        cv2.circle(frame, pixel, 10, (0, 255, 255), -1)
        cv2.circle(frame, pixel, 18, (0, 255, 255), 2)

    lines = [
        f"gesture: {output['gesture']}",
        f"tracking: {str(output['tracking']).lower()}",
        f"cursor: {str(cursor_enabled).lower()}",
        f"hand: {hand_shape}",
        f"click: {str(clicked).lower()}",
        f"confidence: {output['confidence']:.2f}",
    ]

    if point is not None:
        lines.append(f"point: x={point['x']:.3f}, y={point['y']:.3f}")
    else:
        lines.append("point: none")

    draw_text_panel(frame, lines)


def normalized_to_pixel(point: NormalizedPoint, width: int, height: int) -> tuple[int, int]:
    x = int(max(0.0, min(1.0, point.x)) * (width - 1))
    y = int(max(0.0, min(1.0, point.y)) * (height - 1))
    return x, y


def draw_text_panel(frame: object, lines: list[str]) -> None:
    x = 12
    y = 28
    line_height = 28
    panel_width = 330
    panel_height = 18 + line_height * len(lines)

    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (panel_width, panel_height), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)

    for index, line in enumerate(lines):
        cv2.putText(
            frame,
            line,
            (x, y + line_height * index),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )


if __name__ == "__main__":
    main()
