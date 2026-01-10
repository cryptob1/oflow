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

logging.basicConfig(
    level=logging.INFO,
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


def load_settings() -> dict:
    """
    Load settings from ~/.oflow/settings.json.
    Falls back to environment variable defaults if file doesn't exist.

    Returns:
        dict with settings including provider, API keys, and feature flags
    """
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
                }
    except Exception as e:
        logger.warning(f"Failed to load settings from {SETTINGS_FILE}: {e}")

    return {
        'enableCleanup': DEFAULT_ENABLE_CLEANUP,
        'enableMemory': DEFAULT_ENABLE_MEMORY,
        'openaiApiKey': OPENAI_API_KEY,
        'groqApiKey': GROQ_API_KEY,
        'provider': DEFAULT_PROVIDER,
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
            error_text = response.text[:200]  # Limit error message length
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

    logger.debug(f"Settings: provider={provider}, cleanup={enable_cleanup}, memory={enable_memory}")

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


def notify(message: str, duration_ms: int = 1500, persistent: bool = False) -> None:
    """
    Send desktop notification.

    Args:
        message: Notification message text
        duration_ms: Notification duration in milliseconds (default: 1500)
        persistent: If True, notification stays until dismissed (default: False)
    """
    timeout = "0" if persistent else str(duration_ms)
    try:
        subprocess.run(
            ["notify-send", "-t", timeout, message],
            stderr=subprocess.DEVNULL,
            check=False,  # Don't fail if notify-send is not available
        )
    except FileNotFoundError:
        logger.debug("notify-send not available, skipping notification")


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


class VoiceDictationServer:
    def __init__(self):
        self._running = True
        self.is_recording = False
        self.audio_queue = queue.Queue()
        self.audio_data = []

        subprocess.run(
            ["pactl", "set-source-volume", "@DEFAULT_SOURCE@", "150%"],
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

        # Log startup with provider info
        settings = load_settings()
        provider = settings.get('provider', 'openai')
        if provider == "groq":
            logger.info("oflow Ready (Groq Whisper Turbo + Llama 3.1 8B) - 200x faster")
        else:
            logger.info("oflow Ready (OpenAI Whisper + GPT-4o-mini)")

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
        self._play_beep()
        self._show_overlay()
        logger.info("Recording started")

    def _play_beep(self):
        """Play a short beep to indicate recording started."""
        try:
            duration = 0.1  # 100ms
            freq = 800  # Hz
            t = np.linspace(0, duration, int(SAMPLE_RATE * duration), False)
            # Gentle beep with fade in/out
            envelope = np.sin(np.pi * t / duration)  # Smooth envelope
            beep = np.sin(2 * np.pi * freq * t) * envelope * 0.4
            sd.play(beep.astype(np.float32), SAMPLE_RATE, blocking=True)
        except Exception as e:
            logger.debug(f"Beep failed: {e}")

    def _show_overlay(self):
        """Show recording notification."""
        try:
            subprocess.run(
                ["notify-send", "-t", "2000", "oflow", "Recording..."],
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass

    def _hide_overlay(self):
        """Dismiss recording notification (mako auto-dismisses, this is a no-op)."""
        pass

    def _stop_recording(self):
        self.is_recording = False
        self._hide_overlay()

        import time
        time.sleep(0.15)

        while not self.audio_queue.empty():
            self.audio_data.append(self.audio_queue.get())

        if self.audio_data:
            asyncio.run(self._process_recording())

        logger.info("Recording stopped")

    async def _process_recording(self):
        audio = np.concatenate(self.audio_data, axis=0).flatten()

        async for event in process_audio_with_graph(audio):
            if event.type == EventType.STT_OUTPUT:
                text = event.data
                type_text(text)
                # Text is typed directly - no notification needed
                logger.debug(f"Transcribed: {text[:50]}...")

            elif event.type == EventType.STT_ERROR:
                notify(f"❌ Error", 1500)  # Only notify on errors
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


def main() -> None:
    """
    Main entry point for Oflow.

    If called with a command (start/stop/toggle), sends the command to the running server.
    Otherwise, starts the voice dictation server.
    """
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
