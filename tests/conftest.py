"""Pytest configuration and fixtures for oflow tests."""
import sys
import os
import pytest
import numpy as np

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def sample_rate():
    """Standard sample rate for audio tests."""
    return 16000


@pytest.fixture
def valid_audio(sample_rate):
    """Generate valid audio with speech-like characteristics."""
    duration = 1.0
    t = np.linspace(0, duration, int(sample_rate * duration))
    # Mix of frequencies to simulate speech
    audio = (
        np.sin(2 * np.pi * 200 * t) * 0.2 +
        np.sin(2 * np.pi * 400 * t) * 0.15 +
        np.sin(2 * np.pi * 800 * t) * 0.1
    )
    return audio.astype(np.float32)


@pytest.fixture
def silent_audio(sample_rate):
    """Generate silent audio."""
    duration = 1.0
    return np.zeros(int(sample_rate * duration), dtype=np.float32)


@pytest.fixture
def short_audio(sample_rate):
    """Generate audio that's too short."""
    duration = 0.2  # Below MIN_AUDIO_DURATION_SECONDS
    t = np.linspace(0, duration, int(sample_rate * duration))
    audio = np.sin(2 * np.pi * 440 * t) * 0.3
    return audio.astype(np.float32)


@pytest.fixture
def quiet_audio(sample_rate):
    """Generate audio that's too quiet."""
    duration = 1.0
    t = np.linspace(0, duration, int(sample_rate * duration))
    audio = np.sin(2 * np.pi * 440 * t) * 0.005  # Below MIN_AUDIO_AMPLITUDE
    return audio.astype(np.float32)


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests (no external dependencies)")
    config.addinivalue_line("markers", "integration: Integration tests (require API keys)")
    config.addinivalue_line("markers", "slow: Slow tests")
