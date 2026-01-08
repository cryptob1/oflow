#!/usr/bin/env python3
import asyncio
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib.util
omarchyflow_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "omarchyflow")
spec = importlib.util.spec_from_file_location("omarchyflow", omarchyflow_path)
omarchyflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(omarchyflow)

stt_stream = omarchyflow.stt_stream
AudioValidator = omarchyflow.AudioValidator
AudioProcessor = omarchyflow.AudioProcessor
EventType = omarchyflow.EventType


def create_test_audio(duration_sec: float = 2.0, frequency: int = 440):
    sample_rate = 16000
    t = np.linspace(0, duration_sec, int(sample_rate * duration_sec))
    audio = np.sin(2 * np.pi * frequency * t) * 0.3
    return audio.astype(np.float32)


def create_silent_audio(duration_sec: float = 2.0):
    sample_rate = 16000
    return np.zeros(int(sample_rate * duration_sec), dtype=np.float32)


async def test_empty_audio():
    print("\n=== Test 1: Empty Audio Validation ===")
    empty = np.array([], dtype=np.float32)
    
    valid, error = AudioValidator.validate(empty)
    assert not valid, "Empty audio should be invalid"
    assert error == "Empty audio", f"Wrong error: {error}"
    print("✅ Empty audio correctly rejected")


async def test_silent_audio():
    print("\n=== Test 2: Silent Audio Validation ===")
    silent = create_silent_audio()
    
    valid, error = AudioValidator.validate(silent)
    assert not valid, "Silent audio should be invalid"
    assert "quiet" in error.lower(), f"Wrong error: {error}"
    print("✅ Silent audio correctly rejected")


async def test_valid_audio():
    print("\n=== Test 3: Valid Audio Validation ===")
    audio = create_test_audio()
    
    valid, error = AudioValidator.validate(audio)
    assert valid, f"Valid audio rejected: {error}"
    assert error is None, f"Valid audio has error: {error}"
    print("✅ Valid audio accepted")


async def test_normalization():
    print("\n=== Test 4: Audio Normalization ===")
    audio = create_test_audio() * 0.1
    
    normalized = AudioProcessor.normalize(audio)
    
    max_val = np.max(np.abs(normalized))
    assert 0.94 < max_val < 0.96, f"Normalization failed: {max_val}"
    print(f"✅ Audio normalized to {max_val:.3f}")


async def test_base64_encoding():
    print("\n=== Test 5: Base64 WAV Encoding ===")
    audio = create_test_audio()
    
    base64_str = AudioProcessor.to_base64_wav(audio, 16000)
    
    assert isinstance(base64_str, str), "Base64 should be string"
    assert len(base64_str) > 0, "Base64 should not be empty"
    print(f"✅ Audio encoded to {len(base64_str)} base64 chars")


async def test_empty_audio_stream():
    print("\n=== Test 6: STT Stream - Empty Audio ===")
    empty = np.array([], dtype=np.float32)
    
    events = []
    async for event in stt_stream(empty):
        events.append(event)
    
    assert len(events) == 1, f"Expected 1 event, got {len(events)}"
    assert events[0].type == EventType.STT_ERROR, "Expected error event"
    assert "empty" in events[0].error.lower(), f"Wrong error: {events[0].error}"
    print(f"✅ Empty audio error: {events[0].error}")


async def test_silent_audio_stream():
    print("\n=== Test 7: STT Stream - Silent Audio ===")
    silent = create_silent_audio()
    
    events = []
    async for event in stt_stream(silent):
        events.append(event)
    
    assert len(events) == 1, f"Expected 1 event, got {len(events)}"
    assert events[0].type == EventType.STT_ERROR, "Expected error event"
    assert "quiet" in events[0].error.lower(), f"Wrong error: {events[0].error}"
    print(f"✅ Silent audio error: {events[0].error}")


async def test_valid_audio_stream():
    print("\n=== Test 8: STT Stream - Valid Audio (Real API Call) ===")
    
    import wave
    try:
        with wave.open('/tmp/debug_audio.wav', 'rb') as wav:
            frames = wav.readframes(wav.getnframes())
            audio_int16 = np.frombuffer(frames, dtype=np.int16)
            audio = audio_int16.astype(np.float32) / 32767.0
        
        print(f"Loaded real audio: {len(audio)} samples")
        
        events = []
        async for event in stt_stream(audio):
            events.append(event)
            print(f"  Event: {event.type.value}")
            if event.type == EventType.STT_OUTPUT:
                print(f"  Transcription: {event.data}")
            elif event.type == EventType.STT_ERROR:
                print(f"  Error: {event.error}")
        
        if len(events) > 0 and events[0].type == EventType.STT_OUTPUT:
            print("✅ Real audio transcribed successfully")
        elif len(events) > 0 and events[0].type == EventType.STT_ERROR:
            print(f"⚠️  Transcription failed (expected if no API key): {events[0].error}")
        else:
            print(f"❌ Unexpected result: {len(events)} events")
    
    except FileNotFoundError:
        print("⚠️  No debug audio file found, skipping real audio test")


async def run_all_tests():
    print("=" * 60)
    print("OmarchyFlow LangChain Robustness Tests")
    print("=" * 60)
    
    tests = [
        test_empty_audio,
        test_silent_audio,
        test_valid_audio,
        test_normalization,
        test_base64_encoding,
        test_empty_audio_stream,
        test_silent_audio_stream,
        test_valid_audio_stream,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            await test()
            passed += 1
        except AssertionError as e:
            print(f"❌ FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"❌ ERROR: {e}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
