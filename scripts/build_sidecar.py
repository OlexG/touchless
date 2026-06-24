from __future__ import annotations

import subprocess
import sys
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "dist-sidecar"
BUILD_DIR = ROOT / "build" / "pyinstaller"
TARGET_DIR = DIST_DIR / "touchless-sidecar"
MODEL_FILES = (
    ROOT / "models" / "gesture_recognizer.task",
    ROOT / "models" / "hand_landmarker.task",
)


def main() -> None:
    missing_models = [path for path in MODEL_FILES if not path.exists()]
    if missing_models:
        missing = ", ".join(str(path.relative_to(ROOT)) for path in missing_models)
        raise SystemExit(f"Missing model file(s): {missing}")

    if TARGET_DIR.exists():
        if TARGET_DIR.is_dir():
            shutil.rmtree(TARGET_DIR)
        else:
            TARGET_DIR.unlink()

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "touchless-sidecar",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(BUILD_DIR),
        "--hidden-import",
        "mediapipe.tasks.c",
        "--collect-binaries",
        "mediapipe",
    ]

    for model_file in MODEL_FILES:
        command.extend(
            [
                "--add-data",
                f"{model_file}{':'}models",
            ]
        )

    command.append(str(ROOT / "sidecar.py"))
    subprocess.run(command, cwd=ROOT, check=True)
    materialize_symlinks(TARGET_DIR)


def materialize_symlinks(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue

        target = path.resolve(strict=True)
        path.unlink()
        if target.is_dir():
            shutil.copytree(target, path)
        else:
            shutil.copy2(target, path)


if __name__ == "__main__":
    main()
