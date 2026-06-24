import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import "./style.css";

type SidecarEvent = {
  type:
    | "status"
    | "progress"
    | "error"
    | "exit"
    | "log"
    | "voice_status"
    | "voice_command"
    | "voice_transcript"
    | "voice_error"
    | "voice_exit";
  running?: boolean;
  stage?: string;
  listening?: boolean;
  tracking?: boolean;
  gesture?: string;
  pinch?: string;
  classified_gesture?: string;
  hand_shape?: string;
  clicked?: boolean;
  point?: { x: number; y: number } | null;
  confidence?: number;
  cursor?: {
    enabled: boolean;
    accessibility_trusted: boolean | null;
    motion: { dx: number; dy: number } | null;
    command?: {
      kind: string;
      amount?: number;
      direction?: string;
      executed?: boolean;
      error?: string;
    } | null;
  };
  gesture_command?: {
    kind: string;
    amount?: number;
    direction?: string;
  } | null;
  preview?: {
    image: string;
    width: number;
    height: number;
  } | null;
  message?: string;
  command?: "click" | "type";
  text?: string;
  transcript?: string;
  final?: boolean;
  executed?: boolean;
  error?: string;
  code?: number | null;
};

type Settings = {
  control_cursor: boolean;
  debug_window: boolean;
  cursor_gain: number;
  cursor_smoothing_alpha: number;
};

type AccessibilityPermission = {
  trusted: boolean;
};

const toggleButton = getElement<HTMLButtonElement>("toggleButton");
const statusText = getElement("statusText");
const gestureValue = getElement("gestureValue");
const trackingValue = getElement("trackingValue");
const handValue = getElement("handValue");
const voiceValue = getElement("voiceValue");
const voiceCommandValue = getElement("voiceCommandValue");
const startupValue = getElement("startupValue");
const cameraValue = getElement("cameraValue");
const modeValue = getElement("modeValue");
const commandValue = getElement("commandValue");
const classifiedGestureValue = getElement("classifiedGestureValue");
const pointValue = getElement("pointValue");
const cursorValue = getElement("cursorValue");
const accessibilityValue = getElement("accessibilityValue");
const cameraPreview = getElement<HTMLImageElement>("cameraPreview");
const previewEmpty = getElement("previewEmpty");
const log = getElement("log");

const cursorEnabled = getElement<HTMLInputElement>("cursorEnabled");
const debugWindow = getElement<HTMLInputElement>("debugWindow");
const cursorGain = getElement<HTMLInputElement>("cursorGain");
const cursorSmoothingAlpha = getElement<HTMLInputElement>("cursorSmoothingAlpha");

bindOutput(cursorGain, getElement("cursorGainValue"));
bindOutput(cursorSmoothingAlpha, getElement("cursorSmoothingAlphaValue"));

let running = false;
let starting = false;
let startupTimer: number | undefined;
let accessibilityWarningShown = false;

toggleButton.addEventListener("click", async () => {
  toggleButton.disabled = true;
  try {
    if (running || starting) {
      await invoke("stop_sidecar");
      setRunning(false);
    } else {
      setStarting();
      await ensureCameraPermission();
      const accessibility = await requestAccessibilityPermission(true);
      if (!accessibility.trusted) {
        setNeedsPermission("Accessibility");
        appendLog("Approve Touchless in System Settings > Privacy & Security > Accessibility, then press Start again.");
        return;
      }
      await invoke("start_sidecar", { settings: collectSettings() });
    }
  } catch (error) {
    setRunning(false);
    appendLog(String(error));
  } finally {
    toggleButton.disabled = false;
  }
});

void refreshAccessibilityStatus();

await listen<SidecarEvent>("sidecar-event", (event) => {
  const payload = event.payload;
  if (payload.type === "progress") {
    const stage = payload.stage ?? "starting";
    const message = payload.message ?? stage;
    startupValue.textContent = stage;
    statusText.textContent = message;
    appendLog(message);
    return;
  }

  if (payload.type === "status") {
    clearStartupTimer();
    setRunning(Boolean(payload.running));
    startupValue.textContent = payload.running ? "running" : "idle";
    gestureValue.textContent = payload.gesture ?? "none";
    trackingValue.textContent = String(Boolean(payload.tracking));
    handValue.textContent = payload.hand_shape ?? "unknown";
    modeValue.textContent = payload.pinch ?? "none";
    classifiedGestureValue.textContent = payload.classified_gesture ?? "unknown";
    pointValue.textContent = formatPoint(payload.point ?? null);
    cursorValue.textContent = formatCursor(payload.cursor);
    commandValue.textContent = formatCommand(payload);
    accessibilityValue.textContent = formatAccessibility(payload.cursor);
    if ("preview" in payload) {
      updatePreview(payload.preview ?? null);
    }
    maybeWarnAboutAccessibility(payload.cursor);
    return;
  }

  if (payload.type === "voice_status") {
    voiceValue.textContent = payload.stage ?? (payload.listening ? "listening" : "starting");
    if (payload.message) {
      appendLog(payload.message);
    }
    return;
  }

  if (payload.type === "voice_transcript") {
    if (payload.transcript) {
      voiceValue.textContent = payload.final ? "heard" : "listening";
      voiceCommandValue.textContent = payload.transcript;
    }
    return;
  }

  if (payload.type === "voice_command") {
    const commandText = formatVoiceCommand(payload);
    voiceValue.textContent = payload.executed ? "executed" : "blocked";
    voiceCommandValue.textContent = commandText;
    appendLog(`Voice: ${commandText}`);
    if (payload.error) {
      appendLog(payload.error);
    }
    return;
  }

  if (payload.type === "voice_error") {
    voiceValue.textContent = "error";
    if (payload.message) {
      appendLog(payload.message);
    }
    return;
  }

  if (payload.type === "voice_exit") {
    voiceValue.textContent = "exited";
    appendLog(`voice sidecar exited: ${payload.code ?? "unknown"}`);
    return;
  }

  if (payload.type === "exit") {
    clearStartupTimer();
    setRunning(false);
    startupValue.textContent = "exited";
    appendLog(`sidecar exited: ${payload.code ?? "unknown"}`);
    return;
  }

  if (payload.message) {
    appendLog(payload.message);
  }
});

function collectSettings(): Settings {
  return {
    control_cursor: cursorEnabled.checked,
    debug_window: debugWindow.checked,
    cursor_gain: Number(cursorGain.value),
    cursor_smoothing_alpha: Number(cursorSmoothingAlpha.value),
  };
}

function setRunning(next: boolean): void {
  starting = false;
  running = next;
  statusText.textContent = next ? "Running" : "Stopped";
  toggleButton.textContent = next ? "Stop" : "Start";
  startupValue.textContent = next ? "running" : "idle";
  if (!next) {
    updatePreview(null);
    cursorValue.textContent = "idle";
    commandValue.textContent = "none";
    pointValue.textContent = "none";
    voiceValue.textContent = "idle";
  }
}

function setStarting(): void {
  starting = true;
  running = false;
  statusText.textContent = "Preparing detector";
  toggleButton.textContent = "Stop";
  startupValue.textContent = "preflight";
  appendLog("Preparing detector startup.");
  clearStartupTimer();
  startupTimer = window.setTimeout(() => {
    if (!starting) {
      return;
    }
    appendLog("Still waiting for the camera sidecar. If this persists, check Camera permission for Touchless.");
  }, 45000);
}

function setNeedsPermission(permission: "Camera" | "Accessibility"): void {
  clearStartupTimer();
  starting = false;
  running = false;
  statusText.textContent = `Needs ${permission}`;
  startupValue.textContent = `needs_${permission.toLowerCase()}`;
  toggleButton.textContent = "Start";
}

function clearStartupTimer(): void {
  if (startupTimer !== undefined) {
    window.clearTimeout(startupTimer);
    startupTimer = undefined;
  }
}

async function ensureCameraPermission(): Promise<void> {
  if (!navigator.mediaDevices?.getUserMedia) {
    cameraValue.textContent = "unavailable";
    throw new Error("Camera permission cannot be requested from this app window.");
  }

  cameraValue.textContent = "requesting";
  startupValue.textContent = "requesting_camera";
  appendLog("Requesting Camera permission for Touchless.");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: true,
      audio: false,
    });
    for (const track of stream.getTracks()) {
      track.stop();
    }
    cameraValue.textContent = "granted";
    startupValue.textContent = "camera_granted";
  } catch (error) {
    cameraValue.textContent = "blocked";
    setNeedsPermission("Camera");
    appendLog("Camera is blocked for Touchless. Enable it in System Settings > Privacy & Security > Camera, then press Start again.");
    throw error;
  }
}

async function requestAccessibilityPermission(prompt: boolean): Promise<AccessibilityPermission> {
  const permission = await invoke<AccessibilityPermission>(
    "request_accessibility_permission",
    { prompt },
  );
  accessibilityValue.textContent = permission.trusted ? "trusted" : "blocked";
  return permission;
}

async function refreshAccessibilityStatus(): Promise<void> {
  try {
    await requestAccessibilityPermission(false);
  } catch (error) {
    accessibilityValue.textContent = "unknown";
  }
}

function bindOutput(input: HTMLInputElement, output: HTMLElement): void {
  const update = () => {
    output.textContent = input.value;
  };
  input.addEventListener("input", update);
  update();
}

function formatPoint(point: { x: number; y: number } | null): string {
  if (!point) {
    return "none";
  }
  return `${point.x.toFixed(3)}, ${point.y.toFixed(3)}`;
}

function formatCursor(cursor: SidecarEvent["cursor"]): string {
  if (!cursor?.enabled) {
    return "off";
  }
  if (!cursor.motion) {
    return "waiting";
  }
  return `${cursor.motion.dx}, ${cursor.motion.dy}`;
}

function formatCommand(payload: SidecarEvent): string {
  const executed = payload.cursor?.command;
  if (executed?.kind === "scroll") {
    return `scroll ${executed.amount ?? 0}`;
  }
  if (executed?.kind === "space") {
    return `space ${executed.direction ?? "unknown"}`;
  }

  const intent = payload.gesture_command;
  if (intent?.kind === "scroll") {
    return `scroll ${intent.amount ?? 0}`;
  }
  if (intent?.kind === "space") {
    return `space ${intent.direction ?? "unknown"}`;
  }

  return "none";
}

function formatAccessibility(cursor: SidecarEvent["cursor"]): string {
  if (!cursor?.enabled) {
    return "n/a";
  }
  if (cursor.accessibility_trusted === null) {
    return "unknown";
  }
  return cursor.accessibility_trusted ? "trusted" : "blocked";
}

function formatVoiceCommand(payload: SidecarEvent): string {
  if (payload.command === "type") {
    return `type ${payload.text ?? ""}`;
  }
  if (payload.command === "click") {
    return "click";
  }
  return payload.transcript ?? "unknown";
}

function updatePreview(preview: SidecarEvent["preview"]): void {
  if (!preview?.image) {
    cameraPreview.removeAttribute("src");
    cameraPreview.hidden = true;
    previewEmpty.hidden = false;
    return;
  }

  cameraPreview.src = preview.image;
  cameraPreview.hidden = false;
  previewEmpty.hidden = true;
}

function maybeWarnAboutAccessibility(cursor: SidecarEvent["cursor"]): void {
  if (
    accessibilityWarningShown ||
    !cursor?.enabled ||
    cursor.accessibility_trusted !== false
  ) {
    return;
  }

  accessibilityWarningShown = true;
  appendLog("Cursor movement is computed, but macOS Accessibility is blocked. Grant Accessibility permission to Touchless if the cursor does not move.");
}

function appendLog(message: string): void {
  log.textContent = `${new Date().toLocaleTimeString()} ${message}\n${log.textContent}`;
}

function getElement<T extends HTMLElement = HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Missing element #${id}`);
  }
  return element as T;
}
