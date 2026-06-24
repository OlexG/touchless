from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILT_APP = ROOT / "src-tauri" / "target" / "release" / "bundle" / "macos" / "Touchless.app"
CANONICAL_APP = Path("/Applications/Touchless.app")
SIDECAR_DIR = ROOT / "dist-sidecar" / "touchless-sidecar"
VOICE_SIDECAR = ROOT / "dist-voice" / "touchless-voice"
DEFAULT_BUNDLE_ID = "com.touchless.desktop"


def main() -> None:
    if not BUILT_APP.exists():
        raise SystemExit(f"Missing built app: {BUILT_APP}")
    if not SIDECAR_DIR.exists():
        raise SystemExit(f"Missing sidecar directory: {SIDECAR_DIR}")
    if not VOICE_SIDECAR.exists():
        raise SystemExit(f"Missing voice sidecar: {VOICE_SIDECAR}")

    quit_processes()

    if CANONICAL_APP.exists():
        shutil.rmtree(CANONICAL_APP)
    shutil.copytree(BUILT_APP, CANONICAL_APP, symlinks=False)

    resources = CANONICAL_APP / "Contents" / "Resources" / "_up_"
    target_sidecar_root = resources / "dist-sidecar"
    target_sidecar_dir = target_sidecar_root / "touchless-sidecar"
    target_sidecar_root.mkdir(parents=True, exist_ok=True)
    if target_sidecar_dir.exists():
        shutil.rmtree(target_sidecar_dir)
    shutil.copytree(SIDECAR_DIR, target_sidecar_dir, symlinks=False)

    target_voice_root = resources / "dist-voice"
    target_voice_root.mkdir(parents=True, exist_ok=True)
    target_voice = target_voice_root / "touchless-voice"
    if target_voice.exists():
        target_voice.unlink()
    shutil.copy2(VOICE_SIDECAR, target_voice)
    target_voice.chmod(0o755)

    identity = select_codesign_identity()
    clear_macos_xattrs(target_voice)
    sign_executable(target_voice, identity)
    sign_app(CANONICAL_APP, identity)

    if BUILT_APP.exists():
        shutil.rmtree(BUILT_APP)

    subprocess.run(
        [
            "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
            "LaunchServices.framework/Support/lsregister",
            "-f",
            str(CANONICAL_APP),
        ],
        check=False,
    )


def sign_app(app_path: Path, identity: str | None = None) -> None:
    identity = identity or select_codesign_identity()
    print(f"Signing {app_path} with identity: {identity}")
    subprocess.run(
        [
            "codesign",
            "--force",
            "--deep",
            "--sign",
            identity,
            "--timestamp=none",
            "--identifier",
            DEFAULT_BUNDLE_ID,
            str(app_path),
        ],
        check=True,
    )
    verify_app_signature(app_path)


def sign_executable(executable_path: Path, identity: str) -> None:
    subprocess.run(
        [
            "codesign",
            "--force",
            "--sign",
            identity,
            "--timestamp=none",
            str(executable_path),
        ],
        check=True,
    )


def clear_macos_xattrs(path: Path) -> None:
    for attr in ("com.apple.quarantine", "com.apple.provenance"):
        subprocess.run(["xattr", "-d", attr, str(path)], check=False)


def select_codesign_identity() -> str:
    configured_identity = os.environ.get("TOUCHLESS_CODESIGN_IDENTITY")
    if configured_identity:
        return configured_identity

    identities = available_codesign_identities()
    preferred_prefixes = (
        "Developer ID Application:",
        "Apple Development:",
    )

    for prefix in preferred_prefixes:
        for identity in identities:
            if identity.startswith(prefix):
                return identity

    if identities:
        return identities[0]

    print(
        "WARNING: No persistent code signing identity found. Falling back to ad-hoc "
        "signing, which can make macOS ask for Accessibility permission again after "
        "each rebuild."
    )
    return "-"


def available_codesign_identities() -> list[str]:
    result = subprocess.run(
        ["security", "find-identity", "-v", "-p", "codesigning"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    identities: list[str] = []
    for line in result.stdout.splitlines():
        if '"' not in line:
            continue
        identity = line.split('"', 2)[1]
        if identity and identity not in identities:
            identities.append(identity)
    return identities


def verify_app_signature(app_path: Path) -> None:
    subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(app_path)],
        check=True,
    )


def quit_processes() -> None:
    commands = [
        ["pkill", "-f", "/Touchless.app/Contents/MacOS/touchless"],
        ["pkill", "-f", "touchless-sidecar"],
        ["pkill", "-f", "/sidecar.py"],
    ]
    for command in commands:
        subprocess.run(command, check=False)
    subprocess.run(
        [
            "osascript",
            "-e",
            'tell application id "com.touchless.desktop" to quit',
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


if __name__ == "__main__":
    main()
