from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import cv2

from gestures import (
    NormalizedPoint,
    cursor_point,
    palm_center,
)
from hand_detector import (
    HandDetection,
    HandDetector,
    describe_detection,
)
from tracker import GestureModeTracker

LOG_PATH = Path("/tmp/touchless-sidecar.log")
STATUS_LOG_INTERVAL = 90
_status_log_count = 0
_last_status_signature: tuple[object, ...] | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Touchless Python sidecar.")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--camera-open-timeout", type=float, default=8.0)
    parser.add_argument("--enter-frames", type=int, default=2)
    parser.add_argument("--exit-frames", type=int, default=4)
    parser.add_argument("--lost-frames", type=int, default=14)
    parser.add_argument("--smoothing-alpha", type=float, default=0.55)
    parser.add_argument("--control-cursor", action="store_true")
    parser.add_argument("--cursor-gain", type=float, default=0.65)
    parser.add_argument("--max-cursor-step", type=int, default=70)
    parser.add_argument("--cursor-smoothing-alpha", type=float, default=0.20)
    parser.add_argument("--cursor-deadzone-px", type=float, default=2.0)
    parser.add_argument("--preview-fps", type=float, default=4.0)
    parser.add_argument("--debug-window", action="store_true")
    parser.add_argument("--no-mirror", action="store_true")
    parser.add_argument(
        "--self-test-detector",
        action="store_true",
        help="Initialize MediaPipe and exit without opening the camera.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    log("sidecar starting")
    log(f"args: {vars(args)}")

    if args.self_test_detector:
        log("initializing gesture recognizer self-test")
        with HandDetector(max_num_hands=2):
            pass
        log("gesture recognizer self-test passed")
        return

    emit_progress("opening_camera", f"Opening camera index {args.camera}")
    cap = open_camera(args.camera, args.camera_open_timeout)

    if not cap.isOpened():
        emit(
            {
                "type": "error",
                "message": (
                    f"Could not open camera index {args.camera}. "
                    "Grant Camera permission to Touchless in System Settings > "
                    "Privacy & Security > Camera, then restart Touchless."
                ),
            }
        )
        raise SystemExit(1)

    emit_progress("camera_opened", "Camera opened")
    emit({"type": "status", "running": True, "tracking": False})

    log("initializing tracker")
    tracker = GestureModeTracker(
        toggle_frames=args.enter_frames,
        release_frames=args.exit_frames,
        lost_frames=args.lost_frames,
    )
    log("initializing cursor controller")
    cursor_enabled = args.control_cursor

    log("initializing gesture recognizer")
    emit_progress("initializing_detector", "Initializing gesture detector")
    last_preview_at = 0.0
    preview_interval = 1.0 / args.preview_fps if args.preview_fps > 0 else None

    with HandDetector(max_num_hands=1) as detector:
        log("gesture recognizer initialized")
        emit_progress("detector_ready", "Gesture detector ready")
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    emit({"type": "error", "message": "Camera frame read failed"})
                    break

                if not args.no_mirror:
                    frame = cv2.flip(frame, 1)

                detections, results = detector.detect_all(frame)
                cursor_detection = select_cursor_hand(detections)
                cursor_gesture = cursor_detection.gesture if cursor_detection else "None"

                if cursor_detection is None:
                    hand_label = "none"
                    output = tracker.update(
                        hand_visible=False,
                        thumb_up=False,
                        open_palm=False,
                        index_point=None,
                        palm_point=None,
                        confidence=0.0,
                    )
                else:
                    hand_label = describe_detection(cursor_detection)
                    open_palm = is_open_palm(cursor_detection)
                    thumb_up = is_thumb_up(cursor_detection)
                    output = tracker.update(
                        hand_visible=True,
                        thumb_up=thumb_up,
                        open_palm=open_palm,
                        index_point=cursor_point(cursor_detection.landmarks),
                        palm_point=palm_center(cursor_detection.landmarks),
                        confidence=cursor_detection.gesture_score,
                    )

                clicked = False
                displayed_gesture = output["gesture"]
                if displayed_gesture == "none" and cursor_gesture != "None":
                    displayed_gesture = cursor_gesture

                status_payload = {
                    "type": "status",
                    "running": True,
                    "tracking": output["tracking"],
                    "gesture": displayed_gesture,
                    "pinch": output["gesture"],
                    "hand_shape": hand_label,
                    "classified_gesture": cursor_gesture,
                    "clicked": clicked,
                    "point": output["point"],
                    "confidence": output["confidence"],
                    "gesture_command": output.get("command"),
                    "cursor": {"enabled": cursor_enabled, "motion": None},
                }

                if preview_interval is not None:
                    now = time.monotonic()
                    if now - last_preview_at >= preview_interval:
                        preview_frame = frame.copy()
                        detector.draw(preview_frame, results)
                        draw_debug_overlay(
                            preview_frame,
                            output,
                            hand_label,
                            clicked,
                        )
                        status_payload["preview"] = encode_preview_frame(preview_frame)
                        last_preview_at = now

                emit(status_payload)

                if args.debug_window:
                    detector.draw(frame, results)
                    draw_debug_overlay(frame, output, hand_label, clicked)
                    cv2.imshow("Touchless Debug", frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (27, ord("q")):
                        break
        finally:
            cap.release()
            cv2.destroyAllWindows()
            log("sidecar exiting normally")
            emit({"type": "exit", "code": 0})


def emit(payload: dict[str, object]) -> None:
    global _last_status_signature, _status_log_count

    line = json.dumps(payload)
    if payload.get("type") == "status":
        _status_log_count += 1
        status_signature = (
            payload.get("tracking"),
            payload.get("gesture"),
            payload.get("pinch"),
            bool(payload.get("point")),
            payload.get("gesture_command"),
            payload.get("clicked"),
        )
        has_frame_fields = "gesture" in payload or "point" in payload
        if (
            status_signature != _last_status_signature
            or not has_frame_fields
            or _status_log_count % STATUS_LOG_INTERVAL == 0
        ):
            log(f"emit: {json.dumps(payload_for_log(payload))}")
        _last_status_signature = status_signature
    else:
        log(f"emit: {json.dumps(payload_for_log(payload))}")
    try:
        print(line, flush=True)
    except BrokenPipeError:
        log("stdout pipe closed while emitting; exiting sidecar")
        raise SystemExit(0)


def payload_for_log(payload: dict[str, object]) -> dict[str, object]:
    sanitized = dict(payload)
    if "preview" in sanitized:
        sanitized["preview"] = "[jpeg omitted]"
    return sanitized


def emit_progress(stage: str, message: str) -> None:
    emit({"type": "progress", "stage": stage, "message": message})


def select_cursor_hand(
    detections: list[HandDetection],
) -> HandDetection | None:
    return max(
        detections,
        key=lambda detection: (
            1 if is_control_gesture(detection) else 0,
            detection.gesture_score,
            detection.score,
        ),
        default=None,
    )


def is_control_gesture(detection: HandDetection) -> bool:
    return is_thumb_up(detection) or is_open_palm(detection)


def is_open_palm(detection: HandDetection, min_score: float = 0.50) -> bool:
    return detection.gesture == "Open_Palm" and detection.gesture_score >= min_score


def is_thumb_up(detection: HandDetection, min_score: float = 0.50) -> bool:
    return detection.gesture == "Thumb_Up" and detection.gesture_score >= min_score


def open_camera(camera_index: int, timeout_seconds: float) -> cv2.VideoCapture:
    import time

    deadline = time.monotonic() + max(0.0, timeout_seconds)
    attempt = 1

    while True:
        log(f"opening camera index {camera_index}, attempt {attempt}")
        cap = cv2.VideoCapture(camera_index)
        if cap.isOpened():
            log("camera opened")
            return cap

        cap.release()
        if time.monotonic() >= deadline:
            log(f"camera index {camera_index} did not open before timeout")
            return cap

        time.sleep(0.4)
        attempt += 1


def log(message: str) -> None:
    timestamp = datetime.now().isoformat(timespec="milliseconds")
    try:
        with LOG_PATH.open("a", encoding="utf-8") as file:
            file.write(f"{timestamp} {message}\n")
    except OSError:
        pass


def draw_debug_overlay(
    frame: object,
    output: dict[str, object],
    hand_shape: str,
    clicked: bool,
) -> None:
    height, width = frame.shape[:2]
    point = output.get("point")

    if output.get("tracking") and isinstance(point, dict):
        pixel = normalized_to_pixel(
            NormalizedPoint(float(point["x"]), float(point["y"])),
            width,
            height,
        )
        cv2.circle(frame, pixel, 10, (0, 255, 255), -1)
        cv2.circle(frame, pixel, 18, (0, 255, 255), 2)

    lines = [
        f"gesture: {output.get('gesture', 'none')}",
        f"tracking: {str(bool(output.get('tracking'))).lower()}",
        f"command: {format_command(output.get('command'))}",
        f"hand: {hand_shape}",
        f"click: {str(clicked).lower()}",
    ]

    for index, line in enumerate(lines):
        cv2.putText(
            frame,
            line,
            (12, 28 + 28 * index),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )


def normalized_to_pixel(point: NormalizedPoint, width: int, height: int) -> tuple[int, int]:
    x = int(max(0.0, min(1.0, point.x)) * (width - 1))
    y = int(max(0.0, min(1.0, point.y)) * (height - 1))
    return x, y


def cursor_motion_to_dict(motion: object | None) -> dict[str, int] | None:
    if motion is None:
        return None
    return {"dx": int(getattr(motion, "dx")), "dy": int(getattr(motion, "dy"))}


def format_command(command: object) -> str:
    if not isinstance(command, dict):
        return "none"
    kind = command.get("kind")
    if kind == "scroll":
        return f"scroll {command.get('amount', 0)}"
    if kind == "space":
        return f"space {command.get('direction', 'unknown')}"
    return str(kind or "none")


def encode_preview_frame(frame: object) -> dict[str, object] | None:
    height, width = frame.shape[:2]
    max_width = 480
    if width > max_width:
        scale = max_width / width
        frame = cv2.resize(
            frame,
            (max_width, int(height * scale)),
            interpolation=cv2.INTER_AREA,
        )
        height, width = frame.shape[:2]

    ok, encoded = cv2.imencode(
        ".jpg",
        frame,
        [int(cv2.IMWRITE_JPEG_QUALITY), 60],
    )
    if not ok:
        return None

    image = base64.b64encode(encoded).decode("ascii")
    return {
        "image": f"data:image/jpeg;base64,{image}",
        "width": width,
        "height": height,
    }


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        emit({"type": "exit", "code": 130})
        sys.exit(130)
    except Exception as error:
        log(traceback.format_exc())
        emit({"type": "error", "message": f"{type(error).__name__}: {error}"})
        emit({"type": "exit", "code": 1})
        raise
