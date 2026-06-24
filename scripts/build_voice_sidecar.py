from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "voice_sidecar.swift"
DIST_DIR = ROOT / "dist-voice"
TARGET = DIST_DIR / "touchless-voice"


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Missing voice sidecar source: {SOURCE}")

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    if TARGET.exists():
        TARGET.unlink()

    swiftc = shutil.which("swiftc")
    if swiftc is None:
        raise SystemExit("Missing swiftc. Install Xcode command line tools.")

    subprocess.run(
        [
            swiftc,
            "-O",
            str(SOURCE),
            "-o",
            str(TARGET),
            "-framework",
            "Speech",
            "-framework",
            "AVFoundation",
        ],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    main()
