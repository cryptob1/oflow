#!/usr/bin/env python3
"""
Test Groq Whisper transcription with a real audio recording.

Usage:
    # Record a new test clip (3 seconds)
    python tests/test_groq_transcription.py --record
    
    # Test with existing clip
    python tests/test_groq_transcription.py
"""
import httpx
import wave
import io
import numpy as np
import sounddevice as sd
import json
import sys
import os
from pathlib import Path

# Paths
SETTINGS_FILE = Path.home() / ".oflow" / "settings.json"
TEST_AUDIO_FILE = Path(__file__).parent / "test_audio.wav"

SAMPLE_RATE = 16000
RECORD_DURATION = 3


def load_groq_key():
    with open(SETTINGS_FILE) as f:
        settings = json.load(f)
        return settings.get("groqApiKey")


def record_audio():
    """Record audio from microphone and save to file."""
    print(f"Recording for {RECORD_DURATION} seconds... Say something!")
    import time
    time.sleep(0.5)
    
    audio = sd.rec(int(SAMPLE_RATE * RECORD_DURATION), samplerate=SAMPLE_RATE, channels=1, dtype=np.float32)
    sd.wait()
    audio = audio.flatten()
    
    # Normalize
    max_val = np.max(np.abs(audio))
    if max_val > 0:
        audio = audio / max_val * 0.95
    
    # Save as WAV
    audio_int16 = (audio * 32767).astype(np.int16)
    with wave.open(str(TEST_AUDIO_FILE), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(audio_int16.tobytes())
    
    print(f"Saved to {TEST_AUDIO_FILE}")
    print(f"Max amplitude: {max_val:.4f}")
    return audio_int16.tobytes()


def load_audio():
    """Load audio from saved file."""
    if not TEST_AUDIO_FILE.exists():
        print(f"No test audio file found at {TEST_AUDIO_FILE}")
        print("Run with --record first to create one.")
        sys.exit(1)
    
    with wave.open(str(TEST_AUDIO_FILE), "rb") as wav_file:
        return wav_file.readframes(wav_file.getnframes())


def transcribe_with_groq(audio_bytes: bytes):
    """Send audio to Groq Whisper API."""
    api_key = load_groq_key()
    if not api_key:
        print("No Groq API key found in settings!")
        sys.exit(1)
    
    # Create WAV in memory
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(audio_bytes)
    buffer.seek(0)
    wav_bytes = buffer.read()
    
    print(f"\nSending {len(wav_bytes)} bytes to Groq Whisper...")
    
    response = httpx.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("audio.wav", wav_bytes, "audio/wav")},
        data={
            "model": "whisper-large-v3-turbo",
            "response_format": "json",
            "language": "en",
        },
        timeout=30.0,
    )
    
    print(f"Response status: {response.status_code}")
    
    if response.status_code == 200:
        result = response.json()
        print(f"\n✅ Transcription: {result.get('text', 'NO TEXT')}")
        return result.get('text', '')
    else:
        print(f"❌ Error: {response.text}")
        return None


def main():
    if "--record" in sys.argv:
        audio_bytes = record_audio()
    else:
        audio_bytes = load_audio()
    
    transcribe_with_groq(audio_bytes)


if __name__ == "__main__":
    main()
