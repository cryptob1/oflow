#!/usr/bin/env python3
"""
Cortex - Voice dictation for Hyprland/Wayland.

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
from collections import deque
from collections.abc import Callable
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

# Second-brain sink for "note" captures (alongside cortex.py). Optional: if it's
# missing (e.g. a partial install), note mode degrades to a logged error rather
# than crashing dictation.
try:
    import brain

    _HAS_BRAIN = True
except ImportError:
    _HAS_BRAIN = False

load_dotenv()

_debug_mode = os.getenv("DEBUG_MODE", "false").lower() == "true"

# Persist logs to a rotating file (in addition to stderr/journal) so that
# intermittent failures — a mic that drops out, a flaky STT request — can be
# diagnosed after the fact instead of vanishing with the journal buffer.
_LOG_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "cortex"
LOG_FILE = _LOG_DIR / "cortex.log"
_log_handlers: list[logging.Handler] = [logging.StreamHandler()]
try:
    from logging.handlers import RotatingFileHandler

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _log_handlers.append(
        RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3)
    )
except OSError:
    pass  # unwritable state dir — fall back to stderr only

logging.basicConfig(
    level=logging.DEBUG if _debug_mode else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=_log_handlers,
)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration Constants
# ============================================================================

# Unix socket path for IPC with Tauri frontend
SOCKET_PATH = "/tmp/voice-dictation.sock"

# PID file to ensure only one backend runs at a time
PID_FILE = "/tmp/cortex.pid"

# Audio configuration
SAMPLE_RATE = 16000  # 16kHz sample rate (Whisper requirement)
AUDIO_CHANNELS = 1  # Mono audio
NORMALIZATION_TARGET = 0.95  # Normalize audio to 95% of max amplitude
MIN_AUDIO_DURATION_SECONDS = 0.5  # Minimum recording duration
# Minimum peak amplitude for a clip to be treated as speech. Kept low so quiet
# and *whispered* dictation isn't dropped before it reaches the STT model —
# normalization (below) boosts a quiet-but-real clip up to NORMALIZATION_TARGET
# afterwards, so this only needs to clear true silence/noise, not be loud.
# Override with CORTEX_MIN_AMPLITUDE if your environment is noisier.
MIN_AUDIO_AMPLITUDE = float(os.getenv("CORTEX_MIN_AMPLITUDE", "0.006"))
# Speech-presence gate: the loudest 100ms frame must have at least this RMS
# energy, else the clip is treated as no-speech and never sent to the STT model.
# Unlike peak amplitude (which a single click trips), frame RMS reliably tells
# sustained speech from a noise floor — the fix for mics whose steady background
# noise sits at speech level and makes Whisper hallucinate ("Okay.", "Thank
# you."). 0 disables it (default) since the right value is mic/room specific;
# set CORTEX_MIN_SPEECH_RMS just above your measured silence floor. The per-clip
# value is logged ("Audio level: …") so it can be calibrated from real usage.
MIN_SPEECH_RMS = float(os.getenv("CORTEX_MIN_SPEECH_RMS", "0"))
# Raw mic peak above which we trust a short transcription even if it matches a
# stock hallucination phrase ("thank you", "subscribe"). Silence-hallucinations
# come from near-silent clips; a loud, clear clip saying "thank you" is real.
HALLUCINATION_TRUST_PEAK = 0.10
CHUNK_DURATION_SECONDS = 25  # Max chunk duration for Whisper (split long audio)
CHUNK_SPLIT_WINDOW_SECONDS = 3  # Window to search for silence near split point
MAX_RECORDING_SECONDS = 300  # Auto-stop runaway recordings to prevent leaks from stuck state
AUDIO_BLOCKSIZE = 1600  # 100ms chunks → 10 callbacks/sec (3000-chunk queue = 5 min)
# Open the mic on demand — only while actually recording (default). Trades a
# little start latency / pre-roll for zero idle cost. The old always-open stream
# removed the per-record PortAudio open latency, but a persistent stream turns
# into a CPU-spinning zombie after a suspend/resume (PortAudio never resumes it),
# and on-demand feels indistinguishable in practice. Set CORTEX_PERSISTENT_MIC=true
# to keep the stream warm across dictations.
# NOTE: persistent mode is additionally auto-disabled when the default source is
# a Bluetooth device (see _persistent_mic_wanted) — holding a BT mic open forces
# the headset from high-quality A2DP to the low-quality HFP/HSP profile.
PERSISTENT_MIC = os.getenv("CORTEX_PERSISTENT_MIC", "false").lower() == "true"
# Rolling audio retained *before* the hotkey press, so speech that starts as
# your finger comes down on the key isn't clipped. Only effective with a
# persistent stream (otherwise the stream isn't open yet to fill it).
PREROLL_SECONDS = float(os.getenv("CORTEX_PREROLL_SECONDS", "0.5"))
PREROLL_CHUNKS = max(1, round(PREROLL_SECONDS * SAMPLE_RATE / AUDIO_BLOCKSIZE))
# A healthy stream fires the audio callback every ~100ms (AUDIO_BLOCKSIZE). If a
# persistent stream goes this long with no callback it's a zombie (e.g. never
# resumed after suspend) and must be reopened, even if PortAudio still calls it
# "active". Comfortably above the 100ms cadence to avoid false positives.
STREAM_STALE_SECONDS = 1.0
AUDIO_OPEN_MAX_ATTEMPTS = 4  # Retries opening the mic (PortAudio reinit) before giving up
AUDIO_OPEN_RETRY_DELAY = 0.3  # Seconds between mic-open attempts, to let the audio server settle
STREAM_WARN_INTERVAL_SECONDS = 5.0  # Throttle audio-stream distress warnings
# On-demand mic warm-up: a freshly opened PortAudio stream needs a moment before
# the device actually delivers audio; speech in that gap is lost, which clips the
# first word or two ("No one..."-style garbled starts). When we open the mic on
# the hotkey press (PERSISTENT_MIC off, so no pre-roll), we wait — up to this
# budget — for the first real callback before cueing the user and arming capture,
# so their opening words land in the (now-filling) pre-roll instead of the gap.
# A persistent stream is already warm, so this wait is skipped there.
MIC_WARMUP_SECONDS = float(os.getenv("CORTEX_MIC_WARMUP_MS", "250")) / 1000.0

# API configuration
API_TIMEOUT_SECONDS = 30.0  # Timeout for API requests
# Max chunk-transcription requests in flight at once. Long meetings split into
# dozens of chunks; providers cap concurrency (ElevenLabs Scribe = 20), so keep
# under that to avoid 429s. Override via CORTEX_MAX_TRANSCRIBE_CONCURRENCY.
MAX_TRANSCRIBE_CONCURRENCY = int(os.getenv("CORTEX_MAX_TRANSCRIBE_CONCURRENCY", "12"))
# How often the backend checks the vault for due reminders to fire.
REMINDER_POLL_SECONDS = int(os.getenv("CORTEX_REMINDER_POLL_SECONDS", "30"))
# Screen context (opt-in): how often the sampler OCRs the active window for the
# journal/dream. It only records when the window actually changed, so this is a
# ceiling, not a fixed rate.
SCREEN_SAMPLE_SECONDS = int(os.getenv("CORTEX_SCREEN_SAMPLE_SECONDS", "60"))
# Windows never OCR'd — sensitive apps whose contents shouldn't be logged even
# locally. Substring match against the window's class + title (case-insensitive).
DEFAULT_SCREEN_DENYLIST = [
    "1password", "bitwarden", "keepassxc", "keepass", "lastpass", "proton pass",
    "private browsing", "incognito", "gnome-keyring", "polkit",
]
# How long an idle keep-alive socket may be reused before it's recycled. Kept
# below typical server/NAT idle timeouts so we don't try to send on a socket the
# other end has already dropped (e.g. after laptop suspend/resume).
KEEPALIVE_EXPIRY_SECONDS = 45.0

# File paths
TRANSCRIPTS_FILE = Path.home() / ".cortex" / "transcripts.jsonl"
SETTINGS_FILE = Path.home() / ".cortex" / "settings.json"

# Waybar state file (in XDG_RUNTIME_DIR for fast access)
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "cortex"
STATE_FILE = RUNTIME_DIR / "state"

# On-screen recording overlay (cortex-osd.py): datagram socket for live levels
OSD_SOCK = RUNTIME_DIR / "osd.sock"
OSD_LEVEL_GAIN = 6.0  # scale raw mic peak (~0..0.2 for speech) toward 0..1
# gtk4-layer-shell must be LD_PRELOADed (linker-order quirk) for the overlay
GTK4_LAYER_SHELL_LIB = "/usr/lib/libgtk4-layer-shell.so"

# API key format validation
OPENAI_API_KEY_PATTERN = re.compile(r"^sk-")
GROQ_API_KEY_PATTERN = re.compile(r"^gsk_")

# Transcription (speech-to-text) endpoints
OPENAI_WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
DEEPGRAM_STT_URL = "https://api.deepgram.com/v1/listen"

# Chat (LLM cleanup) endpoints — only OpenAI-compatible providers offer this.
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

# Whisper-style conditioning prompt: a realistic sample that primes domain
# vocabulary and keeps the model in transcription (not instruction) mode.
WHISPER_PROMPT = (
    "Push the code to Git and open a PR. Check the API endpoint, run pytest, "
    "then deploy to Kubernetes. Let's refactor the async handler."
)

# Force a fixed transcription language so providers don't auto-switch mid-dictation.
# STT_LANGUAGE is ISO-639-1 (Whisper/Deepgram); the ISO-639-3 form is for ElevenLabs.
STT_LANGUAGE = os.getenv("CORTEX_LANGUAGE", "en")
STT_LANGUAGE_ISO3 = os.getenv("CORTEX_LANGUAGE_ISO3", "eng")

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
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

# Default settings (can be overridden by settings.json)
DEFAULT_ENABLE_CLEANUP = os.getenv("ENABLE_CLEANUP", "true").lower() == "true"
DEFAULT_PROVIDER = os.getenv("PROVIDER", "groq")  # Default to Groq (faster)
DEFAULT_DICTATION_HOTKEY = "copilot"  # push-to-talk key: "copilot" or "f8"
# Fast mode: dictations with at most this many words skip the cleanup LLM hop.
# 0 disables (always clean). 19 (i.e. under 20 words) covers most short/medium
# dictations where the ~200ms cleanup latency is most felt and least needed —
# modern STT models already punctuate short utterances well.
DEFAULT_FAST_MODE_MAX_WORDS = 19


# Pre-rename config dir. Cortex was formerly "oflow" and stored its settings
# and transcript history under ~/.oflow; carry those over on first run so a
# user upgrading from oflow doesn't silently lose their API keys and history.
LEGACY_SETTINGS_DIR = Path.home() / ".oflow"


def migrate_legacy_config() -> None:
    """One-time migration from the pre-rename ~/.oflow config dir.

    Idempotent: only copies a file when its ~/.cortex counterpart is absent, so
    it's a no-op once migrated (or on a fresh install with no ~/.oflow).
    """
    if not LEGACY_SETTINGS_DIR.is_dir():
        return
    import shutil

    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    for name in ("settings.json", "transcripts.jsonl"):
        src = LEGACY_SETTINGS_DIR / name
        dst = SETTINGS_FILE.parent / name
        if src.exists() and not dst.exists():
            try:
                shutil.copy2(src, dst)
                logger.info(f"Migrated {name} from legacy ~/.oflow config")
            except OSError as e:
                logger.warning(f"Could not migrate {name} from ~/.oflow: {e}")


def ensure_data_dir() -> None:
    """Ensure ~/.cortex directory and default files exist."""
    migrate_legacy_config()
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
    Load settings from ~/.cortex/settings.json.
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
                    "elevenlabsApiKey": settings.get("elevenlabsApiKey") or ELEVENLABS_API_KEY,
                    "deepgramApiKey": settings.get("deepgramApiKey") or DEEPGRAM_API_KEY,
                    "provider": settings.get("provider", DEFAULT_PROVIDER),
                    "dictationHotkey": settings.get("dictationHotkey", DEFAULT_DICTATION_HOTKEY),
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
        "elevenlabsApiKey": ELEVENLABS_API_KEY,
        "deepgramApiKey": DEEPGRAM_API_KEY,
        "provider": DEFAULT_PROVIDER,
        "dictationHotkey": DEFAULT_DICTATION_HOTKEY,
        "audioFeedbackTheme": "default",
        "audioFeedbackVolume": 0.3,
        "iconTheme": "nerd-font",
        "enableSpokenPunctuation": False,
        "wordReplacements": {},
    }


HOTKEY_SCRIPT = Path.home() / ".local" / "bin" / "cortex-hotkey"


def apply_dictation_hotkey(choice: str) -> None:
    """Bind the push-to-talk hotkey via the cortex-hotkey helper (Hyprland).

    Best-effort: no-op if the helper or Hyprland isn't present, so this is safe
    on non-Hyprland setups.
    """
    if not HOTKEY_SCRIPT.exists():
        return
    try:
        subprocess.run(
            [str(HOTKEY_SCRIPT), choice],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        logger.info(f"Dictation hotkey applied: {choice}")
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug(f"Could not apply hotkey: {e}")


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


class CortexError(Exception):
    """Base exception for Cortex errors."""

    pass


class ConfigurationError(CortexError):
    """Raised when configuration is invalid."""

    pass


# ============================================================================
# Waybar State Manager
# ============================================================================


class WaybarState:
    """
    Manages Waybar status bar integration.
    Writes state to a file that Waybar can read via custom/cortex module.
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
            "tooltip": tooltip or f"cortex: {state}",
            "class": state,
        }
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.debug(f"Failed to write waybar state: {e}")

    def idle(self):
        self.set_state("idle", "cortex ready")

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


def loudest_frame_rms(audio: np.ndarray, frame_seconds: float = 0.1) -> float:
    """RMS energy of the loudest ~frame_seconds window — a speech-presence signal.

    Peak amplitude is fooled by a single click; averaging energy over a frame is
    not, so this distinguishes sustained (even whispered) speech from a steady
    noise floor far better. Returns the max frame RMS, i.e. "is there ANY
    speech-energy region?", so a mostly-silent clip with a burst of real speech
    still scores high while uniform noise stays low.
    """
    if len(audio) == 0:
        return 0.0
    frame = max(1, int(frame_seconds * SAMPLE_RATE))
    n = len(audio) // frame
    if n == 0:
        return float(np.sqrt(np.mean(audio**2)))
    frames = audio[: n * frame].reshape(n, frame)
    return float(np.sqrt(np.mean(frames**2, axis=1)).max())


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

    def save_transcript(self, raw: str, cleaned: str, timestamp: str, app: str = ""):
        """Append transcript to JSONL file. `app` records the focused window (which
        tool the dictation went into) so the daily journal can reconstruct context."""
        entry = {
            "timestamp": timestamp,
            "raw": raw,
            "cleaned": cleaned,
        }
        if app:
            entry["app"] = app
        with open(self.transcripts_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        self._mirror_to_vault_stream(entry)
        logger.info(f"Saved transcript #{self.count_transcripts()}")

    def _mirror_to_vault_stream(self, entry: dict) -> None:
        """Also append this dictation to a synced, per-machine daily stream in the
        vault — journal/streams/<date>/<host>.jsonl. One file per machine (never
        collides), so the nightly journal can aggregate what you dictated across
        ALL your machines into one coherent day. Best-effort: a vault/sync problem
        must never break dictation. These raw .jsonl streams stay out of Ask (the
        RAG only indexes .md)."""
        if not _HAS_BRAIN:
            return
        try:
            date = str(entry.get("timestamp", ""))[:10]  # YYYY-MM-DD
            if not date:
                return
            host = re.sub(r"[^\w.-]", "_", socket.gethostname() or "unknown")
            d = brain._vault() / "journal" / "streams" / date
            d.mkdir(parents=True, exist_ok=True)
            with open(d / f"{host}.jsonl", "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            logger.debug("vault stream mirror skipped", exc_info=True)

    def append_activity(self, rec: dict) -> None:
        """Log observed screen activity (local OCR of the active window) into the
        day's per-machine journal stream — same file as dictations, tagged so the
        journal/dream treat it as context, not speech. Never touches the local
        transcripts log; it's not a dictation."""
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "kind": "activity",
            "app": f'{rec.get("app", "?")} "{(rec.get("title") or "")[:60]}"',
            "text": rec.get("text", ""),
        }
        self._mirror_to_vault_stream(entry)

    def count_transcripts(self) -> int:
        """Count total transcripts."""
        if not self.transcripts_file.exists():
            return 0
        with open(self.transcripts_file) as f:
            return sum(1 for _ in f)


# ============================================================================
# Transcription
# ============================================================================

# YouTube-style hallucinations. These correlate with near-silent audio —
# Whisper invents them when there's little to transcribe — so a loud, clear
# clip that says these words is real speech (see HALLUCINATION_TRUST_PEAK).
SILENCE_HALLUCINATION_PATTERNS = [
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
]

# AI-assistant responses (Whisper acting like a chatbot). This is model
# leakage, not a silence artifact: it is wrong regardless of how loud the clip
# was, so — unlike the silence patterns above — it is filtered even when loud.
AI_ASSISTANT_PATTERNS = [
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

# Combined list, kept for any external reference.
HALLUCINATION_PATTERNS = SILENCE_HALLUCINATION_PATTERNS + AI_ASSISTANT_PATTERNS

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


# We force English transcription, but accurate models (ElevenLabs Scribe) treat
# the language code as a hint and still auto-switch on short/ambiguous audio —
# producing e.g. Bengali or Devanagari script for English speech. When English is
# the target, drop output dominated by non-Latin letters: it's a misrecognition,
# not something the user wants pasted.
_ENGLISH_TARGET = STT_LANGUAGE.lower().startswith("en")


def is_wrong_language(text: str) -> bool:
    """True if English is the target but the text is mostly non-Latin script."""
    if not _ENGLISH_TARGET:
        return False
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 4:
        return False
    # Latin (incl. accents) lives below U+0250; other scripts (Bengali, CJK,
    # Cyrillic, Arabic, Devanagari, …) sit above it.
    non_latin = sum(1 for c in letters if ord(c) >= 0x250)
    return non_latin / len(letters) > 0.3


def is_hallucination(text: str, peak: float = 0.0) -> bool:
    """Check if text is likely a Whisper hallucination or AI response.

    ``peak`` is the RAW mic peak of the clip (pre-normalization). Silence-
    hallucinations ("Thank you.", "Subscribe") come from near-silent audio, so
    when the clip had strong signal (``peak >= HALLUCINATION_TRUST_PEAK``) we
    trust a short stock phrase as real speech instead of filtering it. The
    default peak of 0.0 means "unknown" and preserves the original,
    always-filter behavior for callers (and tests) that don't pass it.

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
    loud = peak >= HALLUCINATION_TRUST_PEAK
    if len(text_lower) < 60:
        ai_hits = [p for p in AI_ASSISTANT_PATTERNS if p in text_lower]
        silence_hits = [p for p in SILENCE_HALLUCINATION_PATTERNS if p in text_lower]
        # A loud, clear clip is real speech, so forgive silence-hallucination
        # phrases ("thank you", "subscribe") — but never AI-assistant leakage,
        # which is wrong at any volume.
        effective_hits = ai_hits + ([] if loud else silence_hits)
        # Filter if a single pattern accounts for most of the text...
        for pattern in effective_hits:
            if len(text_lower) < len(pattern) + 15:
                logger.info(
                    f"Filtered hallucination (matched {pattern!r}, "
                    f"peak={peak:.3f}): {text[:80]}"
                )
                return True
        # ...or if several distinct patterns stack up in short text. Real speech
        # rarely chains multiple AI/YouTube clichés ("I'm sorry, I cannot help");
        # Whisper hallucinations do. Mirrors the 2+ prompt-leakage heuristic below.
        if len(effective_hits) >= 2:
            logger.info(
                f"Filtered hallucination (matched {effective_hits}, "
                f"peak={peak:.3f}): {text[:80]}"
            )
            return True
        if loud and silence_hits:
            logger.info(
                f"Trusted short phrase despite {silence_hits} (strong signal, "
                f"peak={peak:.3f}): {text[:80]}"
            )

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


# ============================================================================
# Transcription provider registry
# ============================================================================
#
# Each provider plugs into the same pipeline (record → normalize → POST → parse).
# To add one, define an auth/request/parse trio and register it below; the rest
# of the app selects it purely by the "provider" setting.


def _bearer_auth(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def _openai_compatible_request(wav_bytes: bytes, model: str, auth: dict) -> dict:
    """Groq/OpenAI multipart /audio/transcriptions request."""
    return {
        "headers": auth,
        "files": {"file": ("audio.wav", wav_bytes, "audio/wav")},
        "data": {
            "model": model,
            "response_format": "json",
            "language": STT_LANGUAGE,
            "prompt": WHISPER_PROMPT,
        },
    }


def _openai_compatible_parse(payload: dict) -> str:
    return (payload.get("text") or "").strip()


def _elevenlabs_request(wav_bytes: bytes, model: str, auth: dict) -> dict:
    """ElevenLabs Scribe multipart request (model_id, not model).

    language_code pins the language so Scribe doesn't auto-switch mid-dictation.
    Scribe uses ISO-639-3 internally ("eng"), which is a stronger lock than "en".
    """
    return {
        "headers": auth,
        "files": {"file": ("audio.wav", wav_bytes, "audio/wav")},
        "data": {
            "model_id": model,
            "language_code": STT_LANGUAGE_ISO3,
            # Don't transcribe non-speech sounds (fan, AC, laughter) as bracketed
            # tags like "(tonal sound)" — we only want the spoken words.
            "tag_audio_events": "false",
        },
    }


def _deepgram_request(wav_bytes: bytes, model: str, auth: dict) -> dict:
    """Deepgram raw-body request with query params (no multipart)."""
    return {
        "headers": {**auth, "Content-Type": "audio/wav"},
        "params": {
            "model": model,
            "language": STT_LANGUAGE,
            "detect_language": "false",
            "smart_format": "true",
            "punctuate": "true",
        },
        "content": wav_bytes,
    }


def _deepgram_parse(payload: dict) -> str:
    try:
        return payload["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
    except (KeyError, IndexError, TypeError):
        return ""


@dataclass(frozen=True)
class STTProvider:
    name: str
    label: str
    url: str
    default_model: str
    model_env: str            # env var to override the model id
    settings_key: str         # settings.json field holding the API key
    env_key: str              # environment-variable fallback for the key
    signup_url: str           # where to get a key (shown on auth errors)
    warm_url: str             # cheap GET to pre-warm the keep-alive connection
    supports_cleanup: bool    # can this provider's key also run LLM cleanup?
    auth_headers: Callable[[str], dict]
    build_request: Callable[[bytes, str, dict], dict]
    parse_response: Callable[[dict], str]
    key_pattern: re.Pattern | None = None


STT_PROVIDERS: dict[str, STTProvider] = {
    "groq": STTProvider(
        name="groq", label="Groq Whisper",
        url=GROQ_WHISPER_URL, default_model="whisper-large-v3",
        model_env="CORTEX_WHISPER_MODEL",
        settings_key="groqApiKey", env_key="GROQ_API_KEY",
        signup_url="https://console.groq.com/keys",
        warm_url="https://api.groq.com/openai/v1/models",
        supports_cleanup=True,
        auth_headers=_bearer_auth,
        build_request=_openai_compatible_request,
        parse_response=_openai_compatible_parse,
        key_pattern=GROQ_API_KEY_PATTERN,
    ),
    "openai": STTProvider(
        name="openai", label="OpenAI Whisper",
        url=OPENAI_WHISPER_URL, default_model="whisper-1",
        model_env="CORTEX_OPENAI_STT_MODEL",
        settings_key="openaiApiKey", env_key="OPENAI_API_KEY",
        signup_url="https://platform.openai.com/api-keys",
        warm_url="https://api.openai.com/v1/models",
        supports_cleanup=True,
        auth_headers=_bearer_auth,
        build_request=_openai_compatible_request,
        parse_response=_openai_compatible_parse,
        key_pattern=OPENAI_API_KEY_PATTERN,
    ),
    "elevenlabs": STTProvider(
        name="elevenlabs", label="ElevenLabs Scribe",
        url=ELEVENLABS_STT_URL, default_model="scribe_v1",
        model_env="CORTEX_ELEVENLABS_MODEL",
        settings_key="elevenlabsApiKey", env_key="ELEVENLABS_API_KEY",
        signup_url="https://elevenlabs.io/app/settings/api-keys",
        warm_url="https://api.elevenlabs.io/v1/models",
        supports_cleanup=False,
        auth_headers=lambda key: {"xi-api-key": key},
        build_request=_elevenlabs_request,
        parse_response=_openai_compatible_parse,  # Scribe also returns {"text": ...}
    ),
    "deepgram": STTProvider(
        name="deepgram", label="Deepgram Nova-3",
        url=DEEPGRAM_STT_URL, default_model="nova-3",
        model_env="CORTEX_DEEPGRAM_MODEL",
        settings_key="deepgramApiKey", env_key="DEEPGRAM_API_KEY",
        signup_url="https://console.deepgram.com/",
        warm_url="https://api.deepgram.com/v1/auth/token",
        supports_cleanup=False,
        auth_headers=lambda key: {"Authorization": f"Token {key}"},
        build_request=_deepgram_request,
        parse_response=_deepgram_parse,
    ),
}

# Providers tried (in order) for LLM cleanup when the STT provider can't do it.
CLEANUP_FALLBACK_ORDER = ("groq", "openai")


def get_stt_provider(provider: str) -> STTProvider:
    """Resolve a provider config, defaulting to Groq for unknown names."""
    return STT_PROVIDERS.get(provider) or STT_PROVIDERS["groq"]


def get_provider_key(settings: dict, provider: str) -> str:
    """API key for a provider: settings.json first, then env var."""
    cfg = STT_PROVIDERS.get(provider)
    if not cfg:
        return ""
    return (settings.get(cfg.settings_key) or os.getenv(cfg.env_key) or "").strip()


def resolve_cleanup_provider(settings: dict, stt_provider: str, stt_key: str):
    """Pick a chat-capable (provider, key) for LLM cleanup.

    ElevenLabs/Deepgram don't expose a chat endpoint, so when they transcribe we
    fall back to whatever chat key is configured (Groq, then OpenAI). Returns
    (None, None) if no chat-capable key is available — cleanup is then skipped.
    """
    cfg = STT_PROVIDERS.get(stt_provider)
    if cfg and cfg.supports_cleanup and stt_key:
        return stt_provider, stt_key
    for name in CLEANUP_FALLBACK_ORDER:
        key = get_provider_key(settings, name)
        if key:
            return name, key
    return None, None


async def transcribe_audio(
    client: httpx.AsyncClient, audio: np.ndarray, api_key: str, provider: str
) -> str:
    """Transcribe audio via the configured speech-to-text provider."""
    if len(audio) == 0:
        return ""

    max_amplitude = np.max(np.abs(audio))
    if max_amplitude < MIN_AUDIO_AMPLITUDE:
        logger.debug("Skipping silent audio chunk")
        return ""

    normalized = AudioProcessor.normalize(audio)
    wav_bytes = AudioProcessor.to_wav_bytes(normalized)

    cfg = get_stt_provider(provider)
    model = os.getenv(cfg.model_env, cfg.default_model)
    request_kwargs = cfg.build_request(wav_bytes, model, cfg.auth_headers(api_key))

    try:
        response = await _post_with_retry(client, cfg.url, **request_kwargs)
        if response.status_code == 200:
            text = cfg.parse_response(response.json())
            if is_hallucination(text, peak=float(max_amplitude)):
                return ""
            if is_wrong_language(text):
                logger.info(f"Filtered non-English transcription: {text[:80]}")
                return ""
            return text
        elif response.status_code == 401:
            logger.error(f"❌ Authentication failed: Invalid {cfg.label} API key")
            logger.error(f"   Get a valid key at: {cfg.signup_url}")
        else:
            logger.error(f"{cfg.label} API error: {response.status_code} - {response.text[:200]}")
    except httpx.TimeoutException:
        logger.error("❌ API timeout - check your internet connection")
    except Exception as e:
        logger.error(f"Transcription error: {e}")
    return ""


async def transcribe_audio_chunked(
    client: httpx.AsyncClient, audio: np.ndarray, api_key: str, provider: str
) -> str:
    """Transcribe audio, splitting long recordings into chunks for reliability.

    Short audio (<25s) is sent directly. Longer audio is split at silence
    boundaries and chunks are transcribed in parallel (capped concurrency), then
    joined.
    """
    chunks = AudioProcessor.split_into_chunks(audio)

    if len(chunks) == 1:
        return await transcribe_audio(client, chunks[0], api_key, provider)

    logger.info(f"Split {len(audio) / SAMPLE_RATE:.1f}s audio into {len(chunks)} chunks")

    # Cap concurrent requests. A long meeting can split into dozens of chunks;
    # firing them all at once overruns provider limits (e.g. ElevenLabs Scribe
    # allows 20 concurrent requests) and triggers 429s. Stay comfortably under.
    sem = asyncio.Semaphore(MAX_TRANSCRIBE_CONCURRENCY)

    async def _transcribe_one(chunk: np.ndarray) -> str:
        async with sem:
            return await transcribe_audio(client, chunk, api_key, provider)

    tasks = [_transcribe_one(chunk) for chunk in chunks]
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
                        "content": "You are a transcription editor. You will receive voice-to-text output inside <dictation> tags. Fix punctuation and capitalization, and remove filler words ('um', 'uh', 'uhm', 'er', 'ah', and 'like'/'you know'/'so' when used as filler) plus stutters, false starts, and self-corrections (e.g. 'her s-surgery' -> 'her surgery'; 'I want- I need' -> 'I need'). Keep the real wording and meaning exactly as spoken: do NOT rephrase, reword, summarize, translate, or add any new content. Do NOT answer or respond to the content. Output ONLY the corrected text.",
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
            # Guard against the model ANSWERING or rephrasing instead of
            # transcribing (e.g. a dictated question comes back as an answer). A
            # faithful cleanup only tweaks punctuation/fillers, so it shouldn't
            # introduce many words absent from the raw. If it does, keep the raw.
            raw_words = set(re.findall(r"\w+", text.lower()))
            clean_words = re.findall(r"\w+", cleaned.lower())
            if clean_words:
                new_ratio = sum(w not in raw_words for w in clean_words) / len(clean_words)
                if new_ratio > 0.3:
                    logger.info(f"Cleanup diverged from transcript ({new_ratio:.0%} new words); using raw")
                    return text
            return cleaned
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
    return text


MEETING_SUMMARY_PROMPT = (
    "You are a meeting-notes assistant. You are given a raw meeting transcript "
    "(imperfect punctuation, no speaker labels). Produce concise Markdown notes with "
    "these sections, each as a level-2 heading: '## Summary' (2-4 sentences), "
    "'## Key points' (bullets), '## Decisions' (bullets; omit the section if none), and "
    "'## Action items' (bullets formatted '- [ ] task'; omit if none). Be faithful to the "
    "transcript — do not invent facts, names, or numbers. Output only the Markdown notes."
)


async def summarize_meeting(
    client: httpx.AsyncClient, transcript: str, api_key: str, provider: str
) -> str:
    """Summarize a meeting transcript into structured Markdown notes via an LLM.

    Returns "" on any failure — the caller still stores the full transcript, so a
    failed summary never loses the meeting.
    """
    if not transcript or len(transcript) < 20:
        return ""
    if provider == "groq":
        url, model = GROQ_CHAT_URL, "llama-3.3-70b-versatile"
    else:
        url, model = OPENAI_CHAT_URL, "gpt-4o-mini"
    try:
        response = await _post_with_retry(
            client,
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": MEETING_SUMMARY_PROMPT},
                    {"role": "user", "content": transcript},
                ],
                "temperature": 0.2,
                "max_tokens": 1024,
            },
        )
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()
        logger.error(f"Meeting summary failed: HTTP {response.status_code}")
    except Exception as e:
        logger.error(f"Meeting summary error: {e}")
    return ""


# Standalone filler words to drop from a stored transcript (with any trailing
# comma). Word-bounded so real words ("summer", "her") are untouched. Used for
# long meeting transcripts, where an LLM cleanup pass would be slow and risk
# truncation; short dictations still get the smarter LLM cleanup.
_FILLER_RE = re.compile(r"\b(?:um+|uh+|uhm+|umm+|er+|erm+|ah+|hmm+)\b,?", re.IGNORECASE)


def strip_fillers(text: str) -> str:
    """Remove standalone filler words and tidy the resulting whitespace/punctuation."""
    text = _FILLER_RE.sub("", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)   # no space before punctuation
    text = re.sub(r"(,\s*){2,}", ", ", text)       # collapse repeated commas
    text = re.sub(r"[ \t]{2,}", " ", text)         # collapse runs of spaces
    return text.strip()


INITIATIVE_TRIGGER_RE = re.compile(
    r"\b(?:start|create|begin|kick\s*off|new)\s+(?:an?\s+|my\s+)?initiative\b",
    re.IGNORECASE,
)


def is_initiative_intent(text: str) -> bool:
    """True if a note is asking to start an initiative (checked near the start)."""
    return bool(INITIATIVE_TRIGGER_RE.search(text[:160]))


REMINDER_TRIGGER_RE = re.compile(
    r"\b(?:remind me|set a reminder|reminder to)\b", re.IGNORECASE
)

# Voice-intent router: maps a note's leading text to a brain item type. To add a
# new spoken capture type, append (type, regex) here and handle it where notes
# are routed — everything else falls through to "note".
CAPTURE_INTENTS: list[tuple[str, "re.Pattern"]] = [
    ("initiative", INITIATIVE_TRIGGER_RE),
    ("reminder", REMINDER_TRIGGER_RE),
]


def classify_capture(text: str) -> str:
    head = text[:160]
    for kind, rx in CAPTURE_INTENTS:
        if rx.search(head):
            return kind
    return "note"


INITIATIVE_EXTRACT_PROMPT = (
    "The user spoke a note to start a personal initiative (a goal or project their "
    "second brain should track). From the note, extract a concise name (a noun phrase, "
    "max 6 words, without a leading 'initiative to') and the specific goals they mention. "
    'Respond with ONLY JSON: {"name": "...", "goals": ["...", ...]}. '
    "Use an empty goals list if none are stated."
)


async def extract_initiative(
    client: httpx.AsyncClient, text: str, api_key: str, provider: str
) -> tuple[str, list[str]]:
    """LLM-extract an initiative name + goals from a spoken note. Returns ("", [])
    on failure so the caller can fall back to a naive name."""
    url = GROQ_CHAT_URL if provider == "groq" else OPENAI_CHAT_URL
    model = "llama-3.3-70b-versatile" if provider == "groq" else "gpt-4o-mini"
    try:
        resp = await _post_with_retry(
            client, url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": INITIATIVE_EXTRACT_PROMPT},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.1,
                "max_tokens": 300,
                "response_format": {"type": "json_object"},
            },
        )
        if resp.status_code == 200:
            data = json.loads(resp.json()["choices"][0]["message"]["content"])
            name = str(data.get("name", "")).strip()
            goals = [str(g).strip() for g in data.get("goals", []) if str(g).strip()]
            return name, goals
        logger.error(f"Initiative extract failed: HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"Initiative extract error: {e}")
    return "", []


async def extract_reminder(
    client: httpx.AsyncClient, text: str, api_key: str, provider: str, now_iso: str
) -> tuple[str, str]:
    """LLM-extract (task, due) from a spoken reminder. `due` is ISO 8601 or ""
    (no time given). Relative times are resolved against now_iso. ("", "") on error."""
    url = GROQ_CHAT_URL if provider == "groq" else OPENAI_CHAT_URL
    model = "llama-3.3-70b-versatile" if provider == "groq" else "gpt-4o-mini"
    system = (
        f"The user spoke a reminder. The current local datetime is {now_iso}. Extract the "
        "task and its due datetime. Resolve relative times (\"tomorrow at 3\", \"in 2 hours\", "
        "\"Friday morning\") to an absolute local datetime based on the current datetime. "
        'Respond with ONLY JSON: {"task": "...", "due": "YYYY-MM-DDTHH:MM:SS"}. Leave "due" '
        "an empty string if no time is stated."
    )
    try:
        resp = await _post_with_retry(
            client, url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": text}],
                "temperature": 0.0,
                "max_tokens": 200,
                "response_format": {"type": "json_object"},
            },
        )
        if resp.status_code == 200:
            data = json.loads(resp.json()["choices"][0]["message"]["content"])
            return str(data.get("task", "")).strip(), str(data.get("due", "")).strip()
        logger.error(f"Reminder extract failed: HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"Reminder extract error: {e}")
    return "", ""


def load_wav_mono(path: str) -> np.ndarray:
    """Load a 16-bit PCM mono WAV (as recorded for meetings) into a float32 array
    normalized to [-1, 1] at the source sample rate."""
    with wave.open(path, "rb") as w:
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


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

# Spoken commands that, when said at the very end of a dictation, make cortex
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


def _clipboard_ready(text: str, timeout: float = 0.2) -> bool:
    """Poll wl-paste until the clipboard actually serves `text` (or timeout).

    wl-copy returns before the content is necessarily readable by other clients;
    pasting into that gap silently inserts nothing. This closes the race.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            got = subprocess.run(
                ["wl-paste", "--no-newline"], capture_output=True, timeout=1
            ).stdout
        except (subprocess.SubprocessError, OSError):
            return False
        if got.decode("utf-8", "replace") == text:
            return True
        time.sleep(0.01)
    return False


def _screen_context_enabled() -> bool:
    """Screen-context capture is opt-in (privacy) and needs the vault to store to."""
    return _HAS_BRAIN and bool(brain._settings().get("screenContext", False))


def _screen_denylist() -> list[str]:
    dl = brain._settings().get("screenDenylist") if _HAS_BRAIN else None
    return dl if isinstance(dl, list) else DEFAULT_SCREEN_DENYLIST


def _gemini_key() -> str | None:
    """Google API key for the vision captioner (Gemini 2.5 Flash). Without it,
    screen context falls back to local OCR."""
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    return brain._settings().get("geminiApiKey") if _HAS_BRAIN else None


def _capture_screen_context(storage, hint: str = "") -> None:
    """Grab the active window, distill it (Gemini vision → one line), and log it as
    an activity record. Best-effort; safe to call from a thread. Requires a vision
    key — without one we don't capture at all (no surprise local processing); OCR is
    only a fallback if an individual vision call fails."""
    key = _gemini_key()
    if not _screen_context_enabled() or not key:
        return
    try:
        import screen
    except ImportError:
        return
    try:
        rec = screen.describe_active_window(_screen_denylist(), key, hint)
        if rec:
            storage.append_activity(rec)
            logger.debug("Screen context logged (%s): %s", rec.get("mode"), rec.get("app"))
    except Exception:
        logger.debug("Screen context capture failed", exc_info=True)


def _active_window_desc() -> str:
    """Short 'class "title"' of the focused window — so a vanished paste's target
    is visible in the log (right window but empty clipboard, or the wrong window)."""
    try:
        out = subprocess.run(
            ["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=2
        ).stdout
        w = json.loads(out)
        return f'{w.get("class", "?")} "{(w.get("title", "") or "")[:40]}"'
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return "unknown"


def _notify(title: str, body: str = "") -> None:
    """Best-effort desktop notification. Visible even when audio feedback is themed
    'silent', so a failed dictation isn't an invisible no-op."""
    try:
        subprocess.run(
            ["notify-send", "-a", "cortex", "-u", "critical", title, body],
            check=False, timeout=2, stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass


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
        # Wait for the clipboard to actually serve the new text before firing the
        # paste key — wl-copy returns early, and pasting into that gap silently
        # inserts nothing (the intermittent "dictation vanished" on fast takes).
        if not _clipboard_ready(text):
            logger.warning("Clipboard not confirmed before paste — text may not have landed")
        subprocess.run(
            ["ydotool", "key", *chord],
            check=True, stderr=subprocess.DEVNULL, timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Paste failed ({type(e).__name__}: {e}); falling back to typing")
        return False

    logger.info(f"Pasted {len(text)} chars (chord={'+'.join(chord)}) -> {_active_window_desc()}")

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
# Spoken Actions (say the wake word + a command, cortex presses the real key)
# ============================================================================
#
# Every command is "<wake word> <command>" — e.g. "cortex enter", "cortex scratch
# that", "cortex select all". Requiring the wake word is what makes this safe:
# nobody says "cortex" mid-sentence, so a command only fires when you mean it, and
# the command words are consumed (not typed). The wake word is fuzzy-matched
# because Whisper renders the coined word "cortex" inconsistently, and it is
# user-configurable (commandWakeWord).
DEFAULT_WAKE_WORD = "jarvis"

# Fuzzy variants for coined wake words Whisper renders inconsistently. Real
# words and names (jarvis, computer) transcribe deterministically, so they're
# matched literally; only words listed here get the variant treatment. "cortex"
# scatters into "of flow / for flow / a flow", which is why it's not the default.
_WAKE_VARIANTS = {
    "cortex": ["cortex", "oflo", "o flow", "oh flow", "off flow"],
}

# Each action: "action" is "keys" (send chords) or "scratch" (delete last output).
SPOKEN_ACTIONS = [
    {"phrases": ["scratch that", "delete that"], "action": "scratch", "label": "⌫ scratch"},
    {"phrases": ["new paragraph"], "action": "keys", "label": "¶ paragraph", "keys": [_tap(_KEY_ENTER), _tap(_KEY_ENTER)]},
    {"phrases": ["new line", "next line"], "action": "keys", "label": "↵ new line", "keys": [_tap(_KEY_ENTER)]},
    # "answer"/"inter" are common ASR mishearings of "enter" — safe to alias since
    # they only fire right after the wake word, which you only say to give a command.
    {"phrases": ["enter", "return", "send it", "send", "submit", "answer", "inter"], "action": "keys", "label": "↵ enter", "keys": [_tap(_KEY_ENTER)]},
    {"phrases": ["tab"], "action": "keys", "label": "⇥ tab", "keys": [_tap(_KEY_TAB)]},
    {"phrases": ["escape", "cancel"], "action": "keys", "label": "⎋ escape", "keys": [_tap(_KEY_ESC)]},
    {"phrases": ["select all"], "action": "keys", "label": "select all", "keys": [_chord(_KEY_LEFTCTRL, _KEY_A)]},
    {"phrases": ["undo that", "undo"], "action": "keys", "label": "↶ undo", "keys": [_chord(_KEY_LEFTCTRL, _KEY_Z)]},
    {"phrases": ["redo that", "redo"], "action": "keys", "label": "↷ redo", "keys": [_chord(_KEY_LEFTCTRL, _KEY_LEFTSHIFT, _KEY_Z)]},
    {"phrases": ["delete word"], "action": "keys", "label": "⌫ word", "keys": [_chord(_KEY_LEFTCTRL, _KEY_BACKSPACE)]},
]


def _wake_pattern(wake_word: str) -> str:
    """Regex fragment matching the wake word (fuzzy only for coined words)."""
    word = (wake_word or DEFAULT_WAKE_WORD).strip()
    alts = _WAKE_VARIANTS.get(word.lower(), [word])
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
    # Separator tolerates punctuation the STT model inserts after the wake word.
    # Accurate models (ElevenLabs Scribe) punctuate mid-utterance, writing
    # "Jarvis. Enter." or "Jarvis, scratch that" — so a comma OR a period (or
    # other sentence marks) between the wake word and the command must still
    # match. The wake word itself is the guard against accidental triggers.
    pattern = re.compile(
        rf"\b{_wake_pattern(wake_word)}[\s.,;:!?]+(?:" + "|".join(parts) + r")\b",
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
    "do it, cortex enter" yields [text "do it"] + [key Enter]. Returns a single
    text segment when no command is present (the common case), so normal
    dictation — including the literal word "cortex" — is untouched.
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
        # Rolling pre-roll: the last PREROLL_CHUNKS captured while idle, so the
        # start of speech (spoken as the hotkey goes down) is prepended to the
        # recording instead of being lost. Fed on every callback; bounded, so it
        # self-trims. Only meaningful with a persistent stream (PERSISTENT_MIC).
        self._preroll: deque[np.ndarray] = deque(maxlen=PREROLL_CHUNKS)
        # Monotonic time of the last audio callback — a heartbeat used to detect
        # a zombie stream that stopped delivering audio (e.g. after suspend).
        self._last_callback_ts = 0.0
        # Set by _ensure_stream_open when it opens a *fresh* stream (on-demand or
        # self-heal), so _start_recording knows to wait for the mic to warm up
        # before arming capture. A reused live persistent stream leaves it False.
        self._stream_just_opened = False
        # Characters left on screen by the last dictation, so "scratch that" in a
        # following dictation can delete them.
        self._last_output_chars = 0
        # Where the current recording is routed at stop: "dictation" pastes into
        # the focused app (default); "note" saves to the second-brain vault. A
        # `note` command mid-recording (Copilot+N) flips it; reset on every start.
        self._capture_mode = "dictation"
        # A hands-free note session: True between the first Copilot+N (start) and
        # the second (stop & save). While set, the Copilot key *release* does NOT
        # stop the recording — only a second Copilot+N does — so the user needn't
        # hold the combo. Reset on every start and stop.
        self._note_session = False
        # Meeting recording (Copilot+M toggle). Unlike dictation/notes, a meeting
        # captures a system+mic mix to a file via PipeWire + pw-record, so it has
        # its own state rather than reusing the mic InputStream pipeline.
        self._meeting_active = False
        self._meeting_modules: list[str] = []   # pactl module ids to unload on stop
        self._meeting_proc: subprocess.Popen | None = None
        self._meeting_wav: str | None = None

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

        # Mic volume is boosted only *during* a recording and restored on stop
        # (see _start_recording / _restore_mic_volume), so cortex never leaves the
        # system mic altered for other apps like video calls. This holds the
        # level we must put back.
        self._saved_mic_volume: str | None = None

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
        cfg = get_stt_provider(provider)
        logger.info(f"cortex Ready ({cfg.label})")
        self._schedule_on_loop(self._prewarm_connection())
        if not cfg.supports_cleanup:
            cu_provider, _ = resolve_cleanup_provider(settings, provider, get_provider_key(settings, provider))
            if cu_provider:
                logger.info(f"LLM cleanup will use {get_stt_provider(cu_provider).label}")

        # Pre-warm the mic: open the stream now so the first dictation has no
        # open latency and the pre-roll is already filling. Best-effort — if no
        # mic is present yet, recording start will retry and self-heal. Skipped
        # for a Bluetooth default source so we don't force it to a low-quality
        # profile just by sitting idle.
        if self._persistent_mic_wanted():
            try:
                with self._recording_lock:
                    self._ensure_stream_open()
                logger.info("Mic stream pre-opened (persistent)")
            except Exception as e:
                logger.warning(f"Could not pre-open mic (will retry on record): {e}")
        elif PERSISTENT_MIC:
            logger.info("Persistent mic disabled (Bluetooth default source)")

        # Pre-warm the overlay: spawn it once now (paying the GTK startup cost at
        # launch, not on the hotkey) so it shows instantly on the first record.
        if settings.get("enableOverlay", True):
            self._spawn_osd()

        # Keep the Hyprland push-to-talk binding in sync with the chosen hotkey,
        # applying it now and whenever the setting changes in the UI.
        self._applied_hotkey: str | None = None
        self._start_hotkey_watcher()

        # Fire desktop notifications for due reminders (Copilot+N "remind me to…").
        if _HAS_BRAIN:
            threading.Thread(target=self._reminder_watch_loop, daemon=True,
                             name="cortex-reminders").start()
            # Passively describe the active window (opt-in) so the journal/dream see
            # the silent work dictation misses.
            threading.Thread(target=self._screen_sampler_loop, daemon=True,
                             name="cortex-screen").start()

    def _screen_sampler_loop(self):
        """When enabled, describe the active window on a slow tick — but only when
        the window actually changed, so we don't re-caption a window you're sitting
        in (saves vision calls and avoids near-duplicate entries)."""
        storage = StorageManager()
        last_win = None
        while getattr(self, "_running", True):
            time.sleep(SCREEN_SAMPLE_SECONDS)
            if not _screen_context_enabled():
                last_win = None
                continue
            try:
                import screen
                w = screen._active_window()  # cheap identity probe (no screenshot)
                win = ((w.get("class") or ""), (w.get("title") or ""))
                if win == last_win or not any(win):
                    continue  # same window as last sample — skip
                last_win = win
                _capture_screen_context(storage)
            except Exception:
                logger.debug("Screen sample failed", exc_info=True)

    def _reminder_watch_loop(self):
        """Poll the vault for due reminders and fire a notification for each."""
        while getattr(self, "_running", True):
            try:
                for path, task, _due in brain.due_reminders():
                    # Re-check right before firing: another synced device may have
                    # just fired + marked it done (multi-device dedup).
                    if brain._read_frontmatter(path).get("status") != "pending":
                        continue
                    _notify("⏰ Reminder", task)
                    brain.mark_reminder(path, "done")
                    logger.info(f"Reminder fired: {task}")
            except Exception:
                logger.debug("Reminder check failed", exc_info=True)
            time.sleep(REMINDER_POLL_SECONDS)

    def _start_hotkey_watcher(self):
        """Apply the configured hotkey now, then watch settings.json for changes."""
        self._sync_hotkey()
        threading.Thread(target=self._hotkey_watch_loop, daemon=True, name="cortex-hotkey").start()

    def _sync_hotkey(self):
        """Apply the hotkey if the setting differs from what's currently bound."""
        choice = load_settings().get("dictationHotkey", DEFAULT_DICTATION_HOTKEY)
        if choice != self._applied_hotkey:
            apply_dictation_hotkey(choice)
            self._applied_hotkey = choice

    def _hotkey_watch_loop(self):
        """Re-apply the hotkey shortly after settings.json changes on disk."""
        last_mtime = 0.0
        while True:
            time.sleep(2)
            try:
                mtime = SETTINGS_FILE.stat().st_mtime
            except OSError:
                continue
            if mtime != last_mtime:
                last_mtime = mtime
                self._sync_hotkey()

    def _start_async_loop(self):
        """Spin up the persistent event loop (background thread) + HTTP client."""
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="cortex-async"
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
        provider = settings.get("provider", "groq")
        cfg = get_stt_provider(provider)
        api_key = get_provider_key(settings, provider)
        if not api_key:
            return
        try:
            await self._client.get(
                cfg.warm_url,
                headers=cfg.auth_headers(api_key),
                timeout=5.0,
            )
            logger.debug(f"Connection pre-warmed ({cfg.label})")
        except Exception as e:
            logger.debug(f"Pre-warm failed: {e}")

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info: dict, status: int):
        """Callback for audio stream.

        Runs continuously while the (persistent) stream is open, not just during
        a recording. Every chunk is retained in the rolling pre-roll so the lead
        of an utterance isn't clipped; chunks are only committed to the recording
        queue (and drive the overlay meter) while ``is_recording`` is set.
        """
        if status:
            self._warn_stream_distress(f"Audio stream status: {status}")

        # Heartbeat: lets _ensure_stream_open detect a zombie stream (still
        # "active" but no longer delivering callbacks, e.g. after a suspend).
        self._last_callback_ts = time.monotonic()

        chunk = indata.copy()
        # Always keep the short pre-roll warm, even when idle.
        self._preroll.append(chunk)

        if not self.is_recording:
            return

        try:
            self.audio_queue.put_nowait(chunk)
        except queue.Full:
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.put_nowait(chunk)
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
        # Dismiss the resident overlay so it doesn't linger after we exit.
        try:
            self._quit_osd()
        except Exception:
            pass

        # Stop a meeting recorder and release its PipeWire modules so we don't
        # leak virtual sinks on exit.
        if getattr(self, "_meeting_proc", None) is not None:
            try:
                self._meeting_proc.terminate()
            except Exception:
                pass
        try:
            self._meeting_teardown_mix()
        except Exception:
            pass

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
        """Locate cortex-osd.py (next to this file, user install, or system pkg)."""
        candidates = [
            Path(__file__).resolve().parent / "cortex-osd.py",
            Path.home() / ".local" / "share" / "cortex" / "cortex-osd.py",
            Path("/usr/share/cortex/cortex-osd.py"),
            Path("/usr/lib/cortex/cortex-osd.py"),
        ]
        for path in candidates:
            if path.exists():
                return str(path)
        return None

    def _osd_send(self, msg: bytes):
        """Send a control message to the overlay (best-effort, non-blocking)."""
        if self._osd_send_sock is None:
            return
        try:
            self._osd_send_sock.sendto(msg, str(OSD_SOCK))
        except OSError:
            pass

    def _spawn_osd(self):
        """Spawn the resident overlay process if it isn't already running.

        The overlay stays alive for the server's lifetime and hides/shows on
        command, so the GTK startup cost is paid once (at launch) rather than on
        every hotkey press. Best-effort.
        """
        if self._osd_proc is not None and self._osd_proc.poll() is None:
            return  # already resident
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

    def _start_osd(self):
        """Ensure the overlay is resident and reveal it for a new recording."""
        self._spawn_osd()
        self._osd_send(b"show")

    def _stop_osd(self):
        """Hide the overlay; it stays resident (and warm) for the next record."""
        self._osd_send(b"stop")

    def _quit_osd(self):
        """Tell the resident overlay to exit, then reap the process (shutdown)."""
        self._osd_send(b"quit")
        proc = self._osd_proc
        self._osd_proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

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

    def _default_source_is_bluetooth(self) -> bool:
        """True if the default input is a Bluetooth device.

        Holding such a mic open forces the headset out of high-quality A2DP into
        the low-quality HFP/HSP "headset" profile, so we must NOT keep it open
        between dictations (see _persistent_mic_wanted). Best-effort: assumes
        not-Bluetooth if it can't tell.
        """
        try:
            src = subprocess.run(
                ["pactl", "get-default-source"],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip().lower()
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return False
        return src.startswith("bluez")

    def _persistent_mic_wanted(self) -> bool:
        """Whether to hold the mic open between dictations right now.

        Honors PERSISTENT_MIC but backs off for a Bluetooth default source, so a
        BT headset keeps its high-quality audio profile. Re-evaluated on each
        record/stop, so plugging/unplugging a headset is handled live.
        """
        return PERSISTENT_MIC and not self._default_source_is_bluetooth()

    def _read_mic_volume(self) -> str | None:
        """Read the default source's current level as a percentage (e.g. "100%").

        Returns None if it can't be determined (old pactl, no PulseAudio/PipeWire,
        parse miss) — callers then skip the restore rather than guess.
        """
        try:
            out = subprocess.run(
                ["pactl", "get-source-volume", "@DEFAULT_SOURCE@"],
                capture_output=True, text=True, timeout=2,
            ).stdout
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return None
        # e.g. "Volume: front-left: 65536 / 100% / 0.00 dB, front-right: ..."
        m = re.search(r"/\s*(\d+)%", out)
        return f"{m.group(1)}%" if m else None

    def _set_mic_volume(self, level="110%"):
        """Force the default input source to a fixed level, for the duration of a
        recording only.

        Other apps (browsers, meeting tools, AGC) routinely lower the mic gain,
        which starves Whisper of signal and hurts accuracy. Boosting per-record
        gives each dictation a strong, level input; the prior level is snapshotted
        by the caller and put back by _restore_mic_volume on stop, so the system
        mic is left exactly as we found it. Defaults slightly above 100% so quiet/
        whispered speech still carries; override via CORTEX_MIC_VOLUME (e.g. "130%"
        for a very soft speaker, or "100%" to disable the boost).
        """
        level = os.getenv("CORTEX_MIC_VOLUME", level)
        try:
            subprocess.run(
                ["pactl", "set-source-volume", "@DEFAULT_SOURCE@", level],
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    def _restore_mic_volume(self):
        """Put the mic level back to the value snapshotted before we boosted it,
        so cortex doesn't leave the system mic altered for other apps (e.g. calls).
        No-op if nothing was snapshotted."""
        level = self._saved_mic_volume
        self._saved_mic_volume = None
        if not level:
            return
        try:
            subprocess.run(
                ["pactl", "set-source-volume", "@DEFAULT_SOURCE@", level],
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    def _start_recording(self):
        """Start recording audio."""
        with self._recording_lock:
            if self.is_recording:
                logger.debug("Already recording, ignoring start command")
                return

            self.audio_data = []
            self._last_stream_warn = 0.0
            # Every recording starts as dictation; a `note` command (Copilot+N)
            # converts it into a hands-free note session before stop.
            self._capture_mode = "dictation"
            self._note_session = False

            try:
                # Load settings up front so the overlay can be shown *before* any
                # audio setup. The OSD is a decoupled subprocess; making it wait
                # behind mic-gain and stream-open work makes it lag the keypress.
                settings = load_settings()

                # Show the on-screen recording overlay first, so it appears the
                # instant the hotkey is pressed. With a resident overlay this is a
                # non-blocking "show" datagram; it renders in parallel.
                if settings.get("enableOverlay", True):
                    self._start_osd()

                # Ensure the mic stream is live. With PERSISTENT_MIC this is a
                # no-op after startup; otherwise it opens on-demand here. Also
                # self-heals a stream killed by a PipeWire/WirePlumber restart.
                self._ensure_stream_open()

                # On-demand: the mic just opened and isn't delivering audio yet.
                # Wait for it to warm up before arming capture and cueing the
                # user, so the opening words land in the pre-roll below instead of
                # the device-open gap (the "No one..."-style clipped start). No-op
                # for a warm persistent stream.
                self._await_mic_warm()

                # Seed the recording with the pre-roll, then flip capture on, so
                # the buffered leading audio lands *ahead* of the live chunks.
                # Order matters: while is_recording is still False the callback
                # won't enqueue, so nothing can interleave between the two.
                for chunk in list(self._preroll):
                    try:
                        self.audio_queue.put_nowait(chunk)
                    except queue.Full:
                        break
                self.is_recording = True

                self.waybar_state.recording()
                self.audio_feedback.play_start()

                # Refresh the kept-warm connection now, while the user speaks,
                # so a keep-alive that expired during idle is hot by release.
                self._schedule_on_loop(self._prewarm_connection())

                # Boost mic gain for this recording — other apps/AGC may have
                # lowered it. Snapshot the current level first so _stop_recording
                # can restore it (leaving the system mic untouched for calls).
                # Done after capture is live (and covered by the pre-roll) so the
                # pactl round-trips can't clip the start of the utterance.
                self._saved_mic_volume = self._read_mic_volume()
                self._set_mic_volume()

                self.text_processor = TextProcessor(
                    enable_punctuation=settings.get("enableSpokenPunctuation", False),
                    replacements=settings.get("wordReplacements", {}),
                )
                # Pick up audio-feedback changes live (e.g. turning sounds off)
                self.audio_feedback.theme = settings.get("audioFeedbackTheme", "default")
                self.audio_feedback.volume = max(0.0, min(1.0, settings.get("audioFeedbackVolume", 0.3)))

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

    def _await_mic_warm(self):
        """Block briefly until a freshly opened mic starts delivering audio.

        On-demand (PERSISTENT_MIC off), the stream is opened on the hotkey press
        and PortAudio needs a moment before the first callback fires; speech in
        that gap is lost, clipping the opening word(s). We wait — up to
        MIC_WARMUP_SECONDS — for the first chunk to land in the (freshly cleared)
        pre-roll, then let the caller seed it into the recording. Bounded so a
        silent or never-delivering mic can't hang the hotkey. No-op when the
        stream was already warm (persistent reuse) or warm-up is disabled.
        """
        if not self._stream_just_opened or MIC_WARMUP_SECONDS <= 0:
            return
        # The callback appends to _preroll on every chunk; _ensure_stream_open
        # cleared it on open, so a non-empty deque means the first audio landed.
        deadline = time.monotonic() + MIC_WARMUP_SECONDS
        while not self._preroll and time.monotonic() < deadline:
            time.sleep(0.005)
        if not self._preroll:
            logger.debug(f"Mic warm-up: no audio within {MIC_WARMUP_SECONDS * 1000:.0f}ms")

    def _ensure_stream_open(self):
        """Open the mic stream if it isn't already live (idempotent).

        With PERSISTENT_MIC this is called once at startup and is a no-op on
        every subsequent recording — except when the stream has died, in which
        case the stale stream is dropped and a fresh one opened (self-healing).

        Two death modes are handled:
          - PortAudio reports the stream inactive (device removed / server
            restart) — caught by the ``active`` check.
          - The stream is a *zombie*: still ``active`` but delivering no
            callbacks. This happens after a suspend/resume on a long-lived
            persistent stream — PortAudio never resumes it, so the callback
            stops firing and only the (now frozen) pre-roll is ever captured.
            We detect it by the gap since the last callback: when healthy the
            callback fires every ~100ms, so a gap past STREAM_STALE_SECONDS means
            the stream is dead even though ``active`` still says otherwise.

        Callers hold the recording lock.
        """
        if self.stream is not None:
            alive = False
            try:
                gap = time.monotonic() - self._last_callback_ts
                alive = bool(self.stream.active) and gap <= STREAM_STALE_SECONDS
            except Exception:
                alive = False
            if alive:
                self._stream_just_opened = False
                return
            # Inactive or zombie (no callbacks) — discard before reopening. The
            # pre-roll it left behind is stale (possibly from before a suspend),
            # so drop it; a fresh stream refills it with live audio.
            logger.info("Mic stream stale/dead — reopening")
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        # A freshly opened stream carries no valid pre-roll: whatever sits in the
        # deque is stale — the frozen tail of the previous recording when the mic
        # opens on-demand (PERSISTENT_MIC off), or pre-suspend audio from a zombie
        # stream. Drop it so it can't bleed into the next recording. A healthy
        # persistent stream returns above, keeping its real live leading audio.
        self._preroll.clear()
        self.stream = self._open_audio_stream()
        self._stream_just_opened = True
        # Grace: the fresh stream hasn't fired a callback yet, so don't let the
        # staleness check above trip on the next call before audio starts flowing.
        self._last_callback_ts = time.monotonic()

    def _open_audio_stream(self):
        """Open and start the input stream, recovering from a stale/empty
        PortAudio device list.

        PortAudio snapshots the ALSA/PipeWire device list when it first
        initializes and never refreshes it. After the audio server
        (PipeWire/WirePlumber) restarts — or if the mic briefly drops out — the
        cached default-source handle is stale and ``InputStream`` open fails
        with ``ALSA error -2`` ('No such file or directory'). This is the usual
        cause of intermittent "recording failed": reinitializing PortAudio
        re-enumerates devices, so we reinit and retry a few times, giving the
        audio server a moment to settle between attempts. On every failure we
        log the devices PortAudio can see, so a stale list (mic present, open
        still fails) is distinguishable from a genuinely missing mic.
        """

        def _make():
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=AUDIO_CHANNELS,
                dtype=np.float32,
                blocksize=AUDIO_BLOCKSIZE,  # 100ms chunks → 10 callbacks/sec → 3000 chunks = 5 min
                callback=self._audio_callback,
            )
            stream.start()
            return stream

        last_err: Exception | None = None
        for attempt in range(1, AUDIO_OPEN_MAX_ATTEMPTS + 1):
            try:
                stream = _make()
                if attempt > 1:
                    logger.info(f"Audio stream opened on attempt {attempt}")
                return stream
            except Exception as e:
                last_err = e
                logger.warning(
                    f"InputStream open failed "
                    f"(attempt {attempt}/{AUDIO_OPEN_MAX_ATTEMPTS}): "
                    f"{type(e).__name__}: {e}"
                )
                self._log_audio_devices()
                if attempt < AUDIO_OPEN_MAX_ATTEMPTS:
                    # Reinit so PortAudio re-enumerates devices (its cached list
                    # is stale after an audio-server restart), then let the
                    # server settle briefly before the next attempt.
                    try:
                        sd._terminate()
                        sd._initialize()
                    except Exception:
                        logger.exception("PortAudio reinitialization failed")
                    time.sleep(AUDIO_OPEN_RETRY_DELAY)

        logger.error(
            "Could not open the microphone after %d attempts. Check that a "
            "capture device exists; if the audio server was restarted, try: "
            "systemctl --user restart wireplumber pipewire pipewire-pulse",
            AUDIO_OPEN_MAX_ATTEMPTS,
        )
        raise last_err  # type: ignore[misc]

    def _log_audio_devices(self):
        """Log the input devices PortAudio currently sees. This is the single
        most useful breadcrumb for the intermittent 'recording failed' case:
        it tells a stale device list (real mic listed, open still fails) apart
        from a dropped mic (only monitors, or nothing)."""
        try:
            devices = sd.query_devices()
        except Exception as e:
            logger.warning(f"Could not query audio devices: {type(e).__name__}: {e}")
            return
        inputs = [
            f"[{i}] {d['name']} (in={d['max_input_channels']})"
            for i, d in enumerate(devices)
            if d.get("max_input_channels", 0) > 0
        ]
        if not inputs:
            logger.error(
                "PortAudio sees NO input devices — the microphone is not "
                "available to this process (the audio server may have dropped it)."
            )
            return
        logger.info("PortAudio input devices: " + "; ".join(inputs))
        try:
            logger.info(f"PortAudio default input: {sd.query_devices(kind='input')['name']}")
        except Exception:
            pass

    def _rollback_recording(self):
        """Reset recording state after a failed start. Caller must hold the lock."""
        self.is_recording = False
        if self._recording_watchdog is not None:
            self._recording_watchdog.cancel()
            self._recording_watchdog = None
        # Keep a persistent stream open (it's healthy — the failure was
        # elsewhere); only tear it down in on-demand mode (incl. Bluetooth).
        if not self._persistent_mic_wanted() and self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self.audio_data = []
        self._resume_media()
        self._restore_mic_volume()
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

            # Whatever stopped us (toggle, Copilot release, or the watchdog),
            # the note session is over — clear it so the next start is clean.
            self._note_session = False

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

        # Keep the stream open between dictations (persistent mic) so the next
        # one has no open latency and a warm pre-roll. In on-demand mode — which
        # includes a Bluetooth default source — close it now to release the mic
        # while idle (a held-open BT mic drops the headset to low-quality audio).
        if not self._persistent_mic_wanted() and self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        # Resume any media we paused, now that the mic is closed
        self._resume_media()
        # Put the mic level back exactly as we found it (don't leave it boosted
        # for other apps, e.g. video calls).
        self._restore_mic_volume()
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

    def _run_brain(self, *args: str):
        """Fire the brain CLI (cortex-brain) detached, best-effort — used for the
        initiative auto-linker so it never blocks or crashes a capture."""
        try:
            subprocess.Popen(
                [str(Path.home() / ".local" / "bin" / "cortex-brain"), *args],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except Exception as e:
            logger.debug(f"Could not start brain linker: {e}")

    def _save_note(self, text: str):
        """Save a note-mode capture to the second-brain vault (Copilot+N)."""
        if not _HAS_BRAIN:
            logger.error("Note capture requested but brain module is unavailable")
            self.waybar_state.error("Note unavailable")
            self.audio_feedback.play_error()
            return
        try:
            path = brain.add_note(text)
            snippet = (text[:60] + "…") if len(text) > 60 else text
            _notify("📝 Noted", snippet)
            logger.info(f"Note saved → {path}")
            self._run_brain("--link", str(path))  # auto-map to initiatives
        except Exception:
            logger.exception("Failed to save note to brain")
            self.waybar_state.error("Note save failed")
            self.audio_feedback.play_error()

    def _cancel_recording(self):
        """Abort an in-flight mic recording, discarding its audio (no transcribe or
        paste). Used to kill the spurious dictation that a Copilot key-press starts
        when the user actually meant Copilot+M (meeting)."""
        with self._recording_lock:
            if not self.is_recording:
                return
            self._note_session = False
            self._rollback_recording()
            logger.info("Cancelled in-flight dictation (meeting toggle)")

    def _meeting_setup_mix(self) -> str:
        """Load a PipeWire null-sink mixing system output + mic; return the node to
        record from. Records the loaded module ids for teardown."""
        def _default(kind: str) -> str:
            return subprocess.run(
                ["pactl", f"get-default-{kind}"], capture_output=True, text=True, timeout=5
            ).stdout.strip()

        def _load(*args: str) -> None:
            out = subprocess.run(
                ["pactl", "load-module", *args],
                capture_output=True, text=True, timeout=5, check=True,
            ).stdout.strip()
            self._meeting_modules.append(out)

        sink, source = _default("sink"), _default("source")
        self._meeting_modules = []
        _load("module-null-sink", "sink_name=cortex_recmix",
              "sink_properties=device.description=CortexRecMix")
        _load("module-loopback", f"source={sink}.monitor", "sink=cortex_recmix", "latency_msec=20")
        _load("module-loopback", f"source={source}", "sink=cortex_recmix", "latency_msec=20")
        return "cortex_recmix.monitor"

    def _meeting_teardown_mix(self):
        """Unload the PipeWire modules from _meeting_setup_mix (best-effort)."""
        for mod in reversed(self._meeting_modules):
            subprocess.run(["pactl", "unload-module", mod], capture_output=True, timeout=5)
        self._meeting_modules = []

    def _start_meeting(self):
        """Start recording a system+mic meeting mix to a WAV via pw-record."""
        with self._recording_lock:
            if self._meeting_active:
                return
            try:
                target = self._meeting_setup_mix()
                RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
                self._meeting_wav = str(RUNTIME_DIR / "meeting.wav")
                self._meeting_proc = subprocess.Popen(
                    ["pw-record", "--target", target, "--rate", str(SAMPLE_RATE),
                     "--channels", "1", self._meeting_wav],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self._meeting_active = True
                self.waybar_state.recording()
                self.audio_feedback.play_start()
                _notify("🎙️ Recording meeting", "Press Copilot+M again to stop and summarize.")
                logger.info(f"Meeting recording started → {self._meeting_wav}")
            except Exception:
                logger.exception("Failed to start meeting; tearing down")
                self._meeting_teardown_mix()
                self._meeting_active = False
                self.waybar_state.error("Meeting failed")
                self.audio_feedback.play_error()

    def _stop_meeting(self):
        """Stop the meeting recorder, release the mix, and process the audio."""
        with self._recording_lock:
            if not self._meeting_active:
                return
            self._meeting_active = False
            proc, wav = self._meeting_proc, self._meeting_wav
            self._meeting_proc = None
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._meeting_teardown_mix()
        self.audio_feedback.play_stop()
        self.waybar_state.transcribing()
        logger.info("Meeting recording stopped; transcribing")
        _notify("⏹️ Meeting stopped", "Transcribing & summarizing…")
        self._run_on_loop(self._process_meeting(wav))

    async def _process_meeting(self, wav_path: str | None):
        """Transcribe a recorded meeting, summarize it, and save both to the brain."""
        if not wav_path or not Path(wav_path).exists():
            logger.error("Meeting audio missing; nothing to process")
            self.waybar_state.idle()
            return
        settings = load_settings()
        provider = settings.get("provider", "groq")
        api_key = get_provider_key(settings, provider)
        if not api_key:
            self.waybar_state.error("API key not set")
            self.audio_feedback.play_error()
            return
        try:
            audio = load_wav_mono(wav_path)
        finally:
            # We have the audio in memory now — drop the temp file either way.
            try:
                Path(wav_path).unlink(missing_ok=True)
            except Exception:
                pass
        duration = len(audio) / SAMPLE_RATE
        logger.info(f"Processing {duration:.0f}s meeting audio")

        client = self._client
        if client is None:  # loop torn down mid-shutdown
            self.waybar_state.idle()
            return

        raw = await transcribe_audio_chunked(client, audio, api_key, provider)
        if not raw:
            logger.error("Meeting transcription produced no text")
            self.waybar_state.error("No text")
            self.audio_feedback.play_error()
            return

        # Strip filler words from the stored transcript (cheap, local, safe on
        # arbitrarily long text — unlike the LLM cleanup used for short dictations).
        transcript = strip_fillers(raw)

        cleanup_provider, cleanup_key = resolve_cleanup_provider(settings, provider, api_key)
        summary = ""
        if cleanup_key:
            summary = await summarize_meeting(client, transcript, cleanup_key, cleanup_provider)

        if not _HAS_BRAIN:
            logger.error("Meeting recorded but brain module unavailable")
            self.waybar_state.error("Brain unavailable")
            self.audio_feedback.play_error()
            return
        try:
            path = brain.add_meeting(transcript, summary)
            _notify("📝 Meeting saved", f"{duration / 60:.0f} min → {path.name}")
            logger.info(f"Meeting saved → {path}")
            self._run_brain("--link", str(path))  # auto-map to initiatives
        except Exception:
            logger.exception("Failed to save meeting to brain")
            self.waybar_state.error("Meeting save failed")
            self.audio_feedback.play_error()
            return
        self.waybar_state.idle()

    async def _save_initiative(self, client, text: str, provider: str, key: str | None):
        """Create an initiative from a spoken note ("start an initiative…")."""
        name, goals = "", []
        if key:
            name, goals = await extract_initiative(client, text, key, provider)
        if not name:
            # Fallback: use the note text (minus the trigger phrase) as the name.
            stripped = INITIATIVE_TRIGGER_RE.sub("", text, count=1).lstrip(" ,.:—-")
            name = (stripped.split(".")[0].strip() or "New initiative")[:60]
        try:
            path = brain.add_initiative(name, goals, note=text)
            detail = name + (f" · {len(goals)} goal{'s' if len(goals) != 1 else ''}" if goals else "")
            _notify("🎯 Initiative created", detail)
            logger.info(f"Initiative created → {path}")
            self._run_brain("--link-all")  # retro-link existing notes/meetings to it
        except Exception:
            logger.exception("Failed to save initiative; saving as a note instead")
            self._save_note(text)

    async def _save_reminder(self, client, text: str, provider: str, key: str | None):
        """Create a reminder from a spoken note ("remind me to…")."""
        task, due = "", ""
        if key:
            task, due = await extract_reminder(
                client, text, key, provider, datetime.now().isoformat(timespec="seconds"))
        if not task:
            task = REMINDER_TRIGGER_RE.sub("", text, count=1).lstrip(" ,.:to").strip()[:80] or "Reminder"
        try:
            brain.add_reminder(task, due=due, note=text)
            when = ""
            if due:
                try:
                    when = " · " + datetime.fromisoformat(due).strftime("%a %b %-d, %-I:%M %p")
                except ValueError:
                    pass
            _notify("⏰ Reminder set", task + when)
            logger.info(f"Reminder set: {task} (due {due or 'unset'})")
        except Exception:
            logger.exception("Failed to save reminder; saving as a note instead")
            self._save_note(text)

    async def _process_transcription(self):
        """Process recorded audio: transcribe, clean up, and type result."""
        settings = load_settings()
        provider = settings.get("provider", "groq")
        api_key = get_provider_key(settings, provider)
        enable_cleanup = settings.get("enableCleanup", True)
        enable_actions = settings.get("enableSpokenActions", True)
        wake_word = settings.get("commandWakeWord", DEFAULT_WAKE_WORD)
        fast_max_words = settings.get("fastModeMaxWords", DEFAULT_FAST_MODE_MAX_WORDS)

        if not api_key:
            error_msg = f"❌ {get_stt_provider(provider).label} API key not set. Configure it in Settings."
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

        # Speech-presence gate. Logged for every clip so the threshold can be
        # calibrated from real usage; when configured (>0), a clip whose loudest
        # frame is below it is treated as no-speech and never sent to the STT
        # model — the fix for a noisy mic where Whisper hallucinates on silence.
        speech_rms = loudest_frame_rms(audio)
        logger.info(
            f"Audio level: loudest_frame_rms={speech_rms:.4f}, peak={np.max(np.abs(audio)):.4f} "
            f"(speech gate={MIN_SPEECH_RMS or 'off'})"
        )
        if MIN_SPEECH_RMS > 0 and speech_rms < MIN_SPEECH_RMS:
            logger.info(
                f"No speech detected (frame RMS {speech_rms:.4f} < {MIN_SPEECH_RMS}); "
                "skipping transcription"
            )
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
            _notify("Dictation failed", "No text — network/API timeout or nothing recognized. Try again.")
            return

        logger.info(f"Transcription: {(t1 - t0) * 1000:.0f}ms | Raw: {raw_text[:80]}")

        # Apply text processing (spoken punctuation, replacements)
        raw_text = self.text_processor.process(raw_text)

        # Cleanup (if enabled) — fast mode skips it on short dictations. The
        # cleanup LLM may run on a different provider than transcription, since
        # ElevenLabs/Deepgram don't offer a chat endpoint (falls back to Groq/OpenAI).
        skip_for_speed = should_skip_cleanup(raw_text, fast_max_words)
        cleanup_provider, cleanup_key = resolve_cleanup_provider(settings, provider, api_key)
        if enable_cleanup and not skip_for_speed and cleanup_key:
            t0 = time.perf_counter()
            cleaned_text = await cleanup_text(client, raw_text, cleanup_key, cleanup_provider)
            t1 = time.perf_counter()
            logger.info(f"Cleanup: {(t1 - t0) * 1000:.0f}ms ({get_stt_provider(cleanup_provider).label})")
        else:
            cleaned_text = raw_text
            if enable_cleanup and skip_for_speed:
                logger.info(
                    f"Fast mode: skipped cleanup ({len(raw_text.split())} words "
                    f"<= {fast_max_words})"
                )
            elif enable_cleanup and not cleanup_key:
                logger.info("Cleanup skipped: no Groq/OpenAI key configured for the cleanup LLM")

        # Route by capture mode. A note goes to the second-brain vault instead of
        # being pasted; dictation (default) is output into the focused app.
        if self._capture_mode == "note":
            # Route the note by spoken intent: "start an initiative…" → initiative,
            # (future types plug into CAPTURE_INTENTS) — otherwise a plain note.
            kind = classify_capture(cleaned_text) if _HAS_BRAIN else "note"
            if kind == "initiative":
                await self._save_initiative(client, cleaned_text, cleanup_provider, cleanup_key)
            elif kind == "reminder":
                await self._save_reminder(client, cleaned_text, cleanup_provider, cleanup_key)
            else:
                self._save_note(cleaned_text)
        elif enable_actions:
            # Output: run "<wake word> command" voice commands as real keystrokes
            # (default), or paste-and-optionally-submit on the legacy path.
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
            app=_active_window_desc(),
        )
        # Capture what's on screen at this moment (opt-in) so the journal/dream know
        # what you were dictating INTO, not just what you said. Threaded — the vision
        # call must never delay the paste flow.
        threading.Thread(target=_capture_screen_context, args=(storage, cleaned_text),
                          daemon=True, name="cortex-screenctx").start()

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
                        # In a hands-free note session, releasing the Copilot key
                        # must NOT stop the recording — the session ends only on a
                        # second Copilot+N (the `note` toggle below).
                        if not self._note_session:
                            self._stop_recording()
                    elif cmd == "toggle":
                        if self.is_recording:
                            self._stop_recording()
                        else:
                            self._start_recording()
                    elif cmd == "note":
                        # Copilot+N toggles a hands-free note. `note` can only fire
                        # while Super+Shift (the Copilot key) is held, which also
                        # fired `start` — so we're normally recording here.
                        if not self.is_recording:
                            pass  # defensive: nothing to convert
                        elif not self._note_session:
                            # First Copilot+N: convert this recording into a note
                            # session that survives the Copilot release.
                            self._capture_mode = "note"
                            self._note_session = True
                            logger.info("Note session started (Copilot+N again to stop)")
                            self.waybar_state.recording()
                            _notify("🎙️ Recording note", "Press Copilot+N again to stop and save.")
                        else:
                            # Second Copilot+N: end the session and save the note.
                            logger.info("Note session ended by toggle; saving")
                            self._note_session = False
                            _notify("⏹️ Note stopped", "Transcribing & saving…")
                            self._stop_recording()
                    elif cmd == "meeting":
                        # Copilot+M toggles a meeting. The Copilot key-down already
                        # fired `start`, spawning a spurious mic dictation — cancel
                        # it (discard, no paste) before toggling the meeting.
                        self._cancel_recording()
                        if self._meeting_active:
                            self._stop_meeting()
                        else:
                            self._start_meeting()
                except Exception:
                    # A failed recording must never tear down the IPC server,
                    # otherwise the global shortcut goes permanently dead until
                    # cortex is manually restarted. Log, signal the failure, and
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
    if provider not in STT_PROVIDERS:
        logger.error(
            f"❌ Unknown provider '{provider}'. "
            f"Valid options: {', '.join(STT_PROVIDERS)}. Falling back to Groq."
        )
        provider = "groq"
    cfg = get_stt_provider(provider)

    api_key = get_provider_key(settings, provider)
    if not api_key:
        logger.warning(f"⚠️  {cfg.label} API key not configured. Get one at {cfg.signup_url}")
    elif cfg.key_pattern and not cfg.key_pattern.match(api_key):
        logger.error(f"❌ Invalid {cfg.label} API key format.")
    elif provider == "groq" and len(api_key) > GROQ_API_KEY_MAX_LENGTH:
        logger.error(
            "❌ Groq API key looks duplicated (too long). "
            f"Expected ~{GROQ_API_KEY_LENGTH} chars, got {len(api_key)}. Check ~/.cortex/settings.json"
        )

    # Cleanup needs a chat-capable (Groq/OpenAI) key; warn if STT can't and none exists.
    if api_key and not cfg.supports_cleanup and settings.get("enableCleanup", True):
        cu_provider, _ = resolve_cleanup_provider(settings, provider, api_key)
        if not cu_provider:
            logger.info(
                f"ℹ️  {cfg.label} has no LLM cleanup; add a Groq or OpenAI key "
                "to enable cleanup, otherwise it will be skipped."
            )

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
    """Main entry point for Cortex."""
    global _server

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd not in ("start", "stop", "toggle", "note", "meeting"):
            print(f"Unknown command: {cmd}", file=sys.stderr)
            print("Usage: cortex [start|stop|toggle|note|meeting]", file=sys.stderr)
            sys.exit(1)

        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(SOCKET_PATH)
            s.send(cmd.encode())
            s.close()
        except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
            print(f"Server not running: {e}\nStart the server with: ./cortex", file=sys.stderr)
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
