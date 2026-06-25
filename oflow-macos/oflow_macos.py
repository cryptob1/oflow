#!/usr/bin/env python3
"""
oflow-macos - Voice dictation for macOS.

Press Right Shift, speak, press again — your words appear wherever
you're typing. Uses whisper.cpp locally for transcription (no API key needed).

Architecture:
  Menu Bar App (rumps) + Global Hotkey (pynput)
  → Audio Recording (sounddevice) → Validation
  → Local Whisper STT (whisper.cpp) → pynput Text Output

macOS-specific:
  - Global hotkeys via pynput (CGEventTap)
  - Text input via pynput Controller (CGEventPost)
  - Menu bar via rumps (NSStatusItem)
  - Clipboard via pbcopy
  - Local Whisper via whisper.cpp (Metal GPU acceleration)
  - Requires Accessibility + Microphone permissions
"""

from __future__ import annotations

import ctypes
import ctypes.util
import fcntl
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
from datetime import datetime
from pathlib import Path

import numpy as np
import rumps
import sounddevice as sd
from pywhispercpp.model import Model as WhisperModel
from pynput import keyboard

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

SOCKET_PATH = "/tmp/oflow-macos.sock"
PID_FILE = "/tmp/oflow-macos.pid"

# Audio configuration
SAMPLE_RATE = 16000  # 16kHz (Whisper requirement)
AUDIO_CHANNELS = 1  # Mono
NORMALIZATION_TARGET = 0.95
MIN_AUDIO_DURATION_SECONDS = 0.5
MIN_AUDIO_AMPLITUDE = 0.02

# File paths
SETTINGS_FILE = Path.home() / ".oflow" / "settings.json"
TRANSCRIPTS_FILE = Path.home() / ".oflow" / "transcripts.jsonl"

# Whisper model configuration
DEFAULT_WHISPER_MODEL = "large-v3"  # Best for accented English
WHISPER_MODELS_DIR = Path.home() / ".oflow" / "models"

# Default hotkey
DEFAULT_HOTKEY = "right_shift"


# ============================================================================
# macOS Accessibility Check
# ============================================================================


def _is_launched_by_launchd() -> bool:
    # launchd sets XPC_SERVICE_NAME for its jobs.
    return bool(os.getenv("XPC_SERVICE_NAME")) or os.getppid() == 1


def _get_macos_executable_path() -> str | None:
    """Best-effort: return the actual running Mach-O path on macOS."""
    try:
        libc_path = ctypes.util.find_library("c") or "/usr/lib/libSystem.B.dylib"
        libc = ctypes.cdll.LoadLibrary(libc_path)
        ns_get_executable_path = getattr(libc, "_NSGetExecutablePath", None)
        if ns_get_executable_path is None:
            return None

        ns_get_executable_path.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32)]
        ns_get_executable_path.restype = ctypes.c_int

        bufsize = ctypes.c_uint32(1024)
        for _ in range(4):
            buf = ctypes.create_string_buffer(bufsize.value)
            res = ns_get_executable_path(buf, ctypes.byref(bufsize))
            if res == 0:
                return os.path.realpath(buf.value.decode("utf-8"))
            if res != -1:
                break
        return None
    except Exception as e:
        logger.debug(f"Failed to get executable path: {e}")
        return None


def _permission_targets() -> list[str]:
    """Likely executables the user may need to add to macOS privacy lists."""
    candidates: list[str] = []

    def add_path(path: str | None) -> None:
        if not path:
            return
        # If this is inside an app bundle, also suggest the .app path (System Settings
        # file picker is app-centric).
        marker = ".app/Contents/MacOS/"
        if marker in path:
            app_path = path.split(marker, 1)[0] + ".app"
            candidates.append(app_path)
            try:
                real_app = os.path.realpath(app_path)
                if real_app != app_path:
                    candidates.append(real_app)
            except Exception:
                pass

        candidates.append(path)
        try:
            real = os.path.realpath(path)
            if real != path:
                candidates.append(real)
        except Exception:
            pass

    exe = _get_macos_executable_path()
    add_path(exe)

    add_path(sys.executable)

    base_exe = getattr(sys, "_base_executable", None)
    add_path(base_exe)

    try:
        add_path(str(Path(sys.executable).resolve()))
    except Exception:
        pass

    seen: set[str] = set()
    unique: list[str] = []
    for path in candidates:
        if not path:
            continue
        path = os.path.normpath(path)
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def check_accessibility() -> bool:
    """Check if the process has macOS Accessibility permissions."""
    try:
        lib_path = ctypes.util.find_library("ApplicationServices")
        if lib_path:
            lib = ctypes.cdll.LoadLibrary(lib_path)
            return bool(lib.AXIsProcessTrusted())
    except Exception as e:
        logger.debug(f"Accessibility check failed: {e}")
    return False


def prompt_accessibility_instructions():
    """Notify user and open System Settings for Accessibility permissions."""
    targets = _permission_targets()
    primary = targets[0] if targets else "<unknown>"

    if _is_launched_by_launchd():
        hint = (
            "LaunchAgent/background service mode is not supported yet.\n\n"
            "Recommended: quit this and run oflow-macos from Terminal, then add Terminal/iTerm2 to:\n"
            "  Privacy & Security > Accessibility\n"
            "  Privacy & Security > Input Monitoring\n\n"
            "If you still want to try LaunchAgent mode, add this executable instead:\n"
            f"{primary}"
        )
    else:
        hint = (
            "Add your Terminal app (Terminal/iTerm2) to:\n"
            "  Privacy & Security > Accessibility\n"
            "  Privacy & Security > Input Monitoring\n\n"
            "If that doesn't work, add the Python executable instead:\n"
            f"{primary}\n\n"
            "Then restart oflow."
        )

    rumps.notification(
        title="oflow - Permission Required",
        subtitle="Accessibility access needed",
        message=hint,
    )
    subprocess.run(
        ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
        check=False,
    )


# ============================================================================
# Settings Management
# ============================================================================


def ensure_data_dir() -> None:
    """Ensure ~/.oflow directory and default files exist."""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WHISPER_MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if not SETTINGS_FILE.exists():
        default_settings = {
            "whisperModel": DEFAULT_WHISPER_MODEL,
            "audioFeedbackTheme": "default",
            "audioFeedbackVolume": 0.3,
            "enableSpokenPunctuation": False,
            "wordReplacements": {},
        }
        with open(SETTINGS_FILE, "w") as f:
            json.dump(default_settings, f, indent=2)

    if not TRANSCRIPTS_FILE.exists():
        TRANSCRIPTS_FILE.touch()


def load_settings() -> dict:
    """Load settings from ~/.oflow/settings.json."""
    ensure_data_dir()

    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE) as f:
                settings = json.load(f)
                return {
                    "whisperModel": settings.get("whisperModel", DEFAULT_WHISPER_MODEL),
                    "audioFeedbackTheme": settings.get("audioFeedbackTheme", "default"),
                    "audioFeedbackVolume": settings.get("audioFeedbackVolume", 0.3),
                    "enableSpokenPunctuation": settings.get("enableSpokenPunctuation", False),
                    "wordReplacements": settings.get("wordReplacements", {}),
                }
    except json.JSONDecodeError as e:
        logger.error(f"Settings file is invalid JSON: {e}")
    except Exception as e:
        logger.warning(f"Failed to load settings: {e}")

    return {
        "whisperModel": DEFAULT_WHISPER_MODEL,
        "audioFeedbackTheme": "default",
        "audioFeedbackVolume": 0.3,
        "enableSpokenPunctuation": False,
        "wordReplacements": {},
    }


def save_settings(settings: dict) -> None:
    """Save settings to ~/.oflow/settings.json."""
    ensure_data_dir()
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


# ============================================================================
# Process Management
# ============================================================================

_pid_lock_file = None


def acquire_pid_lock() -> bool:
    """Acquire exclusive lock via PID file."""
    global _pid_lock_file
    try:
        _pid_lock_file = open(PID_FILE, "w")
        fcntl.flock(_pid_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _pid_lock_file.write(str(os.getpid()))
        _pid_lock_file.flush()
        return True
    except IOError:
        if _pid_lock_file:
            _pid_lock_file.close()
            _pid_lock_file = None
        logger.info("Another instance is already running")
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
        logger.warning(f"Error releasing PID lock: {e}")


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
# Text Processing
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

        self._punctuation_patterns = []
        if enable_punctuation:
            for phrase, symbol in self.PUNCTUATION_MAP:
                pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
                self._punctuation_patterns.append((pattern, symbol))

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
        for pattern, symbol in self._punctuation_patterns:
            escaped_symbol = symbol.replace("\\", "\\\\")
            result = pattern.sub(escaped_symbol, result)
        result = self._clean_punctuation_spacing(result)
        return result

    def _apply_replacements(self, text: str) -> str:
        """Apply custom word replacements."""
        result = text
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
# Hallucination Filter
# ============================================================================

HALLUCINATION_PATTERNS = [
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

    for pattern in HALLUCINATION_PATTERNS:
        if pattern in text_lower:
            return True

    if len(text) < 3 or text in [".", "..", "...", "!", "?", ","]:
        return True

    for start in AI_RESPONSE_STARTS:
        if text_lower.startswith(start):
            logger.debug(f"Filtered AI response: {text[:50]}...")
            return True

    return False


# ============================================================================
# Transcription (Local whisper.cpp)
# ============================================================================

# Global whisper model (loaded once, reused)
_whisper_model: WhisperModel | None = None


def get_whisper_model(model_name: str | None = None) -> WhisperModel:
    """Get or load the whisper model (singleton)."""
    global _whisper_model
    if _whisper_model is None:
        name = model_name or DEFAULT_WHISPER_MODEL
        logger.info(f"Loading whisper model '{name}' (first run downloads ~400MB)...")
        _whisper_model = WhisperModel(
            name,
            models_dir=str(WHISPER_MODELS_DIR),
            print_realtime=False,
            print_progress=False,
        )
        logger.info(f"Whisper model '{name}' loaded")
    return _whisper_model


def transcribe_audio(audio: np.ndarray) -> str:
    """Transcribe audio using local whisper.cpp."""
    if len(audio) == 0:
        return ""

    max_amplitude = np.max(np.abs(audio))
    if max_amplitude < MIN_AUDIO_AMPLITUDE:
        logger.debug("Skipping silent audio chunk")
        return ""

    normalized = AudioProcessor.normalize(audio)

    try:
        model = get_whisper_model()
        segments = model.transcribe(normalized)
        text = " ".join(seg.text.strip() for seg in segments).strip()

        if is_hallucination(text):
            logger.debug(f"Filtered hallucination: {text}")
            return ""
        return text
    except Exception as e:
        logger.error(f"Transcription error: {e}")
    return ""


# ============================================================================
# Text Output (macOS)
# ============================================================================


def type_text(text: str) -> None:
    """Type text into the active window using pynput, fallback to pbcopy."""
    if not text:
        return

    try:
        kb = keyboard.Controller()
        kb.type(text)
        return
    except Exception as e:
        logger.debug(f"pynput typing failed: {e}")

    # Fallback: copy to clipboard
    try:
        subprocess.run(
            ["pbcopy"],
            input=text.encode("utf-8"),
            check=False,
        )
        logger.info("Text copied to clipboard (typing failed)")
        rumps.notification(
            title="oflow",
            subtitle="Text copied to clipboard",
            message="Paste with Cmd+V (typing into window failed)",
        )
    except Exception as e:
        logger.error(f"Failed to copy to clipboard: {e}")


# ============================================================================
# Menu Bar Application
# ============================================================================


class OflowApp(rumps.App):
    """macOS menu bar application for voice dictation."""

    def __init__(self):
        super().__init__("oflow", title="[MIC]")

        self.is_recording = False
        self._recording_lock = threading.Lock()
        self.audio_queue: queue.Queue[np.ndarray] = queue.Queue()
        self.audio_data: list[np.ndarray] = []

        # Load settings
        settings = load_settings()
        self.audio_feedback = AudioFeedback(
            theme=settings.get("audioFeedbackTheme", "default"),
            volume=settings.get("audioFeedbackVolume", 0.3),
        )
        self.text_processor = TextProcessor(
            enable_punctuation=settings.get("enableSpokenPunctuation", False),
            replacements=settings.get("wordReplacements", {}),
        )

        # Build menu
        self.toggle_item = rumps.MenuItem("Toggle Recording (Right Shift)", callback=self._on_toggle_click)
        self.status_item = rumps.MenuItem("Status: Ready")
        self.status_item.set_callback(None)

        self.menu = [
            self.status_item,
            None,  # separator
            self.toggle_item,
            None,  # separator
            rumps.MenuItem("Change Model...", callback=self._on_change_model),
            None,  # separator
        ]

        # Start audio stream
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=AUDIO_CHANNELS,
            dtype=np.float32,
            callback=self._audio_callback,
        )
        self.stream.start()

        # Setup global hotkey (right shift to toggle)
        self._hotkey_listener = keyboard.Listener(on_release=self._on_key_release)
        self._hotkey_listener.start()

        # Setup Unix socket for CLI control
        self._socket_thread = threading.Thread(target=self._run_socket_server, daemon=True)
        self._socket_thread.start()

        # Check accessibility after app starts (runs on main thread via rumps timer)
        self._accessibility_checked = False

        logger.info("oflow ready (hotkey: Right Shift)")

    @rumps.timer(1)
    def _check_accessibility_once(self, sender):
        """One-shot check for accessibility permissions on the main thread."""
        if self._accessibility_checked:
            return
        self._accessibility_checked = True
        sender.stop()
        if not check_accessibility():
            prompt_accessibility_instructions()

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status):
        """Callback for audio stream - runs in audio thread."""
        if self.is_recording:
            self.audio_queue.put_nowait(indata.copy())

    def _on_key_release(self, key):
        """Toggle recording on right shift release."""
        if key == keyboard.Key.shift_r:
            self._toggle_recording()

    def _on_toggle_click(self, sender):
        """Handle menu click for toggle."""
        self._toggle_recording()

    def _toggle_recording(self):
        """Toggle recording state."""
        if self.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        """Start recording audio."""
        with self._recording_lock:
            if self.is_recording:
                return
            self.is_recording = True
            self.audio_data = []

        self.audio_feedback.play_start()
        self.title = "[REC]"
        self.status_item.title = "Status: Recording..."

        # Reload settings
        settings = load_settings()
        self.text_processor = TextProcessor(
            enable_punctuation=settings.get("enableSpokenPunctuation", False),
            replacements=settings.get("wordReplacements", {}),
        )

        logger.info("Recording started")

    def _stop_recording(self):
        """Stop recording and process audio."""
        with self._recording_lock:
            if not self.is_recording:
                return

        self.audio_feedback.play_stop()
        self.title = "[...]"
        self.status_item.title = "Status: Transcribing..."

        # Grace period to capture tail of speech
        time.sleep(0.15)

        with self._recording_lock:
            self.is_recording = False

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
            self.title = "[MIC]"
            self.status_item.title = "Status: Ready"
            return

        # Process in background thread to avoid blocking the UI
        threading.Thread(target=self._process_in_thread, daemon=True).start()

    def _process_in_thread(self):
        """Run transcription in a background thread."""
        try:
            self._process_transcription()
        except Exception as e:
            logger.error(f"Processing error: {e}")
            self.audio_feedback.play_error()
        finally:
            self.title = "[MIC]"
            self.status_item.title = "Status: Ready"

    def _process_transcription(self):
        """Process recorded audio: transcribe and type result."""
        # Combine all audio
        audio = np.concatenate(self.audio_data, axis=0).flatten()
        duration = len(audio) / SAMPLE_RATE

        # Validate
        valid, error = AudioValidator.validate(audio)
        if not valid:
            logger.warning(f"Audio validation failed: {error}")
            return

        logger.info(f"Processing {duration:.1f}s audio")

        # Transcribe locally
        t0 = time.perf_counter()
        text = transcribe_audio(audio)
        t1 = time.perf_counter()

        if not text:
            logger.warning("Transcription returned empty")
            self.audio_feedback.play_error()
            return

        logger.info(f"Transcription: {(t1 - t0) * 1000:.0f}ms")

        # Apply text processing (spoken punctuation, replacements)
        text = self.text_processor.process(text)

        # Type the result
        type_text(text)
        logger.info(f"Result: {text[:80]}...")

        # Save transcript
        storage = StorageManager()
        storage.save_transcript(
            raw=text,
            cleaned=text,
            timestamp=datetime.now().isoformat(),
        )

    def _on_change_model(self, sender):
        """Show dialog to change whisper model."""
        global _whisper_model
        settings = load_settings()
        current = settings.get("whisperModel", DEFAULT_WHISPER_MODEL)

        response = rumps.Window(
            title="Change Whisper Model",
            message=(
                f"Current model: {current}\n\n"
                "Available models (English):\n"
                "  tiny.en   - Fastest, lower accuracy (~75MB)\n"
                "  base.en   - Fast, decent accuracy (~150MB)\n"
                "  small.en  - Good balance (~500MB)\n"
                "  medium.en - Best accuracy (~1.5GB)\n\n"
                "Enter model name:"
            ),
            default_text=current,
            ok="Save",
            cancel="Cancel",
            dimensions=(320, 24),
        ).run()

        if response.clicked and response.text.strip():
            new_model = response.text.strip()
            settings["whisperModel"] = new_model
            save_settings(settings)
            _whisper_model = None  # Force reload on next transcription
            logger.info(f"Whisper model changed to '{new_model}' (will load on next recording)")
            rumps.notification(
                title="oflow",
                subtitle="Model Changed",
                message=f"Whisper model set to '{new_model}'. Will download on next use if needed.",
            )

    def _run_socket_server(self):
        """Run Unix socket server for CLI control."""
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(SOCKET_PATH)
        sock.listen(1)
        os.chmod(SOCKET_PATH, 0o600)

        while True:
            try:
                sock.settimeout(1.0)
                conn, _ = sock.accept()
                try:
                    cmd = conn.recv(1024).decode().strip()
                    logger.debug(f"Socket command: {cmd}")
                    if cmd == "toggle":
                        self._toggle_recording()
                    elif cmd == "start" and not self.is_recording:
                        self._start_recording()
                    elif cmd == "stop" and self.is_recording:
                        self._stop_recording()
                finally:
                    conn.close()
            except socket.timeout:
                continue
            except OSError:
                break

    def cleanup(self):
        """Clean up resources on exit."""
        if hasattr(self, "stream"):
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass

        if hasattr(self, "_hotkey_listener"):
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass

        if os.path.exists(SOCKET_PATH):
            try:
                os.remove(SOCKET_PATH)
            except Exception:
                pass

        release_pid_lock()


# ============================================================================
# CLI Control
# ============================================================================


def send_command(cmd: str) -> bool:
    """Send a command to the running oflow instance via Unix socket."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(SOCKET_PATH)
        s.send(cmd.encode())
        s.close()
        return True
    except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
        print(f"oflow is not running: {e}", file=sys.stderr)
        print("Start oflow first (run 'oflow-macos' with no arguments)", file=sys.stderr)
        return False


# ============================================================================
# Main Entry Point
# ============================================================================


def main() -> None:
    """Main entry point."""
    # CLI mode: send command to running instance
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd not in ("start", "stop", "toggle"):
            print(f"Unknown command: {cmd}", file=sys.stderr)
            print("Usage: oflow-macos [start|stop|toggle]", file=sys.stderr)
            sys.exit(1)

        if not send_command(cmd):
            sys.exit(1)
        return

    # Server mode: start the menu bar app
    if not acquire_pid_lock():
        logger.info("Another instance is already running, exiting")
        sys.exit(0)

    # Helpful context for permission debugging (macOS privacy rules can be confusing).
    targets = _permission_targets()
    launched_by_launchd = _is_launched_by_launchd()
    logger.info(
        "Process info: pid=%s ppid=%s launchd=%s",
        os.getpid(),
        os.getppid(),
        launched_by_launchd,
    )
    if targets:
        logger.info("Executable candidates: %s", " | ".join(targets[:3]))

    # Check accessibility
    if not check_accessibility():
        logger.warning("Accessibility permission not granted")
        if launched_by_launchd and targets:
            logger.warning(
                "Grant Accessibility + Input Monitoring to the running executable (first): %s",
                " | ".join(targets[:3]),
            )
        elif targets:
            logger.warning(
                "Grant Accessibility + Input Monitoring to your Terminal app (Terminal/iTerm2). "
                "If that still doesn't work, try adding: %s",
                " | ".join(targets[:3]),
            )
        # We still start - rumps will show the alert and we can function
        # partially (clipboard fallback works without accessibility)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    try:
        app = OflowApp()
        app.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        release_pid_lock()
        if os.path.exists(SOCKET_PATH):
            try:
                os.remove(SOCKET_PATH)
            except Exception:
                pass


if __name__ == "__main__":
    main()
