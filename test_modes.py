#!/usr/bin/env python3
"""
Test script to compare single vs streaming transcription modes.
"""

import json
import socket
import subprocess
import time
from pathlib import Path

SOCKET_PATH = "/tmp/voice-dictation.sock"
SETTINGS_FILE = Path.home() / ".oflow" / "settings.json"
LOG_FILE = "/tmp/oflow-backend.log"

def send_command(cmd: str):
    """Send command to backend."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCKET_PATH)
    s.send(cmd.encode())
    s.close()

def set_mode(mode: str):
    """Set transcription mode in settings."""
    with open(SETTINGS_FILE) as f:
        settings = json.load(f)
    settings['transcriptionMode'] = mode
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)

def restart_backend():
    """Restart the backend."""
    subprocess.run(["pkill", "-f", "python.*oflow.py"], stderr=subprocess.DEVNULL)
    time.sleep(1)
    subprocess.Popen(
        [".venv/bin/python", "oflow.py"],
        stdout=open(LOG_FILE, 'w'),
        stderr=subprocess.STDOUT,
        start_new_session=True
    )
    # Wait for socket
    for _ in range(20):
        if Path(SOCKET_PATH).exists():
            time.sleep(0.5)  # Extra wait for full init
            return True
        time.sleep(0.5)
    return False

def get_last_processing_time():
    """Parse log to get last processing time."""
    with open(LOG_FILE) as f:
        lines = f.readlines()

    for line in reversed(lines):
        if "Recording stopped" in line and "processing:" in line:
            # Extract time like "processing: 450ms"
            import re
            match = re.search(r'processing: (\d+)ms', line)
            if match:
                return int(match.group(1))
        if "Recording stopped" in line and ("single:" in line or "streaming:" in line):
            import re
            match = re.search(r'(?:single|streaming): (\d+)ms', line)
            if match:
                return int(match.group(1))
    return None

def test_mode(mode: str, duration: float = 3.0):
    """Test a transcription mode."""
    print(f"\n{'='*50}")
    print(f"Testing {mode.upper()} mode ({duration}s recording)")
    print('='*50)

    # Set mode and restart
    set_mode(mode)
    print(f"Mode set to: {mode}")

    if not restart_backend():
        print("ERROR: Backend failed to start")
        return None
    print("Backend restarted")

    # Record
    print(f"Recording for {duration}s...")
    send_command("start")
    time.sleep(duration)

    # Stop and measure
    stop_time = time.perf_counter()
    send_command("stop")

    # Wait for processing to complete (check log for result)
    for _ in range(30):  # Up to 15 seconds
        time.sleep(0.5)
        proc_time = get_last_processing_time()
        if proc_time is not None:
            break

    if proc_time is None:
        print("ERROR: Could not get processing time from log")
        return None

    print(f"Processing time: {proc_time}ms")
    return proc_time

def main():
    print("="*60)
    print("OFLOW TRANSCRIPTION MODE COMPARISON")
    print("="*60)

    results = {}

    # Test both modes with different durations
    for duration in [2.0, 4.0, 6.0]:
        print(f"\n\n>>> TESTING WITH {duration}s AUDIO <<<")

        single_time = test_mode('single', duration)
        streaming_time = test_mode('streaming', duration)

        results[duration] = {
            'single': single_time,
            'streaming': streaming_time
        }

        if single_time and streaming_time:
            diff = single_time - streaming_time
            pct = (diff / single_time) * 100 if single_time else 0
            print(f"\n>>> {duration}s RESULT: Streaming is {diff}ms faster ({pct:.1f}%)")

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"{'Duration':<12} {'Single':<12} {'Streaming':<12} {'Diff':<12}")
    print("-"*48)
    for dur, times in results.items():
        s = times.get('single', 'N/A')
        st = times.get('streaming', 'N/A')
        if isinstance(s, int) and isinstance(st, int):
            diff = f"{s - st}ms"
        else:
            diff = "N/A"
        print(f"{dur}s{'':<9} {s}ms{'':<6} {st}ms{'':<6} {diff}")

if __name__ == "__main__":
    main()
