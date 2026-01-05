#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

import numpy as np
import httpx
from dotenv import load_dotenv
import base64
import io
import wave
import os
import subprocess
import time
from difflib import SequenceMatcher

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TEST_CASES = [
    {
        "name": "Simple sentence",
        "input": "This is a test message.",
        "expected": "This is a test message."
    },
    {
        "name": "Question",
        "input": "What time is the meeting tomorrow?",
        "expected": "What time is the meeting tomorrow?"
    },
    {
        "name": "List with numbers",
        "input": "First buy groceries, second call mom, third finish the report.",
        "expected_variations": [
            "1. Buy groceries\n2. Call mom\n3. Finish the report",
            "First, buy groceries. Second, call mom. Third, finish the report.",
            "First buy groceries, second call mom, third finish the report."
        ]
    },
    {
        "name": "Mixed case correction",
        "input": "my NAME is ADAM and I LIVE in california.",
        "expected": "My name is Adam and I live in California."
    },
    {
        "name": "Filler words removal",
        "input": "Um so like I need to uh buy some milk.",
        "expected": "I need to buy some milk."
    },
    {
        "name": "Proper nouns",
        "input": "I met John Smith at Google headquarters in Mountain View.",
        "expected": "I met John Smith at Google headquarters in Mountain View."
    },
    {
        "name": "Long sentence",
        "input": "I need to finish the project report before the deadline, then submit it to my manager, and finally prepare for the presentation next week.",
        "expected": "I need to finish the project report before the deadline, then submit it to my manager, and finally prepare for the presentation next week."
    },
    {
        "name": "Technical terms",
        "input": "I need to install Python and configure the API endpoint.",
        "expected": "I need to install Python and configure the API endpoint."
    },
    {
        "name": "Numbers and dates",
        "input": "The meeting is on January fifteenth at three thirty PM.",
        "expected_variations": [
            "The meeting is on January 15th at 3:30 PM.",
            "The meeting is on January fifteenth at three thirty PM.",
            "The meeting is on January 15th at 3:30 pm."
        ]
    },
    {
        "name": "Punctuation variety",
        "input": "Stop! Don't do that. Are you sure? Yes, I am.",
        "expected": "Stop! Don't do that. Are you sure? Yes, I am."
    }
]

def generate_tts(text, output_file):
    try:
        response = httpx.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "tts-1",
                "input": text,
                "voice": "alloy",
                "response_format": "wav"
            },
            timeout=30.0,
        )
        
        if response.status_code == 200:
            with open(output_file, 'wb') as f:
                f.write(response.content)
            return True
        else:
            print(f"TTS failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"TTS error: {e}")
        return False

def wav_to_base64(wav_file):
    with open(wav_file, 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')

def transcribe_audio(audio_base64):
    try:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-audio-preview",
                "modalities": ["text"],
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": """Transcribe this audio with the following formatting rules:

1. Use proper sentence case - capitalize the first word of sentences and proper nouns only
2. Convert ALL-CAPS words to normal case (unless they are acronyms like NASA, API)
3. Fix any capitalization errors
4. Remove filler words (um, uh, like, you know, etc.)
5. Fix grammar and punctuation
6. If speaker lists items using "first, second, third" or "one, two, three", format as numbered list
7. Output ONLY the cleaned transcribed text - no commentary

Now transcribe:"""
                            },
                            {
                                "type": "input_audio",
                                "input_audio": {
                                    "data": audio_base64,
                                    "format": "wav"
                                }
                            }
                        ]
                    }
                ],
            },
            timeout=30.0,
        )
        
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"].strip()
        else:
            print(f"  Transcription API error: {response.status_code}")
            return None
    except Exception as e:
        print(f"  Transcription error: {e}")
        return None

def similarity_ratio(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def check_match(result, test_case):
    if "expected_variations" in test_case:
        for expected in test_case["expected_variations"]:
            ratio = similarity_ratio(result, expected)
            if ratio >= 0.70:
                return True, ratio, expected
        best_match = max(test_case["expected_variations"], 
                        key=lambda x: similarity_ratio(result, x))
        return False, similarity_ratio(result, best_match), best_match
    else:
        ratio = similarity_ratio(result, test_case["expected"])
        return ratio >= 0.70, ratio, test_case["expected"]

def main():
    print("=" * 70)
    print("VOICE TRANSCRIPTION TEST SUITE")
    print("=" * 70)
    print("\nTesting TTS → Transcription pipeline\n")
    
    if not OPENAI_API_KEY:
        print("❌ OPENAI_API_KEY not found")
        return
    
    results = []
    
    for i, test in enumerate(TEST_CASES, 1):
        print(f"\n[Test {i}/{len(TEST_CASES)}] {test['name']}")
        print(f"  Input: \"{test['input']}\"")
        
        tts_file = f"/tmp/test_tts_{i}.wav"
        
        print(f"  🔊 Generating TTS...")
        if not generate_tts(test['input'], tts_file):
            print("  ❌ TTS generation failed")
            results.append({
                "test": test['name'],
                "status": "FAILED",
                "reason": "TTS generation failed"
            })
            continue
        
        audio_base64 = wav_to_base64(tts_file)
        
        print(f"  🔄 Transcribing...")
        result = transcribe_audio(audio_base64)
        
        if not result:
            print("  ❌ Transcription failed")
            results.append({
                "test": test['name'],
                "status": "FAILED",
                "reason": "Transcription failed"
            })
            continue
        
        print(f"  Result: \"{result}\"")
        
        passed, ratio, expected_match = check_match(result, test)
        
        if passed:
            print(f"  ✅ PASS (similarity: {ratio*100:.1f}%)")
            results.append({
                "test": test['name'],
                "status": "PASSED",
                "similarity": ratio,
                "result": result
            })
        else:
            print(f"  ❌ FAIL (similarity: {ratio*100:.1f}%)")
            print(f"  Expected: \"{expected_match}\"")
            results.append({
                "test": test['name'],
                "status": "FAILED",
                "reason": f"Low similarity ({ratio*100:.1f}%)",
                "expected": expected_match,
                "result": result
            })
        
        time.sleep(0.5)
    
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    
    passed = sum(1 for r in results if r['status'] == 'PASSED')
    failed = len(results) - passed
    
    print(f"\nTotal tests: {len(results)}")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    print(f"Success rate: {passed/len(results)*100:.1f}%")
    
    if failed > 0:
        print("\n\nFailed tests details:")
        for r in results:
            if r['status'] == 'FAILED':
                print(f"\n  {r['test']}:")
                print(f"    Reason: {r.get('reason', 'Unknown')}")
                if 'result' in r and 'expected' in r:
                    print(f"    Got:      \"{r['result']}\"")
                    print(f"    Expected: \"{r['expected']}\"")
    
    print("\n" + "=" * 70)

if __name__ == "__main__":
    main()
