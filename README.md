# Touchless Gesture Tracking Prototype

Python prototype plus Tauri macOS shell for webcam-based cursor control. It uses
MediaPipe to detect hand landmarks and gestures. In the macOS app, `Thumb_Up`
toggles cursor tracking on/off; while tracking is on, the tracked point is a
blended index-finger anchor. `Open_Palm` is command mode for scrolling and
switching macOS Spaces.

In the macOS app, hands are only used for cursor movement. Voice commands handle
clicking and typing: say `click` to click, `type start` to begin dictation,
`type clear` to discard captured dictation, and `type end` to paste it.
`start typing`, `start type`, `end typing`, and `end type` are accepted too.

## Setup

Python 3.10-3.12 is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/download_model.py
```

## Run

```bash
python main.py
```

Useful options:

```bash
python main.py --camera 0
python main.py --pinch-threshold 0.55
python main.py --min-pinched-fingers 3
python main.py --smoothing-alpha 0.55
python main.py --control-cursor
python main.py --control-cursor --cursor-gain 0.65 --max-cursor-step 70
python main.py --control-cursor --cursor-smoothing-alpha 0.20 --cursor-deadzone-px 2.0
python main.py --no-mirror
```

Press `q` or `Esc` to quit the preview window.

If the model is missing, run:

```bash
python scripts/download_model.py
```

## Prototype Behavior

- The original Python prototype still supports pinch tracking experiments.
- The macOS app uses MediaPipe `Thumb_Up` as a debounced cursor toggle command.
- Tracking starts after `Thumb_Up` is stable for several consecutive frames.
- Tracking stops when `Thumb_Up` is shown again, or the hand is missing for a
  short timeout.
- While tracking is enabled, a blended index-finger anchor is normalized camera
  space: `x` and `y` are in `0.0..1.0`.
- The macOS app applies One Euro filtering, a small dead zone, short dropout
  tolerance, and a response curve to make slow movement precise while keeping
  faster movement responsive.
- While `Open_Palm` is visible, vertical palm movement emits scroll commands and
  horizontal palm swipes emit macOS Space switching commands.
- Cursor control is opt-in with `--control-cursor`. Index-finger anchor motion
  moves the macOS cursor by relative deltas.

## Cursor Control

Cursor movement is relative, like a trackpad:

- show thumbs-up for a moment to toggle cursor control on,
- move the index finger to move the cursor,
- show thumbs-up again to toggle cursor control off,
- show open palm and move up/down to scroll,
- show open palm and swipe left/right to switch macOS Spaces.

The Tauri macOS app also starts a native Swift voice sidecar:

- say `click` to click at the current cursor position,
- say `type start` to begin capturing dictated text,
- say `type clear` while dictating to discard the current captured text,
- say `type end` to paste the captured text into the focused app.
- `start typing` / `start type` can start dictation, and `end typing` /
  `end type` can finish it.
- command parsing is tolerant of click phrases like `click it` and `tap that`.

macOS may require Accessibility permission for the app that launches Python.
If you run from Terminal, enable Terminal in:

`System Settings -> Privacy & Security -> Accessibility`

## Mac App

The Tauri shell starts and stops a camera sidecar from a desktop UI. The app
requests Camera permission from `Touchless.app` before launching detection, and
requests Accessibility permission from the app process before cursor control.

```bash
npm install
npm run build
npm run tauri -- build
```

Built app:

```text
src-tauri/target/release/bundle/macos/Touchless.app
src-tauri/target/release/bundle/dmg/Touchless_0.1.0_aarch64.dmg
```

For a nontechnical build, create the bundled sidecar first:

```bash
python scripts/build_sidecar.py
dist-sidecar/touchless-sidecar/touchless-sidecar --self-test-detector
python scripts/build_voice_sidecar.py
dist-voice/touchless-voice --self-test
npm run tauri -- build --bundles app
python scripts/install_app.py
```

When `dist-sidecar/touchless-sidecar/touchless-sidecar` exists, `Touchless.app`
uses that bundled executable. The repo `.venv` Python path is only a development
fallback.

Voice recognition uses Apple Speech and AVFoundation. The app needs Microphone,
Speech Recognition, and Accessibility permissions for voice click/type.

## Tests

```bash
pytest
```
