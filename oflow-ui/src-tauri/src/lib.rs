/// Main Tauri application entry point.
mod error;
mod socket_client;

use socket_client::{is_backend_running, send_command};
use std::sync::Arc;
use tauri::{AppHandle, Manager, State, Window};
use tokio::sync::Mutex;

// Only import shortcut plugin for release builds
#[cfg(not(debug_assertions))]
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

// start_sidecar_backend() uses this in both profiles (debug falls back to it
// too), so the import must be unconditional.
use tauri_plugin_shell::ShellExt;

/// Default shortcut key (Copilot key on supported keyboards)
const DEFAULT_SHORTCUT: &str = "XF86Assistant";

/// Settings file path relative to home directory
const SETTINGS_PATH: &str = ".oflow/settings.json";

/// State to track recording status and current shortcut.
struct AppState {
    is_recording: bool,
    current_shortcut: String,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            is_recording: false,
            current_shortcut: DEFAULT_SHORTCUT.to_string(),
        }
    }
}

#[derive(serde::Deserialize, serde::Serialize, Default)]
#[serde(rename_all = "camelCase")]
struct Settings {
    #[serde(default)]
    enable_cleanup: bool,
    #[serde(default)]
    enable_memory: bool,
    #[serde(default)]
    openai_api_key: Option<String>,
    #[serde(default)]
    groq_api_key: Option<String>,
    #[serde(default)]
    provider: Option<String>,
    #[serde(default)]
    transcription_mode: Option<String>,
    #[serde(default)]
    shortcut: Option<String>,
}

/// Reads settings from the settings file.
fn read_settings() -> Settings {
    let home = dirs::home_dir().unwrap_or_default();
    let settings_path = home.join(SETTINGS_PATH);

    if let Ok(contents) = std::fs::read_to_string(&settings_path) {
        // NB: never log `contents` — settings.json holds API keys.
        match serde_json::from_str::<Settings>(&contents) {
            Ok(s) => {
                eprintln!("[oflow] Parsed shortcut: {:?}", s.shortcut);
                s
            }
            Err(e) => {
                eprintln!("[oflow] Failed to parse settings: {}", e);
                Settings::default()
            }
        }
    } else {
        eprintln!("[oflow] Could not read settings file");
        Settings::default()
    }
}

/// Writes settings to the settings file.
fn write_settings(settings: &Settings) -> Result<(), String> {
    let home = dirs::home_dir().ok_or("Could not find home directory")?;
    let settings_path = home.join(SETTINGS_PATH);

    // Ensure directory exists
    if let Some(parent) = settings_path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("Failed to create settings directory: {}", e))?;
    }

    let contents = serde_json::to_string_pretty(settings)
        .map_err(|e| format!("Failed to serialize settings: {}", e))?;

    std::fs::write(&settings_path, contents)
        .map_err(|e| format!("Failed to write settings: {}", e))?;

    Ok(())
}

/// One Markdown file in the second-brain vault (a note-day or a meeting).
#[derive(serde::Serialize)]
struct VaultEntry {
    name: String,
    content: String,
    modified: u64, // unix seconds
}

fn expand_tilde(p: &str) -> std::path::PathBuf {
    if let Some(rest) = p.strip_prefix("~/") {
        if let Some(home) = dirs::home_dir() {
            return home.join(rest);
        }
    }
    std::path::PathBuf::from(p)
}

/// Resolve the brain vault dir the same way brain.py does:
/// env OFLOW_BRAIN_DIR > settings.json brainVaultPath > ~/brain.
fn vault_dir() -> std::path::PathBuf {
    if let Ok(env) = std::env::var("OFLOW_BRAIN_DIR") {
        if !env.is_empty() {
            return expand_tilde(&env);
        }
    }
    let home = dirs::home_dir().unwrap_or_default();
    if let Ok(contents) = std::fs::read_to_string(home.join(SETTINGS_PATH)) {
        if let Ok(v) = serde_json::from_str::<serde_json::Value>(&contents) {
            if let Some(p) = v.get("brainVaultPath").and_then(|x| x.as_str()) {
                if !p.is_empty() {
                    return expand_tilde(p);
                }
            }
        }
    }
    home.join("brain")
}

/// List Markdown entries in the vault's `notes/` or `meetings/` folder,
/// newest first. Returns an empty list if the folder doesn't exist yet.
#[tauri::command]
fn read_vault(kind: String) -> Result<Vec<VaultEntry>, String> {
    if !matches!(kind.as_str(), "notes" | "meetings" | "initiatives" | "dreams" | "reminders" | "journal") {
        return Err("invalid vault kind".to_string());
    }
    let dir = vault_dir().join(&kind);
    let mut entries: Vec<VaultEntry> = Vec::new();
    let read = match std::fs::read_dir(&dir) {
        Ok(r) => r,
        Err(_) => return Ok(entries), // vault/folder not created yet
    };
    for e in read.flatten() {
        let path = e.path();
        if path.extension().and_then(|x| x.to_str()) != Some("md") {
            continue;
        }
        let content = std::fs::read_to_string(&path).unwrap_or_default();
        let name = path
            .file_stem()
            .and_then(|x| x.to_str())
            .unwrap_or("")
            .to_string();
        let modified = e
            .metadata()
            .ok()
            .and_then(|m| m.modified().ok())
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_secs())
            .unwrap_or(0);
        entries.push(VaultEntry { name, content, modified });
    }
    entries.sort_by(|a, b| b.modified.cmp(&a.modified));
    Ok(entries)
}

/// Answer + cited sources from an "ask my brain" query.
#[derive(serde::Serialize, serde::Deserialize)]
struct AskResult {
    answer: String,
    #[serde(default)]
    sources: Vec<String>,
}

/// Run a natural-language query over the vault via the brain_search CLI and
/// return the synthesized answer + sources. Blocking subprocess is fine here —
/// it's a user-triggered, occasional call.
#[tauri::command]
async fn ask_brain(query: String) -> Result<AskResult, String> {
    if query.trim().is_empty() {
        return Err("empty query".to_string());
    }
    let home = dirs::home_dir().ok_or("Could not find home directory")?;
    let launcher = home.join(".local/bin/oflow-brain");
    let out = std::process::Command::new(&launcher)
        .arg("--json")
        .arg(&query)
        .output()
        .map_err(|e| format!("Failed to run brain search: {}", e))?;
    if !out.status.success() {
        return Err(format!(
            "brain search failed: {}",
            String::from_utf8_lossy(&out.stderr)
        ));
    }
    let stdout = String::from_utf8_lossy(&out.stdout);
    // The JSON is the last stdout line starting with '{' (logs go to stderr).
    let line = stdout
        .lines()
        .rev()
        .find(|l| l.trim_start().starts_with('{'))
        .ok_or("no answer returned")?;
    serde_json::from_str::<AskResult>(line).map_err(|e| format!("parse error: {}", e))
}

/// Run the brain CLI with --json and return the last JSON line (object or array).
fn run_brain_json(args: &[&str]) -> Result<String, String> {
    let home = dirs::home_dir().ok_or("Could not find home directory")?;
    let mut cmd = std::process::Command::new(home.join(".local/bin/oflow-brain"));
    cmd.arg("--json");
    for a in args {
        cmd.arg(a);
    }
    let out = cmd.output().map_err(|e| format!("Failed to run brain: {}", e))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).to_string());
    }
    let stdout = String::from_utf8_lossy(&out.stdout);
    stdout
        .lines()
        .rev()
        .find(|l| {
            let t = l.trim_start();
            t.starts_with('{') || t.starts_with('[')
        })
        .map(|s| s.to_string())
        .ok_or_else(|| "no json output".to_string())
}

#[derive(serde::Serialize, serde::Deserialize)]
struct Initiative {
    slug: String,
    title: String,
    #[serde(default)]
    status: String,
    #[serde(default)]
    goals: Vec<String>,
    #[serde(default)]
    linked: u32,
}

#[tauri::command]
async fn list_initiatives() -> Result<Vec<Initiative>, String> {
    let json = run_brain_json(&["--initiatives"])?;
    serde_json::from_str(&json).map_err(|e| format!("parse error: {}", e))
}

#[derive(serde::Serialize, serde::Deserialize)]
struct InitiativeStatus {
    title: String,
    status: String,
    #[serde(default)]
    linked: u32,
}

#[tauri::command]
async fn initiative_status(name: String) -> Result<InitiativeStatus, String> {
    if name.trim().is_empty() {
        return Err("empty initiative".to_string());
    }
    let json = run_brain_json(&["--initiative", &name])?;
    serde_json::from_str(&json).map_err(|e| format!("parse error: {}", e))
}

#[derive(serde::Serialize, serde::Deserialize)]
struct DreamSuggestion {
    #[serde(default)]
    name: String,
    #[serde(default)]
    why: String,
}

#[derive(serde::Serialize, serde::Deserialize)]
struct DreamResult {
    #[serde(default)]
    relinked: u32,
    #[serde(default)]
    initiatives: u32,
    #[serde(default)]
    stale: Vec<String>,
    #[serde(default)]
    suggestions: Vec<DreamSuggestion>,
    #[serde(default)]
    journal: String,
}

/// Run a consolidation "dream" now (re-link, refresh initiative statuses,
/// suggest emergent initiatives, write a journal). Can take a while — the UI
/// shows a spinner.
#[tauri::command]
async fn run_dream() -> Result<DreamResult, String> {
    // Manual dream always runs (the nightly timer coordinates via --dream alone).
    let json = run_brain_json(&["--dream", "--force"])?;
    serde_json::from_str(&json).map_err(|e| format!("parse error: {}", e))
}

#[derive(serde::Serialize, serde::Deserialize)]
struct JournalResult {
    #[serde(default)]
    date: String,
    #[serde(default)]
    dictations: u32,
    #[serde(default)]
    journal: String,
    #[serde(default)]
    skipped: bool,
    #[serde(default)]
    reason: String,
}

/// Synthesize today's journal from the dictation stream, on demand.
#[tauri::command]
async fn run_journal() -> Result<JournalResult, String> {
    let json = run_brain_json(&["--journal"])?;
    serde_json::from_str(&json).map_err(|e| format!("parse error: {}", e))
}

/// Starts recording audio.
///
/// # Returns
///
/// Returns `Ok(())` if recording started successfully, or an error message if it failed.
#[tauri::command]
async fn start_recording() -> Result<(), String> {
    send_command("start")
        .await
        .map_err(|e| format!("Failed to start recording: {}", e))?;
    Ok(())
}

/// Stops recording audio.
///
/// # Returns
///
/// Returns `Ok(())` if recording stopped successfully, or an error message if it failed.
#[tauri::command]
async fn stop_recording() -> Result<(), String> {
    send_command("stop")
        .await
        .map_err(|e| format!("Failed to stop recording: {}", e))?;
    Ok(())
}

/// Toggles recording state.
///
/// # Returns
///
/// Returns the new recording state (`true` if recording, `false` if stopped),
/// or an error message if the operation failed.
#[tauri::command]
async fn toggle_recording(
    state: State<'_, Arc<Mutex<AppState>>>,
) -> Result<bool, String> {
    let mut app_state = state.lock().await;

    let command = if app_state.is_recording {
        "stop"
    } else {
        "start"
    };

    send_command(command)
        .await
        .map_err(|e| format!("Failed to toggle recording: {}", e))?;

    app_state.is_recording = !app_state.is_recording;
    Ok(app_state.is_recording)
}

/// Gets the current recording status.
///
/// # Returns
///
/// Returns `true` if recording is active, `false` otherwise.
/// Note: This returns the local state, not the actual backend state.
#[tauri::command]
async fn get_recording_status(
    state: State<'_, Arc<Mutex<AppState>>>,
) -> Result<bool, String> {
    let app_state = state.lock().await;
    Ok(app_state.is_recording)
}

/// Gets the current global shortcut.
#[tauri::command]
async fn get_shortcut(
    state: State<'_, Arc<Mutex<AppState>>>,
) -> Result<String, String> {
    let app_state = state.lock().await;
    eprintln!("[oflow] get_shortcut called, returning: {}", app_state.current_shortcut);
    Ok(app_state.current_shortcut.clone())
}

/// Sets the keyboard shortcut by updating Hyprland bindings.
/// On Wayland/Hyprland, we can't use Tauri global shortcuts, so we update the WM config directly.
#[tauri::command]
async fn set_shortcut(
    state: State<'_, Arc<Mutex<AppState>>>,
    shortcut: String,
) -> Result<(), String> {
    eprintln!("[oflow] set_shortcut called with: {}", shortcut);

    let home = dirs::home_dir().ok_or("Could not find home directory")?;
    eprintln!("[oflow] home dir: {:?}", home);
    let bindings_file = home.join(".config/hypr/bindings.conf");

    if !bindings_file.exists() {
        return Err("Hyprland bindings file not found".to_string());
    }

    // Read current bindings
    let contents = std::fs::read_to_string(&bindings_file)
        .map_err(|e| format!("Failed to read bindings file: {}", e))?;

    // Remove old oflow bindings — match every line the oflow block emits: its
    // comment(s), the start/stop bind(d)/bindr lines, and the note/meeting
    // bind/unbind lines (all invoke oflow-ctl; older builds used oflow.py).
    let new_lines: Vec<&str> = contents
        .lines()
        .filter(|line| {
            !line.contains("# Oflow voice dictation")
                && !line.contains("oflow-ctl")
                && !line.contains("oflow.py")
                && !line.contains("SUPER SHIFT, N")
                && !line.contains("SUPER SHIFT, M")
        })
        .collect();

    // Convert shortcut format (e.g., "Super+I" -> "SUPER, I", "XF86Assistant" -> ", XF86Assistant")
    let hypr_shortcut = if shortcut.starts_with("XF86") {
        // Special keys like XF86Assistant (Copilot key) - no modifier needed
        format!(", {}", shortcut)
    } else {
        shortcut
            .replace("Super+", "SUPER, ")
            .replace("Ctrl+", "CTRL ")
            .replace("Shift+", "SHIFT ")
            .replace("Alt+", "ALT ")
    };

    // Add new bindings using oflow-ctl helper script. Note & meeting capture ride
    // on the Copilot key (which holds Super+Shift), so include them when binding
    // that key — mirrors scripts/oflow-hotkey so the two generators stay in sync.
    let is_copilot = shortcut.starts_with("XF86") || shortcut.contains("F23");
    let brain_binds = if is_copilot {
        "\nunbind = SUPER SHIFT, N\nbind = SUPER SHIFT, N, exec, oflow-ctl note\nunbind = SUPER SHIFT, M\nbind = SUPER SHIFT, M, exec, oflow-ctl meeting"
    } else {
        ""
    };
    let new_bindings = format!(
        "\n# Oflow voice dictation (push-to-talk: hold {} to record, release to stop)\nbind = {}, exec, oflow-ctl start\nbindr = {}, exec, oflow-ctl stop{}",
        shortcut,
        hypr_shortcut,
        hypr_shortcut,
        brain_binds
    );

    let mut final_content = new_lines.join("\n");
    // Remove trailing empty lines
    while final_content.ends_with("\n\n") {
        final_content.pop();
    }
    final_content.push_str(&new_bindings);
    final_content.push('\n');

    eprintln!("[oflow] Writing to: {:?}", bindings_file);
    std::fs::write(&bindings_file, &final_content)
        .map_err(|e| format!("Failed to write bindings file: {}", e))?;
    eprintln!("[oflow] Write successful");

    eprintln!("[oflow] Reloading Hyprland");
    let reload_result = std::process::Command::new("hyprctl")
        .arg("reload")
        .output();
    eprintln!("[oflow] Hyprctl result: {:?}", reload_result);

    let mut app_state = state.lock().await;
    app_state.current_shortcut = shortcut.clone();

    let mut settings = read_settings();
    settings.shortcut = Some(shortcut.clone());
    write_settings(&settings)?;

    eprintln!("[oflow] Shortcut successfully updated to: {}", shortcut);
    Ok(())
}

/// Checks if the backend is running.
///
/// # Returns
///
/// Returns `true` if the backend socket is accessible, `false` otherwise.
#[tauri::command]
async fn check_backend_status() -> Result<bool, String> {
    Ok(is_backend_running().await)
}

fn start_development_backend() {
    let oflow_dir = std::env::var("OFLOW_DIR")
        .unwrap_or_else(|_| dirs::home_dir()
            .map(|h| h.join("code/oflow").to_string_lossy().to_string())
            .unwrap_or_default());
    
    let python_path = format!("{oflow_dir}/.venv/bin/python");
    let script_path = format!("{oflow_dir}/oflow.py");
    
    if std::path::Path::new(&python_path).exists() && std::path::Path::new(&script_path).exists() {
        log::info!("Starting development backend from {}", script_path);
        match std::process::Command::new(&python_path)
            .arg(&script_path)
            .spawn()
        {
            Ok(_) => log::info!("Development backend started"),
            Err(e) => log::error!("Failed to start development backend: {}", e),
        }
    } else {
        log::error!("Development backend not found at {} or {}", python_path, script_path);
    }
}

fn start_sidecar_backend(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let sidecar = app.shell().sidecar("oflow-backend")?;
    let (mut rx, _child) = sidecar.spawn()?;
    
    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    log::info!("[backend] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Stderr(line) => {
                    log::warn!("[backend] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Terminated(payload) => {
                    log::info!("[backend] Terminated with code: {:?}", payload.code);
                    break;
                }
                _ => {}
            }
        }
    });
    
    log::info!("Sidecar backend started");
    Ok(())
}

/// Shows the main window.
#[tauri::command]
async fn show_window(window: Window) -> Result<(), String> {
    window
        .show()
        .map_err(|e| format!("Failed to show window: {}", e))?;
    window
        .set_focus()
        .map_err(|e| format!("Failed to focus window: {}", e))?;
    Ok(())
}

/// Hides the main window.
#[tauri::command]
async fn hide_window(window: Window) -> Result<(), String> {
    window
        .hide()
        .map_err(|e| format!("Failed to hide window: {}", e))?;
    Ok(())
}

/// Sets up the system tray icon with context menu.
fn setup_tray(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
    use tauri::menu::{MenuBuilder, MenuItemBuilder};

    // Create context menu
    let show_item = MenuItemBuilder::with_id("show", "Show").build(app)?;
    let quit_item = MenuItemBuilder::with_id("quit", "Quit").build(app)?;
    let menu = MenuBuilder::new(app)
        .item(&show_item)
        .separator()
        .item(&quit_item)
        .build()?;

    TrayIconBuilder::new()
        .tooltip("oflow - Voice Dictation")
        .icon(
            app.default_window_icon()
                .ok_or("Failed to get default icon")?
                .clone(),
        )
        .menu(&menu)
        .on_menu_event(|app, event| {
            match event.id().as_ref() {
                "show" => {
                    if let Some(window) = app.get_webview_window("main") {
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                }
                "quit" => {
                    app.exit(0);
                }
                _ => {}
            }
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                if let Some(window) = tray.app_handle().get_webview_window("main") {
                    if window.is_visible().unwrap_or(false) {
                        let _ = window.hide();
                    } else {
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                }
            }
        })
        .build(app)?;

    log::info!("System tray initialized");
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Read settings to get the configured shortcut
    let settings = read_settings();
    let initial_shortcut = settings
        .shortcut
        .unwrap_or_else(|| DEFAULT_SHORTCUT.to_string());

    // Create shared app state
    let app_state = Arc::new(Mutex::new(AppState {
        is_recording: false,
        current_shortcut: initial_shortcut.clone(),
    }));

    // Clone for the shortcut handler (release mode only)
    #[cfg(not(debug_assertions))]
    let handler_state = app_state.clone();

    #[cfg(not(debug_assertions))]
    let shortcut_initial = initial_shortcut.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // Another instance was launched - focus existing window
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .setup(move |app| {
            // Initialize plugins
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            app.handle().plugin(tauri_plugin_fs::init())?;
            app.handle().plugin(tauri_plugin_shell::init())?;

            // Initialize app state (share with handler)
            app.manage(app_state.clone());

            // Setup global shortcut plugin with handler (only in release mode)
            // In dev mode, Hyprland handles shortcuts via scripts/dev.sh
            #[cfg(not(debug_assertions))]
            {
                let state = handler_state.clone();
                app.handle().plugin(
                    tauri_plugin_global_shortcut::Builder::new()
                        .with_handler(move |_app, shortcut, event| {
                            log::info!("Shortcut event: {:?} - {:?}", shortcut, event.state());
                            let state = state.clone();
                            match event.state() {
                                ShortcutState::Pressed => {
                                    tauri::async_runtime::spawn(async move {
                                        let mut app_state = state.lock().await;
                                        if !app_state.is_recording {
                                            log::info!("Sending 'start' command to backend...");
                                            if let Err(e) = send_command("start").await {
                                                log::error!("Failed to start recording: {}", e);
                                            } else {
                                                app_state.is_recording = true;
                                                log::info!("Recording started (shortcut pressed)");
                                            }
                                        }
                                    });
                                }
                                ShortcutState::Released => {
                                    tauri::async_runtime::spawn(async move {
                                        let mut app_state = state.lock().await;
                                        if app_state.is_recording {
                                            log::info!("Sending 'stop' command to backend...");
                                            if let Err(e) = send_command("stop").await {
                                                log::error!("Failed to stop recording: {}", e);
                                            } else {
                                                app_state.is_recording = false;
                                                log::info!("Recording stopped (shortcut released)");
                                            }
                                        }
                                    });
                                }
                            }
                        })
                        .build(),
                )?;

                // Register the initial shortcut
                if let Ok(shortcut) = shortcut_initial.parse::<Shortcut>() {
                    if let Err(e) = app.global_shortcut().register(shortcut) {
                        log::error!("Failed to register initial shortcut '{}': {}", shortcut_initial, e);
                    } else {
                        log::info!("Registered global shortcut: {}", shortcut_initial);
                    }
                } else {
                    log::error!("Invalid shortcut format: {}", shortcut_initial);
                }
            }

            #[cfg(debug_assertions)]
            log::info!("Dev mode: shortcuts handled by Hyprland bindings");

            // Setup system tray (non-fatal if it fails)
            if let Err(e) = setup_tray(app.handle()) {
                log::error!("Failed to setup system tray: {}", e);
            }

            // Get main window
            let window = app
                .get_webview_window("main")
                .ok_or("Main window not found")?;

            // Start hidden when launched with --hidden (e.g. from autostart):
            // the app lives in the system tray and is shown on demand. Without
            // the flag (e.g. user launches oflow manually) show + focus it.
            let start_hidden = std::env::args().any(|a| a == "--hidden");
            if start_hidden {
                let _ = window.hide();
                log::info!("Started hidden (--hidden); running in tray");
            } else {
                window.show().map_err(|e| {
                    format!("Failed to show window on startup: {}", e)
                })?;
                window.set_focus().map_err(|e| {
                    format!("Failed to focus window on startup: {}", e)
                })?;
                log::info!("Window shown and focused");
            }

            // Handle window close - minimize to tray instead of quitting
            let window_handle = window.clone();
            window.on_window_event(move |event| {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window_handle.hide();
                }
            });

            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if !is_backend_running().await {
                    log::info!("Backend not running, starting...");
                    if let Err(e) = start_sidecar_backend(&app_handle) {
                        log::warn!("Sidecar backend failed: {}, trying development backend", e);
                        start_development_backend();
                    }
                } else {
                    log::info!("Backend already running");
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            start_recording,
            stop_recording,
            toggle_recording,
            get_recording_status,
            check_backend_status,
            show_window,
            hide_window,
            get_shortcut,
            set_shortcut,
            read_vault,
            ask_brain,
            list_initiatives,
            initiative_status,
            run_dream,
            run_journal
        ])
        .run(tauri::generate_context!())
        .map_err(|e| {
            eprintln!("Failed to run Tauri application: {}", e);
            e
        })
        .expect("error while running tauri application");
}
