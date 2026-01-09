"""Unit tests for audio validation and processing.

These tests run without external dependencies (no API calls required).
"""
import base64
import wave
import io
import numpy as np
import pytest

# Import from oflow module
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oflow import (
    AudioValidator,
    AudioProcessor,
    SAMPLE_RATE,
    MIN_AUDIO_DURATION_SECONDS,
    MIN_AUDIO_AMPLITUDE,
    NORMALIZATION_TARGET,
)


class TestAudioValidator:
    """Tests for AudioValidator.validate()."""

    @pytest.mark.unit
    def test_empty_audio_rejected(self):
        """Empty audio should be rejected."""
        empty = np.array([], dtype=np.float32)
        valid, error = AudioValidator.validate(empty)

        assert not valid
        assert error == "Empty audio"

    @pytest.mark.unit
    def test_silent_audio_rejected(self, silent_audio):
        """Silent audio (all zeros) should be rejected."""
        valid, error = AudioValidator.validate(silent_audio)

        assert not valid
        assert "quiet" in error.lower()

    @pytest.mark.unit
    def test_short_audio_rejected(self, short_audio):
        """Audio shorter than MIN_AUDIO_DURATION_SECONDS should be rejected."""
        valid, error = AudioValidator.validate(short_audio)

        assert not valid
        assert "short" in error.lower()

    @pytest.mark.unit
    def test_quiet_audio_rejected(self, quiet_audio):
        """Audio with amplitude below MIN_AUDIO_AMPLITUDE should be rejected."""
        valid, error = AudioValidator.validate(quiet_audio)

        assert not valid
        assert "quiet" in error.lower()

    @pytest.mark.unit
    def test_valid_audio_accepted(self, valid_audio):
        """Valid audio with sufficient duration and amplitude should pass."""
        valid, error = AudioValidator.validate(valid_audio)

        assert valid
        assert error is None

    @pytest.mark.unit
    def test_loud_audio_accepted(self, sample_rate):
        """Loud audio should be accepted."""
        duration = 1.0
        t = np.linspace(0, duration, int(sample_rate * duration))
        loud_audio = np.sin(2 * np.pi * 440 * t) * 0.8  # High amplitude
        loud_audio = loud_audio.astype(np.float32)

        valid, error = AudioValidator.validate(loud_audio)

        assert valid
        assert error is None


class TestAudioProcessor:
    """Tests for AudioProcessor."""

    @pytest.mark.unit
    def test_normalize_empty_audio(self):
        """Normalizing empty audio should return empty audio."""
        empty = np.array([], dtype=np.float32)
        result = AudioProcessor.normalize(empty)

        assert len(result) == 0

    @pytest.mark.unit
    def test_normalize_silent_audio(self, silent_audio):
        """Normalizing silent audio should return zeros."""
        result = AudioProcessor.normalize(silent_audio)

        assert np.allclose(result, 0)

    @pytest.mark.unit
    def test_normalize_scales_to_target(self, valid_audio):
        """Normalization should scale max amplitude to NORMALIZATION_TARGET."""
        # Scale down the audio first
        quiet = valid_audio * 0.1
        result = AudioProcessor.normalize(quiet)

        max_amplitude = np.max(np.abs(result))
        assert abs(max_amplitude - NORMALIZATION_TARGET) < 0.01

    @pytest.mark.unit
    def test_normalize_preserves_waveform_shape(self, valid_audio):
        """Normalization should preserve the relative shape of the waveform."""
        quiet = valid_audio * 0.1
        result = AudioProcessor.normalize(quiet)

        # Check correlation (shape preserved)
        correlation = np.corrcoef(valid_audio, result)[0, 1]
        assert correlation > 0.99

    @pytest.mark.unit
    def test_to_base64_wav_produces_valid_wav(self, valid_audio, sample_rate):
        """to_base64_wav should produce valid base64-encoded WAV data."""
        base64_str = AudioProcessor.to_base64_wav(valid_audio, sample_rate)

        # Should be non-empty string
        assert isinstance(base64_str, str)
        assert len(base64_str) > 0

        # Should be valid base64
        wav_bytes = base64.b64decode(base64_str)

        # Should be valid WAV file
        buffer = io.BytesIO(wav_bytes)
        with wave.open(buffer, 'rb') as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2  # 16-bit
            assert wav_file.getframerate() == sample_rate

    @pytest.mark.unit
    def test_to_base64_wav_preserves_duration(self, valid_audio, sample_rate):
        """WAV encoding should preserve audio duration."""
        base64_str = AudioProcessor.to_base64_wav(valid_audio, sample_rate)
        wav_bytes = base64.b64decode(base64_str)

        buffer = io.BytesIO(wav_bytes)
        with wave.open(buffer, 'rb') as wav_file:
            n_frames = wav_file.getnframes()
            duration = n_frames / wav_file.getframerate()
            expected_duration = len(valid_audio) / sample_rate

            assert abs(duration - expected_duration) < 0.01

    @pytest.mark.unit
    def test_to_base64_wav_empty_audio(self):
        """WAV encoding of empty audio should produce valid but empty WAV."""
        empty = np.array([], dtype=np.float32)
        base64_str = AudioProcessor.to_base64_wav(empty, 16000)

        wav_bytes = base64.b64decode(base64_str)
        buffer = io.BytesIO(wav_bytes)
        with wave.open(buffer, 'rb') as wav_file:
            assert wav_file.getnframes() == 0
