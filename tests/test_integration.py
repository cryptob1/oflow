"""Integration tests for Oflow transcription pipeline.

These tests require API keys and make real API calls.
Run with: pytest -m integration

Skip in CI if no API keys: pytest -m "not integration"
"""
import json
import os
import wave
import io
import numpy as np
import pytest
import httpx
from pathlib import Path

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oflow import (
    WhisperAPI,
    load_settings,
    SAMPLE_RATE,
    ConfigurationError,
)


# Settings file location
SETTINGS_FILE = Path.home() / ".oflow" / "settings.json"


def get_groq_api_key():
    """Get Groq API key from settings or environment."""
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE) as f:
            settings = json.load(f)
            if key := settings.get("groqApiKey"):
                return key
    return os.getenv("GROQ_API_KEY")


def get_openai_api_key():
    """Get OpenAI API key from settings or environment."""
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE) as f:
            settings = json.load(f)
            if key := settings.get("openaiApiKey"):
                return key
    return os.getenv("OPENAI_API_KEY")


def create_speech_audio(text: str = "hello", duration: float = 1.0) -> np.ndarray:
    """Create audio that simulates speech characteristics.

    Note: This is synthetic audio. For real speech tests, use pre-recorded files.
    """
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration))
    # Mix of frequencies that roughly simulate speech formants
    audio = (
        np.sin(2 * np.pi * 200 * t) * 0.3 +
        np.sin(2 * np.pi * 400 * t) * 0.2 +
        np.sin(2 * np.pi * 800 * t) * 0.1 +
        np.random.normal(0, 0.02, len(t))  # Add some noise
    )
    # Normalize
    audio = audio / np.max(np.abs(audio)) * 0.95
    return audio.astype(np.float32)


def audio_to_wav_bytes(audio: np.ndarray) -> bytes:
    """Convert audio array to WAV bytes."""
    audio_int16 = (audio * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(audio_int16.tobytes())
    buffer.seek(0)
    return buffer.read()


# Skip all integration tests if no API keys
groq_api_key = get_groq_api_key()
openai_api_key = get_openai_api_key()

skip_groq = pytest.mark.skipif(
    not groq_api_key,
    reason="GROQ_API_KEY not set"
)

skip_openai = pytest.mark.skipif(
    not openai_api_key,
    reason="OPENAI_API_KEY not set"
)


class TestWhisperAPIGroq:
    """Integration tests for Groq Whisper API."""

    @pytest.mark.integration
    @skip_groq
    def test_whisper_api_init_groq(self):
        """WhisperAPI should initialize with Groq provider."""
        api = WhisperAPI(api_key=groq_api_key, provider="groq")

        assert api.provider == "groq"
        assert api.model == "whisper-large-v3-turbo"
        assert "groq.com" in api.api_url

    @pytest.mark.integration
    @skip_groq
    def test_whisper_api_rejects_empty_key(self):
        """WhisperAPI should reject empty API key."""
        with pytest.raises(ConfigurationError):
            WhisperAPI(api_key="", provider="groq")

    @pytest.mark.integration
    @pytest.mark.slow
    @skip_groq
    def test_groq_transcription_returns_text(self):
        """Groq Whisper should return transcription for audio."""
        audio = create_speech_audio(duration=1.5)
        wav_bytes = audio_to_wav_bytes(audio)

        # Direct API call (not async for simplicity)
        response = httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {groq_api_key}"},
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={
                "model": "whisper-large-v3-turbo",
                "response_format": "json",
                "language": "en",
            },
            timeout=30.0,
        )

        assert response.status_code == 200
        result = response.json()
        assert "text" in result
        # Synthetic audio won't produce meaningful text, but API should respond


class TestWhisperAPIOpenAI:
    """Integration tests for OpenAI Whisper API."""

    @pytest.mark.integration
    @skip_openai
    def test_whisper_api_init_openai(self):
        """WhisperAPI should initialize with OpenAI provider."""
        api = WhisperAPI(api_key=openai_api_key, provider="openai")

        assert api.provider == "openai"
        assert api.model == "whisper-1"
        assert "openai.com" in api.api_url


class TestSettings:
    """Tests for settings loading."""

    @pytest.mark.unit
    def test_load_settings_returns_dict(self):
        """load_settings should return a dictionary."""
        settings = load_settings()

        assert isinstance(settings, dict)
        assert "enableCleanup" in settings
        assert "enableMemory" in settings
        assert "provider" in settings

    @pytest.mark.unit
    def test_load_settings_has_defaults(self):
        """load_settings should have default values."""
        settings = load_settings()

        assert isinstance(settings["enableCleanup"], bool)
        assert isinstance(settings["enableMemory"], bool)
        assert settings["provider"] in ("openai", "groq")


class TestRecordedAudio:
    """Tests using pre-recorded audio files for reliable transcription testing."""

    @pytest.mark.integration
    @pytest.mark.slow
    @skip_groq
    def test_transcribe_recorded_audio(self):
        """Test transcription with pre-recorded audio file if available."""
        test_audio_path = Path(__file__).parent / "test_audio.wav"

        if not test_audio_path.exists():
            pytest.skip("No test_audio.wav file - run test_groq_transcription.py --record first")

        with open(test_audio_path, "rb") as f:
            wav_bytes = f.read()

        response = httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {groq_api_key}"},
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={
                "model": "whisper-large-v3-turbo",
                "response_format": "json",
                "language": "en",
            },
            timeout=30.0,
        )

        assert response.status_code == 200
        result = response.json()
        text = result.get("text", "")

        # Should return some text
        assert len(text) > 0
        print(f"Transcription: {text}")
