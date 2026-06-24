use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{
    fs::OpenOptions,
    io::Write,
    io::{BufRead, BufReader},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Emitter, Manager, State};

#[derive(Debug, Serialize)]
struct AccessibilityPermission {
    trusted: bool,
}

#[derive(Debug, Deserialize)]
struct SidecarSettings {
    control_cursor: bool,
    debug_window: bool,
    cursor_gain: f64,
    cursor_smoothing_alpha: f64,
}

#[derive(Debug, Serialize, Clone)]
#[serde(tag = "type")]
enum SidecarEvent {
    #[serde(rename = "error")]
    Error { message: String },
    #[serde(rename = "progress")]
    Progress { stage: String, message: String },
    #[serde(rename = "exit")]
    Exit { code: Option<i32> },
}

struct SidecarState {
    child: Arc<Mutex<Option<Child>>>,
    voice_child: Arc<Mutex<Option<Child>>>,
    cursor: Arc<Mutex<AppCursorController>>,
}

#[tauri::command]
fn request_accessibility_permission(prompt: bool) -> AccessibilityPermission {
    app_log(&format!(
        "request_accessibility_permission invoked prompt={prompt}"
    ));
    let trusted = macos_input::accessibility_trusted_with_prompt(prompt);
    if prompt && !trusted {
        macos_input::open_accessibility_settings();
    }
    AccessibilityPermission { trusted }
}

#[tauri::command]
fn start_sidecar(
    app: AppHandle,
    state: State<'_, SidecarState>,
    settings: SidecarSettings,
) -> Result<(), String> {
    app_log("start_sidecar invoked");
    let child_handle = state.child.clone();
    let mut child_slot = state.child.lock().map_err(|_| {
        app_log("failed to lock sidecar state");
        "sidecar state lock poisoned".to_string()
    })?;

    if let Some(existing_child) = child_slot.as_mut() {
        match existing_child.try_wait() {
            Ok(Some(_status)) => {
                app_log("clearing exited sidecar child");
                *child_slot = None;
            }
            Ok(None) => {
                app_log("sidecar already running");
                return Ok(());
            }
            Err(error) => {
                app_log(&format!("failed to inspect sidecar: {error}"));
                *child_slot = None;
                return Err(format!("failed to inspect sidecar: {error}"));
            }
        }
    }

    let sidecar_runtime = sidecar_runtime(&app)?;
    if let Ok(mut cursor) = state.cursor.lock() {
        cursor.configure(&settings);
    }
    app_log(&format!(
        "resolved sidecar cwd={} command={} args_prefix={:?}",
        sidecar_runtime.cwd.display(),
        sidecar_runtime.command.display(),
        sidecar_runtime.args_prefix
    ));

    let mut command = Command::new(&sidecar_runtime.command);
    command
        .args(&sidecar_runtime.args_prefix)
        .arg("--cursor-gain")
        .arg(settings.cursor_gain.to_string())
        .arg("--cursor-smoothing-alpha")
        .arg(settings.cursor_smoothing_alpha.to_string())
        .current_dir(&sidecar_runtime.cwd)
        .env("PYTHONUNBUFFERED", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    if settings.control_cursor {
        command.arg("--control-cursor");
    }

    if settings.debug_window {
        command.arg("--debug-window");
    }

    let mut child = command.spawn().map_err(|error| {
        app_log(&format!("failed to start sidecar: {error}"));
        format!("failed to start sidecar: {error}")
    })?;
    let child_pid = child.id();
    app_log(&format!("spawned sidecar pid={child_pid}"));
    let _ = app.emit(
        "sidecar-event",
        SidecarEvent::Progress {
            stage: "sidecar_spawned".to_string(),
            message: format!("Camera sidecar process spawned with pid {child_pid}"),
        },
    );

    if let Some(stdout) = child.stdout.take() {
        let app_for_stdout = app.clone();
        let cursor_for_stdout = state.cursor.clone();
        thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                match serde_json::from_str::<serde_json::Value>(&line) {
                    Ok(mut value) => {
                        if value.get("type").and_then(|kind| kind.as_str()) == Some("status") {
                            if let Ok(mut cursor) = cursor_for_stdout.lock() {
                                cursor.apply_to_status(&mut value);
                            }
                        }
                        let _ = app_for_stdout.emit("sidecar-event", value);
                    }
                    Err(_) => {
                        let _ = app_for_stdout.emit(
                            "sidecar-event",
                            SidecarEvent::Error {
                                message: format!("sidecar stdout: {line}"),
                            },
                        );
                    }
                }
            }
        });
    }

    if let Some(stderr) = child.stderr.take() {
        let app_for_stderr = app.clone();
        thread::spawn(move || {
            let reader = BufReader::new(stderr);
            for line in reader.lines().map_while(Result::ok) {
                let _ = app_for_stderr.emit("sidecar-event", SidecarEvent::Error { message: line });
            }
        });
    }

    *child_slot = Some(child);
    drop(child_slot);

    start_voice_sidecar(app.clone(), state.voice_child.clone())?;

    let app_for_wait = app.clone();
    let cursor_for_wait = state.cursor.clone();
    thread::spawn(move || loop {
        thread::sleep(Duration::from_millis(250));

        let wait_result = {
            let mut child_slot = match child_handle.lock() {
                Ok(child_slot) => child_slot,
                Err(_) => {
                    app_log("failed to lock sidecar state from wait thread");
                    return;
                }
            };

            let Some(child) = child_slot.as_mut() else {
                return;
            };

            if child.id() != child_pid {
                return;
            }

            match child.try_wait() {
                Ok(Some(status)) => {
                    *child_slot = None;
                    Some(Ok(status.code()))
                }
                Ok(None) => None,
                Err(error) => {
                    *child_slot = None;
                    Some(Err(error.to_string()))
                }
            }
        };

        match wait_result {
            Some(Ok(code)) => {
                app_log(&format!("sidecar exited with code {code:?}"));
                if let Ok(mut cursor) = cursor_for_wait.lock() {
                    cursor.reset();
                }
                let _ = app_for_wait.emit("sidecar-event", SidecarEvent::Exit { code });
                return;
            }
            Some(Err(message)) => {
                app_log(&format!("sidecar wait failed: {message}"));
                if let Ok(mut cursor) = cursor_for_wait.lock() {
                    cursor.reset();
                }
                let _ = app_for_wait.emit(
                    "sidecar-event",
                    SidecarEvent::Error {
                        message: format!("sidecar wait failed: {message}"),
                    },
                );
                let _ = app_for_wait.emit("sidecar-event", SidecarEvent::Exit { code: None });
                return;
            }
            None => {}
        }
    });

    app_log("start_sidecar completed");
    Ok(())
}

#[tauri::command]
fn stop_sidecar(state: State<'_, SidecarState>) -> Result<(), String> {
    app_log("stop_sidecar invoked");
    kill_child(&state.child, "sidecar")?;
    kill_child(&state.voice_child, "voice sidecar")?;

    if let Ok(mut cursor) = state.cursor.lock() {
        cursor.reset();
    }

    Ok(())
}

fn kill_child(child: &Arc<Mutex<Option<Child>>>, label: &str) -> Result<(), String> {
    let mut child_slot = child
        .lock()
        .map_err(|_| format!("{label} state lock poisoned"))?;

    if let Some(mut child) = child_slot.take() {
        app_log(&format!("killing {label} pid={}", child.id()));
        child
            .kill()
            .map_err(|error| format!("failed to stop {label}: {error}"))?;
        let _ = child.wait();
    }

    Ok(())
}

#[derive(Debug)]
struct AppCursorController {
    enabled: bool,
    gain: f64,
    vertical_gain: f64,
    max_step: i64,
    deadzone_px: f64,
    previous_point: Option<(f64, f64)>,
    filter_x: OneEuroFilter,
    filter_y: OneEuroFilter,
    carry_dx: f64,
    carry_dy: f64,
    missing_point_frames: u8,
    max_missing_point_frames: u8,
    last_update_at: Option<Instant>,
    screen_width: f64,
    screen_height: f64,
}

impl Default for AppCursorController {
    fn default() -> Self {
        let (screen_width, screen_height) =
            macos_input::main_display_size().unwrap_or((1440.0, 900.0));

        Self {
            enabled: false,
            gain: 0.65,
            vertical_gain: 1.20,
            max_step: 70,
            deadzone_px: 2.0,
            previous_point: None,
            filter_x: OneEuroFilter::new(1.2, 0.045, 1.0),
            filter_y: OneEuroFilter::new(1.2, 0.045, 1.0),
            carry_dx: 0.0,
            carry_dy: 0.0,
            missing_point_frames: 0,
            max_missing_point_frames: 3,
            last_update_at: None,
            screen_width,
            screen_height,
        }
    }
}

impl AppCursorController {
    fn configure(&mut self, settings: &SidecarSettings) {
        self.enabled = settings.control_cursor;
        self.gain = settings.cursor_gain.clamp(0.2, 2.0);
        self.vertical_gain = 1.20;
        self.configure_filters(settings.cursor_smoothing_alpha);
        if let Some((width, height)) = macos_input::main_display_size() {
            self.screen_width = width;
            self.screen_height = height;
        }
        self.reset_motion();
    }

    fn reset(&mut self) {
        self.enabled = false;
        self.reset_motion();
    }

    fn reset_motion(&mut self) {
        self.previous_point = None;
        self.filter_x.reset();
        self.filter_y.reset();
        self.carry_dx = 0.0;
        self.carry_dy = 0.0;
        self.missing_point_frames = 0;
        self.last_update_at = None;
    }

    fn configure_filters(&mut self, smoothing_alpha: f64) {
        let smoothing = smoothing_alpha.clamp(0.08, 0.8);
        let min_cutoff = 0.55 + smoothing * 3.25;
        let beta = 0.018 + smoothing * 0.14;
        self.filter_x.configure(min_cutoff, beta, 1.0);
        self.filter_y.configure(min_cutoff, beta, 1.0);
    }

    fn apply_to_status(&mut self, value: &mut serde_json::Value) {
        let trusted = macos_input::accessibility_trusted();
        let command_result = self.apply_gesture_command(value, trusted);
        let tracking = value
            .get("tracking")
            .and_then(|tracking| tracking.as_bool())
            .unwrap_or(false);
        let motion = match self.point_from_status(value) {
            Some(point) => self.update(point, trusted),
            None if tracking => self.handle_missing_point(),
            None => {
                self.reset_motion();
                None
            }
        };

        if self.enabled
            && trusted
            && value
                .get("clicked")
                .and_then(|clicked| clicked.as_bool())
                .unwrap_or(false)
        {
            macos_input::click();
        }

        value["cursor"] = json!({
            "enabled": self.enabled,
            "accessibility_trusted": trusted,
            "motion": motion.map(|(dx, dy)| json!({ "dx": dx, "dy": dy })),
            "command": command_result,
            "source": "touchless_app",
        });
    }

    fn apply_gesture_command(
        &mut self,
        value: &serde_json::Value,
        trusted: bool,
    ) -> Option<serde_json::Value> {
        let command = value.get("gesture_command")?;
        if command.is_null() {
            return None;
        }

        let kind = command.get("kind").and_then(|kind| kind.as_str())?;
        if !trusted {
            return Some(json!({
                "kind": kind,
                "executed": false,
                "error": "Accessibility is not trusted for Touchless",
            }));
        }

        match kind {
            "scroll" => {
                let amount = command
                    .get("amount")
                    .and_then(|amount| amount.as_i64())
                    .unwrap_or(0)
                    .clamp(-12, 12);
                if amount != 0 {
                    macos_input::scroll_lines(amount);
                }
                Some(json!({
                    "kind": "scroll",
                    "amount": amount,
                    "executed": amount != 0,
                }))
            }
            "space" => {
                let direction = command
                    .get("direction")
                    .and_then(|direction| direction.as_str())
                    .unwrap_or("");
                let executed = match direction {
                    "left" => {
                        macos_input::switch_space_left();
                        true
                    }
                    "right" => {
                        macos_input::switch_space_right();
                        true
                    }
                    _ => false,
                };
                Some(json!({
                    "kind": "space",
                    "direction": direction,
                    "executed": executed,
                }))
            }
            _ => Some(json!({
                "kind": kind,
                "executed": false,
                "error": "Unknown gesture command",
            })),
        }
    }

    fn point_from_status(&self, value: &serde_json::Value) -> Option<(f64, f64)> {
        if !self.enabled {
            return None;
        }

        let point = value.get("point")?;
        Some((
            point.get("x")?.as_f64()?.clamp(0.0, 1.0),
            point.get("y")?.as_f64()?.clamp(0.0, 1.0),
        ))
    }

    fn update(&mut self, point: (f64, f64), trusted: bool) -> Option<(i64, i64)> {
        self.missing_point_frames = 0;
        let now = Instant::now();
        let dt = self
            .last_update_at
            .replace(now)
            .map(|previous| (now - previous).as_secs_f64().clamp(1.0 / 120.0, 0.12))
            .unwrap_or(1.0 / 30.0);
        let filtered_point = (
            self.filter_x.filter(point.0, dt),
            self.filter_y.filter(point.1, dt),
        );
        let Some(previous) = self.previous_point.replace(filtered_point) else {
            return Some((0, 0));
        };

        let raw_dx = (filtered_point.0 - previous.0) * self.screen_width * self.gain;
        let raw_dy =
            (filtered_point.1 - previous.1) * self.screen_height * self.gain * self.vertical_gain;
        let dx_float = self.apply_response_curve(raw_dx) + self.carry_dx;
        let dy_float = self.apply_response_curve(raw_dy) + self.carry_dy;
        let mut dx = dx_float as i64;
        let mut dy = dy_float as i64;
        self.carry_dx = dx_float - dx as f64;
        self.carry_dy = dy_float - dy as f64;
        dx = dx.clamp(-self.max_step, self.max_step);
        dy = dy.clamp(-self.max_step, self.max_step);

        if trusted && (dx != 0 || dy != 0) {
            macos_input::move_relative(dx, dy);
        }

        Some((dx, dy))
    }

    fn handle_missing_point(&mut self) -> Option<(i64, i64)> {
        self.missing_point_frames = self.missing_point_frames.saturating_add(1);
        if self.missing_point_frames > self.max_missing_point_frames {
            self.reset_motion();
        }
        None
    }

    fn apply_response_curve(&self, delta: f64) -> f64 {
        let magnitude = delta.abs();
        if magnitude < self.deadzone_px {
            return 0.0;
        }

        let sign = delta.signum();
        let adjusted = if magnitude < 10.0 {
            magnitude * 0.45
        } else {
            let boost = 1.0 + ((magnitude - 10.0) / 55.0).min(0.65);
            magnitude * boost
        };
        sign * adjusted
    }
}

fn app_log(message: &str) {
    let timestamp_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0);

    if let Ok(mut file) = OpenOptions::new()
        .create(true)
        .append(true)
        .open("/tmp/touchless-app.log")
    {
        let _ = writeln!(file, "{timestamp_ms} {message}");
    }
}

struct SidecarRuntime {
    command: PathBuf,
    args_prefix: Vec<String>,
    cwd: PathBuf,
}

#[derive(Debug)]
struct LowPassFilter {
    value: Option<f64>,
}

impl LowPassFilter {
    fn new() -> Self {
        Self { value: None }
    }

    fn filter(&mut self, value: f64, alpha: f64) -> f64 {
        let next = match self.value {
            Some(previous) => previous + alpha * (value - previous),
            None => value,
        };
        self.value = Some(next);
        next
    }

    fn reset(&mut self) {
        self.value = None;
    }
}

#[derive(Debug)]
struct OneEuroFilter {
    min_cutoff: f64,
    beta: f64,
    derivative_cutoff: f64,
    value_filter: LowPassFilter,
    derivative_filter: LowPassFilter,
    previous_raw: Option<f64>,
}

impl OneEuroFilter {
    fn new(min_cutoff: f64, beta: f64, derivative_cutoff: f64) -> Self {
        Self {
            min_cutoff,
            beta,
            derivative_cutoff,
            value_filter: LowPassFilter::new(),
            derivative_filter: LowPassFilter::new(),
            previous_raw: None,
        }
    }

    fn configure(&mut self, min_cutoff: f64, beta: f64, derivative_cutoff: f64) {
        self.min_cutoff = min_cutoff.max(0.001);
        self.beta = beta.max(0.0);
        self.derivative_cutoff = derivative_cutoff.max(0.001);
    }

    fn filter(&mut self, value: f64, dt: f64) -> f64 {
        let dt = dt.max(0.001);
        let derivative = self
            .previous_raw
            .map(|previous| (value - previous) / dt)
            .unwrap_or(0.0);
        self.previous_raw = Some(value);

        let filtered_derivative = self
            .derivative_filter
            .filter(derivative, smoothing_alpha(self.derivative_cutoff, dt));
        let cutoff = self.min_cutoff + self.beta * filtered_derivative.abs();
        self.value_filter.filter(value, smoothing_alpha(cutoff, dt))
    }

    fn reset(&mut self) {
        self.value_filter.reset();
        self.derivative_filter.reset();
        self.previous_raw = None;
    }
}

fn smoothing_alpha(cutoff: f64, dt: f64) -> f64 {
    let tau = 1.0 / (2.0 * std::f64::consts::PI * cutoff.max(0.001));
    1.0 / (1.0 + tau / dt.max(0.001))
}

fn sidecar_runtime(app: &AppHandle) -> Result<SidecarRuntime, String> {
    let resource_roots = resource_roots(app);
    for root in &resource_roots {
        let onedir_executable = root
            .join("dist-sidecar")
            .join("touchless-sidecar")
            .join("touchless-sidecar");
        if onedir_executable.exists() {
            return Ok(SidecarRuntime {
                command: onedir_executable,
                args_prefix: Vec::new(),
                cwd: root.clone(),
            });
        }

        let executable = root.join("dist-sidecar").join("touchless-sidecar");
        if executable.exists() {
            return Ok(SidecarRuntime {
                command: executable,
                args_prefix: Vec::new(),
                cwd: root.clone(),
            });
        }
    }

    if let Some(root) = dev_repo_dir() {
        return Ok(SidecarRuntime {
            command: python_path(&root),
            args_prefix: vec![
                "-u".to_string(),
                root.join("sidecar.py").display().to_string(),
            ],
            cwd: root,
        });
    }

    for root in resource_roots {
        let sidecar = root.join("sidecar.py");
        if sidecar.exists() {
            return Ok(SidecarRuntime {
                command: PathBuf::from("python3"),
                args_prefix: vec!["-u".to_string(), sidecar.display().to_string()],
                cwd: root,
            });
        }
    }

    Err("could not resolve camera sidecar runtime".to_string())
}

fn resource_roots(app: &AppHandle) -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if let Ok(resource_dir) = app.path().resource_dir() {
        roots.push(resource_dir.join("_up_"));
        roots.push(resource_dir);
    }
    roots
}

fn dev_repo_dir() -> Option<PathBuf> {
    let current_dir = std::env::current_dir().ok();
    if let Some(dir) = current_dir.as_ref() {
        if dir.join("sidecar.py").exists() {
            return Some(dir.clone());
        }
    }

    let source_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(PathBuf::from);
    if let Some(dir) = source_dir {
        if dir.join("sidecar.py").exists() {
            return Some(dir);
        }
    }

    None
}

fn python_path(repo_dir: &PathBuf) -> PathBuf {
    let venv_python = repo_dir.join(".venv").join("bin").join("python");
    if venv_python.exists() {
        venv_python
    } else {
        PathBuf::from("python3")
    }
}

fn start_voice_sidecar(
    app: AppHandle,
    child_handle: Arc<Mutex<Option<Child>>>,
) -> Result<(), String> {
    app_log("start_voice_sidecar invoked");
    let mut child_slot = child_handle
        .lock()
        .map_err(|_| "voice sidecar state lock poisoned".to_string())?;

    if let Some(existing_child) = child_slot.as_mut() {
        match existing_child.try_wait() {
            Ok(Some(_status)) => {
                app_log("clearing exited voice sidecar child");
                *child_slot = None;
            }
            Ok(None) => {
                app_log("voice sidecar already running");
                return Ok(());
            }
            Err(error) => {
                app_log(&format!("failed to inspect voice sidecar: {error}"));
                *child_slot = None;
                return Err(format!("failed to inspect voice sidecar: {error}"));
            }
        }
    }

    let runtime = voice_runtime(&app)?;
    app_log(&format!(
        "resolved voice sidecar cwd={} command={} args_prefix={:?}",
        runtime.cwd.display(),
        runtime.command.display(),
        runtime.args_prefix
    ));

    let mut command = Command::new(&runtime.command);
    command
        .args(&runtime.args_prefix)
        .current_dir(&runtime.cwd)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let mut child = command.spawn().map_err(|error| {
        app_log(&format!("failed to start voice sidecar: {error}"));
        format!("failed to start voice sidecar: {error}")
    })?;
    let child_pid = child.id();
    app_log(&format!("spawned voice sidecar pid={child_pid}"));
    let _ = app.emit(
        "sidecar-event",
        json!({
            "type": "voice_status",
            "stage": "voice_spawned",
            "listening": false,
            "message": format!("Voice sidecar process spawned with pid {child_pid}"),
        }),
    );

    if let Some(stdout) = child.stdout.take() {
        let app_for_stdout = app.clone();
        thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                match serde_json::from_str::<serde_json::Value>(&line) {
                    Ok(mut value) => {
                        apply_voice_command(&mut value);
                        let _ = app_for_stdout.emit("sidecar-event", value);
                    }
                    Err(_) => {
                        let _ = app_for_stdout.emit(
                            "sidecar-event",
                            SidecarEvent::Error {
                                message: format!("voice stdout: {line}"),
                            },
                        );
                    }
                }
            }
        });
    }

    if let Some(stderr) = child.stderr.take() {
        let app_for_stderr = app.clone();
        thread::spawn(move || {
            let reader = BufReader::new(stderr);
            for line in reader.lines().map_while(Result::ok) {
                let _ = app_for_stderr.emit(
                    "sidecar-event",
                    json!({
                        "type": "voice_error",
                        "message": line,
                    }),
                );
            }
        });
    }

    *child_slot = Some(child);
    drop(child_slot);

    let app_for_wait = app.clone();
    thread::spawn(move || loop {
        thread::sleep(Duration::from_millis(250));

        let wait_result = {
            let mut child_slot = match child_handle.lock() {
                Ok(child_slot) => child_slot,
                Err(_) => {
                    app_log("failed to lock voice sidecar state from wait thread");
                    return;
                }
            };

            let Some(child) = child_slot.as_mut() else {
                return;
            };

            if child.id() != child_pid {
                return;
            }

            match child.try_wait() {
                Ok(Some(status)) => {
                    *child_slot = None;
                    Some(Ok(status.code()))
                }
                Ok(None) => None,
                Err(error) => {
                    *child_slot = None;
                    Some(Err(error.to_string()))
                }
            }
        };

        match wait_result {
            Some(Ok(code)) => {
                app_log(&format!("voice sidecar exited with code {code:?}"));
                let _ = app_for_wait.emit(
                    "sidecar-event",
                    json!({
                        "type": "voice_exit",
                        "code": code,
                    }),
                );
                return;
            }
            Some(Err(message)) => {
                app_log(&format!("voice sidecar wait failed: {message}"));
                let _ = app_for_wait.emit(
                    "sidecar-event",
                    json!({
                        "type": "voice_error",
                        "message": format!("voice sidecar wait failed: {message}"),
                    }),
                );
                return;
            }
            None => {}
        }
    });

    app_log("start_voice_sidecar completed");
    Ok(())
}

fn apply_voice_command(value: &mut serde_json::Value) {
    if value.get("type").and_then(|kind| kind.as_str()) != Some("voice_command") {
        return;
    }

    let trusted = macos_input::accessibility_trusted();
    value["accessibility_trusted"] = json!(trusted);
    let command = value.get("command").and_then(|command| command.as_str());
    app_log(&format!(
        "voice command received command={command:?} trusted={trusted} transcript={:?}",
        value
            .get("transcript")
            .and_then(|transcript| transcript.as_str())
    ));

    if !trusted {
        value["executed"] = json!(false);
        value["error"] = json!("Accessibility is not trusted for Touchless");
        app_log("voice command blocked by Accessibility");
        return;
    }

    match command {
        Some("click") => {
            macos_input::click();
            value["executed"] = json!(true);
            app_log("voice click executed");
        }
        Some("type") => {
            let Some(text) = value
                .get("text")
                .and_then(|text| text.as_str())
                .map(str::to_string)
            else {
                value["executed"] = json!(false);
                value["error"] = json!("Voice type command was missing text");
                app_log("voice type command missing text");
                return;
            };
            let executed = macos_input::paste_text(&text);
            value["executed"] = json!(executed);
            if !executed {
                value["error"] = json!("Failed to paste dictated text");
            }
            app_log(&format!(
                "voice type executed={executed} text_len={}",
                text.chars().count()
            ));
        }
        _ => {
            value["executed"] = json!(false);
            value["error"] = json!("Unknown voice command");
            app_log("unknown voice command");
        }
    }
}

fn voice_runtime(app: &AppHandle) -> Result<SidecarRuntime, String> {
    let resource_roots = resource_roots(app);
    for root in &resource_roots {
        let executable = root.join("dist-voice").join("touchless-voice");
        if executable.exists() {
            return Ok(SidecarRuntime {
                command: executable,
                args_prefix: Vec::new(),
                cwd: root.clone(),
            });
        }
    }

    if let Some(root) = dev_repo_dir() {
        let executable = root.join("dist-voice").join("touchless-voice");
        if executable.exists() {
            return Ok(SidecarRuntime {
                command: executable,
                args_prefix: Vec::new(),
                cwd: root,
            });
        }
    }

    Err("could not resolve voice sidecar runtime; run scripts/build_voice_sidecar.py".to_string())
}

fn main() {
    tauri::Builder::default()
        .manage(SidecarState {
            child: Arc::new(Mutex::new(None)),
            voice_child: Arc::new(Mutex::new(None)),
            cursor: Arc::new(Mutex::new(AppCursorController::default())),
        })
        .invoke_handler(tauri::generate_handler![
            request_accessibility_permission,
            start_sidecar,
            stop_sidecar
        ])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                let state = window.state::<SidecarState>();
                let _ = stop_sidecar(state);
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(target_os = "macos")]
mod macos_input {
    use std::{ffi::c_void, process::Command, thread, time::Duration};

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct CGPoint {
        x: f64,
        y: f64,
    }

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct CGSize {
        width: f64,
        height: f64,
    }

    #[repr(C)]
    #[derive(Clone, Copy)]
    struct CGRect {
        origin: CGPoint,
        size: CGSize,
    }

    const K_CG_HID_EVENT_TAP: u32 = 0;
    const K_CG_EVENT_LEFT_MOUSE_DOWN: u32 = 1;
    const K_CG_EVENT_LEFT_MOUSE_UP: u32 = 2;
    const K_CG_EVENT_MOUSE_MOVED: u32 = 5;
    const K_CG_MOUSE_BUTTON_LEFT: u32 = 0;
    const K_CG_SCROLL_EVENT_UNIT_LINE: u32 = 1;
    const K_CG_EVENT_FLAG_MASK_CONTROL: u64 = 1 << 18;
    const K_CG_EVENT_FLAG_MASK_COMMAND: u64 = 1 << 20;
    const KEY_CODE_LEFT_ARROW: u16 = 123;
    const KEY_CODE_RIGHT_ARROW: u16 = 124;
    const KEY_CODE_V: u16 = 9;

    #[link(name = "ApplicationServices", kind = "framework")]
    extern "C" {
        fn AXIsProcessTrusted() -> u8;
        fn AXIsProcessTrustedWithOptions(options: *const c_void) -> u8;
        static kAXTrustedCheckOptionPrompt: *const c_void;
        fn CGEventCreate(source: *const c_void) -> *mut c_void;
        fn CGEventGetLocation(event: *mut c_void) -> CGPoint;
        fn CGEventCreateMouseEvent(
            source: *const c_void,
            mouse_type: u32,
            mouse_cursor_position: CGPoint,
            mouse_button: u32,
        ) -> *mut c_void;
        fn CGEventPost(tap: u32, event: *mut c_void);
        fn CGEventCreateKeyboardEvent(
            source: *const c_void,
            virtual_key: u16,
            key_down: u8,
        ) -> *mut c_void;
        fn CGEventCreateScrollWheelEvent(
            source: *const c_void,
            units: u32,
            wheel_count: u32,
            wheel1: i32,
            wheel2: i32,
            wheel3: i32,
        ) -> *mut c_void;
        fn CGEventSetFlags(event: *mut c_void, flags: u64);
        fn CGMainDisplayID() -> u32;
        fn CGDisplayBounds(display: u32) -> CGRect;
    }

    #[link(name = "CoreFoundation", kind = "framework")]
    extern "C" {
        static kCFBooleanTrue: *const c_void;
        fn CFDictionaryCreate(
            allocator: *const c_void,
            keys: *const *const c_void,
            values: *const *const c_void,
            num_values: isize,
            key_callbacks: *const c_void,
            value_callbacks: *const c_void,
        ) -> *const c_void;
        fn CFRelease(cf: *const c_void);
    }

    pub fn accessibility_trusted() -> bool {
        unsafe { AXIsProcessTrusted() != 0 }
    }

    pub fn accessibility_trusted_with_prompt(prompt: bool) -> bool {
        if !prompt {
            return accessibility_trusted();
        }

        unsafe {
            let keys = [kAXTrustedCheckOptionPrompt];
            let values = [kCFBooleanTrue];
            let options = CFDictionaryCreate(
                std::ptr::null(),
                keys.as_ptr(),
                values.as_ptr(),
                1,
                std::ptr::null(),
                std::ptr::null(),
            );

            if options.is_null() {
                return accessibility_trusted();
            }

            let trusted = AXIsProcessTrustedWithOptions(options) != 0;
            CFRelease(options);
            trusted
        }
    }

    pub fn open_accessibility_settings() {
        let _ = Command::new("open")
            .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
            .spawn();
    }

    pub fn main_display_size() -> Option<(f64, f64)> {
        unsafe {
            let bounds = CGDisplayBounds(CGMainDisplayID());
            if bounds.size.width > 0.0 && bounds.size.height > 0.0 {
                Some((bounds.size.width, bounds.size.height))
            } else {
                None
            }
        }
    }

    pub fn move_relative(dx: i64, dy: i64) {
        if let Some(current) = current_location() {
            post_mouse_event(
                K_CG_EVENT_MOUSE_MOVED,
                CGPoint {
                    x: current.x + dx as f64,
                    y: current.y + dy as f64,
                },
            );
        }
    }

    pub fn click() {
        if let Some(current) = current_location() {
            post_mouse_event(K_CG_EVENT_LEFT_MOUSE_DOWN, current);
            thread::sleep(Duration::from_millis(35));
            post_mouse_event(K_CG_EVENT_LEFT_MOUSE_UP, current);
        }
    }

    pub fn scroll_lines(amount: i64) {
        let amount = amount.clamp(-12, 12) as i32;
        if amount == 0 {
            return;
        }

        unsafe {
            let event = CGEventCreateScrollWheelEvent(
                std::ptr::null(),
                K_CG_SCROLL_EVENT_UNIT_LINE,
                1,
                amount,
                0,
                0,
            );
            if !event.is_null() {
                CGEventPost(K_CG_HID_EVENT_TAP, event);
                CFRelease(event);
            }
        }
    }

    pub fn switch_space_left() {
        post_key_event(KEY_CODE_LEFT_ARROW, true, K_CG_EVENT_FLAG_MASK_CONTROL);
        post_key_event(KEY_CODE_LEFT_ARROW, false, K_CG_EVENT_FLAG_MASK_CONTROL);
    }

    pub fn switch_space_right() {
        post_key_event(KEY_CODE_RIGHT_ARROW, true, K_CG_EVENT_FLAG_MASK_CONTROL);
        post_key_event(KEY_CODE_RIGHT_ARROW, false, K_CG_EVENT_FLAG_MASK_CONTROL);
    }

    pub fn paste_text(text: &str) -> bool {
        if !write_pasteboard(text) {
            return false;
        }

        post_key_event(KEY_CODE_V, true, K_CG_EVENT_FLAG_MASK_COMMAND);
        post_key_event(KEY_CODE_V, false, K_CG_EVENT_FLAG_MASK_COMMAND);
        true
    }

    fn current_location() -> Option<CGPoint> {
        unsafe {
            let event = CGEventCreate(std::ptr::null());
            if event.is_null() {
                return None;
            }
            let location = CGEventGetLocation(event);
            CFRelease(event);
            Some(location)
        }
    }

    fn post_mouse_event(mouse_type: u32, point: CGPoint) {
        unsafe {
            let event = CGEventCreateMouseEvent(
                std::ptr::null(),
                mouse_type,
                point,
                K_CG_MOUSE_BUTTON_LEFT,
            );
            if !event.is_null() {
                CGEventPost(K_CG_HID_EVENT_TAP, event);
                CFRelease(event);
            }
        }
    }

    fn post_key_event(key_code: u16, key_down: bool, flags: u64) {
        unsafe {
            let event = CGEventCreateKeyboardEvent(
                std::ptr::null(),
                key_code,
                if key_down { 1 } else { 0 },
            );
            if !event.is_null() {
                CGEventSetFlags(event, flags);
                CGEventPost(K_CG_HID_EVENT_TAP, event);
                CFRelease(event);
            }
        }
    }

    fn write_pasteboard(text: &str) -> bool {
        let Ok(mut child) = Command::new("pbcopy")
            .stdin(std::process::Stdio::piped())
            .spawn()
        else {
            return false;
        };

        if let Some(stdin) = child.stdin.as_mut() {
            if std::io::Write::write_all(stdin, text.as_bytes()).is_err() {
                return false;
            }
        }

        child.wait().map(|status| status.success()).unwrap_or(false)
    }
}

#[cfg(not(target_os = "macos"))]
mod macos_input {
    pub fn accessibility_trusted() -> bool {
        false
    }

    pub fn accessibility_trusted_with_prompt(_prompt: bool) -> bool {
        false
    }

    pub fn open_accessibility_settings() {}

    pub fn main_display_size() -> Option<(f64, f64)> {
        None
    }

    pub fn move_relative(_dx: i64, _dy: i64) {}

    pub fn click() {}

    pub fn scroll_lines(_amount: i64) {}

    pub fn switch_space_left() {}

    pub fn switch_space_right() {}

    pub fn paste_text(_text: &str) -> bool {
        false
    }
}
