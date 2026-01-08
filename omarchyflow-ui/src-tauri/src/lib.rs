/// Main Tauri application entry point.
mod error;
mod socket_client;

use socket_client::{is_backend_running, send_command};
use tauri::{
    AppHandle, Manager, State, Window,
};
use tauri_plugin_shell::ShellExt;

/// State to track recording status.
#[derive(Default)]
struct RecordingState {
    is_recording: bool,
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
    state: State<'_, tauri::async_runtime::Mutex<RecordingState>>,
) -> Result<bool, String> {
    let mut recording_state = state.lock().await;
    
    let command = if recording_state.is_recording {
        "stop"
    } else {
        "start"
    };

    send_command(command)
        .await
        .map_err(|e| format!("Failed to toggle recording: {}", e))?;

    recording_state.is_recording = !recording_state.is_recording;
    Ok(recording_state.is_recording)
}

/// Gets the current recording status.
///
/// # Returns
///
/// Returns `true` if recording is active, `false` otherwise.
/// Note: This returns the local state, not the actual backend state.
#[tauri::command]
async fn get_recording_status(
    state: State<'_, tauri::async_runtime::Mutex<RecordingState>>,
) -> Result<bool, String> {
    let recording_state = state.lock().await;
    Ok(recording_state.is_recording)
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

/// Sets up the system tray icon.
fn setup_tray(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    use tauri::tray::{TrayIconBuilder, TrayIconEvent};

    TrayIconBuilder::new()
        .tooltip("OmarchyFlow")
        .icon(
            app.default_window_icon()
                .ok_or("Failed to get default icon")?
                .clone(),
        )
        .on_tray_icon_event(move |tray, event| {
            if let TrayIconEvent::Click {
                button: tauri::tray::MouseButton::Left,
                button_state: _,
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

    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .setup(|app| {
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

            // Initialize recording state
            app.manage(tauri::async_runtime::Mutex::new(RecordingState::default()));

            // Setup system tray
            setup_tray(app.handle())?;

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

            // Spawn Python backend as sidecar
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                match handle
                    .shell()
                    .sidecar("omarchyflow-backend")
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

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            start_recording,
            stop_recording,
            toggle_recording,
            get_recording_status,
            check_backend_status,
            show_window,
            hide_window
        ])
        .run(tauri::generate_context!())
        .map_err(|e| {
            eprintln!("Failed to run Tauri application: {}", e);
            e
        })
        .expect("error while running tauri application");
}
