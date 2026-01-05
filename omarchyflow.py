#!/usr/bin/env python3 -u
"""
OmarchyFlow - Voice dictation for Omarchy (Hyprland/Wayland)

A WhisperFlow/Willow alternative supporting OpenAI & Gemini direct audio APIs.
Uses Unix domain sockets for IPC with Hyprland keybindings.
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

import base64
import io
import logging
import os
import queue
import signal
import socket
import subprocess
import threading
import time
import wave
from typing import TYPE_CHECKING

import httpx
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv

if TYPE_CHECKING:
    from numpy.typing import NDArray

# Load environment variables
load_dotenv()

# =============================================================================
# Constants
# =============================================================================
SOCKET_PATH = "/tmp/voice-dictation.sock"
DEBUG_AUDIO_PATH = "/tmp/debug_audio.wav"

# Audio constants
DEFAULT_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_DTYPE = np.float32
INT16_MAX = 32767
AUDIO_SAMPLE_WIDTH = 2  # 16-bit = 2 bytes
NORMALIZATION_TARGET = 0.95

# API constants
API_TIMEOUT = 15.0
CLEANUP_TIMEOUT = 10.0

# Notification durations (ms)
NOTIFY_SHORT = 1000
NOTIFY_MEDIUM = 1500
NOTIFY_LONG = 2000

# =============================================================================
# Configuration
# =============================================================================
OPENROUTER_API_KEY: str | None = os.getenv("OPENROUTER_API_KEY")
OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
SAMPLE_RATE: int = int(os.getenv("SAMPLE_RATE", str(DEFAULT_SAMPLE_RATE)))
DEBUG_MODE: bool = os.getenv("DEBUG_MODE", "false").lower() == "true"

# Mode flags
USE_AUDIO_DIRECT: bool = os.getenv("USE_AUDIO_DIRECT", "false").lower() == "true"
USE_OPENAI_DIRECT: bool = os.getenv("USE_OPENAI_DIRECT", "false").lower() == "true"
USE_OPENROUTER_GEMINI: bool = os.getenv("USE_OPENROUTER_GEMINI", "false").lower() == "true"

# =============================================================================
# Logging setup
# =============================================================================
logging.basicConfig(
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# Configuration Validation
# =============================================================================
def validate_configuration() -> None:
    """Validate configuration and API keys.

    Raises:
        ValueError: If configuration is invalid or required API keys are missing.
    """
    modes_enabled = sum([USE_OPENAI_DIRECT, USE_OPENROUTER_GEMINI, USE_AUDIO_DIRECT])

    if modes_enabled > 1:
        raise ValueError(
            "Multiple transcription modes enabled. "
            "Set only ONE of: USE_OPENAI_DIRECT, USE_OPENROUTER_GEMINI, USE_AUDIO_DIRECT"
        )

    if USE_OPENAI_DIRECT and not OPENAI_API_KEY:
        raise ValueError("USE_OPENAI_DIRECT=true but OPENAI_API_KEY not set")

    if USE_OPENROUTER_GEMINI and not OPENROUTER_API_KEY:
        raise ValueError("USE_OPENROUTER_GEMINI=true but OPENROUTER_API_KEY not set")

    if USE_AUDIO_DIRECT and not OPENROUTER_API_KEY:
        raise ValueError("USE_AUDIO_DIRECT=true but OPENROUTER_API_KEY not set")

    # If no mode is enabled, we need Whisper (local) - check for OpenRouter for cleanup
    if modes_enabled == 0 and not OPENROUTER_API_KEY:
        logger.warning(
            "No API mode enabled and OPENROUTER_API_KEY not set. "
            "Using local Whisper without LLM cleanup."
        )


# =============================================================================
# Preambles to strip from transcriptions
# =============================================================================
TRANSCRIPTION_PREAMBLES = [
    "here is the transcription:",
    "following your rules,",
    "the transcription is:",
    "following the formatting rules,",
    "removing all filler words",
    "sure. here is the transcribed text:",
    "the following is the cleaned transcription:",
    "applying the rules,",
]


def notify(message: str, duration_ms: int = NOTIFY_MEDIUM) -> None:
    """Send a desktop notification.

    Args:
        message: The notification message.
        duration_ms: Duration in milliseconds.
    """
    subprocess.run(
        ["notify-send", "-t", str(duration_ms), message],
        stderr=subprocess.DEVNULL,
    )


class VoiceDictation:
    """Main voice dictation server class.

    Handles audio recording, transcription via various APIs, and text injection.
    Uses Unix domain sockets for IPC with Hyprland keybindings.
    """

    def __init__(self) -> None:
        """Initialize the voice dictation server.

        Raises:
            RuntimeError: If socket initialization fails.
        """
        self._running = True
        self._init_model()
        self._init_audio()
        self._init_socket()
        self._setup_signal_handlers()

        logger.info("Voice Dictation Server Ready")

    def _init_model(self) -> None:
        """Initialize the transcription model based on configuration."""
        self.model = None

        if USE_OPENAI_DIRECT:
            logger.info("Using OpenAI Direct API for audio transcription")
        elif USE_OPENROUTER_GEMINI:
            logger.info("Using OpenRouter Gemini 2.5 Flash for audio transcription")
        elif USE_AUDIO_DIRECT:
            logger.info("Using audio-direct mode (Voxtral)")
        else:
            # Import here to avoid loading Whisper if not needed
            from faster_whisper import WhisperModel

            logger.info("Loading local Whisper model...")
            self.model = WhisperModel("small", device="cpu", compute_type="int8")
            logger.info("Whisper model loaded")

    def _init_audio(self) -> None:
        """Initialize audio recording components."""
        self.sample_rate = SAMPLE_RATE
        self.is_recording = False
        self.audio_queue: queue.Queue[NDArray[np.float32]] = queue.Queue()
        self.audio_data: list[NDArray[np.float32]] = []

        # Set microphone volume
        subprocess.run(
            ["pactl", "set-source-volume", "@DEFAULT_SOURCE@", "150%"],
            stderr=subprocess.DEVNULL,
        )

        # Initialize audio stream
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=AUDIO_CHANNELS,
            dtype=AUDIO_DTYPE,
            callback=self._audio_callback,
        )
        self.stream.start()

    def _audio_callback(
        self, indata: NDArray[np.float32], frames: int, time_info: object, status: object
    ) -> None:
        """Audio stream callback - queues audio data when recording.

        Args:
            indata: Input audio data.
            frames: Number of frames.
            time_info: Time information (unused).
            status: Stream status (unused).
        """
        if self.is_recording:
            self.audio_queue.put(indata.copy())

    def _init_socket(self) -> None:
        """Initialize Unix domain socket for IPC.

        Raises:
            RuntimeError: If socket initialization fails.
        """
        self.socket: socket.socket | None = None

        try:
            # Clean up existing socket
            if os.path.exists(SOCKET_PATH):
                os.remove(SOCKET_PATH)

            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.socket.bind(SOCKET_PATH)
            self.socket.listen(1)
            os.chmod(SOCKET_PATH, 0o666)
        except OSError as e:
            self._cleanup()
            raise RuntimeError(f"Failed to initialize socket: {e}") from e

    def _setup_signal_handlers(self) -> None:
        """Setup graceful shutdown signal handlers."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum: int, frame: object) -> None:
        """Handle shutdown signals gracefully.

        Args:
            signum: Signal number.
            frame: Current stack frame (unused).
        """
        logger.info(f"Received signal {signum}, shutting down...")
        self._running = False

    def _cleanup(self) -> None:
        """Clean up resources on shutdown."""
        logger.debug("Cleaning up resources...")

        if hasattr(self, "stream"):
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.debug(f"Error closing audio stream: {e}")

        if hasattr(self, "socket") and self.socket:
            try:
                self.socket.close()
            except Exception as e:
                logger.debug(f"Error closing socket: {e}")

        if os.path.exists(SOCKET_PATH):
            try:
                os.remove(SOCKET_PATH)
            except Exception as e:
                logger.debug(f"Error removing socket file: {e}")

        # Clean up debug audio file
        if os.path.exists(DEBUG_AUDIO_PATH):
            try:
                os.remove(DEBUG_AUDIO_PATH)
            except Exception as e:
                logger.debug(f"Error removing debug audio: {e}")

    def toggle(self) -> None:
        """Toggle recording state."""
        if self.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        """Start audio recording."""
        self.is_recording = True
        self.audio_data = []
        notify("🎤 Recording...", NOTIFY_SHORT)
        logger.info("Recording started")

    def _stop_recording(self) -> None:
        """Stop recording and initiate transcription."""
        self.is_recording = False
        notify("⏹️ Stopping...", NOTIFY_SHORT)

        # Small delay to capture trailing audio
        time.sleep(0.1)

        # Drain audio queue
        while not self.audio_queue.empty():
            self.audio_data.append(self.audio_queue.get())

        if self.audio_data:
            threading.Thread(target=self._transcribe, daemon=True).start()

        logger.info("Recording stopped")

    def _audio_to_base64(self, audio_array: NDArray[np.float32]) -> str:
        """Convert audio array to base64-encoded WAV.

        Args:
            audio_array: Normalized float32 audio array.

        Returns:
            Base64-encoded WAV data.
        """
        audio_int16 = (audio_array * INT16_MAX).astype(np.int16)

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(AUDIO_CHANNELS)
            wav_file.setsampwidth(AUDIO_SAMPLE_WIDTH)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(audio_int16.tobytes())

        buffer.seek(0)
        return base64.b64encode(buffer.read()).decode("utf-8")

    def _normalize_audio(self, audio_array: NDArray[np.float32]) -> NDArray[np.float32]:
        """Normalize audio to target peak level.

        Args:
            audio_array: Input audio array.

        Returns:
            Normalized audio array.
        """
        max_val = np.max(np.abs(audio_array))
        if max_val > 0:
            return audio_array / max_val * NORMALIZATION_TARGET
        return audio_array

    def _save_audio_debug(self, audio_array: NDArray[np.float32]) -> None:
        """Save audio to file for debugging.

        Args:
            audio_array: Audio array to save.
        """
        if not DEBUG_MODE:
            return

        audio_int16 = (audio_array * INT16_MAX).astype(np.int16)

        with wave.open(DEBUG_AUDIO_PATH, "wb") as wav_file:
            wav_file.setnchannels(AUDIO_CHANNELS)
            wav_file.setsampwidth(AUDIO_SAMPLE_WIDTH)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(audio_int16.tobytes())

        logger.debug(f"Saved debug audio to {DEBUG_AUDIO_PATH}")

    def _strip_preambles(self, text: str) -> str:
        """Strip common LLM preambles from transcription.

        Args:
            text: Raw transcription text.

        Returns:
            Cleaned text without preambles.
        """
        text = text.strip()
        text_lower = text.lower()

        for preamble in TRANSCRIPTION_PREAMBLES:
            if text_lower.startswith(preamble):
                text = text[len(preamble) :].strip()
                text_lower = text.lower()

        return text.lstrip("\n").strip()

    def _transcribe_with_openai_direct(
        self, audio_array: NDArray[np.float32]
    ) -> str | None:
        """Transcribe audio using OpenAI's direct audio API.

        Args:
            audio_array: Normalized audio array.

        Returns:
            Transcribed text or None on failure.
        """
        try:
            audio_base64 = self._audio_to_base64(audio_array)

            response = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-audio-preview",
                    "modalities": ["text"],
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Transcribe."},
                                {
                                    "type": "input_audio",
                                    "input_audio": {"data": audio_base64, "format": "wav"},
                                },
                            ],
                        }
                    ],
                },
                timeout=API_TIMEOUT,
            )

            if response.status_code == 200:
                result = response.json()
                text = result["choices"][0]["message"]["content"]
                if text:
                    return self._strip_preambles(text)
                logger.warning("OpenAI returned empty content")
                return None

            logger.error(f"OpenAI API failed: {response.status_code} - {response.text}")
            return None

        except httpx.TimeoutException:
            logger.error("OpenAI API timeout")
            return None
        except Exception as e:
            logger.error(f"OpenAI Direct error: {e}")
            return None

    def _transcribe_with_gemini(self, audio_array: NDArray[np.float32]) -> str | None:
        """Transcribe audio using Gemini via OpenRouter.

        Args:
            audio_array: Normalized audio array.

        Returns:
            Transcribed text or None on failure.
        """
        try:
            audio_base64 = self._audio_to_base64(audio_array)

            response = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "google/gemini-2.5-flash",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Transcribe."},
                                {
                                    "type": "input_audio",
                                    "input_audio": {"data": audio_base64, "format": "wav"},
                                },
                            ],
                        }
                    ],
                },
                timeout=API_TIMEOUT,
            )

            if response.status_code == 200:
                result = response.json()
                text = result["choices"][0]["message"]["content"]
                if text:
                    return text.strip()
                logger.warning("Gemini returned empty content")
                return None

            logger.error(f"Gemini API failed: {response.status_code} - {response.text}")
            return None

        except httpx.TimeoutException:
            logger.error("Gemini API timeout")
            return None
        except Exception as e:
            logger.error(f"Gemini error: {e}")
            return None

    def _transcribe_with_audio_llm(self, audio_array: NDArray[np.float32]) -> str | None:
        """Transcribe audio using Voxtral via OpenRouter.

        Args:
            audio_array: Normalized audio array.

        Returns:
            Transcribed text or None on failure.
        """
        try:
            duration = len(audio_array) / self.sample_rate
            logger.debug(f"Audio duration: {duration:.2f}s")

            audio_base64 = self._audio_to_base64(audio_array)
            logger.debug(f"Base64 length: {len(audio_base64)}")

            response = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "mistralai/voxtral-small-24b-2507",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a transcription system. Output ONLY the transcribed "
                                "text. No preambles. No commentary. No explanations. "
                                "Just the cleaned text."
                            ),
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Transcribe the following audio clip. Output only the "
                                        "spoken words with proper punctuation and capitalization. "
                                        "Remove filler words like um, uh, and like."
                                    ),
                                },
                                {
                                    "type": "input_audio",
                                    "inputAudio": {"data": audio_base64, "format": "wav"},
                                },
                            ],
                        },
                    ],
                },
                timeout=API_TIMEOUT,
            )

            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()

            logger.error(f"Audio LLM failed: {response.status_code} - {response.text}")
            return None

        except httpx.TimeoutException:
            logger.error("Audio LLM timeout")
            return None
        except Exception as e:
            logger.error(f"Audio LLM error: {e}")
            return None

    def _clean_with_llm(self, raw_text: str) -> str:
        """Clean raw transcription using LLM.

        Args:
            raw_text: Raw transcription from Whisper.

        Returns:
            Cleaned and formatted text.
        """
        if not OPENROUTER_API_KEY:
            return raw_text

        try:
            response = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "google/gemini-2.5-flash",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a transcription system. Output ONLY the transcribed "
                                "text. No preambles. No commentary. No explanations. "
                                "Just the cleaned text."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"""This is raw voice-to-text transcription that needs cleanup and formatting.

Instructions:
- Fix grammar, punctuation, and capitalization
- Remove filler words (um, uh, like, you know, etc.)
- If the speaker mentions multiple points using words like "first", "second", "third" OR "one", "two", "three", format them as a numbered list using "1.", "2.", "3." format
- If there are bullet points or lists mentioned, format them properly
- Keep the meaning intact
- Return ONLY the cleaned and formatted text, nothing else

Raw transcription: {raw_text}""",
                        },
                    ],
                },
                timeout=CLEANUP_TIMEOUT,
            )

            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
            return raw_text

        except Exception as e:
            logger.warning(f"LLM cleanup failed: {e}")
            return raw_text

    def _type_text(self, text: str) -> None:
        """Type text into the active window using available tools.

        Tries wtype (Wayland), then xdotool (X11), then clipboard as fallback.

        Args:
            text: Text to type.
        """
        try:
            subprocess.run(["wtype", text], check=True, stderr=subprocess.DEVNULL)
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        try:
            subprocess.run(
                ["xdotool", "type", "--clearmodifiers", text],
                check=True,
                stderr=subprocess.DEVNULL,
            )
            return
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # Fallback to clipboard
        subprocess.run(["wl-copy", text], stderr=subprocess.DEVNULL)
        logger.info("Text copied to clipboard (wtype/xdotool unavailable)")

    def _transcribe(self) -> None:
        """Transcribe recorded audio and type the result."""
        audio = np.concatenate(self.audio_data, axis=0).flatten()
        audio = self._normalize_audio(audio)

        self._save_audio_debug(audio)

        text: str | None = None

        if USE_OPENAI_DIRECT:
            notify("🎙️ Transcribing with OpenAI...", NOTIFY_MEDIUM)
            text = self._transcribe_with_openai_direct(audio)
        elif USE_OPENROUTER_GEMINI:
            notify("🎵 Transcribing with Gemini...", NOTIFY_MEDIUM)
            text = self._transcribe_with_gemini(audio)
        elif USE_AUDIO_DIRECT:
            notify("🎵 Processing audio with LLM...", NOTIFY_MEDIUM)
            text = self._transcribe_with_audio_llm(audio)
        else:
            # Local Whisper
            if self.model is None:
                logger.error("Whisper model not loaded")
                notify("❌ Whisper model not loaded", NOTIFY_LONG)
                return

            segments, _ = self.model.transcribe(
                audio, language="en", beam_size=5, vad_filter=True
            )
            raw_text = " ".join([s.text for s in segments]).strip()

            if not raw_text:
                logger.info("No speech detected")
                return

            notify("🧹 Cleaning text...", NOTIFY_MEDIUM)
            text = self._clean_with_llm(raw_text)

        if not text:
            notify("❌ Transcription failed", NOTIFY_LONG)
            return

        self._type_text(text)

        # Truncate for notification
        display_text = text[:50] + "..." if len(text) > 50 else text
        notify(f"✓ {display_text}", NOTIFY_LONG)
        logger.info(f"Transcribed: {text}")

    def run(self) -> None:
        """Main server loop - accept socket connections and handle commands."""
        if self.socket is None:
            raise RuntimeError("Socket not initialized")

        try:
            while self._running:
                try:
                    # Use timeout to allow checking _running flag
                    self.socket.settimeout(1.0)
                    conn, _ = self.socket.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if not self._running:
                        break
                    raise

                try:
                    cmd = conn.recv(1024).decode().strip()
                    logger.debug(f"Received command: {cmd}")

                    if cmd == "start":
                        if not self.is_recording:
                            self._start_recording()
                    elif cmd == "stop":
                        if self.is_recording:
                            self._stop_recording()
                    elif cmd == "toggle":
                        self.toggle()
                    else:
                        logger.warning(f"Unknown command: {cmd}")
                finally:
                    conn.close()

        finally:
            self._cleanup()


def main() -> None:
    """Main entry point."""
    if len(sys.argv) > 1:
        # Client mode - send command to server
        cmd = sys.argv[1]
        if cmd not in ("start", "stop", "toggle"):
            print(f"Unknown command: {cmd}")
            print("Usage: omarchyflow [start|stop|toggle]")
            sys.exit(1)

        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(SOCKET_PATH)
            s.send(cmd.encode())
            s.close()
        except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
            print(f"Server not running. Start with: ./omarchyflow")
            logger.debug(f"Connection error: {e}")
            sys.exit(1)
    else:
        # Server mode
        validate_configuration()
        VoiceDictation().run()


if __name__ == "__main__":
    main()
