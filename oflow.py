#!/usr/bin/env python3
"""
Oflow - Voice dictation for Hyprland/Wayland.

Records audio via global hotkey (Super+D toggle mode), transcribes using
Groq Whisper (or OpenAI), optionally cleans with Llama 3.1 (or GPT-4o-mini),
and types the result into the active window using wtype.

Architecture:
  Audio Recording → Validation → Whisper STT → LLM Cleanup → wtype Output

Features:
  - Global hotkey support (Super+D toggle mode)
  - Audio validation before API calls
  - Automatic grammar and punctuation correction
  - Hallucination filtering
  - Waybar integration for status display
  - Audio feedback tones
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import httpx
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv

load_dotenv()

_debug_mode = os.getenv("DEBUG_MODE", "false").lower() == "true"
logging.basicConfig(
    level=logging.DEBUG if _debug_mode else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration Constants
# ============================================================================

# Unix socket path for IPC with Tauri frontend
SOCKET_PATH = "/tmp/voice-dictation.sock"

# PID file to ensure only one backend runs at a time
PID_FILE = "/tmp/oflow.pid"

# Audio configuration
SAMPLE_RATE = 16000  # 16kHz sample rate (Whisper requirement)
AUDIO_CHANNELS = 1  # Mono audio
NORMALIZATION_TARGET = 0.95  # Normalize audio to 95% of max amplitude
MIN_AUDIO_DURATION_SECONDS = 0.5  # Minimum recording duration
MIN_AUDIO_AMPLITUDE = 0.02  # Minimum amplitude to detect speech

# API configuration
API_TIMEOUT_SECONDS = 30.0  # Timeout for API requests

# File paths
TRANSCRIPTS_FILE = Path.home() / ".oflow" / "transcripts.jsonl"
SETTINGS_FILE = Path.home() / ".oflow" / "settings.json"

# Waybar state file (in XDG_RUNTIME_DIR for fast access)
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "oflow"
STATE_FILE = RUNTIME_DIR / "state"

# API key format validation
OPENAI_API_KEY_PATTERN = re.compile(r"^sk-")
GROQ_API_KEY_PATTERN = re.compile(r"^gsk_")

# API endpoints
OPENAI_WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

# ============================================================================
# Environment Variables and Configuration
# ============================================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

# Default settings (can be overridden by settings.json)
DEFAULT_ENABLE_CLEANUP = os.getenv("ENABLE_CLEANUP", "true").lower() == "true"
DEFAULT_PROVIDER = os.getenv("PROVIDER", "groq")  # Default to Groq (faster)


def ensure_data_dir() -> None:
    """Ensure ~/.oflow directory and default files exist."""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Create default settings file if it doesn't exist
    if not SETTINGS_FILE.exists():
        default_settings = {
            "enableCleanup": DEFAULT_ENABLE_CLEANUP,
            "provider": DEFAULT_PROVIDER,
            "iconTheme": "nerd-font",
        }
        with open(SETTINGS_FILE, "w") as f:
            json.dump(default_settings, f, indent=2)

    # Create empty transcripts file if it doesn't exist
    if not TRANSCRIPTS_FILE.exists():
        TRANSCRIPTS_FILE.touch()


def load_settings() -> dict:
    """
    Load settings from ~/.oflow/settings.json.
    Falls back to environment variable defaults if file doesn't exist.
    """
    ensure_data_dir()

    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE) as f:
                settings = json.load(f)

                # Validate and sanitize API keys
                groq_key = settings.get("groqApiKey") or GROQ_API_KEY
                openai_key = settings.get("openaiApiKey") or OPENAI_API_KEY

                # Check for duplicated API keys (common copy-paste error)
                if groq_key and len(groq_key) > 60:
                    logger.warning(
                        f"⚠️  Groq API key looks duplicated (length: {len(groq_key)}). "
                        "Expected ~56 chars. Please check your settings."
                    )
                    # Try to fix by taking first half if it looks like a duplicate
                    if len(groq_key) == 112 and groq_key[:56] == groq_key[56:]:
                        logger.info("Auto-fixing duplicated API key...")
                        groq_key = groq_key[:56]

                if openai_key and len(openai_key) > 100:
                    logger.warning(
                        f"⚠️  OpenAI API key looks duplicated (length: {len(openai_key)}). "
                        "Please check your settings."
                    )

                return {
                    "enableCleanup": settings.get("enableCleanup", DEFAULT_ENABLE_CLEANUP),
                    "openaiApiKey": openai_key,
                    "groqApiKey": groq_key,
                    "provider": settings.get("provider", DEFAULT_PROVIDER),
                    "audioFeedbackTheme": settings.get("audioFeedbackTheme", "default"),
                    "audioFeedbackVolume": settings.get("audioFeedbackVolume", 0.3),
                    "iconTheme": settings.get("iconTheme", "nerd-font"),
                    "enableSpokenPunctuation": settings.get("enableSpokenPunctuation", False),
                    "wordReplacements": settings.get("wordReplacements", {}),
                }
    except json.JSONDecodeError as e:
        logger.error(f"❌ Settings file is invalid JSON: {e}")
        logger.error(f"   Fix or delete: {SETTINGS_FILE}")
    except Exception as e:
        logger.warning(f"Failed to load settings from {SETTINGS_FILE}: {e}")

    return {
        "enableCleanup": DEFAULT_ENABLE_CLEANUP,
        "openaiApiKey": OPENAI_API_KEY,
        "groqApiKey": GROQ_API_KEY,
        "provider": DEFAULT_PROVIDER,
        "audioFeedbackTheme": "default",
        "audioFeedbackVolume": 0.3,
        "iconTheme": "nerd-font",
        "enableSpokenPunctuation": False,
        "wordReplacements": {},
    }


# ============================================================================
# Process Management
# ============================================================================


def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def acquire_pid_lock() -> bool:
    """
    Acquire exclusive lock via PID file.
    Returns True if lock acquired, False if another instance is running.
    """
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())

            if is_process_running(old_pid):
                logger.info(f"Backend already running (PID {old_pid})")
                return False
            else:
                logger.info(f"Stale PID file found (PID {old_pid} not running), cleaning up")
                os.remove(PID_FILE)
                if os.path.exists(SOCKET_PATH):
                    os.remove(SOCKET_PATH)
        except (ValueError, IOError) as e:
            logger.warning(f"Error reading PID file: {e}, cleaning up")
            os.remove(PID_FILE)

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    return True


def release_pid_lock() -> None:
    """Release the PID file lock."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        logger.warning(f"Error removing PID file: {e}")


# ============================================================================
# Custom Exceptions
# ============================================================================


class OflowError(Exception):
    """Base exception for Oflow errors."""

    pass


class ConfigurationError(OflowError):
    """Raised when configuration is invalid."""

    pass


# ============================================================================
# Waybar State Manager
# ============================================================================


class WaybarState:
    """
    Manages Waybar status bar integration.
    Writes state to a file that Waybar can read via custom/oflow module.
    """

    ICON_THEMES = {
        "emoji": {"idle": "🎙️", "recording": "🎤", "transcribing": "⏳", "error": "❌"},
        "nerd-font": {
            "idle": "󰍬",  # nf-md-microphone
            "recording": "󰍮",  # nf-md-microphone_variant
            "transcribing": "󰦖",  # nf-md-text_to_speech
            "error": "󰍭",  # nf-md-microphone_off
        },
        "minimal": {"idle": "○", "recording": "●", "transcribing": "◐", "error": "×"},
        "text": {"idle": "[MIC]", "recording": "[REC]", "transcribing": "[...]", "error": "[ERR]"},
    }

    def __init__(self, theme: str = "nerd-font"):
        self.theme = theme
        self.icons = self.ICON_THEMES.get(theme, self.ICON_THEMES["minimal"])
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    def set_state(self, state: str, tooltip: str = ""):
        """Update Waybar state file."""
        icon = self.icons.get(state, self.icons["idle"])
        data = {
            "text": icon,
            "alt": state,
            "tooltip": tooltip or f"oflow: {state}",
            "class": state,
        }
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.debug(f"Failed to write waybar state: {e}")

    def idle(self):
        self.set_state("idle", "oflow ready")

    def recording(self):
        self.set_state("recording", "Recording...")

    def transcribing(self):
        self.set_state("transcribing", "Transcribing...")

    def error(self, msg: str = ""):
        self.set_state("error", msg or "Error occurred")


# ============================================================================
# Audio Feedback
# ============================================================================


class AudioFeedback:
    """Generates audio feedback tones for recording events."""

    def __init__(self, theme: str = "default", volume: float = 0.3):
        self.theme = theme
        self.volume = max(0.0, min(1.0, volume))

    def _generate_tone(self, frequency: float, duration_ms: int, fade_ms: int = 10) -> np.ndarray:
        """Generate a sine wave tone with fade in/out."""
        num_samples = int(SAMPLE_RATE * duration_ms / 1000)
        fade_samples = int(SAMPLE_RATE * fade_ms / 1000)

        t = np.linspace(0, duration_ms / 1000, num_samples, False)
        tone = np.sin(2 * np.pi * frequency * t)

        envelope = np.ones(num_samples)
        if fade_samples > 0 and num_samples > fade_samples * 2:
            fade_in = np.linspace(0, 1, fade_samples)
            fade_out = np.linspace(1, 0, fade_samples)
            envelope[:fade_samples] = fade_in
            envelope[-fade_samples:] = fade_out

        return (tone * envelope * self.volume).astype(np.float32)

    def _generate_two_tone(self, freq1: float, freq2: float, duration_ms: int) -> np.ndarray:
        """Generate a two-tone sound."""
        half_duration = duration_ms // 2
        tone1 = self._generate_tone(freq1, half_duration, fade_ms=15)
        tone2 = self._generate_tone(freq2, half_duration, fade_ms=15)
        return np.concatenate([tone1, tone2])

    def _generate_click(self, duration_ms: int = 20) -> np.ndarray:
        """Generate a short click sound."""
        num_samples = int(SAMPLE_RATE * duration_ms / 1000)
        t = np.arange(num_samples)
        envelope = np.exp(-5.0 * t / num_samples)
        noise = np.where(t % 2 == 0, 1.0, -1.0)
        return (noise * envelope * self.volume * 0.5).astype(np.float32)

    def play_start(self):
        """Play recording start sound."""
        if self.theme == "silent":
            return
        try:
            if self.theme == "subtle":
                sound = self._generate_tone(1200, 40, fade_ms=8)
            elif self.theme == "mechanical":
                sound = self._generate_click(25)
            else:
                sound = self._generate_two_tone(440, 880, 120)
            sd.play(sound, SAMPLE_RATE, blocking=False)
        except Exception as e:
            logger.debug(f"Audio feedback failed: {e}")

    def play_stop(self):
        """Play recording stop sound."""
        if self.theme == "silent":
            return
        try:
            if self.theme == "subtle":
                sound = self._generate_tone(800, 40, fade_ms=8)
            elif self.theme == "mechanical":
                sound = self._generate_click(15)
            else:
                sound = self._generate_two_tone(880, 440, 120)
            sd.play(sound, SAMPLE_RATE, blocking=False)
        except Exception as e:
            logger.debug(f"Audio feedback failed: {e}")

    def play_error(self):
        """Play error sound."""
        if self.theme == "silent":
            return
        try:
            sound = self._generate_two_tone(300, 200, 150)
            sd.play(sound, SAMPLE_RATE, blocking=False)
        except Exception as e:
            logger.debug(f"Audio feedback failed: {e}")


# ============================================================================
# Text Processing (Spoken Punctuation & Replacements)
# ============================================================================


class TextProcessor:
    """Post-processes transcribed text with spoken punctuation and word replacements."""

    PUNCTUATION_MAP = [
        ("question mark", "?"),
        ("exclamation mark", "!"),
        ("exclamation point", "!"),
        ("open parenthesis", "("),
        ("close parenthesis", ")"),
        ("open paren", "("),
        ("close paren", ")"),
        ("open bracket", "["),
        ("close bracket", "]"),
        ("open brace", "{"),
        ("close brace", "}"),
        ("new paragraph", "\n\n"),
        ("new line", "\n"),
        ("forward slash", "/"),
        ("back slash", "\\"),
        ("period", "."),
        ("comma", ","),
        ("colon", ":"),
        ("semicolon", ";"),
        ("dash", "-"),
        ("hyphen", "-"),
        ("underscore", "_"),
        ("hash", "#"),
        ("hashtag", "#"),
        ("percent", "%"),
        ("ampersand", "&"),
        ("asterisk", "*"),
        ("plus", "+"),
        ("equals", "="),
        ("slash", "/"),
        ("pipe", "|"),
        ("tilde", "~"),
        ("backtick", "`"),
        ("tab", "\t"),
    ]

    def __init__(
        self, enable_punctuation: bool = False, replacements: dict[str, str] | None = None
    ):
        self.enable_punctuation = enable_punctuation
        self.replacements = replacements or {}

    def process(self, text: str) -> str:
        """Apply all text transformations."""
        if not text:
            return text

        result = text

        if self.enable_punctuation:
            result = self._apply_punctuation(result)

        if self.replacements:
            result = self._apply_replacements(result)

        return result

    def _apply_punctuation(self, text: str) -> str:
        """Convert spoken punctuation to symbols."""
        result = text

        for phrase, symbol in self.PUNCTUATION_MAP:
            pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
            result = pattern.sub(symbol, result)

        result = self._clean_punctuation_spacing(result)
        return result

    def _apply_replacements(self, text: str) -> str:
        """Apply custom word replacements."""
        result = text
        for word, replacement in self.replacements.items():
            pattern = re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
            result = pattern.sub(replacement, result)
        return result

    def _clean_punctuation_spacing(self, text: str) -> str:
        """Clean up spacing around punctuation marks."""
        result = text
        for punct in [".", ",", "?", "!", ":", ";", ")", "]", "}"]:
            result = result.replace(f" {punct}", punct)
        for punct in ["(", "[", "{"]:
            result = result.replace(f"{punct} ", punct)
        result = result.replace(" \n", "\n").replace("\n ", "\n")
        result = result.replace(" \t", "\t").replace("\t ", "\t")
        return result


# ============================================================================
# Audio Processing
# ============================================================================


class AudioValidator:
    """Validates audio data before sending to transcription API."""

    @staticmethod
    def validate(audio: np.ndarray) -> tuple[bool, str | None]:
        """Validate audio data for transcription."""
        if len(audio) == 0:
            return False, "Empty audio"

        min_samples = int(SAMPLE_RATE * MIN_AUDIO_DURATION_SECONDS)
        if len(audio) < min_samples:
            duration = len(audio) / SAMPLE_RATE
            return False, f"Audio too short ({duration:.2f}s, need >{MIN_AUDIO_DURATION_SECONDS}s)"

        max_amplitude = np.max(np.abs(audio))
        if max_amplitude < MIN_AUDIO_AMPLITUDE:
            return False, "Audio too quiet (no speech detected)"

        return True, None


class AudioProcessor:
    """Processes audio data for transcription."""

    @staticmethod
    def normalize(audio: np.ndarray) -> np.ndarray:
        """Normalize audio to target amplitude."""
        if len(audio) == 0:
            return audio
        max_val = np.max(np.abs(audio))
        if max_val > 0:
            return audio / max_val * NORMALIZATION_TARGET
        return audio

    @staticmethod
    def to_wav_bytes(audio: np.ndarray) -> bytes:
        """Convert audio array to WAV bytes."""
        audio_int16 = (audio * 32767).astype(np.int16)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(AUDIO_CHANNELS)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(audio_int16.tobytes())
        buffer.seek(0)
        return buffer.read()


# ============================================================================
# Storage
# ============================================================================


class StorageManager:
    """Manages transcript storage."""

    def __init__(self):
        self.transcripts_file = TRANSCRIPTS_FILE
        self.transcripts_file.parent.mkdir(parents=True, exist_ok=True)

    def save_transcript(self, raw: str, cleaned: str, timestamp: str):
        """Append transcript to JSONL file."""
        entry = {
            "timestamp": timestamp,
            "raw": raw,
            "cleaned": cleaned,
        }
        with open(self.transcripts_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        logger.info(f"Saved transcript #{self.count_transcripts()}")

    def count_transcripts(self) -> int:
        """Count total transcripts."""
        if not self.transcripts_file.exists():
            return 0
        with open(self.transcripts_file) as f:
            return sum(1 for _ in f)


# ============================================================================
# Transcription
# ============================================================================

# Common Whisper hallucinations to filter out
HALLUCINATION_PATTERNS = [
    # YouTube-style hallucinations
    "thank you",
    "thanks for watching",
    "subscribe",
    "like and subscribe",
    "see you next time",
    "bye",
    "goodbye",
    "thanks for listening",
    "please subscribe",
    "don't forget to",
    "hit the bell",
    "leave a comment",
    "check out",
    "follow me",
    "peace",
    "take care",
    "have a great",
    "i'll see you",
    "catch you",
    "until next time",
    "stay tuned",
    # AI assistant responses (when Whisper acts like a chatbot)
    "i'm sorry",
    "i cannot",
    "i can't",
    "as an ai",
    "i don't have",
    "i'm not able",
    "i'm unable",
    "how can i help",
    "how may i assist",
    "is there anything",
    "let me know if",
    "feel free to ask",
]

# Patterns that indicate Whisper is answering instead of transcribing
AI_RESPONSE_STARTS = [
    "i think you",
    "i believe you",
    "i would suggest",
    "i can help",
    "sure,",
    "certainly!",
    "of course!",
    "absolutely!",
    "yes, i",
    "no, i",
    "well, i think",
    "the answer is",
    "the solution is",
    "here's how",
    "here is how",
]


def is_hallucination(text: str) -> bool:
    """Check if text is likely a Whisper hallucination or AI response."""
    if not text:
        return False
    text_lower = text.lower().strip()

    # Check for common hallucination patterns
    for pattern in HALLUCINATION_PATTERNS:
        if pattern in text_lower:
            return True

    # Very short text that's just punctuation
    if len(text) < 3 or text in [".", "..", "...", "!", "?", ","]:
        return True

    # Detect AI-style responses (Whisper answering instead of transcribing)
    for start in AI_RESPONSE_STARTS:
        if text_lower.startswith(start):
            logger.debug(f"Filtered AI response: {text[:50]}...")
            return True

    return False


async def transcribe_audio(
    client: httpx.AsyncClient, audio: np.ndarray, api_key: str, provider: str
) -> str:
    """Transcribe audio using Whisper API."""
    if len(audio) == 0:
        return ""

    max_amplitude = np.max(np.abs(audio))
    if max_amplitude < MIN_AUDIO_AMPLITUDE:
        logger.debug("Skipping silent audio chunk")
        return ""

    normalized = AudioProcessor.normalize(audio)
    wav_bytes = AudioProcessor.to_wav_bytes(normalized)

    if provider == "groq":
        url = GROQ_WHISPER_URL
        model = "whisper-large-v3-turbo"
    else:
        url = OPENAI_WHISPER_URL
        model = "whisper-1"

    try:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={
                "model": model,
                "response_format": "json",
                "language": "en",
                # Prompt helps guide Whisper to just transcribe, not respond
                "prompt": "Transcribe exactly what is spoken. Do not answer questions or add commentary.",
            },
        )
        if response.status_code == 200:
            text = response.json().get("text", "").strip()
            if is_hallucination(text):
                logger.debug(f"Filtered hallucination: {text}")
                return ""
            return text
        elif response.status_code == 401:
            logger.error(f"❌ Authentication failed: Invalid {provider.capitalize()} API key")
            logger.error(
                f"   Get a valid key at: {'https://console.groq.com/keys' if provider == 'groq' else 'https://platform.openai.com/api-keys'}"
            )
        else:
            logger.error(f"Whisper API error: {response.status_code} - {response.text[:200]}")
    except httpx.TimeoutException:
        logger.error(f"❌ API timeout - check your internet connection")
    except Exception as e:
        logger.error(f"Transcription error: {e}")
    return ""


async def cleanup_text(client: httpx.AsyncClient, text: str, api_key: str, provider: str) -> str:
    """Clean up text using LLM."""
    if not text or len(text) < 3:
        return text

    if provider == "groq":
        url = GROQ_CHAT_URL
        model = "llama-3.1-8b-instant"
    else:
        url = OPENAI_CHAT_URL
        model = "gpt-4o-mini"

    try:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a transcription editor. Fix ONLY grammar and punctuation errors. Do NOT add, remove, or change any words. Do NOT add greetings, sign-offs, or any other content. Output ONLY the corrected text, nothing else.",
                    },
                    {"role": "user", "content": text},
                ],
                "temperature": 0.1,
                "max_tokens": len(text) + 50,
            },
        )
        if response.status_code == 200:
            cleaned = response.json()["choices"][0]["message"]["content"].strip()
            # If cleanup is way longer than input, it's probably adding content
            if len(cleaned) > len(text) * 1.5:
                logger.debug("Cleanup added too much content, using original")
                return text
            return cleaned
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
    return text


# ============================================================================
# Text Output
# ============================================================================


def type_text(text: str) -> None:
    """Type text into the active window using wtype."""
    if not text:
        return

    # Try wtype first (Wayland)
    try:
        subprocess.run(
            ["wtype", text],
            check=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Try xdotool (X11)
    try:
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", text],
            check=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: copy to clipboard
    try:
        subprocess.run(
            ["wl-copy", text],
            stderr=subprocess.DEVNULL,
            check=False,
        )
        logger.info("Text copied to clipboard (typing failed)")
    except FileNotFoundError:
        logger.warning("No text input method available (wtype, xdotool, or wl-copy)")


# ============================================================================
# Voice Dictation Server
# ============================================================================


class VoiceDictationServer:
    """Main server that handles voice dictation via Unix socket."""

    def __init__(self):
        self._running = True
        self.is_recording = False
        self.audio_queue: queue.Queue[np.ndarray] = queue.Queue()
        self.audio_data: list[np.ndarray] = []

        # Load settings
        settings = load_settings()
        self.audio_feedback = AudioFeedback(
            theme=settings.get("audioFeedbackTheme", "default"),
            volume=settings.get("audioFeedbackVolume", 0.3),
        )
        self.waybar_state = WaybarState(
            theme=settings.get("iconTheme", "minimal"),
        )
        self.text_processor = TextProcessor(
            enable_punctuation=settings.get("enableSpokenPunctuation", False),
            replacements=settings.get("wordReplacements", {}),
        )

        # Set initial waybar state
        self.waybar_state.idle()

        # Set mic volume
        try:
            subprocess.run(
                ["pactl", "set-source-volume", "@DEFAULT_SOURCE@", "100%"],
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except FileNotFoundError:
            pass

        # Start audio stream
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=AUDIO_CHANNELS,
            dtype=np.float32,
            callback=self._audio_callback,
        )
        self.stream.start()

        # Setup socket
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(SOCKET_PATH)
        self.socket.listen(1)
        os.chmod(SOCKET_PATH, 0o600)

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Pre-warm HTTP connection
        settings = load_settings()
        provider = settings.get("provider", "groq")
        if provider == "groq":
            logger.info("oflow Ready (Groq - optimized mode)")
            asyncio.run(self._prewarm_connection())
        else:
            logger.info("oflow Ready (OpenAI Whisper + GPT-4o-mini)")

    async def _prewarm_connection(self):
        """Pre-warm HTTP connection to reduce first-request latency."""
        settings = load_settings()
        api_key = settings.get("groqApiKey")
        if not api_key:
            return

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            logger.info("Connection pre-warmed")
        except Exception as e:
            logger.debug(f"Pre-warm failed: {e}")

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info: dict, status: int):
        """Callback for audio stream."""
        if self.is_recording:
            self.audio_queue.put(indata.copy())

    def _signal_handler(self, signum: int, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self._running = False

    def _cleanup(self):
        """Clean up resources."""
        if hasattr(self, "stream"):
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass

        if hasattr(self, "socket"):
            try:
                self.socket.close()
            except Exception:
                pass

        if os.path.exists(SOCKET_PATH):
            try:
                os.remove(SOCKET_PATH)
            except Exception:
                pass

        release_pid_lock()

    def _start_recording(self):
        """Start recording audio."""
        self.is_recording = True
        self.audio_data = []

        self.waybar_state.recording()
        self.audio_feedback.play_start()

        # Reload settings in case they changed
        settings = load_settings()
        self.text_processor = TextProcessor(
            enable_punctuation=settings.get("enableSpokenPunctuation", False),
            replacements=settings.get("wordReplacements", {}),
        )

        logger.info("Recording started")

    def _stop_recording(self):
        """Stop recording and process audio."""
        self.waybar_state.transcribing()
        self.audio_feedback.play_stop()

        start = time.perf_counter()

        # Keep recording flag ON during the grace period so callback
        # continues queueing audio. This prevents cutting off the end.
        time.sleep(0.15)
        self.is_recording = False

        # Small additional delay to let any in-flight callback complete
        time.sleep(0.05)

        # Drain remaining audio from queue
        while not self.audio_queue.empty():
            try:
                data = self.audio_queue.get_nowait()
                self.audio_data.append(data)
            except queue.Empty:
                break

        if not self.audio_data:
            logger.info("Recording stopped (no audio)")
            self.waybar_state.idle()
            return

        asyncio.run(self._process_transcription())
        total_ms = (time.perf_counter() - start) * 1000
        logger.info(f"Recording stopped ({total_ms:.0f}ms)")

    async def _process_transcription(self):
        """Process recorded audio: transcribe, clean up, and type result."""
        settings = load_settings()
        provider = settings.get("provider", "groq")
        api_key = settings.get("groqApiKey") if provider == "groq" else settings.get("openaiApiKey")
        enable_cleanup = settings.get("enableCleanup", True)

        if not api_key:
            error_msg = f"❌ {provider.capitalize()} API key not set. Configure it in Settings."
            logger.error(error_msg)
            self.waybar_state.error("API key not set")
            self.audio_feedback.play_error()
            return

        # Combine all audio
        audio = np.concatenate(self.audio_data, axis=0).flatten()
        duration = len(audio) / SAMPLE_RATE

        # Validate
        valid, error = AudioValidator.validate(audio)
        if not valid:
            logger.warning(f"Audio validation failed: {error}")
            self.waybar_state.idle()
            return

        audio = AudioProcessor.normalize(audio)

        async with httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS) as client:
            logger.info(f"Processing {duration:.1f}s audio")

            # Transcribe
            t0 = time.perf_counter()
            raw_text = await transcribe_audio(client, audio, api_key, provider)
            t1 = time.perf_counter()

            if not raw_text:
                logger.error(
                    f"❌ Transcription failed. Check your {provider.capitalize()} API key. "
                    f"Get one at: https://console.groq.com/keys"
                    if provider == "groq"
                    else "https://platform.openai.com/api-keys"
                )
                self.waybar_state.error("Transcription failed")
                self.audio_feedback.play_error()
                return

            logger.info(f"Transcription: {(t1 - t0) * 1000:.0f}ms")

            # Apply text processing (spoken punctuation, replacements)
            raw_text = self.text_processor.process(raw_text)

            # Cleanup (if enabled)
            if enable_cleanup:
                t0 = time.perf_counter()
                cleaned_text = await cleanup_text(client, raw_text, api_key, provider)
                t1 = time.perf_counter()
                logger.info(f"Cleanup: {(t1 - t0) * 1000:.0f}ms")
            else:
                cleaned_text = raw_text

            # Type the result
            type_text(cleaned_text)
            logger.info(f"Result: {cleaned_text[:50]}...")

            # Set waybar back to idle
            self.waybar_state.idle()

            # Save transcript
            storage = StorageManager()
            storage.save_transcript(
                raw=raw_text,
                cleaned=cleaned_text,
                timestamp=datetime.now().isoformat(),
            )

    def run(self):
        """Main server loop."""
        try:
            while self._running:
                try:
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
                    logger.debug(f"Command: {cmd}")

                    if cmd == "start" and not self.is_recording:
                        self._start_recording()
                    elif cmd == "stop" and self.is_recording:
                        self._stop_recording()
                    elif cmd == "toggle":
                        if self.is_recording:
                            self._stop_recording()
                        else:
                            self._start_recording()
                finally:
                    conn.close()
        finally:
            self._cleanup()


# ============================================================================
# Configuration Validation
# ============================================================================


def check_dependencies() -> list[str]:
    """Check for required system dependencies."""
    missing = []

    # Check for text input tools
    has_wtype = subprocess.run(["which", "wtype"], capture_output=True).returncode == 0
    has_xdotool = subprocess.run(["which", "xdotool"], capture_output=True).returncode == 0
    has_wl_copy = subprocess.run(["which", "wl-copy"], capture_output=True).returncode == 0

    if not (has_wtype or has_xdotool):
        missing.append("wtype or xdotool (for typing text)")
        if not has_wl_copy:
            logger.warning("⚠️  No text input method available! Install wtype: sudo pacman -S wtype")
        else:
            logger.warning(
                "⚠️  wtype not found. Text will be copied to clipboard instead. "
                "Install wtype for auto-typing: sudo pacman -S wtype"
            )

    return missing


def validate_configuration() -> None:
    """Validate application configuration."""
    TRANSCRIPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Check dependencies
    check_dependencies()

    settings = load_settings()
    provider = settings.get("provider", "groq")

    if provider == "groq":
        api_key = settings.get("groqApiKey")
        if not api_key:
            logger.warning(
                "⚠️  Groq API key not configured. "
                "Get one at https://console.groq.com/keys (free tier available)"
            )
        elif not GROQ_API_KEY_PATTERN.match(api_key):
            logger.error("❌ Invalid Groq API key format. Expected format: gsk_...")
        elif len(api_key) > 60:
            logger.error(
                "❌ Groq API key looks duplicated (too long). "
                f"Expected ~56 chars, got {len(api_key)}. Check ~/.oflow/settings.json"
            )
    else:
        api_key = settings.get("openaiApiKey")
        if not api_key:
            logger.warning(
                "⚠️  OpenAI API key not configured. "
                "Set it in the UI Settings or via OPENAI_API_KEY environment variable."
            )
        elif not OPENAI_API_KEY_PATTERN.match(api_key):
            logger.error("❌ Invalid OpenAI API key format. Expected format: sk-...")

    logger.info("Configuration validated successfully")


# ============================================================================
# Main Entry Point
# ============================================================================


def main() -> None:
    """Main entry point for Oflow."""
    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd not in ("start", "stop", "toggle"):
            print(f"Unknown command: {cmd}", file=sys.stderr)
            print("Usage: oflow [start|stop|toggle]", file=sys.stderr)
            sys.exit(1)

        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(SOCKET_PATH)
            s.send(cmd.encode())
            s.close()
        except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
            print(f"Server not running: {e}\nStart the server with: ./oflow", file=sys.stderr)
            sys.exit(1)
    else:
        if not acquire_pid_lock():
            logger.info("Another backend is already running, exiting")
            sys.exit(0)

        try:
            validate_configuration()
        except ConfigurationError as e:
            release_pid_lock()
            print(f"Configuration error: {e}", file=sys.stderr)
            sys.exit(1)

        try:
            VoiceDictationServer().run()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
