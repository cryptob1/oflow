/// Custom error types for the application.
use thiserror::Error;

/// Errors that can occur when communicating with the Python backend.
#[derive(Error, Debug)]
pub enum SocketError {
    /// Failed to connect to the Unix socket.
    #[error("Failed to connect to backend: {0}")]
    ConnectionFailed(String),

    /// Failed to send command to the backend.
    #[error("Failed to send command: {0}")]
    SendFailed(String),

    /// Failed to read response from the backend.
    #[error("Failed to read response: {0}")]
    ReadFailed(String),

    /// Backend is not running or not responding.
    #[error("Backend is not running or not responding")]
    BackendNotRunning,

    /// Invalid command format.
    #[error("Invalid command: {0}")]
    InvalidCommand(String),
}

impl From<std::io::Error> for SocketError {
    fn from(err: std::io::Error) -> Self {
        SocketError::ConnectionFailed(err.to_string())
    }
}

