# OmarchyFlow LangChain Rebuild

## Summary

Rebuilt OmarchyFlow using LangChain voice agent architecture to fix reliability issues with audio handling and API calls.

## Problem Statement

The original implementation had several critical issues:
1. **No audio validation** - Never checked if audio was valid before sending to API
2. **Race conditions** - `time.sleep(0.1)` doesn't guarantee all audio is captured
3. **No retry logic** - Single API failure = complete failure
4. **Blocking I/O** - Thread blocks on HTTP requests
5. **Poor error visibility** - User sees generic "Transcription failed" with no details

## Solution: LangChain Architecture

Implemented the "Sandwich Architecture" pattern from LangChain's voice agent documentation:
- **Async streaming** - Non-blocking I/O with concurrent processing
- **Event-driven** - Clear event types for observability
- **Producer-consumer pattern** - Audio sending and transcript receiving happen concurrently
- **Validation** - Audio is validated before API calls
- **Retry logic** - Exponential backoff for transient failures

## Implementation Details

### New File: `omarchyflow_langchain.py`

**Key improvements:**

1. **Audio Validation**
```python
class AudioValidator:
    @staticmethod
    def validate(audio: np.ndarray) -> tuple[bool, str | None]:
        if len(audio) == 0:
            return False, "Empty audio"
        
        max_amplitude = np.max(np.abs(audio))
        if max_amplitude < 0.01:
            return False, "Audio too quiet (no speech detected)"
        
        return True, None
```

2. **Retry with Exponential Backoff**
```python
async def transcribe_with_retry(
    stt_provider,
    audio_base64: str,
    max_retries: int = 3,
) -> str:
    for attempt in range(max_retries):
        try:
            return await stt_provider.transcribe(audio_base64)
        except httpx.TimeoutException:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)
```

3. **Event-Driven Error Handling**
```python
class EventType(Enum):
    AUDIO_CHUNK = "audio_chunk"
    STT_OUTPUT = "stt_output"
    STT_ERROR = "stt_error"

@dataclass
class VoiceEvent:
    type: EventType
    data: bytes | str | None = None
    error: str | None = None
```

4. **Async Streaming Pipeline**
```python
async def stt_stream(audio_data: np.ndarray) -> AsyncIterator[VoiceEvent]:
    # Validate audio
    valid, error_msg = AudioValidator.validate(normalized_audio)
    if not valid:
        yield VoiceEvent(type=EventType.STT_ERROR, error=error_msg)
        return
    
    # Process with retry
    try:
        text = await transcribe_with_retry(stt, audio_base64)
        yield VoiceEvent(type=EventType.STT_OUTPUT, data=text)
    except Exception as e:
        yield VoiceEvent(type=EventType.STT_ERROR, error=str(e))
```

### Test Suite: `test_langchain_robustness.py`

Comprehensive tests validating:
- Empty audio rejection
- Silent audio rejection  
- Valid audio acceptance
- Audio normalization (target: 0.95 peak)
- Base64 WAV encoding
- STT stream error handling
- Real audio transcription

**Test Results: 8/8 passed (100%)**

## Benefits Over Original Implementation

| Feature | Original | LangChain Rebuild |
|---------|----------|-------------------|
| Audio validation | ❌ None | ✅ Pre-send validation |
| Error handling | ❌ Generic failures | ✅ Detailed error events |
| API retries | ❌ None | ✅ 3 retries with backoff |
| I/O model | ❌ Blocking threads | ✅ Async/await |
| Race conditions | ❌ `sleep(0.1)` hack | ✅ Proper async drain |
| Observability | ❌ Logs only | ✅ Event-driven feedback |
| Testing | ⚠️ Basic | ✅ Comprehensive suite |

## Architecture Comparison

### Original (Problematic)
```
Audio → Queue → sleep(0.1) → Thread → Blocking HTTP → Type text
                  ⚠️           ⚠️        ⚠️
```

### LangChain (Robust)
```
Audio → Validate → Normalize → AsyncIterator[VoiceEvent]
           ↓
        STT Stream (async)
           ↓
        Retry Logic (3x with backoff)
           ↓
        Event: STT_OUTPUT | STT_ERROR
           ↓
        Type text | Show error
```

## Usage

### Starting the Server

```bash
cd ~/voice-assistant
.venv/bin/python3 ./omarchyflow_langchain.py &
```

### Using Voice Dictation

1. Press and hold **Super+I**
2. Speak your text
3. Release **Super+I**
4. Text appears in active window

### Configuration

Same `.env` file as original:
```bash
USE_OPENAI_DIRECT=true          # or
USE_OPENROUTER_GEMINI=true

OPENAI_API_KEY=sk-...           # or
OPENROUTER_API_KEY=sk-or-v1-... 
```

## Dependencies

Added `langchain-core` for async streaming patterns:
```bash
uv pip install langchain-core
```

## Testing

Run the test suite:
```bash
cd ~/voice-assistant
.venv/bin/python3 test_langchain_robustness.py
```

Expected output:
```
============================================================
OmarchyFlow LangChain Robustness Tests
============================================================

=== Test 1: Empty Audio Validation ===
✅ Empty audio correctly rejected

=== Test 2: Silent Audio Validation ===
✅ Silent audio correctly rejected

=== Test 3: Valid Audio Validation ===
✅ Valid audio accepted

=== Test 4: Audio Normalization ===
✅ Audio normalized to 0.950

=== Test 5: Base64 WAV Encoding ===
✅ Audio encoded to 85392 base64 chars

=== Test 6: STT Stream - Empty Audio ===
✅ Empty audio error: Empty audio

=== Test 7: STT Stream - Silent Audio ===
✅ Silent audio error: Audio too quiet (no speech detected)

=== Test 8: STT Stream - Valid Audio (Real API Call) ===
✅ Real audio transcribed successfully

============================================================
Results: 8 passed, 0 failed
============================================================
```

## Migration

Hyprland keybindings updated to use new script:
```conf
bind = SUPER, I, exec, /home/vish/voice-assistant/.venv/bin/python /home/vish/voice-assistant/omarchyflow_langchain.py start
bindr = SUPER, I, exec, /home/vish/voice-assistant/.venv/bin/python /home/vish/voice-assistant/omarchyflow_langchain.py stop
```

Original `omarchyflow.py` kept for reference but no longer active.

## Future Enhancements

Potential improvements based on LangChain architecture:
1. **Streaming STT** - Start transcription before all audio arrives (requires WebSocket API)
2. **Agent integration** - Add LangChain agent for smart text formatting
3. **Multi-provider fallback** - Automatically fall back to Gemini if OpenAI fails
4. **Voice commands** - "new paragraph", "delete that", etc.
5. **Real-time feedback** - Show partial transcripts as they arrive

## References

- [LangChain Voice Agent Guide](https://docs.langchain.com/oss/python/langchain/voice-agent)
- Original implementation: `omarchyflow.py`
- New implementation: `omarchyflow_langchain.py`
- Test suite: `test_langchain_robustness.py`

---

**Status:** ✅ Complete and tested
**Current server:** `omarchyflow_langchain.py` (PID: 324457)
**Keybindings:** Updated and reloaded
