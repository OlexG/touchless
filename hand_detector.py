from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Sequence

import cv2
import mediapipe as mp
from mediapipe.tasks.python.core import base_options
from mediapipe.tasks.python.vision import drawing_utils
from mediapipe.tasks.python.vision import gesture_recognizer
from mediapipe.tasks.python.vision import hand_landmarker
from mediapipe.tasks.python.vision.core import vision_task_running_mode


@dataclass(frozen=True)
class HandDetection:
    index: int
    landmarks: Sequence[object]
    handedness: str
    score: float
    gesture: str
    gesture_score: float


class HandDetector:
    def __init__(
        self,
        *,
        max_num_hands: int = 1,
        model_path: str | Path = "models/gesture_recognizer.task",
        min_detection_confidence: float = 0.6,
        min_tracking_confidence: float = 0.6,
    ) -> None:
        self._timestamp_ms = 0
        self._model_path = resolve_model_path(model_path)

        if not self._model_path.exists():
            raise FileNotFoundError(
                f"Missing MediaPipe model at {self._model_path}. "
                "Run `python scripts/download_model.py` first."
            )

        options = gesture_recognizer.GestureRecognizerOptions(
            base_options=base_options.BaseOptions(
                model_asset_path=str(self._model_path)
            ),
            running_mode=vision_task_running_mode.VisionTaskRunningMode.VIDEO,
            num_hands=max_num_hands,
            min_hand_detection_confidence=min_detection_confidence,
            min_hand_presence_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._recognizer = gesture_recognizer.GestureRecognizer.create_from_options(
            options
        )

    def detect(self, frame_bgr: object) -> tuple[HandDetection | None, object]:
        detections, results = self.detect_all(frame_bgr)
        return (detections[0] if detections else None, results)

    def detect_all(self, frame_bgr: object) -> tuple[list[HandDetection], object]:
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        self._timestamp_ms += 1
        results = self._recognizer.recognize_for_video(mp_image, self._timestamp_ms)

        if not results.hand_landmarks:
            return [], results

        detections = []
        for index, landmarks in enumerate(results.hand_landmarks):
            handedness = "unknown"
            score = 0.0

            if results.handedness and len(results.handedness) > index and results.handedness[index]:
                classification = results.handedness[index][0]
                handedness = classification.category_name or "unknown"
                score = float(classification.score or 0.0)

            gesture = "None"
            gesture_score = 0.0
            if results.gestures and len(results.gestures) > index and results.gestures[index]:
                classification = results.gestures[index][0]
                gesture = classification.category_name or "None"
                gesture_score = float(classification.score or 0.0)

            detections.append(
                HandDetection(
                    index=index,
                    landmarks=landmarks,
                    handedness=handedness,
                    score=score,
                    gesture=gesture,
                    gesture_score=gesture_score,
                )
            )

        return detections, results

    def draw(self, frame_bgr: object, results: object) -> None:
        if not getattr(results, "hand_landmarks", None):
            return

        for landmarks in results.hand_landmarks:
            drawing_utils.draw_landmarks(
                frame_bgr,
                landmarks,
                hand_landmarker.HandLandmarksConnections.HAND_CONNECTIONS,
            )

    def close(self) -> None:
        self._recognizer.close()

    def __enter__(self) -> "HandDetector":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


def first_detection_with_gesture(
    detections: Sequence[HandDetection],
    gesture: str,
    *,
    exclude_index: int | None = None,
) -> HandDetection | None:
    candidates = (
        detection
        for detection in detections
        if detection.index != exclude_index and detection.gesture == gesture
    )
    return max(candidates, key=lambda detection: detection.gesture_score, default=None)


def describe_detection(detection: HandDetection | None) -> str:
    if detection is None:
        return "none"
    if detection.gesture == "None":
        return detection.handedness
    return f"{detection.handedness} {detection.gesture}"


def make_detection(
    *,
    index: int,
    landmarks: Sequence[object],
    handedness: str = "unknown",
    score: float = 1.0,
    gesture: str = "None",
    gesture_score: float = 1.0,
) -> HandDetection:
    return HandDetection(
        index=index,
        landmarks=landmarks,
        handedness=handedness,
        score=score,
        gesture=gesture,
        gesture_score=gesture_score,
    )


def resolve_model_path(model_path: str | Path) -> Path:
    path = Path(model_path)
    if path.exists():
        return path

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        bundled_path = Path(bundle_root) / path
        if bundled_path.exists():
            return bundled_path

    return path
