#!/usr/bin/env python3
"""
Test suite for OmarchyFlow voice transcription.

Tests the TTS → Transcription pipeline using OpenAI's APIs.
Generates audio via TTS and verifies transcription accuracy.
"""
from __future__ import annotations

import atexit
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# Configure output buffering
sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Track temp files for cleanup
_temp_files: list[Path] = []


def _cleanup_temp_files() -> None:
    """Clean up all temporary files on exit."""
    for path in _temp_files:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


atexit.register(_cleanup_temp_files)


@dataclass
class TestCase:
    """Represents a single test case."""

    name: str
    input_text: str
    expected: str | None = None
    expected_variations: list[str] | None = None


@dataclass
class TestResult:
    """Represents the result of a single test."""

    test_name: str
    status: str  # "PASSED", "FAILED"
    similarity: float | None = None
    result: str | None = None
    expected: str | None = None
    reason: str | None = None


TEST_CASES: list[TestCase] = [
    TestCase(
        name="Simple sentence",
        input_text="This is a test message.",
        expected="This is a test message.",
    ),
    TestCase(
        name="Question",
        input_text="What time is the meeting tomorrow?",
        expected="What time is the meeting tomorrow?",
    ),
    TestCase(
        name="List with numbers",
        input_text="First buy groceries, second call mom, third finish the report.",
        expected_variations=[
            "1. Buy groceries\n2. Call mom\n3. Finish the report",
            "First, buy groceries. Second, call mom. Third, finish the report.",
            "First buy groceries, second call mom, third finish the report.",
        ],
    ),
    TestCase(
        name="Mixed case correction",
        input_text="my NAME is ADAM and I LIVE in california.",
        expected="My name is Adam and I live in California.",
    ),
    TestCase(
        name="Filler words removal",
        input_text="Um so like I need to uh buy some milk.",
        expected="I need to buy some milk.",
    ),
    TestCase(
        name="Proper nouns",
        input_text="I met John Smith at Google headquarters in Mountain View.",
        expected="I met John Smith at Google headquarters in Mountain View.",
    ),
    TestCase(
        name="Long sentence",
        input_text=(
            "I need to finish the project report before the deadline, "
            "then submit it to my manager, and finally prepare for the presentation next week."
        ),
        expected=(
            "I need to finish the project report before the deadline, "
            "then submit it to my manager, and finally prepare for the presentation next week."
        ),
    ),
    TestCase(
        name="Technical terms",
        input_text="I need to install Python and configure the API endpoint.",
        expected="I need to install Python and configure the API endpoint.",
    ),
    TestCase(
        name="Numbers and dates",
        input_text="The meeting is on January fifteenth at three thirty PM.",
        expected_variations=[
            "The meeting is on January 15th at 3:30 PM.",
            "The meeting is on January fifteenth at three thirty PM.",
            "The meeting is on January 15th at 3:30 pm.",
            "The meeting is on January 15 at 3:30 PM.",
        ],
    ),
    TestCase(
        name="Punctuation variety",
        input_text="Stop! Don't do that. Are you sure? Yes, I am.",
        expected="Stop! Don't do that. Are you sure? Yes, I am.",
    ),
]


def generate_tts(text: str, output_file: Path) -> bool:
    """Generate TTS audio from text using OpenAI API.

    Args:
        text: Text to convert to speech.
        output_file: Path to save the WAV file.

    Returns:
        True if successful, False otherwise.
    """
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
                "response_format": "wav",
            },
            timeout=30.0,
        )

        if response.status_code == 200:
            output_file.write_bytes(response.content)
            _temp_files.append(output_file)
            return True

        print(f"TTS failed: {response.status_code}")
        return False

    except httpx.TimeoutException:
        print("TTS timeout")
        return False
    except Exception as e:
        print(f"TTS error: {e}")
        return False


def transcribe_audio(audio_path: Path) -> str | None:
    """Transcribe audio using OpenAI's audio API.

    Args:
        audio_path: Path to the WAV audio file.

    Returns:
        Transcribed text or None on failure.
    """
    try:
        import base64

        audio_base64 = base64.b64encode(audio_path.read_bytes()).decode("utf-8")

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

Now transcribe:""",
                            },
                            {
                                "type": "input_audio",
                                "input_audio": {"data": audio_base64, "format": "wav"},
                            },
                        ],
                    }
                ],
            },
            timeout=30.0,
        )

        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"].strip()

        print(f"  Transcription API error: {response.status_code}")
        return None

    except httpx.TimeoutException:
        print("  Transcription timeout")
        return None
    except Exception as e:
        print(f"  Transcription error: {e}")
        return None


def similarity_ratio(a: str, b: str) -> float:
    """Calculate similarity ratio between two strings.

    Args:
        a: First string.
        b: Second string.

    Returns:
        Similarity ratio between 0.0 and 1.0.
    """
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def check_match(result: str, test_case: TestCase) -> tuple[bool, float, str]:
    """Check if transcription result matches expected output.

    Args:
        result: Transcription result.
        test_case: Test case to check against.

    Returns:
        Tuple of (passed, similarity_ratio, matched_expected).
    """
    if test_case.expected_variations:
        for expected in test_case.expected_variations:
            ratio = similarity_ratio(result, expected)
            if ratio >= 0.70:
                return True, ratio, expected

        best_match = max(
            test_case.expected_variations, key=lambda x: similarity_ratio(result, x)
        )
        return False, similarity_ratio(result, best_match), best_match

    if test_case.expected:
        ratio = similarity_ratio(result, test_case.expected)
        return ratio >= 0.70, ratio, test_case.expected

    return False, 0.0, ""


def run_tests() -> list[TestResult]:
    """Run all test cases.

    Returns:
        List of test results.
    """
    results: list[TestResult] = []

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        for i, test in enumerate(TEST_CASES, 1):
            print(f"\n[Test {i}/{len(TEST_CASES)}] {test.name}")
            print(f'  Input: "{test.input_text}"')

            tts_file = temp_path / f"test_tts_{i}.wav"

            print("  Generating TTS...")
            if not generate_tts(test.input_text, tts_file):
                print("  TTS generation failed")
                results.append(
                    TestResult(
                        test_name=test.name,
                        status="FAILED",
                        reason="TTS generation failed",
                    )
                )
                continue

            print("  Transcribing...")
            result = transcribe_audio(tts_file)

            if not result:
                print("  Transcription failed")
                results.append(
                    TestResult(
                        test_name=test.name,
                        status="FAILED",
                        reason="Transcription failed",
                    )
                )
                continue

            print(f'  Result: "{result}"')

            passed, ratio, expected_match = check_match(result, test)

            if passed:
                print(f"  PASS (similarity: {ratio * 100:.1f}%)")
                results.append(
                    TestResult(
                        test_name=test.name,
                        status="PASSED",
                        similarity=ratio,
                        result=result,
                    )
                )
            else:
                print(f"  FAIL (similarity: {ratio * 100:.1f}%)")
                print(f'  Expected: "{expected_match}"')
                results.append(
                    TestResult(
                        test_name=test.name,
                        status="FAILED",
                        reason=f"Low similarity ({ratio * 100:.1f}%)",
                        expected=expected_match,
                        result=result,
                    )
                )

            # Rate limiting
            time.sleep(0.5)

    return results


def print_summary(results: list[TestResult]) -> None:
    """Print test summary.

    Args:
        results: List of test results.
    """
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)

    passed = sum(1 for r in results if r.status == "PASSED")
    failed = len(results) - passed

    print(f"\nTotal tests: {len(results)}")
    print(f" Passed: {passed}")
    print(f" Failed: {failed}")
    print(f"Success rate: {passed / len(results) * 100:.1f}%")

    if failed > 0:
        print("\n\nFailed tests details:")
        for r in results:
            if r.status == "FAILED":
                print(f"\n  {r.test_name}:")
                print(f"    Reason: {r.reason or 'Unknown'}")
                if r.result and r.expected:
                    print(f'    Got:      "{r.result}"')
                    print(f'    Expected: "{r.expected}"')

    print("\n" + "=" * 70)


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, 1 for failures).
    """
    print("=" * 70)
    print("VOICE TRANSCRIPTION TEST SUITE")
    print("=" * 70)
    print("\nTesting TTS -> Transcription pipeline\n")

    if not OPENAI_API_KEY:
        print(" OPENAI_API_KEY not found")
        return 1

    results = run_tests()
    print_summary(results)

    failed = sum(1 for r in results if r.status == "FAILED")
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
