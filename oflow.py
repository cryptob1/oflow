#!/usr/bin/env python3
"""
Oflow v2 - WhisperFlow-inspired voice dictation with memory.

A production-ready voice dictation system that records audio, transcribes it using
OpenAI's Whisper API, cleans it up with GPT-4o-mini, and types it into the active window.

Architecture:
  Audio Input → Whisper (transcribe) → GPT-4o-mini (cleanup) → Storage → Memory Builder

Features:
  - Global hotkey support (Super+I)
  - Audio validation before API calls
  - Automatic grammar and punctuation correction
  - Optional memory system for learning user patterns
  - Unix socket IPC for communication with Tauri frontend
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
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import AsyncIterator, TypedDict

import httpx
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_groq import ChatGroq

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
MIN_AUDIO_DURATION_SECONDS = 0.5  # Minimum recording duration (increased to prevent hallucinations)
MIN_AUDIO_AMPLITUDE = 0.02  # Minimum amplitude to detect speech (increased for better detection)

# API configuration
API_TIMEOUT_SECONDS = 15.0  # Timeout for API requests
MAX_RETRIES = 3  # Maximum retry attempts for API calls

# File paths
TRANSCRIPTS_FILE = Path.home() / ".oflow" / "transcripts.jsonl"
MEMORIES_FILE = Path.home() / ".oflow" / "memories.json"
SETTINGS_FILE = Path.home() / ".oflow" / "settings.json"

# Waybar state file (in XDG_RUNTIME_DIR for fast access)
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "oflow"
STATE_FILE = RUNTIME_DIR / "state"

# Memory system configuration
MEMORY_BUILD_THRESHOLD = 1000  # Build memories every N transcripts
MAX_MEMORIES_IN_CONTEXT = 5  # Maximum memories to include in cleanup prompt

# API key format validation
OPENAI_API_KEY_PATTERN = re.compile(r"^sk-")
GROQ_API_KEY_PATTERN = re.compile(r"^gsk_")

# API endpoints
OPENAI_WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# ============================================================================
# Environment Variables and Configuration
# ============================================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

# Default settings (can be overridden by settings.json)
DEFAULT_ENABLE_CLEANUP = os.getenv("ENABLE_CLEANUP", "true").lower() == "true"
DEFAULT_ENABLE_MEMORY = os.getenv("ENABLE_MEMORY", "false").lower() == "true"
DEFAULT_PROVIDER = os.getenv("PROVIDER", "groq")  # Default to Groq (faster)


def ensure_data_dir() -> None:
    """Ensure ~/.oflow directory and default files exist."""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Create default settings file if it doesn't exist
    if not SETTINGS_FILE.exists():
        default_settings = {
            'enableCleanup': DEFAULT_ENABLE_CLEANUP,
            'enableMemory': DEFAULT_ENABLE_MEMORY,
            'provider': DEFAULT_PROVIDER,
        }
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(default_settings, f, indent=2)

    # Create empty transcripts file if it doesn't exist
    if not TRANSCRIPTS_FILE.exists():
        TRANSCRIPTS_FILE.touch()


def load_settings() -> dict:
    """
    Load settings from ~/.oflow/settings.json.
    Falls back to environment variable defaults if file doesn't exist.

    Returns:
        dict with settings including provider, API keys, and feature flags
    """
    ensure_data_dir()

    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE) as f:
                settings = json.load(f)
                return {
                    'enableCleanup': settings.get('enableCleanup', DEFAULT_ENABLE_CLEANUP),
                    'enableMemory': settings.get('enableMemory', DEFAULT_ENABLE_MEMORY),
                    'openaiApiKey': settings.get('openaiApiKey') or OPENAI_API_KEY,
                    'groqApiKey': settings.get('groqApiKey') or GROQ_API_KEY,
                    'provider': settings.get('provider', DEFAULT_PROVIDER),
                    # New settings for VoxType-like features
                    'audioFeedbackTheme': settings.get('audioFeedbackTheme', 'default'),
                    'audioFeedbackVolume': settings.get('audioFeedbackVolume', 0.3),
                    'iconTheme': settings.get('iconTheme', 'minimal'),
                    'enableSpokenPunctuation': settings.get('enableSpokenPunctuation', False),
                    'wordReplacements': settings.get('wordReplacements', {}),
                }
    except Exception as e:
        logger.warning(f"Failed to load settings from {SETTINGS_FILE}: {e}")

    return {
        'enableCleanup': DEFAULT_ENABLE_CLEANUP,
        'enableMemory': DEFAULT_ENABLE_MEMORY,
        'openaiApiKey': OPENAI_API_KEY,
        'groqApiKey': GROQ_API_KEY,
        'provider': DEFAULT_PROVIDER,
        # Defaults for new features
        'audioFeedbackTheme': 'default',
        'audioFeedbackVolume': 0.3,
        'iconTheme': 'minimal',
        'enableSpokenPunctuation': False,
        'wordReplacements': {},
    }


# ============================================================================
# Custom Exceptions
# ============================================================================

def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    try:
        os.kill(pid, 0)  # Signal 0 checks if process exists
        return True
    except (OSError, ProcessLookupError):
        return False


def acquire_pid_lock() -> bool:
    """
    Acquire exclusive lock via PID file.

    Returns True if lock acquired (we should run).
    Returns False if another instance is already running.
    """
    # Check if PID file exists
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
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

    # Write our PID
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

    return True


def release_pid_lock() -> None:
    """Release the PID file lock."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        logger.warning(f"Error removing PID file: {e}")


class OflowError(Exception):
    """Base exception for Oflow errors."""
    pass


class ConfigurationError(OflowError):
    """Raised when configuration is invalid."""
    pass


class AudioValidationError(OflowError):
    """Raised when audio validation fails."""
    pass


class TranscriptionError(OflowError):
    """Raised when transcription fails."""
    pass


class APIError(OflowError):
    """Raised when API calls fail."""
    pass


# ============================================================================
# Waybar State Manager
# ============================================================================

class WaybarState:
    """
    Manages Waybar status bar integration.

    Writes state to a file that Waybar can read via custom/oflow module.
    State includes: idle, recording, transcribing, error
    """

    # Icon themes (like VoxType)
    ICON_THEMES = {
        "emoji": {"idle": "🎙️", "recording": "🎤", "transcribing": "⏳", "error": "❌"},
        "nerd-font": {"idle": "\uf130", "recording": "\uf111", "transcribing": "\uf110", "error": "\uf131"},
        "minimal": {"idle": "○", "recording": "●", "transcribing": "◐", "error": "×"},
        "text": {"idle": "[MIC]", "recording": "[REC]", "transcribing": "[...]", "error": "[ERR]"},
    }

    def __init__(self, theme: str = "minimal"):
        self.theme = theme
        self.icons = self.ICON_THEMES.get(theme, self.ICON_THEMES["minimal"])
        self._ensure_runtime_dir()

    def _ensure_runtime_dir(self):
        """Ensure runtime directory exists."""
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    def set_state(self, state: str, tooltip: str = ""):
        """
        Update Waybar state file.

        Args:
            state: One of 'idle', 'recording', 'transcribing', 'error'
            tooltip: Optional tooltip text
        """
        icon = self.icons.get(state, self.icons["idle"])

        # Waybar JSON format
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
    """
    Generates audio feedback tones for recording events.

    Supports multiple themes like VoxType:
    - default: Pleasant two-tone beeps
    - subtle: Quiet clicks
    - mechanical: Typewriter-like sounds
    - silent: No sounds
    """

    def __init__(self, theme: str = "default", volume: float = 0.3):
        self.theme = theme
        self.volume = max(0.0, min(1.0, volume))

    def _generate_tone(self, frequency: float, duration_ms: int, fade_ms: int = 10) -> np.ndarray:
        """Generate a sine wave tone with fade in/out."""
        num_samples = int(SAMPLE_RATE * duration_ms / 1000)
        fade_samples = int(SAMPLE_RATE * fade_ms / 1000)

        t = np.linspace(0, duration_ms / 1000, num_samples, False)
        tone = np.sin(2 * np.pi * frequency * t)

        # Apply fade envelope
        envelope = np.ones(num_samples)
        if fade_samples > 0 and num_samples > fade_samples * 2:
            fade_in = np.linspace(0, 1, fade_samples)
            fade_out = np.linspace(1, 0, fade_samples)
            envelope[:fade_samples] = fade_in
            envelope[-fade_samples:] = fade_out

        return (tone * envelope * self.volume).astype(np.float32)

    def _generate_two_tone(self, freq1: float, freq2: float, duration_ms: int) -> np.ndarray:
        """Generate a two-tone sound (rising or falling)."""
        half_duration = duration_ms // 2
        tone1 = self._generate_tone(freq1, half_duration, fade_ms=15)
        tone2 = self._generate_tone(freq2, half_duration, fade_ms=15)
        return np.concatenate([tone1, tone2])

    def _generate_click(self, duration_ms: int = 20) -> np.ndarray:
        """Generate a short click sound."""
        num_samples = int(SAMPLE_RATE * duration_ms / 1000)
        t = np.arange(num_samples)
        # Exponential decay noise burst
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
            else:  # default
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
            else:  # default
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
    """
    Post-processes transcribed text with spoken punctuation and word replacements.

    Applied before LLM cleanup for fast, deterministic corrections.
    """

    # Spoken punctuation map (like VoxType)
    PUNCTUATION_MAP = [
        # Multi-word phrases first (order matters)
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
        # Single words
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

    def __init__(self, enable_punctuation: bool = False, replacements: dict = None):
        self.enable_punctuation = enable_punctuation
        self.replacements = replacements or {}

    def process(self, text: str) -> str:
        """Apply all text transformations."""
        if not text:
            return text

        result = text

        # Apply spoken punctuation first
        if self.enable_punctuation:
            result = self._apply_punctuation(result)

        # Apply custom replacements
        if self.replacements:
            result = self._apply_replacements(result)

        return result

    def _apply_punctuation(self, text: str) -> str:
        """Convert spoken punctuation to symbols."""
        result = text

        for phrase, symbol in self.PUNCTUATION_MAP:
            # Case-insensitive word boundary replacement
            pattern = re.compile(r'\b' + re.escape(phrase) + r'\b', re.IGNORECASE)
            result = pattern.sub(symbol, result)

        # Clean up spacing around punctuation
        result = self._clean_punctuation_spacing(result)

        return result

    def _apply_replacements(self, text: str) -> str:
        """Apply custom word replacements."""
        result = text

        for word, replacement in self.replacements.items():
            pattern = re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE)
            result = pattern.sub(replacement, result)

        return result

    def _clean_punctuation_spacing(self, text: str) -> str:
        """Clean up spacing around punctuation marks."""
        result = text

        # Remove space before punctuation
        for punct in ['.', ',', '?', '!', ':', ';', ')', ']', '}']:
            result = result.replace(f" {punct}", punct)

        # Remove space after opening brackets
        for punct in ['(', '[', '{']:
            result = result.replace(f"{punct} ", punct)

        # Clean newlines/tabs
        result = result.replace(" \n", "\n").replace("\n ", "\n")
        result = result.replace(" \t", "\t").replace("\t ", "\t")

        return result


# LangGraph State
class TranscriptionState(TypedDict):
    audio_base64: str
    audio_duration_seconds: float  # Track duration for hallucination detection
    raw_transcript: str
    cleaned_text: str
    timestamp: str
    memories: list[str]
    error: str | None


# Hallucination detection: max words per second of audio (speaking rate ~150 WPM = 2.5 WPS)
MAX_WORDS_PER_SECOND = 4.0  # Allow some margin


class EventType(Enum):
    STT_OUTPUT = "stt_output"
    STT_ERROR = "stt_error"


@dataclass
class VoiceEvent:
    type: EventType
    data: str | None = None
    error: str | None = None


class AudioValidator:
    """Validates audio data before sending to transcription API."""

    @staticmethod
    def validate(audio: np.ndarray) -> tuple[bool, str | None]:
        """
        Validate audio data for transcription.

        Checks that audio is not empty, meets minimum duration, and has sufficient
        amplitude to indicate speech.

        Args:
            audio: Audio data as numpy array (float32, normalized to [-1, 1])

        Returns:
            Tuple of (is_valid, error_message). If valid, error_message is None.
            If invalid, error_message describes the issue.

        Raises:
            AudioValidationError: If audio format is invalid (should not happen).
        """
        if len(audio) == 0:
            return False, "Empty audio"

        abs_audio = np.abs(audio)
        if len(abs_audio) == 0:
            return False, "Empty audio"

        # Check minimum duration
        min_samples = int(SAMPLE_RATE * MIN_AUDIO_DURATION_SECONDS)
        if len(audio) < min_samples:
            duration = len(audio) / SAMPLE_RATE
            return False, (
                f"Audio too short ({duration:.2f}s, "
                f"need >{MIN_AUDIO_DURATION_SECONDS}s)"
            )

        # Check minimum amplitude (speech detection)
        max_amplitude = np.max(abs_audio)
        if max_amplitude < MIN_AUDIO_AMPLITUDE:
            return False, "Audio too quiet (no speech detected)"

        return True, None


class AudioProcessor:
    """Processes audio data for transcription."""

    @staticmethod
    def normalize(audio: np.ndarray) -> np.ndarray:
        """
        Normalize audio to target amplitude.

        Normalizes audio so the maximum amplitude equals NORMALIZATION_TARGET.
        This ensures consistent volume levels for transcription.

        Args:
            audio: Audio data as numpy array (float32, normalized to [-1, 1])

        Returns:
            Normalized audio array with max amplitude = NORMALIZATION_TARGET
        """
        if len(audio) == 0:
            return audio
        max_val = np.max(np.abs(audio))
        if max_val > 0:
            return audio / max_val * NORMALIZATION_TARGET
        return audio

    @staticmethod
    def to_base64_wav(audio: np.ndarray, sample_rate: int) -> str:
        """
        Convert audio array to base64-encoded WAV file.

        Args:
            audio: Audio data as numpy array (float32, normalized to [-1, 1])
            sample_rate: Sample rate in Hz (typically 16000)

        Returns:
            Base64-encoded WAV file as string

        Raises:
            ValueError: If audio conversion fails
        """
        try:
            # Convert float32 [-1, 1] to int16 [-32768, 32767]
            audio_int16 = (audio * 32767).astype(np.int16)

            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav_file:
                wav_file.setnchannels(AUDIO_CHANNELS)
                wav_file.setsampwidth(2)  # 16-bit = 2 bytes per sample
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(audio_int16.tobytes())

            buffer.seek(0)
            return base64.b64encode(buffer.read()).decode("utf-8")
        except Exception as e:
            raise ValueError(f"Failed to encode audio to WAV: {e}") from e


class WhisperAPI:
    """
    Whisper API client for speech-to-text transcription.

    Supports both OpenAI and Groq backends. Groq is ~200x faster.
    """

    def __init__(self, api_key: str, provider: str = "openai") -> None:
        """
        Initialize Whisper API client.

        Args:
            api_key: API key (OpenAI or Groq)
            provider: "openai" or "groq"

        Raises:
            ConfigurationError: If API key is invalid
        """
        if not api_key:
            raise ConfigurationError(f"{provider.title()} API key is required")

        self.api_key = api_key
        self.provider = provider
        self.client = httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS)

        if provider == "groq":
            self.api_url = GROQ_WHISPER_URL
            self.model = "whisper-large-v3-turbo"  # Fastest Groq model
        else:
            self.api_url = OPENAI_WHISPER_URL
            self.model = "whisper-1"

    async def transcribe(self, audio_base64: str) -> str:
        """
        Transcribe audio using Whisper API.

        Args:
            audio_base64: Base64-encoded WAV audio file

        Returns:
            Transcribed text as string

        Raises:
            APIError: If API request fails
            TranscriptionError: If transcription is empty
        """
        try:
            audio_bytes = base64.b64decode(audio_base64)
        except Exception as e:
            raise TranscriptionError(f"Failed to decode audio: {e}") from e

        files = {
            "file": ("audio.wav", audio_bytes, "audio/wav"),
        }
        data = {
            "model": self.model,
            "response_format": "json",
            "language": "en",  # Specify language to reduce hallucinations
            "prompt": "",  # Empty prompt - just transcribe what's said
        }

        try:
            response = await self.client.post(
                self.api_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files=files,
                data=data,
            )
        except httpx.TimeoutException as e:
            raise APIError(f"Whisper API timeout: {e}") from e
        except httpx.RequestError as e:
            raise APIError(f"Whisper API request failed: {e}") from e

        if response.status_code != 200:
            error_text = response.text[:500]  # Show more error details
            logger.error(f"Whisper API error: status={response.status_code}, response={error_text}")
            raise APIError(
                f"Whisper API error ({response.status_code}): {error_text}"
            )

        try:
            result = response.json()
            text = result.get("text", "")
        except Exception as e:
            raise APIError(f"Failed to parse Whisper API response: {e}") from e

        if not text or not text.strip():
            raise TranscriptionError("Empty transcription from Whisper API")

        return text.strip()

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()


class TextCleanupAgent:
    """Node 2: LLM cleanup and formatting (supports OpenAI and Groq)"""

    def __init__(self, api_key: str, provider: str = "openai"):
        if provider == "groq":
            self.llm = ChatGroq(
                model="llama-3.1-8b-instant",  # 560 tokens/sec - fastest for cleanup
                temperature=0.3,
                groq_api_key=api_key,
            )
        else:
            self.llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0.3,
                openai_api_key=api_key,
            )

    async def cleanup(self, raw_text: str, memories: list[str] | None = None) -> str:
        memory_context = ""
        if memories:
            memory_context = f"\n\nUser context (learned patterns):\n" + "\n".join(f"- {m}" for m in memories[:5])

        prompt = f"""Clean up this voice transcript. ONLY fix minor errors - do NOT add, expand, or change the meaning.

Rules:
- Fix spelling and grammar mistakes
- Fix punctuation
- Remove filler words (um, uh, like) if excessive
- Keep the EXACT same meaning and length
- Do NOT add new content or expand on what was said
- Do NOT interpret or elaborate
- If the input is short, the output should be short{memory_context}

Input: {raw_text}

Output (cleaned text only, nothing else):"""

        response = await self.llm.ainvoke(prompt)
        return response.content.strip()


class StorageManager:
    """Node 3: Flat file storage"""
    
    def __init__(self):
        self.transcripts_file = TRANSCRIPTS_FILE
        self.memories_file = MEMORIES_FILE
        self.transcripts_file.parent.mkdir(parents=True, exist_ok=True)

    def save_transcript(self, raw: str, cleaned: str, timestamp: str):
        """Append transcript to JSONL file"""
        entry = {
            "timestamp": timestamp,
            "raw": raw,
            "cleaned": cleaned,
        }
        
        with open(self.transcripts_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        
        logger.info(f"Saved transcript #{self.count_transcripts()}")

    def count_transcripts(self) -> int:
        """Count total transcripts"""
        if not self.transcripts_file.exists():
            return 0
        
        with open(self.transcripts_file) as f:
            return sum(1 for _ in f)

    def get_recent_transcripts(self, n: int = 1000) -> list[dict]:
        """Get last N transcripts"""
        if not self.transcripts_file.exists():
            return []
        
        with open(self.transcripts_file) as f:
            lines = f.readlines()
            return [json.loads(line) for line in lines[-n:]]

    def load_memories(self) -> list[str]:
        """Load existing memories"""
        if not self.memories_file.exists():
            return []
        
        with open(self.memories_file) as f:
            data = json.load(f)
            return data.get("memories", [])

    def save_memories(self, memories: list[str]):
        """Save memories to file"""
        data = {
            "last_updated": datetime.now().isoformat(),
            "memories": memories,
        }
        
        with open(self.memories_file, "w") as f:
            json.dump(data, f, indent=2)
        
        logger.info(f"Saved {len(memories)} memories")


class MemoryBuilder:
    """Background job: Build memories from transcripts"""

    def __init__(self, api_key: str, provider: str = "openai"):
        if provider == "groq":
            self.llm = ChatGroq(
                model="llama-3.1-8b-instant",  # Fast model for memory building
                temperature=0.5,
                groq_api_key=api_key,
            )
        else:
            self.llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0.5,
                openai_api_key=api_key,
            )

    async def build_memories(self, transcripts: list[dict]) -> list[str]:
        """Analyze transcripts and extract patterns/memories"""
        if not transcripts:
            return []
        
        # Concatenate recent transcripts
        transcript_text = "\n\n".join(
            f"[{t['timestamp']}] {t['cleaned']}" for t in transcripts[-1000:]
        )
        
        prompt = f"""Analyze these voice transcripts and extract patterns about the user's speech and writing preferences:

1. Common technical terms or proper nouns they use (e.g., "LangGraph", "GPT-4o-mini")
2. Preferred writing style (formal/casual, punctuation habits)
3. Domain-specific vocabulary
4. Frequently mentioned topics or concepts

Output 5-10 concise bullet points that will help improve future transcription cleanup.

TRANSCRIPTS:
{transcript_text[:8000]}  # Limit to ~8K chars

PATTERNS (output as numbered list):"""

        response = await self.llm.ainvoke(prompt)
        
        # Parse response into list
        memories = []
        for line in response.content.strip().split("\n"):
            line = line.strip()
            if line and (line[0].isdigit() or line.startswith("-")):
                # Remove numbering/bullets
                cleaned = line.lstrip("0123456789.-) ")
                if cleaned:
                    memories.append(cleaned)
        
        return memories


# LangGraph Pipeline
def create_transcription_graph(api_key: str, enable_cleanup: bool = True, enable_memory: bool = False, provider: str = "openai"):
    """Create the LangGraph workflow

    Args:
        api_key: API key for Whisper and LLM (OpenAI or Groq)
        enable_cleanup: Whether to use LLM for text cleanup
        enable_memory: Whether to use the memory system for learning patterns
        provider: "openai" or "groq" (Groq is ~200x faster)
    """

    whisper = WhisperAPI(api_key, provider=provider)
    cleanup_agent = TextCleanupAgent(api_key, provider=provider) if enable_cleanup else None
    storage = StorageManager()
    memory_builder = MemoryBuilder(api_key, provider=provider) if enable_memory else None

    async def node_whisper(state: TranscriptionState) -> TranscriptionState:
        """Node 1: Transcribe with Whisper"""
        try:
            raw_transcript = await whisper.transcribe(state["audio_base64"])
            state["raw_transcript"] = raw_transcript
            state["cleaned_text"] = raw_transcript  # Default if cleanup disabled
            logger.info(f"Whisper: {raw_transcript[:100]}...")
        except Exception as e:
            state["error"] = str(e)
            logger.error(f"Whisper failed: {e}")
        return state

    async def node_cleanup(state: TranscriptionState) -> TranscriptionState:
        """Node 2: Clean up with GPT-4o-mini"""
        if state.get("error") or not enable_cleanup:
            return state

        try:
            cleaned = await cleanup_agent.cleanup(
                state["raw_transcript"],
                state.get("memories", [])
            )
            state["cleaned_text"] = cleaned
            logger.info(f"Cleaned: {cleaned[:100]}...")
        except Exception as e:
            logger.warning(f"Cleanup failed, using raw: {e}")
            # Fall back to raw transcript
        return state

    async def node_storage(state: TranscriptionState) -> TranscriptionState:
        """Node 3: Store transcript"""
        if state.get("error"):
            return state

        try:
            storage.save_transcript(
                raw=state["raw_transcript"],
                cleaned=state["cleaned_text"],
                timestamp=state["timestamp"],
            )

            # Check if we should build memories
            if enable_memory and storage.count_transcripts() % MEMORY_BUILD_THRESHOLD == 0:
                logger.info(f"Threshold reached, building memories...")
                recent = storage.get_recent_transcripts(MEMORY_BUILD_THRESHOLD)
                memories = await memory_builder.build_memories(recent)
                storage.save_memories(memories)
                state["memories"] = memories
        except Exception as e:
            logger.warning(f"Storage failed: {e}")

        return state

    # Build graph
    workflow = StateGraph(TranscriptionState)

    workflow.add_node("whisper", node_whisper)
    if enable_cleanup:
        workflow.add_node("cleanup", node_cleanup)
        workflow.add_node("storage", node_storage)
        
        workflow.set_entry_point("whisper")
        workflow.add_edge("whisper", "cleanup")
        workflow.add_edge("cleanup", "storage")
        workflow.add_edge("storage", END)
    else:
        workflow.add_node("storage", node_storage)
        
        workflow.set_entry_point("whisper")
        workflow.add_edge("whisper", "storage")
        workflow.add_edge("storage", END)
    
    return workflow.compile()


async def process_audio_with_graph(audio: np.ndarray) -> AsyncIterator[VoiceEvent]:
    """Process audio through LangGraph pipeline"""

    # Validate
    normalized_audio = AudioProcessor.normalize(audio)
    valid, error_msg = AudioValidator.validate(normalized_audio)
    if not valid:
        yield VoiceEvent(type=EventType.STT_ERROR, error=error_msg)
        return

    # Convert to base64
    audio_base64 = AudioProcessor.to_base64_wav(normalized_audio, SAMPLE_RATE)

    # Load settings from JSON file (allows UI to control behavior)
    settings = load_settings()
    provider = settings.get('provider', 'openai')
    enable_cleanup = settings['enableCleanup']
    enable_memory = settings['enableMemory']

    # Select API key based on provider
    if provider == "groq":
        api_key = settings.get('groqApiKey')
        if not api_key:
            yield VoiceEvent(type=EventType.STT_ERROR, error="Groq API key not set. Configure it in Settings.")
            return
    else:
        api_key = settings.get('openaiApiKey')
        if not api_key:
            yield VoiceEvent(type=EventType.STT_ERROR, error="OpenAI API key not set. Configure it in Settings.")
            return

    # Log settings (mask API key for security)
    masked_key = api_key[:8] + "..." + api_key[-4:] if api_key and len(api_key) > 12 else "NOT SET"
    logger.info(f"Settings: provider={provider}, cleanup={enable_cleanup}, memory={enable_memory}, api_key={masked_key}")

    # Load existing memories
    storage = StorageManager()
    memories = storage.load_memories() if enable_memory else []

    # Create initial state
    initial_state: TranscriptionState = {
        "audio_base64": audio_base64,
        "raw_transcript": "",
        "cleaned_text": "",
        "timestamp": datetime.now().isoformat(),
        "memories": memories,
        "error": None,
    }

    # Run through graph
    try:
        graph = create_transcription_graph(
            api_key=api_key,
            enable_cleanup=enable_cleanup,
            enable_memory=enable_memory,
            provider=provider
        )
        final_state = await graph.ainvoke(initial_state)

        if final_state.get("error"):
            yield VoiceEvent(type=EventType.STT_ERROR, error=final_state["error"])
        else:
            # Return cleaned text (or raw if cleanup disabled)
            yield VoiceEvent(
                type=EventType.STT_OUTPUT,
                data=final_state["cleaned_text"]
            )
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        yield VoiceEvent(type=EventType.STT_ERROR, error=str(e))


def validate_configuration() -> None:
    """
    Validate application configuration.

    Checks that all required configuration is present and valid.
    API key can come from either environment variable or settings file.

    Raises:
        ConfigurationError: If configuration is invalid
    """
    # Ensure data directory exists first (needed to read settings)
    TRANSCRIPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Check for API key based on provider
    settings = load_settings()
    provider = settings.get('provider', 'openai')

    if provider == "groq":
        api_key = settings.get('groqApiKey')
        if not api_key:
            logger.warning(
                "Groq API key not configured. "
                "Set it in the UI Settings or via GROQ_API_KEY environment variable."
            )
        elif not GROQ_API_KEY_PATTERN.match(api_key):
            logger.warning(
                "Invalid Groq API key format. "
                "Expected format: gsk_..."
            )
    else:
        api_key = settings.get('openaiApiKey')
        if not api_key:
            logger.warning(
                "OpenAI API key not configured. "
                "Set it in the UI Settings or via OPENAI_API_KEY environment variable."
            )
        elif not OPENAI_API_KEY_PATTERN.match(api_key):
            logger.warning(
                "Invalid OpenAI API key format. "
                "Expected format: sk-..."
            )

    logger.info("Configuration validated successfully")




def type_text(text: str) -> None:
    """
    Type text into the active window.

    Attempts to type text using wtype (Wayland) or xdotool (X11).
    Falls back to copying to clipboard if typing fails.

    Args:
        text: Text to type
    """
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


class ChunkedTranscriber:
    """
    Handles chunked audio transcription for low-latency results.

    Sends audio chunks to Groq while recording, so by the time the user
    releases the key, most audio is already transcribed.
    """

    CHUNK_SECONDS = 0.75  # Send chunks every 0.75 seconds

    def __init__(self, api_key: str, provider: str = "groq"):
        self.api_key = api_key
        self.provider = provider
        self.transcripts: list[str] = []
        self.pending_audio: list[np.ndarray] = []
        self._client: httpx.AsyncClient | None = None

        if provider == "groq":
            self.whisper_url = GROQ_WHISPER_URL
            self.whisper_model = "whisper-large-v3-turbo"
            self.llm_url = "https://api.groq.com/openai/v1/chat/completions"
            self.llm_model = "llama-3.1-8b-instant"
        else:
            self.whisper_url = OPENAI_WHISPER_URL
            self.whisper_model = "whisper-1"
            self.llm_url = "https://api.openai.com/v1/chat/completions"
            self.llm_model = "gpt-4o-mini"

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for current event loop."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _transcribe_chunk(self, audio: np.ndarray) -> str:
        """Transcribe a single audio chunk."""
        # Normalize and convert to WAV
        if len(audio) == 0:
            return ""

        normalized = AudioProcessor.normalize(audio)
        audio_int16 = (normalized * 32767).astype(np.int16)

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(audio_int16.tobytes())
        buffer.seek(0)
        audio_bytes = buffer.read()

        try:
            client = self._get_client()
            response = await client.post(
                self.whisper_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": ("audio.wav", audio_bytes, "audio/wav")},
                data={
                    "model": self.whisper_model,
                    "response_format": "json",
                    "language": "en",
                },
            )
            if response.status_code == 200:
                return response.json().get("text", "").strip()
            else:
                logger.error(f"Whisper API error: {response.status_code}")
                return ""
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return ""

    async def _cleanup_text(self, text: str) -> str:
        """Clean up text using LLM."""
        if not text.strip():
            return ""

        try:
            client = self._get_client()
            response = await client.post(
                self.llm_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.llm_model,
                    "messages": [
                        {"role": "system", "content": "Fix grammar/punctuation. Output only the cleaned text, nothing else."},
                        {"role": "user", "content": text}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 500,
                },
            )
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
            return text  # Return original on error
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            return text

    def add_audio(self, audio: np.ndarray):
        """Add audio data to pending buffer."""
        self.pending_audio.append(audio)

    def get_pending_duration(self) -> float:
        """Get duration of pending audio in seconds."""
        if not self.pending_audio:
            return 0.0
        total_samples = sum(len(a) for a in self.pending_audio)
        return total_samples / SAMPLE_RATE

    async def process_pending_chunk(self) -> str | None:
        """Process pending audio if we have enough for a chunk."""
        if self.get_pending_duration() < self.CHUNK_SECONDS:
            return None

        # Combine pending audio into a chunk
        chunk = np.concatenate(self.pending_audio, axis=0).flatten()
        self.pending_audio = []

        # Validate
        valid, _ = AudioValidator.validate(chunk)
        if not valid:
            return ""

        # Transcribe
        text = await self._transcribe_chunk(chunk)
        if text:
            self.transcripts.append(text)
            logger.info(f"Chunk transcribed: {text[:30]}...")
        return text

    async def finalize(self, enable_cleanup: bool = True) -> str:
        """
        Finalize transcription: process remaining audio and cleanup.

        This is called when the user releases the key. It processes
        the last chunk and runs cleanup in parallel for speed.
        """
        import time
        start = time.perf_counter()

        # Create a fresh client for this event loop
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get any remaining audio
            final_audio = None
            if self.pending_audio:
                final_audio = np.concatenate(self.pending_audio, axis=0).flatten()
                self.pending_audio = []

            previous_text = " ".join(self.transcripts)

            # Task 1: Transcribe final chunk (if any)
            async def transcribe_final():
                if final_audio is not None and len(final_audio) > SAMPLE_RATE * 0.1:
                    valid, _ = AudioValidator.validate(final_audio)
                    if valid:
                        return await self._transcribe_with_client(client, final_audio)
                return ""

            # Task 2: Cleanup previous transcripts (if enabled)
            async def cleanup_previous():
                if enable_cleanup and previous_text.strip():
                    return await self._cleanup_with_client(client, previous_text)
                return previous_text

            # Run in parallel
            if enable_cleanup:
                final_text, cleaned_previous = await asyncio.gather(
                    transcribe_final(),
                    cleanup_previous()
                )
            else:
                final_text = await transcribe_final()
                cleaned_previous = previous_text

            # Combine results
            if final_text:
                result = (cleaned_previous + " " + final_text).strip()
            else:
                result = cleaned_previous.strip()

        latency = (time.perf_counter() - start) * 1000
        logger.info(f"Finalize latency: {latency:.0f}ms")

        return result

    # Common Whisper hallucinations to filter out
    HALLUCINATION_PATTERNS = [
        "thank you", "thanks for watching", "subscribe", "like and subscribe",
        "see you next time", "bye", "goodbye", "thanks for listening",
        "please subscribe", "don't forget to", "hit the bell", "leave a comment",
        "check out", "follow me", "peace", "take care", "have a great",
        "i'll see you", "catch you", "until next time", "stay tuned",
    ]

    async def _transcribe_with_client(self, client: httpx.AsyncClient, audio: np.ndarray) -> str:
        """Transcribe audio with provided client."""
        if len(audio) == 0:
            return ""

        # Check if audio has enough amplitude (skip silent chunks)
        max_amplitude = np.max(np.abs(audio))
        if max_amplitude < MIN_AUDIO_AMPLITUDE:
            logger.debug("Skipping silent audio chunk")
            return ""

        normalized = AudioProcessor.normalize(audio)
        audio_int16 = (normalized * 32767).astype(np.int16)

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(audio_int16.tobytes())
        buffer.seek(0)

        try:
            response = await client.post(
                self.whisper_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": ("audio.wav", buffer.read(), "audio/wav")},
                data={
                    "model": self.whisper_model,
                    "response_format": "json",
                    "language": "en",
                    "prompt": "",  # Empty prompt - just transcribe
                },
            )
            if response.status_code == 200:
                text = response.json().get("text", "").strip()
                # Filter out common hallucinations
                if self._is_hallucination(text):
                    logger.debug(f"Filtered hallucination: {text}")
                    return ""
                return text
        except Exception as e:
            logger.error(f"Final transcription error: {e}")
        return ""

    def _is_hallucination(self, text: str) -> bool:
        """Check if text is likely a Whisper hallucination."""
        if not text:
            return False
        text_lower = text.lower().strip()
        # Check for common hallucination patterns
        for pattern in self.HALLUCINATION_PATTERNS:
            if pattern in text_lower:
                return True
        # Very short text that's just punctuation
        if len(text) < 3 or text in [".", "..", "...", "!", "?", ","]:
            return True
        return False

    async def _cleanup_with_client(self, client: httpx.AsyncClient, text: str) -> str:
        """Cleanup text with provided client."""
        if not text or len(text) < 3:
            return text

        try:
            response = await client.post(
                self.llm_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.llm_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a transcription editor. Fix ONLY grammar and punctuation errors. Do NOT add, remove, or change any words. Do NOT add greetings, sign-offs, or any other content. Output ONLY the corrected text, nothing else."
                        },
                        {"role": "user", "content": text}
                    ],
                    "temperature": 0.1,  # Lower temperature for more deterministic output
                    "max_tokens": len(text) + 50,  # Limit to roughly input length
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
            logger.error(f"Final cleanup error: {e}")
        return text

    def reset(self):
        """Reset state for next recording."""
        self.transcripts = []
        self.pending_audio = []

    async def close(self):
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


class VoiceDictationServer:
    def __init__(self):
        self._running = True
        self.is_recording = False
        self.audio_queue = queue.Queue()
        self.audio_data = []

        # Persistent HTTP client for connection reuse
        self._http_client: httpx.AsyncClient | None = None

        # Load settings for audio feedback and waybar
        settings = load_settings()
        self.audio_feedback = AudioFeedback(
            theme=settings.get('audioFeedbackTheme', 'default'),
            volume=settings.get('audioFeedbackVolume', 0.3),
        )
        self.waybar_state = WaybarState(
            theme=settings.get('iconTheme', 'minimal'),
        )
        self.text_processor = TextProcessor(
            enable_punctuation=settings.get('enableSpokenPunctuation', False),
            replacements=settings.get('wordReplacements', {}),
        )

        # Set initial waybar state
        self.waybar_state.idle()

        subprocess.run(
            ["pactl", "set-source-volume", "@DEFAULT_SOURCE@", "100%"],
            stderr=subprocess.DEVNULL,
        )

        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=AUDIO_CHANNELS,
            dtype=np.float32,
            callback=self._audio_callback,
        )
        self.stream.start()

        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)

        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(SOCKET_PATH)
        self.socket.listen(1)
        os.chmod(SOCKET_PATH, 0o600)

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Pre-warm HTTP connection
        settings = load_settings()
        provider = settings.get('provider', 'openai')
        if provider == "groq":
            logger.info("oflow Ready (Groq - optimized mode)")
            # Pre-warm connection in background
            asyncio.run(self._prewarm_connection())
        else:
            logger.info("oflow Ready (OpenAI Whisper + GPT-4o-mini)")

    async def _prewarm_connection(self):
        """Pre-warm HTTP connection to reduce first-request latency."""
        settings = load_settings()
        api_key = settings.get('groqApiKey')
        if not api_key:
            return

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Simple models list request to establish connection
                await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            logger.info("Connection pre-warmed")
        except Exception as e:
            logger.debug(f"Pre-warm failed: {e}")

    def _audio_callback(self, indata, frames, time_info, status):
        if self.is_recording:
            self.audio_queue.put(indata.copy())

    def _signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self._running = False

    def _cleanup(self):
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

        # Release PID lock
        release_pid_lock()

    def _start_recording(self):
        self.is_recording = True
        self.audio_data = []

        # Update waybar state and play audio feedback
        self.waybar_state.recording()
        self.audio_feedback.play_start()

        # Reload settings in case they changed
        settings = load_settings()

        # Update text processor with current settings
        self.text_processor = TextProcessor(
            enable_punctuation=settings.get('enableSpokenPunctuation', False),
            replacements=settings.get('wordReplacements', {}),
        )

        logger.info("Recording started")

    def _stop_recording(self):
        self.is_recording = False

        # Update waybar state and play stop sound
        self.waybar_state.transcribing()
        self.audio_feedback.play_stop()

        start = time.perf_counter()
        time.sleep(0.1)  # Brief pause to collect final audio

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
        import time as time_module

        settings = load_settings()
        provider = settings.get('provider', 'groq')
        api_key = settings.get('groqApiKey') if provider == 'groq' else settings.get('openaiApiKey')
        enable_cleanup = settings.get('enableCleanup', True)

        if not api_key:
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

        async with httpx.AsyncClient(timeout=30.0) as client:
            transcriber = ChunkedTranscriber(api_key, provider)

            logger.info(f"Processing {duration:.1f}s audio")
            t0 = time_module.perf_counter()
            raw_text = await transcriber._transcribe_with_client(client, audio)
            t1 = time_module.perf_counter()
            logger.info(f"Transcription: {(t1-t0)*1000:.0f}ms")

            if not raw_text:
                logger.warning("No transcription result")
                self.waybar_state.idle()
                return

            # Apply text processing (spoken punctuation, replacements)
            raw_text = self.text_processor.process(raw_text)

            # Cleanup (if enabled)
            if enable_cleanup:
                t0 = time_module.perf_counter()
                cleaned_text = await transcriber._cleanup_with_client(client, raw_text)
                t1 = time_module.perf_counter()
                logger.info(f"Cleanup: {(t1-t0)*1000:.0f}ms")
            else:
                cleaned_text = raw_text

            # Type the result
            type_text(cleaned_text)
            logger.info(f"Result: {cleaned_text[:50]}...")

            # Set waybar back to idle
            self.waybar_state.idle()

            # Save
            storage = StorageManager()
            storage.save_transcript(
                raw=raw_text,
                cleaned=cleaned_text,
                timestamp=datetime.now().isoformat(),
            )

    async def _process_parallel_chunks(self):
        """Process audio - use single request for short audio, parallel for long."""
        import time as time_module

        settings = load_settings()
        provider = settings.get('provider', 'groq')
        api_key = settings.get('groqApiKey') if provider == 'groq' else settings.get('openaiApiKey')
        enable_cleanup = settings.get('enableCleanup', True)

        if not api_key:
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

        async with httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_connections=10)) as client:
            transcriber = ChunkedTranscriber(api_key, provider)

            # For short audio (< 2s), just send it all at once (faster)
            if duration < 2.0:
                logger.info(f"Processing {duration:.1f}s audio")
                t0 = time_module.perf_counter()
                raw_text = await transcriber._transcribe_with_client(client, audio)
                t1 = time_module.perf_counter()
                logger.info(f"Transcription: {(t1-t0)*1000:.0f}ms")
            else:
                # For longer audio, split and process in parallel
                chunk_seconds = 1.0  # Larger chunks for fewer requests
                chunk_size = int(SAMPLE_RATE * chunk_seconds)
                chunks = [audio[i:i+chunk_size] for i in range(0, len(audio), chunk_size)]

                # Merge tiny last chunk
                if len(chunks) > 1 and len(chunks[-1]) < SAMPLE_RATE * 0.3:
                    chunks[-2] = np.concatenate([chunks[-2], chunks[-1]])
                    chunks = chunks[:-1]

                logger.info(f"Parallel mode: {len(chunks)} chunks ({duration:.1f}s audio)")
                t0 = time_module.perf_counter()

                tasks = [transcriber._transcribe_with_client(client, c) for c in chunks]
                transcripts = await asyncio.gather(*tasks)
                raw_text = " ".join(t.strip() for t in transcripts if t.strip())

                t1 = time_module.perf_counter()
                logger.info(f"Transcription: {(t1-t0)*1000:.0f}ms")

            if not raw_text:
                logger.warning("No transcription result")
                self.waybar_state.idle()
                return

            # Apply text processing (spoken punctuation, replacements)
            raw_text = self.text_processor.process(raw_text)

            # Cleanup (if enabled)
            if enable_cleanup:
                t0 = time_module.perf_counter()
                cleaned_text = await transcriber._cleanup_with_client(client, raw_text)
                t1 = time_module.perf_counter()
                logger.info(f"Cleanup: {(t1-t0)*1000:.0f}ms")
            else:
                cleaned_text = raw_text

            # Type the result
            type_text(cleaned_text)
            logger.info(f"Result: {cleaned_text[:50]}...")

            # Set waybar back to idle
            self.waybar_state.idle()

            # Save
            storage = StorageManager()
            storage.save_transcript(
                raw=raw_text,
                cleaned=cleaned_text,
                timestamp=datetime.now().isoformat(),
            )

    async def _process_recording(self):
        audio = np.concatenate(self.audio_data, axis=0).flatten()

        async for event in process_audio_with_graph(audio):
            if event.type == EventType.STT_OUTPUT:
                text = event.data
                # Apply text processing
                text = self.text_processor.process(text)
                type_text(text)
                self.waybar_state.idle()
                logger.debug(f"Transcribed: {text[:50]}...")

            elif event.type == EventType.STT_ERROR:
                self.waybar_state.error(event.error or "Error")
                self.audio_feedback.play_error()
                logger.error(f"Transcription error: {event.error}")

    def run(self):
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

                    logger.info(f"Command received: {cmd} (is_recording={self.is_recording})")
                    if cmd == "start" and not self.is_recording:
                        self._start_recording()
                    elif cmd == "stop" and self.is_recording:
                        logger.info("Calling _stop_recording...")
                        self._stop_recording()
                        logger.info("_stop_recording completed")
                    elif cmd == "stop" and not self.is_recording:
                        logger.info("Stop received but not recording - ignored")
                    elif cmd == "toggle":
                        if self.is_recording:
                            self._stop_recording()
                        else:
                            self._start_recording()
                finally:
                    conn.close()
        finally:
            self._cleanup()


def setup_waybar() -> None:
    """Install Waybar integration for oflow."""
    script_dir = Path(__file__).parent
    waybar_config_src = script_dir / "waybar-oflow.jsonc"
    waybar_css_src = script_dir / "waybar-oflow.css"

    # Find waybar config
    waybar_dir = Path.home() / ".config" / "waybar"
    waybar_config = waybar_dir / "config.jsonc"
    if not waybar_config.exists():
        waybar_config = waybar_dir / "config"

    waybar_style = waybar_dir / "style.css"

    print("oflow Waybar Setup")
    print("=" * 40)

    if not waybar_dir.exists():
        print(f"Waybar config directory not found: {waybar_dir}")
        print("\nManual install:")
        print(f"  1. Copy module config from: {waybar_config_src}")
        print(f"  2. Copy CSS from: {waybar_css_src}")
        sys.exit(1)

    # Show module config
    if waybar_config.exists():
        content = waybar_config.read_text()
        if "custom/oflow" in content:
            print("oflow module already in Waybar config")
        else:
            print(f"\nAdd to your Waybar config ({waybar_config}):")
            print()
            print('  1. Add "custom/oflow" to modules-right (or left/center)')
            print()
            print("  2. Add this module definition:")
            print()
            print(waybar_config_src.read_text())

    # Install CSS
    if waybar_style.exists():
        css_content = waybar_style.read_text()
        if "#custom-oflow" in css_content:
            print("oflow styles already in Waybar CSS")
        else:
            print(f"\nInstalling CSS to {waybar_style}...")
            with open(waybar_style, "a") as f:
                f.write("\n/* oflow voice dictation */\n")
                f.write(waybar_css_src.read_text())
            print("CSS installed")
    else:
        print(f"\nWaybar style.css not found at {waybar_style}")
        print("Add this CSS manually:")
        print(waybar_css_src.read_text())

    print()
    print("Restart Waybar: killall waybar && waybar &")


def main() -> None:
    """
    Main entry point for Oflow.

    If called with a command (start/stop/toggle), sends the command to the running server.
    Otherwise, starts the voice dictation server.
    """
    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        # Handle setup commands
        if cmd == "setup":
            if len(sys.argv) > 2 and sys.argv[2] == "waybar":
                setup_waybar()
                sys.exit(0)
            else:
                print("Usage: oflow setup waybar")
                sys.exit(1)

        if cmd not in ("start", "stop", "toggle"):
            print(f"Unknown command: {cmd}", file=sys.stderr)
            print("Usage: oflow [start|stop|toggle]", file=sys.stderr)
            print("       oflow setup waybar", file=sys.stderr)
            sys.exit(1)

        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(SOCKET_PATH)
            s.send(cmd.encode())
            s.close()
        except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
            print(
                f"Server not running: {e}\n"
                "Start the server with: ./oflow",
                file=sys.stderr
            )
            sys.exit(1)
    else:
        # Check if another backend is already running
        if not acquire_pid_lock():
            logger.info("Another backend is already running, exiting")
            sys.exit(0)  # Exit successfully - this is expected behavior

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
