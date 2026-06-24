# Touchless Development Notes

## Product Shape

- Treat `/Applications/Touchless.app` as the canonical app. Do not test or grant permissions to random generated app copies under `src-tauri/target/...` when debugging user-facing behavior.
- macOS Camera and Accessibility permissions attach to the app identity/path/signature that actually requests the protected resource. Multiple `Touchless.app` copies with the same bundle id can confuse System Settings. Ad-hoc signing is not enough for stable Accessibility trust across rebuilds because the designated requirement can collapse to a build-specific `cdhash`.
- The app should request Camera permission from the Tauri WebView before starting detection. The detector sidecar should not be the first user-facing permission prompt.
- Cursor movement/click injection belongs to the Tauri app process, not the Python sidecar. The Python sidecar should emit gesture intent and preview/status only.
- Hands are only for cursor control. Do not reintroduce hand/fist clicking unless the product direction changes. Voice commands own click/type: `click` clicks; `type start` / `start typing` / `start type` begin dictation capture; `type clear` discards captured dictation while staying in dictation mode; `type end` / `end typing` / `end type` finish and paste the captured text.
- The Mac app cursor mode is MediaPipe `Thumb_Up` toggle plus blended index-finger anchor tracking. Show `Thumb_Up` to start tracking, move the index finger while active, and show `Thumb_Up` again to stop tracking. `Open_Palm` is command mode: vertical palm motion scrolls and horizontal palm swipes switch macOS Spaces. Do not use custom finger-straightness rules or MediaPipe `Pointing_Up` as the primary cursor gate; those proved brittle for sideways/down pointing.
- Cursor feel is owned by the Tauri/Rust input layer, not the Python sidecar. Python should emit raw normalized cursor anchors and command intents; Rust applies One Euro filtering, response curves, dead zones, and missing-point tolerance before posting macOS input.
- Voice recognition is handled by the native Swift helper `voice_sidecar.swift`, built into `dist-voice/touchless-voice`. It uses Apple Speech and AVFoundation, so the app needs Microphone and Speech Recognition usage descriptions and permissions. Keep voice command handling dictation-app style: start aliases enter a dictation state, everything after that is text, clear aliases clear the current buffer, and end aliases terminate/paste. Use the latest start command in a stale cumulative transcript, and reset stale idle transcripts so old words do not poison future click/type commands.

## Relaunch Procedure

Use this when validating the app manually:

```bash
pkill -f '/Touchless.app/Contents/MacOS/touchless' || true
pkill -f 'touchless-sidecar' || true
pkill -f '/sidecar.py' || true
osascript -e 'tell application id "com.touchless.desktop" to quit' >/dev/null 2>&1 || true
rm -f /tmp/touchless-app.log /tmp/touchless-sidecar.log
open -n /Applications/Touchless.app
```

Then inspect:

```bash
pgrep -af 'Touchless.app/Contents/MacOS/touchless|touchless-sidecar|sidecar.py'
tail -n 200 /tmp/touchless-app.log
tail -n 240 /tmp/touchless-sidecar.log
```

## Canonical App Cleanup

If macOS Settings appears to show the wrong app, check for duplicate bundles:

```bash
mdfind 'kMDItemFSName == "Touchless.app"'
find "$HOME/Desktop" "$HOME/Applications" /Applications -name 'Touchless.app' -type d -maxdepth 8 2>/dev/null
```

Keep only `/Applications/Touchless.app` for user testing. If replacing it from a fresh build:

```bash
REPO_ROOT="$(pwd)"
rm -rf /Applications/Touchless.app
ditto "$REPO_ROOT/src-tauri/target/release/bundle/macos/Touchless.app" /Applications/Touchless.app
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f /Applications/Touchless.app
```

Only reset permissions when intentionally testing first-run behavior:

```bash
tccutil reset Camera com.touchless.desktop
tccutil reset Accessibility com.touchless.desktop
```

## Permission Identity Fix

The working fix for the "System Settings already shows Touchless checked, but cursor control still does not work" state is:

1. Install and run only `/Applications/Touchless.app`.
2. Remove or ignore generated bundles under `src-tauri/target/...`; do not grant permissions to those copies.
3. Sign `/Applications/Touchless.app` with a persistent Apple code signing identity and the stable identifier `com.touchless.desktop`. Do not rely on ad-hoc signing (`codesign --sign -`) for normal development installs.
4. Reopen System Settings after reinstalling/signing. The Accessibility list can show a stale checked `Touchless` row from an older app identity while the currently running bundle is still not trusted.
5. Relaunch `/Applications/Touchless.app` from `/Applications`, then press Start and let the app request permissions.

Verify the installed app identity before debugging permissions:

```bash
codesign -dv /Applications/Touchless.app 2>&1 | sed -n '1,20p'
```

The output must include:

```text
Identifier=com.touchless.desktop
Signature size=
```

Also check the designated requirement:

```bash
codesign -dr - /Applications/Touchless.app 2>&1
```

For stable rebuilds, this should reference the Developer ID / Apple Development certificate requirement, not only a `cdhash`. If it only prints `cdhash H"..."`, the app is ad-hoc signed and macOS may ask for Accessibility permission again after every rebuild.

If the identifier is an ad-hoc generated value such as `touchless-...`, or the designated requirement is only `cdhash`, reinstall with:

```bash
.venv/bin/python scripts/install_app.py
```

That script copies the built app into `/Applications/Touchless.app`, copies the PyInstaller onedir sidecar into app resources, registers the app with LaunchServices, removes the generated repo bundle, and signs the installed app with `com.touchless.desktop`. It prefers a persistent signing identity in this order: `TOUCHLESS_CODESIGN_IDENTITY`, `Developer ID Application`, `Apple Development`, then any available code signing identity. It falls back to ad-hoc signing only if no persistent identity exists.

Only after confirming both the stable identifier and persistent signing identity should you reset Accessibility or Camera permissions. Resetting TCC before fixing the app identity just creates another confusing Settings entry.

Important: switching from the old ad-hoc signature to a persistent certificate may require approving Accessibility one last time. After that, future rebuilds signed with the same certificate should keep the same trust identity.

If System Settings shows `Touchless` as checked but the app still opens Settings or logs `request_accessibility_permission invoked prompt=true`, the checked row is stale. This happened after moving from ad-hoc signing to the Developer ID signature. Fix it by resetting only the stale Accessibility entry, closing System Settings, and relaunching the Developer ID-signed app:

```bash
tccutil reset Accessibility com.touchless.desktop
pkill -f '/Touchless.app/Contents/MacOS/touchless' || true
pkill -f 'touchless-sidecar' || true
pkill -f '/sidecar.py' || true
osascript -e 'tell application id "com.apple.SystemSettings" to quit' >/dev/null 2>&1 || true
rm -f /tmp/touchless-app.log /tmp/touchless-sidecar.log
open -n /Applications/Touchless.app
```

Then approve `Touchless` once in System Settings. Do not keep toggling an already-checked row; remove/reset the stale entry so macOS recreates it for the current Developer ID-signed app.

## Build Procedure

Run logic checks:

```bash
.venv/bin/python -m pytest
npm run build
(cd src-tauri && cargo check --offline)
```

Build the bundled Python sidecar before building the app:

```bash
.venv/bin/python scripts/build_sidecar.py
dist-sidecar/touchless-sidecar/touchless-sidecar --self-test-detector
.venv/bin/python scripts/build_voice_sidecar.py
dist-voice/touchless-voice --self-test
npm run tauri -- build --bundles app
.venv/bin/python scripts/install_app.py
```

The Tauri app prefers `/Applications/Touchless.app/Contents/Resources/_up_/dist-sidecar/touchless-sidecar/touchless-sidecar`. The repo `.venv/bin/python` path is only a development fallback.

The Tauri app also expects `/Applications/Touchless.app/Contents/Resources/_up_/dist-voice/touchless-voice`. `scripts/install_app.py` copies it into the installed app before signing. If Start fails with `could not resolve voice sidecar runtime`, run `scripts/build_voice_sidecar.py` and reinstall.

The sidecar build uses PyInstaller `--onedir`, not `--onefile`, to avoid slow first-run unpacking. If `dist-sidecar/touchless-sidecar` is a file instead of a directory, rebuild the sidecar.

Do not include `dist-sidecar` directly in `tauri.conf.json` resources. Tauri's resource scanner has trouble with the native library tree in PyInstaller onedir output. Build the app first, then use `scripts/install_app.py` to copy `dist-sidecar/touchless-sidecar` into `/Applications/Touchless.app/Contents/Resources/_up_/dist-sidecar/touchless-sidecar`.

`scripts/install_app.py` signs `/Applications/Touchless.app` with the stable identifier `com.touchless.desktop` and a persistent code signing identity when one is available. This matters for macOS Camera and Accessibility permissions; without a stable signing identity, System Settings can show `Touchless` as checked while the newly rebuilt app is not actually trusted.

## Startup Progress

Startup is observable through `sidecar-event` progress events:

- `sidecar_spawned`: Rust successfully spawned the sidecar process.
- `opening_camera`: Python started camera acquisition.
- `camera_opened`: OpenCV acquired the camera.
- `initializing_detector`: MediaPipe GestureRecognizer initialization started.
- `detector_ready`: MediaPipe is ready and frame status events should follow.

## Known Failure Modes

- Camera turns on briefly then off: check `/tmp/touchless-sidecar.log`. This often means preflight Camera permission succeeded, but the sidecar crashed after opening the camera.
- Voice commands do nothing: check `/tmp/touchless-voice.log` and `/tmp/touchless-app.log`. Confirm Microphone, Speech Recognition, and Accessibility permissions are granted to the current `/Applications/Touchless.app`.
- `type ...` uses pasteboard plus Cmd+V from the Tauri app process. If it does not type but logs an executed voice command, verify the target app has keyboard focus and Accessibility is trusted for Touchless.
- `ModuleNotFoundError: No module named 'mediapipe.tasks.c'`: the PyInstaller sidecar is missing MediaPipe's native task package. Rebuild with `scripts/build_sidecar.py` and verify `--self-test-detector`.
- App stuck in a startup stage: inspect `/tmp/touchless-app.log` for the sidecar command path and `/tmp/touchless-sidecar.log` for detector startup.
- Cursor diagnostics show motion but cursor does not move: Accessibility is not trusted for `/Applications/Touchless.app`.

## Constraints

- Do not use localhost testing for this project.
- Prefer direct app/process/log checks over blind UI automation. Tauri WebView controls often do not expose stable AppleScript button labels.
- Ask before making uncertain product-level changes, but it is fine to inspect logs, run tests, rebuild, and relaunch to diagnose runtime failures.
