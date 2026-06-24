from __future__ import annotations

from pathlib import Path
from urllib.request import urlretrieve


HAND_LANDMARKER_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
GESTURE_RECOGNIZER_URL = (
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/"
    "gesture_recognizer/float16/1/gesture_recognizer.task"
)
MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
MODELS = {
    "hand landmarker": (HAND_LANDMARKER_URL, MODEL_DIR / "hand_landmarker.task"),
    "gesture recognizer": (
        GESTURE_RECOGNIZER_URL,
        MODEL_DIR / "gesture_recognizer.task",
    ),
}


def main() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    for name, (url, path) in MODELS.items():
        if path.exists() and path.stat().st_size > 0:
            print(f"Model already exists: {path}")
            continue

        print(f"Downloading MediaPipe {name} model to {path}")
        urlretrieve(url, path)

    print("Done")


if __name__ == "__main__":
    main()
