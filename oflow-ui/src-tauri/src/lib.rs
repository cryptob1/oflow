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

// Only import shell for release builds (sidecar)
#[cfg(not(debug_assertions))]
use tauri_plugin_shell::ShellExt;

/// Default shortcut key
const DEFAULT_SHORTCUT: &str = "Super+I";

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

/// Settings structure matching the JSON file.
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
    shortcut: Option<String>,
}

/// Reads settings from the settings file.
fn read_settings() -> Settings {
    let home = dirs::home_dir().unwrap_or_default();
    let settings_path = home.join(SETTINGS_PATH);

    if let Ok(contents) = std::fs::read_to_string(&settings_path) {
        serde_json::from_str(&contents).unwrap_or_default()
    } else {
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
    state: State<'_, Mutex<AppState>>,
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
    state: State<'_, Mutex<AppState>>,
) -> Result<bool, String> {
    let app_state = state.lock().await;
    Ok(app_state.is_recording)
}

/// Gets the current global shortcut.
#[tauri::command]
async fn get_shortcut(
    state: State<'_, Mutex<AppState>>,
) -> Result<String, String> {
    let app_state = state.lock().await;
    Ok(app_state.current_shortcut.clone())
}

/// Sets the keyboard shortcut by updating Hyprland bindings.
/// On Wayland/Hyprland, we can't use Tauri global shortcuts, so we update the WM config directly.
#[tauri::command]
async fn set_shortcut(
    state: State<'_, Mutex<AppState>>,
    shortcut: String,
) -> Result<(), String> {
    log::info!("set_shortcut called with: {}", shortcut);

    // Update Hyprland bindings file
    let home = dirs::home_dir().ok_or("Could not find home directory")?;
    let bindings_file = home.join(".config/hypr/bindings.conf");

    if !bindings_file.exists() {
        return Err("Hyprland bindings file not found".to_string());
    }

    // Read current bindings
    let contents = std::fs::read_to_string(&bindings_file)
        .map_err(|e| format!("Failed to read bindings file: {}", e))?;

    // Remove old oflow bindings
    let mut new_lines: Vec<&str> = Vec::new();
    let mut skip_next = false;
    for line in contents.lines() {
        if line.contains("# Oflow voice dictation") {
            skip_next = true;
            continue;
        }
        if skip_next && (line.starts_with("bind") && line.contains("oflow")) {
            continue;
        }
        skip_next = false;
        new_lines.push(line);
    }

    // Convert shortcut format (e.g., "Super+I" -> "SUPER, I")
    let hypr_shortcut = shortcut
        .replace("Super+", "SUPER, ")
        .replace("Ctrl+", "CTRL ")
        .replace("Shift+", "SHIFT ")
        .replace("Alt+", "ALT ");

    // Get the oflow script path
    let oflow_dir = std::env::current_dir()
        .map_err(|e| format!("Failed to get current dir: {}", e))?;
    let python_path = oflow_dir.join(".venv/bin/python");
    let script_path = oflow_dir.join("oflow.py");

    // Add new bindings
    let new_bindings = format!(
        "\n# Oflow voice dictation (push-to-talk: hold {} to record, release to stop)\nbind = {}, exec, {} {} start\nbindr = {}, exec, {} {} stop",
        shortcut,
        hypr_shortcut,
        python_path.display(),
        script_path.display(),
        hypr_shortcut,
        python_path.display(),
        script_path.display()
    );

    let mut final_content = new_lines.join("\n");
    // Remove trailing empty lines
    while final_content.ends_with("\n\n") {
        final_content.pop();
    }
    final_content.push_str(&new_bindings);
    final_content.push('\n');

    // Write updated bindings
    std::fs::write(&bindings_file, &final_content)
        .map_err(|e| format!("Failed to write bindings file: {}", e))?;

    // Reload Hyprland
    let _ = std::process::Command::new("hyprctl")
        .arg("reload")
        .output();

    // Update state
    let mut app_state = state.lock().await;
    app_state.current_shortcut = shortcut.clone();

    // Save to settings file
    let mut settings = read_settings();
    settings.shortcut = Some(shortcut.clone());
    write_settings(&settings)?;

    log::info!("Shortcut successfully updated to: {}", shortcut);
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

            // Show window on startup (user can hide it if they want)
            window.show().map_err(|e| {
                format!("Failed to show window on startup: {}", e)
            })?;

            // Try to bring window to front
            window.set_focus().map_err(|e| {
                format!("Failed to focus window on startup: {}", e)
            })?;

            log::info!("Window shown and focused");

            // Handle window close - minimize to tray instead of quitting
            let window_handle = window.clone();
            window.on_window_event(move |event| {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window_handle.hide();
                }
            });

            // Spawn Python backend as sidecar (only in release mode)
            // In dev mode, the backend is started by scripts/dev.sh
            #[cfg(not(debug_assertions))]
            {
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    match handle
                        .shell()
                        .sidecar("oflow-backend")
                        .map_err(|e| format!("Failed to create sidecar: {}", e))
                    {
                        Ok(sidecar) => {
                            match sidecar.spawn() {
                                Ok((mut rx, _child)) => {
                                    while let Some(event) = rx.recv().await {
                                        if let tauri_plugin_shell::process::CommandEvent::Stdout(line) =
                                            event
                                        {
                                            if let Ok(text) = String::from_utf8(line) {
                                                log::info!("Backend: {}", text);
                                            }
                                        }
                                    }
                                }
                                Err(e) => {
                                    log::error!("Failed to spawn backend: {}", e);
                                }
                            }
                        }
                        Err(e) => {
                            log::error!("{}", e);
                        }
                    }
                });
            }

            #[cfg(debug_assertions)]
            log::info!("Dev mode: backend should be started by scripts/dev.sh");

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
            set_shortcut
        ])
        .run(tauri::generate_context!())
        .map_err(|e| {
            eprintln!("Failed to run Tauri application: {}", e);
            e
        })
        .expect("error while running tauri application");
}
