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
import fcntl
import io
import json
import logging
import os
import queue
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
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

try:
    import psutil

    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

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
CHUNK_DURATION_SECONDS = 25  # Max chunk duration for Whisper (split long audio)
CHUNK_SPLIT_WINDOW_SECONDS = 3  # Window to search for silence near split point
MAX_RECORDING_SECONDS = 300  # Auto-stop runaway recordings to prevent leaks from stuck state
STREAM_WARN_INTERVAL_SECONDS = 5.0  # Throttle audio-stream distress warnings

# API configuration
API_TIMEOUT_SECONDS = 30.0  # Timeout for API requests
# How long an idle keep-alive socket may be reused before it's recycled. Kept
# below typical server/NAT idle timeouts so we don't try to send on a socket the
# other end has already dropped (e.g. after laptop suspend/resume).
KEEPALIVE_EXPIRY_SECONDS = 45.0

# File paths
TRANSCRIPTS_FILE = Path.home() / ".oflow" / "transcripts.jsonl"
SETTINGS_FILE = Path.home() / ".oflow" / "settings.json"

# Waybar state file (in XDG_RUNTIME_DIR for fast access)
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "oflow"
STATE_FILE = RUNTIME_DIR / "state"

# On-screen recording overlay (oflow-osd.py): datagram socket for live levels
OSD_SOCK = RUNTIME_DIR / "osd.sock"
OSD_LEVEL_GAIN = 6.0  # scale raw mic peak (~0..0.2 for speech) toward 0..1
# gtk4-layer-shell must be LD_PRELOADed (linker-order quirk) for the overlay
GTK4_LAYER_SHELL_LIB = "/usr/lib/libgtk4-layer-shell.so"

# API key format validation
OPENAI_API_KEY_PATTERN = re.compile(r"^sk-")
GROQ_API_KEY_PATTERN = re.compile(r"^gsk_")

# API endpoints
OPENAI_WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

# API key validation constants
GROQ_API_KEY_LENGTH = 56
GROQ_API_KEY_MAX_LENGTH = 60  # Warn if longer (likely duplicated)

# LLM cleanup constants
CLEANUP_TOKEN_BUFFER = 200  # Extra tokens for LLM cleanup (tokens ≈ chars/4)

# ============================================================================
# Environment Variables and Configuration
# ============================================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

# Default settings (can be overridden by settings.json)
DEFAULT_ENABLE_CLEANUP = os.getenv("ENABLE_CLEANUP", "true").lower() == "true"
DEFAULT_PROVIDER = os.getenv("PROVIDER", "groq")  # Default to Groq (faster)
# Fast mode: dictations with at most this many words skip the cleanup LLM hop.
# 0 disables (always clean). 8 covers short commands/replies where the ~200ms
# cleanup latency is most felt and least needed.
DEFAULT_FAST_MODE_MAX_WORDS = 8


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
                if groq_key and len(groq_key) > GROQ_API_KEY_MAX_LENGTH:
                    logger.warning(
                        f"⚠️  Groq API key looks duplicated (length: {len(groq_key)}). "
                        f"Expected ~{GROQ_API_KEY_LENGTH} chars. Please check your settings."
                    )
                    # Try to fix by taking first half if it looks like a duplicate
                    if (
                        len(groq_key) == GROQ_API_KEY_LENGTH * 2
                        and groq_key[:GROQ_API_KEY_LENGTH] == groq_key[GROQ_API_KEY_LENGTH:]
                    ):
                        logger.info("Auto-fixing duplicated API key...")
                        groq_key = groq_key[:GROQ_API_KEY_LENGTH]

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
                    "pauseMediaWhileRecording": settings.get("pauseMediaWhileRecording", True),
                    "enableOverlay": settings.get("enableOverlay", True),
                    "submitKeywords": settings.get("submitKeywords", SUBMIT_KEYWORDS_DEFAULT),
                    "enableSpokenActions": settings.get("enableSpokenActions", True),
                    "commandWakeWord": settings.get("commandWakeWord", DEFAULT_WAKE_WORD),
                    "fastModeMaxWords": settings.get("fastModeMaxWords", DEFAULT_FAST_MODE_MAX_WORDS),
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

# Global to store PID lock file handle (keeps lock alive)
_pid_lock_file = None


def acquire_pid_lock() -> bool:
    """
    Acquire exclusive lock via PID file using fcntl.
    Returns True if lock acquired, False if another instance is running.
    """
    global _pid_lock_file

    try:
        # Open in write mode
        _pid_lock_file = open(PID_FILE, "w")

        # Try to acquire exclusive lock (non-blocking)
        fcntl.flock(_pid_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        # We got the lock - write our PID
        _pid_lock_file.write(str(os.getpid()))
        _pid_lock_file.flush()

        return True
    except IOError:
        # Lock already held by another process
        if _pid_lock_file:
            _pid_lock_file.close()
            _pid_lock_file = None
        logger.info("Backend already running (PID lock held)")
        return False


def release_pid_lock() -> None:
    """Release the PID file lock."""
    global _pid_lock_file
    try:
        if _pid_lock_file:
            fcntl.flock(_pid_lock_file.fileno(), fcntl.LOCK_UN)
            _pid_lock_file.close()
            _pid_lock_file = None
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

        # Pre-compile punctuation patterns once (not on every process() call)
        self._punctuation_patterns = []
        if enable_punctuation:
            for phrase, symbol in self.PUNCTUATION_MAP:
                pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
                self._punctuation_patterns.append((pattern, symbol))

        # Pre-compile replacement patterns
        self._replacement_patterns = []
        for word, replacement in self.replacements.items():
            pattern = re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
            self._replacement_patterns.append((pattern, replacement))

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
        # Use pre-compiled patterns instead of compiling each time
        for pattern, symbol in self._punctuation_patterns:
            # Escape backslashes in replacement string for regex
            escaped_symbol = symbol.replace("\\", "\\\\")
            result = pattern.sub(escaped_symbol, result)
        result = self._clean_punctuation_spacing(result)
        return result

    def _apply_replacements(self, text: str) -> str:
        """Apply custom word replacements."""
        result = text
        # Use pre-compiled patterns
        for pattern, replacement in self._replacement_patterns:
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

    @staticmethod
    def split_into_chunks(audio: np.ndarray) -> list[np.ndarray]:
        """Split long audio into chunks at silence boundaries.

        For audio shorter than CHUNK_DURATION_SECONDS, returns it as-is.
        For longer audio, splits near silence points every ~CHUNK_DURATION_SECONDS.
        """
        chunk_samples = int(CHUNK_DURATION_SECONDS * SAMPLE_RATE)
        if len(audio) <= chunk_samples:
            return [audio]

        window_samples = int(CHUNK_SPLIT_WINDOW_SECONDS * SAMPLE_RATE)
        chunks = []
        offset = 0

        while offset < len(audio):
            remaining = len(audio) - offset
            if remaining <= chunk_samples:
                chunks.append(audio[offset:])
                break

            # Find the quietest point in a window around the target split
            target = offset + chunk_samples
            search_start = max(offset + chunk_samples - window_samples, offset)
            search_end = min(offset + chunk_samples + window_samples, len(audio))
            window = audio[search_start:search_end]

            # Compute RMS energy in small frames (20ms) to find silence
            frame_size = int(0.02 * SAMPLE_RATE)  # 20ms frames
            num_frames = len(window) // frame_size
            if num_frames > 0:
                frames = window[: num_frames * frame_size].reshape(num_frames, frame_size)
                energies = np.sqrt(np.mean(frames**2, axis=1))
                quietest_frame = np.argmin(energies)
                split_point = search_start + quietest_frame * frame_size
            else:
                split_point = target

            chunks.append(audio[offset:split_point])
            offset = split_point

        # Filter out empty/tiny chunks
        return [c for c in chunks if len(c) >= int(0.1 * SAMPLE_RATE)]


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

# Whisper conditioning prompt leakage. The priming prompt in transcribe_audio
# contains engineering vocabulary; when audio is weak Whisper sometimes echoes
# the prompt instead of transcribing. Real leakage repeats multiple phrases
# verbatim — a single phrase is usually legitimate user dictation about
# engineering work, so we only filter when 2+ phrases co-occur.
PROMPT_LEAKAGE_PHRASES = [
    "push the code",
    "open a pr",
    "check the api endpoint",
    "run pytest",
    "deploy to kubernetes",
    "let's refactor",
    "async handler",
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
    """Check if text is likely a Whisper hallucination or AI response.

    Logs the matched pattern at INFO when filtering so users can see exactly
    why a recording produced no output.
    """
    if not text:
        return False
    text_lower = text.lower().strip()

    if len(text) < 3 or text in [".", "..", "...", "!", "?", ","]:
        logger.info(f"Filtered hallucination (too short): {text!r}")
        return True

    # Check for common hallucination patterns, but ONLY for very short text
    # where the hallucination phrase makes up the bulk of the content.
    # Real Whisper hallucinations are brief repetitive phrases ("Thank you",
    # "Subscribe", etc). Longer text that merely contains these as substrings
    # is real speech and must not be filtered.
    if len(text_lower) < 60:
        hits = [p for p in HALLUCINATION_PATTERNS if p in text_lower]
        # Filter if a single pattern accounts for most of the text...
        for pattern in hits:
            if len(text_lower) < len(pattern) + 15:
                logger.info(f"Filtered hallucination (matched {pattern!r}): {text[:80]}")
                return True
        # ...or if several distinct patterns stack up in short text. Real speech
        # rarely chains multiple AI/YouTube clichés ("I'm sorry, I cannot help");
        # Whisper hallucinations do. Mirrors the 2+ prompt-leakage heuristic below.
        if len(hits) >= 2:
            logger.info(f"Filtered hallucination (matched {hits}): {text[:80]}")
            return True

    # Conditioning-prompt leakage: require 2+ matches, since a single phrase
    # is usually legitimate engineering dictation.
    leakage_hits = [p for p in PROMPT_LEAKAGE_PHRASES if p in text_lower]
    if len(leakage_hits) >= 2:
        logger.info(f"Filtered prompt leakage (matched {leakage_hits}): {text[:80]}")
        return True

    for start in AI_RESPONSE_STARTS:
        if text_lower.startswith(start):
            logger.info(f"Filtered AI response (starts with {start!r}): {text[:80]}")
            return True

    # Catch generic AI-style responses: short text that is essentially just
    # a chatbot reply with no real dictated content around it
    if len(text_lower) < 60:
        ai_phrases = [
            "what would you like",
            "what do you need",
            "can i assist",
            "do you have any",
            "would you like me to",
            "shall i",
            "may i help",
            "i'd be happy to",
            "i'd be glad to",
            "that's a great question",
            "good question",
            "great question",
            "you're welcome",
            "happy to help",
        ]
        for phrase in ai_phrases:
            if phrase in text_lower and len(text_lower) < len(phrase) + 15:
                logger.info(f"Filtered AI response (matched {phrase!r}): {text[:80]}")
                return True

    return False


# A pooled keep-alive connection can be closed under us by the server, an idle
# NAT/firewall timeout, or a laptop suspend/resume. httpx does NOT auto-retry
# POST, so without this a single dead socket would fail an otherwise-fine
# dictation. Re-sending is safe: re-transcribing the same audio or re-running
# cleanup has no harmful side effect. We retry once, on a fresh connection.
_RETRYABLE_POST_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)


async def _post_with_retry(
    client: httpx.AsyncClient, url: str, *, retries: int = 1, **kwargs
) -> httpx.Response:
    """POST that retries once on a transient connection failure.

    Guards the persistent (keep-alive) client against stale sockets. Only
    connection-level errors are retried; HTTP error *responses* (401, 5xx) are
    returned to the caller unchanged, exactly as a plain client.post would.
    """
    for attempt in range(retries + 1):
        try:
            return await client.post(url, **kwargs)
        except _RETRYABLE_POST_ERRORS as e:
            if attempt >= retries:
                raise
            logger.warning(
                f"POST {url} failed ({type(e).__name__}); retrying on a fresh connection"
            )


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
        # whisper-large-v3-turbo is Groq's fastest available speech model
        # (distil-whisper was decommissioned). Override with OFLOW_WHISPER_MODEL.
        model = os.getenv("OFLOW_WHISPER_MODEL", "whisper-large-v3-turbo")
    else:
        url = OPENAI_WHISPER_URL
        model = "whisper-1"

    try:
        response = await _post_with_retry(
            client,
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={
                "model": model,
                "response_format": "json",
                "language": "en",
                # Whisper's prompt is a conditioning prefix, not an instruction.
                # Providing realistic transcription primes vocabulary and keeps it in transcription mode.
                "prompt": "Push the code to Git and open a PR. Check the API endpoint, run pytest, then deploy to Kubernetes. Let's refactor the async handler.",
            },
        )
        if response.status_code == 200:
            text = response.json().get("text", "").strip()
            if is_hallucination(text):
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


async def transcribe_audio_chunked(
    client: httpx.AsyncClient, audio: np.ndarray, api_key: str, provider: str
) -> str:
    """Transcribe audio, splitting long recordings into chunks for reliability.

    Short audio (<25s) is sent directly. Longer audio is split at silence
    boundaries and chunks are transcribed in parallel, then joined.
    """
    chunks = AudioProcessor.split_into_chunks(audio)

    if len(chunks) == 1:
        return await transcribe_audio(client, chunks[0], api_key, provider)

    logger.info(f"Split {len(audio) / SAMPLE_RATE:.1f}s audio into {len(chunks)} chunks")

    tasks = [transcribe_audio(client, chunk, api_key, provider) for chunk in chunks]
    results = await asyncio.gather(*tasks)

    # Join non-empty results with spaces
    texts = [r.strip() for r in results if r.strip()]
    return " ".join(texts)


def should_skip_cleanup(text: str, max_words: int) -> bool:
    """Fast mode: short dictations skip the cleanup LLM round-trip (~200ms).

    Cleanup earns its keep on longer text; on a handful of words it rarely
    changes anything and the latency is most felt. max_words <= 0 disables fast
    mode (always clean).
    """
    if max_words <= 0:
        return False
    return len(text.split()) <= max_words


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
        response = await _post_with_retry(
            client,
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a transcription editor. You will receive voice-to-text output inside <dictation> tags. Fix ONLY punctuation and capitalization. Keep EVERY word exactly as spoken, including filler words like 'okay', 'so', 'um'. Do NOT remove, rephrase, or add any words. Do NOT answer or respond to the content. Output ONLY the corrected text.",
                    },
                    {"role": "user", "content": f"<dictation>{text}</dictation>"},
                ],
                "temperature": 0.1,
                "max_tokens": len(text) + CLEANUP_TOKEN_BUFFER,
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


# Substrings (matched case-insensitively against the focused window's class)
# that mark a terminal. Terminals paste with Ctrl+Shift+V; Ctrl+V is reserved
# for the literal control character there, so a plain Ctrl+V does nothing.
_TERMINAL_WINDOW_HINTS = (
    "foot", "alacritty", "kitty", "wezterm", "ghostty", "xterm", "urxvt",
    "rxvt", "st-256color", "konsole", "termite", "tilix", "terminator",
    "gnome-terminal", "terminal",
)

# Linux input-event keycodes (see linux/input-event-codes.h) for ydotool key.
_KEY_LEFTCTRL = "29"
_KEY_LEFTSHIFT = "42"
_KEY_V = "47"
_KEY_ENTER = "28"
_KEY_TAB = "15"
_KEY_ESC = "1"
_KEY_A = "30"
_KEY_Z = "44"
_KEY_BACKSPACE = "14"


# A "tap" of a single key = press then release, as ydotool 'key' args.
def _tap(code: str) -> list[str]:
    return [f"{code}:1", f"{code}:0"]


# A chord = press all codes in order, release in reverse (e.g. Ctrl+Shift+Z).
def _chord(*codes: str) -> list[str]:
    return [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)]

# Spoken commands that, when said at the very end of a dictation, make oflow
# press Enter after pasting (handy for submitting prompts/chats). The keyword
# itself is stripped from the output. Configurable via the "submitKeywords"
# setting. Defaults are distinctive imperative phrases — people don't naturally
# *end* a dictation with "press enter" / "hit enter" unless they mean the
# command, whereas the bare word "enter" appears in normal sentences ("the data
# you enter"). A two-word command is what makes this robust. Add "enter" to the
# setting yourself if you want the looser (riskier) single-word trigger.
SUBMIT_KEYWORDS_DEFAULT = ["press enter", "hit enter", "new line and enter"]


def extract_submit_keyword(text: str, keywords: list[str]) -> tuple[str, bool]:
    """If *text* ends with a submit phrase, strip it and return (text, True).

    Multi-word phrases match with flexible whitespace/case so "Press Enter",
    "press  enter" and "press enter." all trigger. The phrase must be at the
    very end (optionally followed by punctuation) so it only fires when the
    user clearly tacked it on as a command.
    """
    if not text or not keywords:
        return text, False
    parts = []
    for kw in keywords:
        words = [w for w in kw.split() if w]
        if words:
            parts.append(r"\s+".join(re.escape(w) for w in words))
    if not parts:
        return text, False
    pattern = "|".join(parts)
    m = re.search(rf"(?:^|\b)(?:{pattern})\b[\s.!?,;:'\"]*$", text, re.IGNORECASE)
    if m:
        return text[: m.start()].rstrip().rstrip(",.;:"), True
    return text, False


def press_enter() -> None:
    """Press the Enter key via ydotool (e.g. to submit a prompt after pasting)."""
    try:
        subprocess.run(
            ["ydotool", "key", f"{_KEY_ENTER}:1", f"{_KEY_ENTER}:0"],
            check=True, stderr=subprocess.DEVNULL, timeout=5,
        )
        logger.info("Pressed Enter (submit keyword)")
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(f"press_enter failed (ydotool): {e}")


def _paste_chord() -> list[str]:
    """ydotool 'key' args for the paste shortcut suited to the focused window:
    Ctrl+Shift+V for terminals, Ctrl+V everywhere else."""
    ctrl_v = [f"{_KEY_LEFTCTRL}:1", f"{_KEY_V}:1", f"{_KEY_V}:0", f"{_KEY_LEFTCTRL}:0"]
    ctrl_shift_v = [
        f"{_KEY_LEFTCTRL}:1", f"{_KEY_LEFTSHIFT}:1", f"{_KEY_V}:1",
        f"{_KEY_V}:0", f"{_KEY_LEFTSHIFT}:0", f"{_KEY_LEFTCTRL}:0",
    ]
    try:
        out = subprocess.run(
            ["hyprctl", "activewindow", "-j"],
            capture_output=True, text=True, timeout=2,
        ).stdout
        cls = (json.loads(out).get("class") or "").lower()
        if any(hint in cls for hint in _TERMINAL_WINDOW_HINTS):
            return ctrl_shift_v
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        # Unknown focus → assume a normal app; Ctrl+V is the safe default.
        pass
    return ctrl_v


def _paste_text(text: str) -> bool:
    """Insert text in one shot via the clipboard + a single paste keystroke.

    Far faster than per-character injection and immune to per-keystroke timing
    issues. Preserves the user's existing clipboard. Needs wl-copy and ydotool;
    returns False if either is missing or the paste fails, so the caller can fall
    back to typing.
    """
    # Best-effort snapshot of the current clipboard so dictation doesn't clobber it.
    try:
        prev = subprocess.run(
            ["wl-paste", "--no-newline"], capture_output=True, timeout=2
        ).stdout
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        prev = None

    chord = _paste_chord()
    try:
        subprocess.run(["wl-copy"], input=text.encode(), check=True, timeout=5)
        subprocess.run(
            ["ydotool", "key", *chord],
            check=True, stderr=subprocess.DEVNULL, timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Paste failed ({type(e).__name__}: {e}); falling back to typing")
        return False

    logger.info(f"Pasted {len(text)} chars (chord={'+'.join(chord)})")

    # Restore the prior clipboard once the paste has consumed our offer. The delay
    # avoids a race where apps that read the selection lazily get the old data.
    if prev:
        try:
            time.sleep(0.25)
            subprocess.run(["wl-copy"], input=prev, check=False, timeout=5)
        except (subprocess.SubprocessError, OSError):
            pass
    return True


def type_text(text: str) -> None:
    """Insert transcribed text into the active window.

    Defaults to one-shot clipboard paste (outputMode="paste"): instant and
    reliable across apps. Falls back to per-keystroke injection (outputMode="type"
    or if paste is unavailable). For typing, prefers ydotool (uinput) over wtype:
    wtype uploads a custom keymap on a virtual Wayland keyboard, which XWayland
    clients (e.g. Chrome/Electron running under XWayland) ignore — they decode its
    keycodes against the seat's US layout instead, turning dictated text into the
    number row ("12345..."). ydotool injects real keycodes through kernel uinput,
    which Wayland, XWayland, and X11 all decode identically.
    """
    if not text:
        return

    settings = load_settings()

    # One-shot paste is the default; fall through to typing if it's unavailable.
    if settings.get("outputMode", "paste") == "paste" and _paste_text(text):
        return

    # Delay between keystrokes. A too-small value drops characters in apps with
    # heavy input handling (e.g. React web apps like Gemini), which can't keep up
    # when keystrokes arrive faster than they process them. 12ms is reliable
    # across web apps while staying imperceptibly fast; tune via settings if needed.
    delay_ms = str(settings.get("typingDelayMs", 12))

    # Try ydotool first (works in XWayland apps where wtype produces digits).
    # Requires ydotoold running; YDOTOOL_SOCKET is auto-detected by recent versions.
    try:
        subprocess.run(
            ["ydotool", "type", "--key-delay", delay_ms, "--", text],
            check=True,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        return
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fall back to wtype (reliable in terminals; may misfire in Chromium/Electron)
    try:
        subprocess.run(
            ["wtype", "-d", delay_ms, text],
            check=True,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        return
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Try xdotool (X11)
    try:
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--delay", delay_ms, text],
            check=True,
            stderr=subprocess.DEVNULL,
            timeout=30,
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
# Spoken Actions (say the wake word + a command, oflow presses the real key)
# ============================================================================
#
# Every command is "<wake word> <command>" — e.g. "oflow enter", "oflow scratch
# that", "oflow select all". Requiring the wake word is what makes this safe:
# nobody says "oflow" mid-sentence, so a command only fires when you mean it, and
# the command words are consumed (not typed). The wake word is fuzzy-matched
# because Whisper renders the coined word "oflow" inconsistently, and it is
# user-configurable (commandWakeWord).
DEFAULT_WAKE_WORD = "oflow"

# Plausible Whisper transcriptions of "oflow" that are also unlikely to appear
# right before a command word in ordinary speech. Used only for the default wake
# word; a custom wake word is matched literally (case-insensitive). Tune after
# hearing what Whisper actually produces.
_OFLOW_WAKE_VARIANTS = ["oflow", "oflo", "o flow", "oh flow", "off flow"]

# Each action: "action" is "keys" (send chords) or "scratch" (delete last output).
SPOKEN_ACTIONS = [
    {"phrases": ["scratch that", "delete that"], "action": "scratch", "label": "⌫ scratch"},
    {"phrases": ["new paragraph"], "action": "keys", "label": "¶ paragraph", "keys": [_tap(_KEY_ENTER), _tap(_KEY_ENTER)]},
    {"phrases": ["new line", "next line"], "action": "keys", "label": "↵ new line", "keys": [_tap(_KEY_ENTER)]},
    {"phrases": ["enter", "return", "send it", "send", "submit"], "action": "keys", "label": "↵ enter", "keys": [_tap(_KEY_ENTER)]},
    {"phrases": ["tab"], "action": "keys", "label": "⇥ tab", "keys": [_tap(_KEY_TAB)]},
    {"phrases": ["escape", "cancel"], "action": "keys", "label": "⎋ escape", "keys": [_tap(_KEY_ESC)]},
    {"phrases": ["select all"], "action": "keys", "label": "select all", "keys": [_chord(_KEY_LEFTCTRL, _KEY_A)]},
    {"phrases": ["undo that", "undo"], "action": "keys", "label": "↶ undo", "keys": [_chord(_KEY_LEFTCTRL, _KEY_Z)]},
    {"phrases": ["redo that", "redo"], "action": "keys", "label": "↷ redo", "keys": [_chord(_KEY_LEFTCTRL, _KEY_LEFTSHIFT, _KEY_Z)]},
    {"phrases": ["delete word"], "action": "keys", "label": "⌫ word", "keys": [_chord(_KEY_LEFTCTRL, _KEY_BACKSPACE)]},
]


def _wake_pattern(wake_word: str) -> str:
    """Regex fragment matching the wake word (fuzzy for the default 'oflow')."""
    word = (wake_word or DEFAULT_WAKE_WORD).strip()
    alts = _OFLOW_WAKE_VARIANTS if word.lower() == DEFAULT_WAKE_WORD else [word]
    return "(?:" + "|".join(r"\s+".join(re.escape(w) for w in a.split()) for a in alts) + ")"


def _compile_actions(specs: list[dict], wake_word: str):
    """Compile a regex matching '<wake> <command>' and a group->spec index map.

    Command phrases are ordered longest-first so "undo that" wins over "undo".
    """
    indexed = []
    for i, spec in enumerate(specs):
        for ph in spec["phrases"]:
            indexed.append((i, ph))
    indexed.sort(key=lambda t: -len(t[1].split()))
    group_to_spec = {}
    parts = []
    for k, (i, ph) in enumerate(indexed):
        g = f"a{k}"
        group_to_spec[g] = i
        parts.append(f"(?P<{g}>" + r"\s+".join(re.escape(w) for w in ph.split()) + ")")
    if not parts:
        return None, {}
    # Separator tolerates a comma the cleanup LLM may insert after the wake word
    # ("Oflow, scratch that") but not a period — a sentence boundary shouldn't
    # let a command leak across clauses.
    pattern = re.compile(
        rf"\b{_wake_pattern(wake_word)}[\s,]+(?:" + "|".join(parts) + r")\b",
        re.IGNORECASE,
    )
    return pattern, group_to_spec


def segment_spoken_actions(
    text: str, specs: list[dict] = SPOKEN_ACTIONS, wake_word: str = DEFAULT_WAKE_WORD
) -> list[tuple]:
    """Split *text* into an ordered list of segments to replay in place.

    A command is "<wake> <phrase>"; everything else is literal text. Segments are
    ("text", str), ("key", keys, label), or ("scratch", label). Whitespace and a
    stray punctuation mark left flush against a removed command are trimmed so
    "do it, oflow enter" yields [text "do it"] + [key Enter]. Returns a single
    text segment when no command is present (the common case), so normal
    dictation — including the literal word "oflow" — is untouched.
    """
    pattern, group_to_spec = _compile_actions(specs, wake_word)
    if not text or pattern is None:
        return [("text", text)]

    raw_segments: list[tuple] = []
    pos = 0
    for m in pattern.finditer(text):
        if m.start() > pos:
            raw_segments.append(("text", text[pos:m.start()]))
        g = next(g for g in group_to_spec if m.group(g) is not None)
        spec = specs[group_to_spec[g]]
        if spec["action"] == "scratch":
            raw_segments.append(("scratch", spec["label"]))
        else:
            raw_segments.append(("key", spec["keys"], spec["label"]))
        pos = m.end()
    if pos < len(text):
        raw_segments.append(("text", text[pos:]))

    if not any(seg[0] != "text" for seg in raw_segments):
        return [("text", text)]

    # Trim whitespace/orphan punctuation at text↔command boundaries.
    cleaned: list[tuple] = []
    for i, seg in enumerate(raw_segments):
        if seg[0] != "text":
            cleaned.append(seg)
            continue
        s = seg[1]
        if i > 0 and raw_segments[i - 1][0] != "text":
            s = s.lstrip(" \t.,;:!?")  # drop a mark stranded after a command
        if i + 1 < len(raw_segments) and raw_segments[i + 1][0] != "text":
            s = s.rstrip(" \t")
        if s:
            cleaned.append(("text", s))
    return cleaned or [("text", "")]


def _send_keys(key_chords: list[list[str]]) -> None:
    """Send one or more key taps via ydotool (e.g. Enter, or Enter+Enter)."""
    for chord in key_chords:
        try:
            subprocess.run(
                ["ydotool", "key", *chord],
                check=True, stderr=subprocess.DEVNULL, timeout=5,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning(f"_send_keys failed (ydotool): {e}")
            return
        time.sleep(0.01)


# Cap so a runaway count can't hammer the keyboard for seconds.
_MAX_SCRATCH_BACKSPACES = 2000


def _send_backspaces(n: int) -> None:
    """Delete the last n characters with a single batched ydotool Backspace run."""
    n = min(n, _MAX_SCRATCH_BACKSPACES)
    if n <= 0:
        return
    chord = []
    for _ in range(n):
        chord += _tap(_KEY_BACKSPACE)
    try:
        subprocess.run(
            ["ydotool", "key", *chord],
            check=True, stderr=subprocess.DEVNULL, timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(f"_send_backspaces failed (ydotool): {e}")


def output_with_actions(text: str, wake_word: str = DEFAULT_WAKE_WORD, prev_chars: int = 0) -> int:
    """Type *text*, executing wake-word commands as real keystrokes.

    Returns the net number of characters this call left on screen, so the caller
    can persist it and let a later "scratch that" delete across dictations.
    prev_chars seeds that history (what a leading "scratch that" should remove).

    Fast path: a dictation with no commands is a single paste — identical to
    type_text.
    """
    segments = segment_spoken_actions(text, wake_word=wake_word)
    if len(segments) == 1 and segments[0][0] == "text":
        type_text(segments[0][1])
        return len(segments[0][1])

    last_run = prev_chars  # characters a "scratch" can remove right now
    net = prev_chars
    for kind, *rest in segments:
        if kind == "text":
            run = rest[0]
            if run:
                type_text(run)
                time.sleep(0.03)  # let the paste land before the next keystroke
                last_run = len(run)
                net += len(run)
        elif kind == "scratch":
            _send_backspaces(last_run)
            logger.info(f"Spoken action: scratch ({last_run} chars)")
            net -= last_run
            last_run = 0
            time.sleep(0.03)
        else:  # key
            keys, label = rest
            _send_keys(keys)
            logger.info(f"Spoken action: {label}")
            time.sleep(0.03)
    return max(0, net)


# ============================================================================
# Voice Dictation Server
# ============================================================================


class VoiceDictationServer:
    """Main server that handles voice dictation via Unix socket."""

    def __init__(self):
        self._running = True
        self.is_recording = False
        self._recording_lock = threading.Lock()
        # MPRIS players we paused for the current recording (resumed on stop)
        self._paused_players: list[str] = []
        # On-screen recording overlay process + level-send socket
        self._osd_proc: subprocess.Popen | None = None
        try:
            self._osd_send_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self._osd_send_sock.setblocking(False)
        except OSError:
            self._osd_send_sock = None
        # Bounded queue: max 3000 chunks = ~300 seconds (5 minutes) at 10 chunks/sec (prevents memory leak)
        self.audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=3000)
        self.audio_data: list[np.ndarray] = []
        # Characters left on screen by the last dictation, so "scratch that" in a
        # following dictation can delete them.
        self._last_output_chars = 0

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

        # Audio stream (created on-demand, not always running)
        self.stream = None
        self._recording_watchdog: threading.Timer | None = None
        self._last_stream_warn = 0.0

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

        # One persistent asyncio loop + HTTP client for the whole server life.
        # Reusing the client keeps the TLS/HTTP connection to the API warm
        # between dictations, removing a ~100-250ms handshake from every one.
        # The client is created on, and only ever touched from, this loop's
        # thread — an httpx.AsyncClient is bound to its event loop, so the old
        # "asyncio.run() per dictation" model could never have reused it safely.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._client: httpx.AsyncClient | None = None
        self._start_async_loop()

        settings = load_settings()
        provider = settings.get("provider", "groq")
        if provider == "groq":
            logger.info("oflow Ready (Groq - optimized mode)")
            self._schedule_on_loop(self._prewarm_connection())
        else:
            logger.info("oflow Ready (OpenAI Whisper + GPT-4o-mini)")

    def _start_async_loop(self):
        """Spin up the persistent event loop (background thread) + HTTP client."""
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="oflow-async"
        )
        self._loop_thread.start()
        # Create the client on the loop thread so it binds to the right loop.
        asyncio.run_coroutine_threadsafe(self._init_client(), self._loop).result(timeout=5)

    async def _init_client(self):
        """Construct the persistent HTTP client (runs on the loop thread)."""
        self._client = httpx.AsyncClient(
            timeout=API_TIMEOUT_SECONDS,
            limits=httpx.Limits(
                max_keepalive_connections=4,
                keepalive_expiry=KEEPALIVE_EXPIRY_SECONDS,
            ),
        )

    def _run_on_loop(self, coro):
        """Run a coroutine on the persistent loop and block for its result."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def _schedule_on_loop(self, coro):
        """Fire-and-forget a coroutine on the persistent loop (no blocking)."""
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError:
            pass  # loop shutting down

    async def _prewarm_connection(self):
        """Warm the persistent connection so the next request skips the handshake.

        Called at startup and again on key-down, so a keep-alive that expired
        while idle is refreshed before the user finishes speaking.
        """
        if self._client is None:
            return
        settings = load_settings()
        api_key = settings.get("groqApiKey")
        if not api_key:
            return
        try:
            await self._client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=5.0,
            )
            logger.debug("Connection pre-warmed")
        except Exception as e:
            logger.debug(f"Pre-warm failed: {e}")

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info: dict, status: int):
        """Callback for audio stream."""
        if not self.is_recording:
            return

        if status:
            self._warn_stream_distress(f"Audio stream status: {status}")

        try:
            self.audio_queue.put_nowait(indata.copy())
        except queue.Full:
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.put_nowait(indata.copy())
            except queue.Empty:
                pass
            self._warn_stream_distress("Audio queue full, discarding oldest data")

        # Feed the on-screen overlay a normalized level (best-effort, non-blocking)
        if self._osd_send_sock is not None:
            try:
                peak = float(np.abs(indata).max())
                level = min(1.0, peak * OSD_LEVEL_GAIN)
                self._osd_send_sock.sendto(struct.pack("<f", level), str(OSD_SOCK))
            except (OSError, ValueError):
                pass

    def _warn_stream_distress(self, message: str):
        """Rate-limited warning for audio stream issues to avoid log spam."""
        now = time.monotonic()
        if now - self._last_stream_warn >= STREAM_WARN_INTERVAL_SECONDS:
            logger.warning(message)
            self._last_stream_warn = now

    def _signal_handler(self, signum: int, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, shutting down...")
        self._running = False

    def _cleanup(self):
        """Clean up resources."""
        if hasattr(self, "stream") and self.stream is not None:
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

        # Tear down the persistent loop + client (close the client on its own
        # loop, then stop the loop so its thread can exit).
        loop = getattr(self, "_loop", None)
        if loop is not None:
            client = getattr(self, "_client", None)
            if client is not None:
                try:
                    asyncio.run_coroutine_threadsafe(client.aclose(), loop).result(timeout=2)
                except Exception:
                    pass
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass

        release_pid_lock()

    def _osd_script(self) -> str | None:
        """Locate oflow-osd.py (next to this file, user install, or system pkg)."""
        candidates = [
            Path(__file__).resolve().parent / "oflow-osd.py",
            Path.home() / ".local" / "share" / "oflow" / "oflow-osd.py",
            Path("/usr/share/oflow/oflow-osd.py"),
            Path("/usr/lib/oflow/oflow-osd.py"),
        ]
        for path in candidates:
            if path.exists():
                return str(path)
        return None

    def _start_osd(self):
        """Spawn the on-screen recording overlay (best-effort)."""
        if self._osd_proc is not None and self._osd_proc.poll() is None:
            return  # already showing
        script = self._osd_script()
        if not script:
            return
        env = dict(os.environ)
        # gtk4-layer-shell must be preloaded before libwayland-client
        preload = env.get("LD_PRELOAD", "")
        if GTK4_LAYER_SHELL_LIB not in preload:
            env["LD_PRELOAD"] = (
                f"{GTK4_LAYER_SHELL_LIB}:{preload}" if preload else GTK4_LAYER_SHELL_LIB
            )
        try:
            self._osd_proc = subprocess.Popen(
                ["/usr/bin/python3", script],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except (OSError, ValueError) as e:
            logger.debug(f"Failed to start overlay: {e}")
            self._osd_proc = None

    def _stop_osd(self):
        """Tell the overlay to fade out and exit (it also idle-times-out)."""
        if self._osd_send_sock is not None:
            try:
                self._osd_send_sock.sendto(b"stop", str(OSD_SOCK))
            except OSError:
                pass
        self._osd_proc = None

    def _pause_media(self):
        """Pause any currently-playing MPRIS media players (music, video) so
        the dictation mic isn't competing with audio. Only players that were
        actually Playing are remembered, and only those are resumed later.
        Best-effort: silently no-ops if playerctl is missing or fails."""
        self._paused_players = []
        try:
            players = subprocess.run(
                ["playerctl", "-l"], capture_output=True, text=True, timeout=2
            ).stdout.split()
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return
        for player in players:
            try:
                status = subprocess.run(
                    ["playerctl", "-p", player, "status"],
                    capture_output=True, text=True, timeout=2,
                ).stdout.strip()
                if status == "Playing":
                    subprocess.run(["playerctl", "-p", player, "pause"], timeout=2)
                    self._paused_players.append(player)
            except (subprocess.SubprocessError, OSError):
                continue
        if self._paused_players:
            logger.info(f"Paused media players for recording: {self._paused_players}")

    def _resume_media(self):
        """Resume the MPRIS players we paused in _pause_media."""
        for player in self._paused_players:
            try:
                subprocess.run(["playerctl", "-p", player, "play"], timeout=2)
            except (subprocess.SubprocessError, OSError):
                continue
        self._paused_players = []

    def _start_recording(self):
        """Start recording audio."""
        with self._recording_lock:
            if self.is_recording:
                logger.debug("Already recording, ignoring start command")
                return

            self.is_recording = True
            self.audio_data = []
            self._last_stream_warn = 0.0

            try:
                # Start audio stream on-demand (saves CPU when idle).
                # Self-heals a stale PortAudio device list (e.g. after a
                # PipeWire/WirePlumber restart) by reinitializing and retrying.
                self.stream = self._open_audio_stream()

                self.waybar_state.recording()
                self.audio_feedback.play_start()

                # Refresh the kept-warm connection now, while the user speaks,
                # so a keep-alive that expired during idle is hot by release.
                self._schedule_on_loop(self._prewarm_connection())

                # Reload settings in case they changed
                settings = load_settings()
                self.text_processor = TextProcessor(
                    enable_punctuation=settings.get("enableSpokenPunctuation", False),
                    replacements=settings.get("wordReplacements", {}),
                )

                # Show the on-screen recording overlay
                if settings.get("enableOverlay", True):
                    self._start_osd()

                # Pause any playing music/video so it doesn't bleed into the mic
                if settings.get("pauseMediaWhileRecording", True):
                    self._pause_media()

                # Watchdog auto-stops a stuck recording so a missed key-up
                # cannot leave the stream open and leak indefinitely.
                self._recording_watchdog = threading.Timer(
                    MAX_RECORDING_SECONDS, self._on_recording_timeout
                )
                self._recording_watchdog.daemon = True
                self._recording_watchdog.start()
            except Exception:
                logger.exception("Failed to start recording, rolling back")
                self._rollback_recording()
                raise

            logger.info("Recording started")

    def _open_audio_stream(self):
        """Open and start the input stream, recovering from a stale PortAudio
        device list.

        PortAudio snapshots the ALSA/PipeWire device list when it first
        initializes and never refreshes it. After the audio server
        (PipeWire/WirePlumber) restarts, the cached default-source handle is
        gone and ``InputStream`` open fails with ``ALSA error -2`` ('No such
        file or directory'). Reinitializing PortAudio re-enumerates devices, so
        we retry once after a clean reinit before giving up.
        """

        def _make():
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=AUDIO_CHANNELS,
                dtype=np.float32,
                blocksize=1600,  # 100ms chunks → 10 callbacks/sec → 3000 chunks = 5 min
                callback=self._audio_callback,
            )
            stream.start()
            return stream

        try:
            return _make()
        except Exception:
            logger.warning(
                "InputStream open failed; reinitializing PortAudio (device list "
                "likely stale after an audio-server restart) and retrying once"
            )
            try:
                sd._terminate()
                sd._initialize()
            except Exception:
                logger.exception("PortAudio reinitialization failed")
            return _make()

    def _rollback_recording(self):
        """Reset recording state after a failed start. Caller must hold the lock."""
        self.is_recording = False
        if self._recording_watchdog is not None:
            self._recording_watchdog.cancel()
            self._recording_watchdog = None
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self.audio_data = []
        self._resume_media()
        self._stop_osd()
        try:
            self.waybar_state.idle()
        except Exception:
            pass

    def _on_recording_timeout(self):
        """Fired by the watchdog when a recording exceeds MAX_RECORDING_SECONDS."""
        if not self.is_recording:
            return
        logger.warning(
            f"Recording exceeded {MAX_RECORDING_SECONDS}s, auto-stopping to prevent leak"
        )
        self._stop_recording()

    def _stop_recording(self):
        """Stop recording and process audio."""
        # Use lock only for state check and initial transition
        with self._recording_lock:
            if not self.is_recording:
                logger.debug("Not recording, ignoring stop command")
                return

            if self._recording_watchdog is not None:
                self._recording_watchdog.cancel()
                self._recording_watchdog = None

            # Set waybar state immediately while still locked
            self.waybar_state.transcribing()

        # Release lock before audio feedback and processing
        self.audio_feedback.play_stop()

        start = time.perf_counter()

        # Keep recording flag ON for a short grace period so the callback
        # captures the trailing audio. With push-to-talk you release when done,
        # so this is kept small to minimize stop->paste latency.
        time.sleep(0.06)

        with self._recording_lock:
            self.is_recording = False

        # Tiny delay to let any in-flight callback complete
        time.sleep(0.03)

        # Stop and close the audio stream
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        # Resume any media we paused, now that the mic is closed
        self._resume_media()
        # Dismiss the recording overlay
        self._stop_osd()

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

        self._run_on_loop(self._process_transcription())
        total_ms = (time.perf_counter() - start) * 1000
        logger.info(f"Recording stopped ({total_ms:.0f}ms)")

        # Clean up audio data to free memory
        self.audio_data.clear()

        # Drain any remaining items in queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

        # Log memory usage in debug mode
        if DEBUG_MODE and _HAS_PSUTIL:
            try:
                import psutil

                process = psutil.Process()
                mem_mb = process.memory_info().rss / 1024 / 1024
                logger.debug(f"Memory usage: {mem_mb:.1f} MB")
            except Exception:
                pass

    async def _process_transcription(self):
        """Process recorded audio: transcribe, clean up, and type result."""
        settings = load_settings()
        provider = settings.get("provider", "groq")
        api_key = settings.get("groqApiKey") if provider == "groq" else settings.get("openaiApiKey")
        enable_cleanup = settings.get("enableCleanup", True)
        enable_actions = settings.get("enableSpokenActions", True)
        wake_word = settings.get("commandWakeWord", DEFAULT_WAKE_WORD)
        fast_max_words = settings.get("fastModeMaxWords", DEFAULT_FAST_MODE_MAX_WORDS)

        if not api_key:
            error_msg = f"❌ {provider.capitalize()} API key not set. Configure it in Settings."
            logger.error(error_msg)
            self.waybar_state.error("API key not set")
            self.audio_feedback.play_error()
            return

        # Combine all audio
        audio = np.concatenate(self.audio_data, axis=0).flatten()
        duration = len(audio) / SAMPLE_RATE

        # Clear audio_data immediately after concatenation to free memory
        self.audio_data.clear()

        # Validate
        valid, error = AudioValidator.validate(audio)
        if not valid:
            logger.warning(f"Audio validation failed: {error}")
            self.waybar_state.idle()
            return

        # Reuse the persistent, kept-warm client (created on this loop's thread).
        client = self._client
        if client is None:  # defensive: loop torn down mid-shutdown
            self.waybar_state.idle()
            return

        logger.info(f"Processing {duration:.1f}s audio")

        # Transcribe (auto-chunks long audio for reliability)
        t0 = time.perf_counter()
        raw_text = await transcribe_audio_chunked(client, audio, api_key, provider)
        t1 = time.perf_counter()

        # With spoken actions on, "press enter"/"new line"/etc. are handled by
        # the segmenter below (mid-stream, not just at the end). With it off, we
        # fall back to the legacy end-of-dictation submit-keyword behavior.
        submit = False
        if not enable_actions:
            submit_keywords = settings.get("submitKeywords", SUBMIT_KEYWORDS_DEFAULT)
            if raw_text and submit_keywords:
                raw_text, submit = extract_submit_keyword(raw_text, submit_keywords)

        if not raw_text:
            if submit:
                # User said only "enter" -> just press Enter, nothing to paste
                press_enter()
                self.waybar_state.idle()
                return
            logger.error(
                "❌ Transcription produced no text. "
                "See log above for the reason "
                "(filtered as hallucination, empty API response, or auth/network error)."
            )
            self.waybar_state.error("No text")
            self.audio_feedback.play_error()
            return

        logger.info(f"Transcription: {(t1 - t0) * 1000:.0f}ms | Raw: {raw_text[:80]}")

        # Apply text processing (spoken punctuation, replacements)
        raw_text = self.text_processor.process(raw_text)

        # Cleanup (if enabled) — fast mode skips it on short dictations.
        skip_for_speed = should_skip_cleanup(raw_text, fast_max_words)
        if enable_cleanup and not skip_for_speed:
            t0 = time.perf_counter()
            cleaned_text = await cleanup_text(client, raw_text, api_key, provider)
            t1 = time.perf_counter()
            logger.info(f"Cleanup: {(t1 - t0) * 1000:.0f}ms")
        else:
            cleaned_text = raw_text
            if enable_cleanup and skip_for_speed:
                logger.info(
                    f"Fast mode: skipped cleanup ({len(raw_text.split())} words "
                    f"<= {fast_max_words})"
                )

        # Output: run "<wake word> command" voice commands as real keystrokes
        # (default), or paste-and-optionally-submit on the legacy path.
        if enable_actions:
            self._last_output_chars = output_with_actions(
                cleaned_text, wake_word, self._last_output_chars
            )
        else:
            type_text(cleaned_text)
            if submit:
                time.sleep(0.05)  # let the paste land before Enter
                press_enter()
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
                except Exception:
                    # A failed recording must never tear down the IPC server,
                    # otherwise the global shortcut goes permanently dead until
                    # oflow is manually restarted. Log, signal the failure, and
                    # keep listening.
                    logger.exception("Command handling failed; server staying alive")
                    try:
                        self.waybar_state.error("Recording failed")
                    except Exception:
                        pass
                    try:
                        self.audio_feedback.play_error()
                    except Exception:
                        pass
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

# Global server reference for stdin shutdown
_server: VoiceDictationServer | None = None


def stdin_listener() -> None:
    """Listen for shutdown command from Tauri sidecar (stdin)."""
    global _server
    while True:
        try:
            line = sys.stdin.readline()
            if not line:  # EOF
                break
            cmd = line.strip().lower()
            if cmd == "shutdown":
                logger.info("Received shutdown command from Tauri")
                if _server:
                    _server._running = False
                break
        except (EOFError, OSError):
            break


def main() -> None:
    """Main entry point for Oflow."""
    global _server

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

        # Start stdin listener for Tauri sidecar shutdown
        stdin_thread = threading.Thread(target=stdin_listener, daemon=True)
        stdin_thread.start()

        try:
            _server = VoiceDictationServer()
            logger.info("Backend ready")  # Signal to Tauri that we're ready
            sys.stdout.flush()
            _server.run()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            sys.exit(0)
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
