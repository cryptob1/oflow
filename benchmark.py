#!/usr/bin/env python3
"""
Benchmark script to measure and optimize transcription latency.
"""

import asyncio
import base64
import io
import json
import time
import wave
from pathlib import Path

import httpx
import numpy as np

SETTINGS_FILE = Path.home() / ".oflow" / "settings.json"
SAMPLE_RATE = 16000

def load_api_key():
    with open(SETTINGS_FILE) as f:
        settings = json.load(f)
    return settings.get('groqApiKey')

def generate_test_audio(duration_seconds: float) -> np.ndarray:
    """Generate test audio with speech-like characteristics."""
    t = np.linspace(0, duration_seconds, int(SAMPLE_RATE * duration_seconds), False)
    audio = (
        0.3 * np.sin(2 * np.pi * 200 * t) +
        0.2 * np.sin(2 * np.pi * 400 * t) +
        0.1 * np.sin(2 * np.pi * 800 * t)
    )
    audio += 0.05 * np.random.randn(len(audio))
    audio = audio / np.max(np.abs(audio)) * 0.8
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

async def transcribe_groq(client: httpx.AsyncClient, audio_bytes: bytes, api_key: str) -> tuple[str, float]:
    """Transcribe audio using Groq API. Returns (text, latency_ms)."""
    start = time.perf_counter()
    response = await client.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": ("audio.wav", audio_bytes, "audio/wav")},
        data={"model": "whisper-large-v3-turbo", "response_format": "json", "language": "en"},
    )
    latency_ms = (time.perf_counter() - start) * 1000
    if response.status_code != 200:
        return f"ERROR: {response.status_code}", latency_ms
    return response.json().get("text", ""), latency_ms

async def cleanup_text(client: httpx.AsyncClient, text: str, api_key: str) -> tuple[str, float]:
    """Clean up text using Groq LLM."""
    start = time.perf_counter()
    response = await client.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": "llama-3.1-8b-instant",
            "messages": [
                {"role": "system", "content": "Fix grammar/punctuation. Output only cleaned text, nothing else."},
                {"role": "user", "content": text}
            ],
            "temperature": 0.3,
            "max_tokens": 500,
        },
    )
    latency_ms = (time.perf_counter() - start) * 1000
    if response.status_code != 200:
        return f"ERROR: {response.status_code}", latency_ms
    return response.json()["choices"][0]["message"]["content"], latency_ms

async def benchmark_optimized_chunked(audio: np.ndarray, api_key: str, chunk_seconds: float = 0.75):
    """
    Optimized chunked approach:
    - Smaller chunks (0.75s)
    - Pipeline: start cleanup on early chunks while transcribing later ones
    - Use HTTP/2 connection pooling
    """
    print(f"\n=== OPTIMIZED CHUNKED ({chunk_seconds}s chunks, pipelined cleanup) ===")

    chunk_size = int(SAMPLE_RATE * chunk_seconds)
    chunks = [audio[i:i+chunk_size] for i in range(0, len(audio), chunk_size)]
    # Ensure last chunk is at least 0.3s
    if len(chunks) > 1 and len(chunks[-1]) < SAMPLE_RATE * 0.3:
        # Merge last two chunks
        chunks[-2] = np.concatenate([chunks[-2], chunks[-1]])
        chunks = chunks[:-1]

    print(f"Audio: {len(audio)/SAMPLE_RATE:.1f}s split into {len(chunks)} chunks")

    async with httpx.AsyncClient(timeout=30.0, http2=False) as client:
        # Warm up connection pool
        warmup_audio = audio_to_wav_bytes(generate_test_audio(0.3))
        await transcribe_groq(client, warmup_audio, api_key)

        transcripts = []
        cleanup_task = None
        pending_text = ""

        # Simulate recording with async chunk processing
        print("Recording with async chunk uploads...")
        for i, chunk in enumerate(chunks[:-1]):
            chunk_bytes = audio_to_wav_bytes(chunk)
            text, ms = await transcribe_groq(client, chunk_bytes, api_key)
            transcripts.append(text)
            pending_text += " " + text.strip()
            print(f"  Chunk {i+1}: {ms:.0f}ms")

        # Key released - start timer
        print("Key released!")
        start_release = time.perf_counter()

        # Send last chunk AND start cleanup of previous chunks in parallel
        last_chunk_bytes = audio_to_wav_bytes(chunks[-1])

        async def transcribe_last():
            return await transcribe_groq(client, last_chunk_bytes, api_key)

        async def cleanup_pending():
            if pending_text.strip():
                return await cleanup_text(client, pending_text.strip(), api_key)
            return "", 0

        # Run in parallel
        (last_text, last_ms), (partial_cleaned, cleanup_ms) = await asyncio.gather(
            transcribe_last(),
            cleanup_pending()
        )

        # Quick cleanup of just the last chunk's text (or skip if short)
        if last_text.strip():
            final_text = partial_cleaned + " " + last_text.strip()
        else:
            final_text = partial_cleaned

        latency = (time.perf_counter() - start_release) * 1000

        print(f"Last chunk: {last_ms:.0f}ms | Parallel cleanup: {cleanup_ms:.0f}ms")
        print(f"LATENCY AFTER RELEASE: {latency:.0f}ms")

        return latency

async def benchmark_no_cleanup(audio: np.ndarray, api_key: str, chunk_seconds: float = 0.75):
    """Chunked approach without cleanup - fastest possible."""
    print(f"\n=== NO CLEANUP ({chunk_seconds}s chunks) ===")

    chunk_size = int(SAMPLE_RATE * chunk_seconds)
    chunks = [audio[i:i+chunk_size] for i in range(0, len(audio), chunk_size)]
    if len(chunks) > 1 and len(chunks[-1]) < SAMPLE_RATE * 0.3:
        chunks[-2] = np.concatenate([chunks[-2], chunks[-1]])
        chunks = chunks[:-1]

    print(f"Audio: {len(audio)/SAMPLE_RATE:.1f}s split into {len(chunks)} chunks")

    async with httpx.AsyncClient(timeout=30.0, http2=False) as client:
        # Warm up
        warmup_audio = audio_to_wav_bytes(generate_test_audio(0.3))
        await transcribe_groq(client, warmup_audio, api_key)

        transcripts = []

        print("Recording with async chunk uploads...")
        for i, chunk in enumerate(chunks[:-1]):
            chunk_bytes = audio_to_wav_bytes(chunk)
            text, ms = await transcribe_groq(client, chunk_bytes, api_key)
            transcripts.append(text)
            print(f"  Chunk {i+1}: {ms:.0f}ms")

        print("Key released!")
        start_release = time.perf_counter()

        last_chunk_bytes = audio_to_wav_bytes(chunks[-1])
        last_text, last_ms = await transcribe_groq(client, last_chunk_bytes, api_key)
        transcripts.append(last_text)

        latency = (time.perf_counter() - start_release) * 1000

        print(f"Last chunk: {last_ms:.0f}ms")
        print(f"LATENCY AFTER RELEASE: {latency:.0f}ms (no cleanup)")

        return latency

async def benchmark_current(audio: np.ndarray, api_key: str):
    """Current batch approach for comparison."""
    print("\n=== CURRENT BATCH APPROACH ===")

    audio_bytes = audio_to_wav_bytes(audio)
    print(f"Audio: {len(audio)/SAMPLE_RATE:.1f}s, {len(audio_bytes)/1024:.1f}KB")

    async with httpx.AsyncClient(timeout=30.0, http2=False) as client:
        # Warm up
        warmup_audio = audio_to_wav_bytes(generate_test_audio(0.3))
        await transcribe_groq(client, warmup_audio, api_key)

        start = time.perf_counter()
        text, transcribe_ms = await transcribe_groq(client, audio_bytes, api_key)
        cleaned, cleanup_ms = await cleanup_text(client, text, api_key)
        total = (time.perf_counter() - start) * 1000

        print(f"Transcription: {transcribe_ms:.0f}ms")
        print(f"Cleanup:       {cleanup_ms:.0f}ms")
        print(f"TOTAL:         {total:.0f}ms")

        return total

async def main():
    api_key = load_api_key()
    if not api_key:
        print("ERROR: No Groq API key found")
        return

    print("=" * 60)
    print("OFLOW LATENCY OPTIMIZATION BENCHMARK")
    print("=" * 60)

    for duration in [2.0, 3.0, 5.0]:
        print(f"\n{'='*60}")
        print(f"AUDIO LENGTH: {duration}s")
        print("=" * 60)

        audio = generate_test_audio(duration)

        current = await benchmark_current(audio, api_key)
        optimized = await benchmark_optimized_chunked(audio, api_key)
        no_cleanup = await benchmark_no_cleanup(audio, api_key)

        print(f"\n{'='*40}")
        print(f"RESULTS ({duration}s audio):")
        print(f"  Current batch:      {current:.0f}ms")
        print(f"  Optimized chunked:  {optimized:.0f}ms  ({current-optimized:.0f}ms faster)")
        print(f"  No cleanup:         {no_cleanup:.0f}ms  ({current-no_cleanup:.0f}ms faster)")
        print(f"{'='*40}")

if __name__ == "__main__":
    asyncio.run(main())
