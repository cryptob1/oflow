/// Unix socket client for communicating with the Python backend.
use crate::error::SocketError;
use std::path::Path;
use tokio::io::AsyncWriteExt;
use tokio::net::UnixStream;
use tokio::time::{timeout, Duration};

/// Path to the Unix socket used for communication with the Python backend.
const SOCKET_PATH: &str = "/tmp/voice-dictation.sock";
/// Timeout for socket operations in seconds.
const SOCKET_TIMEOUT: Duration = Duration::from_secs(2);

/// Sends a command to the Python backend via Unix socket.
///
/// # Arguments
///
/// * `command` - The command to send ("start", "stop", or "toggle")
///
/// # Returns
///
/// Returns `Ok(())` if the command was sent successfully, or an error if communication failed.
///
/// # Errors
///
/// Returns `SocketError` if:
/// - The socket cannot be connected to
/// - The command cannot be sent
/// - The backend is not responding
pub async fn send_command(command: &str) -> Result<(), SocketError> {
    // Validate command
    if !matches!(command, "start" | "stop" | "toggle") {
        return Err(SocketError::InvalidCommand(format!(
            "Invalid command: {}. Must be 'start', 'stop', or 'toggle'",
            command
        )));
    }

    // Check if socket exists
    if !Path::new(SOCKET_PATH).exists() {
        return Err(SocketError::BackendNotRunning);
    }

    // Connect to socket with timeout
    let mut stream = timeout(SOCKET_TIMEOUT, UnixStream::connect(SOCKET_PATH))
        .await
        .map_err(|_| SocketError::ConnectionFailed("Connection timeout".to_string()))?
        .map_err(|e| SocketError::ConnectionFailed(e.to_string()))?;

    // Send command with timeout
    let command_bytes = command.as_bytes();
    timeout(SOCKET_TIMEOUT, stream.write_all(command_bytes))
        .await
        .map_err(|_| SocketError::SendFailed("Send timeout".to_string()))?
        .map_err(|e| SocketError::SendFailed(e.to_string()))?;

    // Flush to ensure data is sent
    timeout(SOCKET_TIMEOUT, stream.flush())
        .await
        .map_err(|_| SocketError::SendFailed("Flush timeout".to_string()))?
        .map_err(|e| SocketError::SendFailed(e.to_string()))?;

    // Close the connection (backend doesn't send response)
    drop(stream);

    Ok(())
}

/// Checks if the backend is running by attempting to connect to the socket.
pub async fn is_backend_running() -> bool {
    if !Path::new(SOCKET_PATH).exists() {
        return false;
    }
    
    // Actually try to connect - stale socket files can exist after crashes
    timeout(Duration::from_millis(500), UnixStream::connect(SOCKET_PATH))
        .await
        .map(|r| r.is_ok())
        .unwrap_or(false)
}

