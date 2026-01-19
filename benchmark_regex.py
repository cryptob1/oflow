#!/usr/bin/env python3
"""Quick benchmark showing why pre-compilation matters."""

import re
import time

# Sample text to process
text = "Hello comma world period This is a test comma with multiple comma instances period"


# Method 1: Compile pattern every time (OLD WAY - SLOW)
def method_old(text, iterations=10000):
    start = time.perf_counter()
    for _ in range(iterations):
        result = text
        for phrase, symbol in [("comma", ","), ("period", ".")]:
            pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
            result = pattern.sub(symbol, result)
    end = time.perf_counter()
    return (end - start) * 1000  # milliseconds


# Method 2: Pre-compile patterns once (NEW WAY - FAST)
def method_new(text, iterations=10000):
    # Pre-compile patterns ONCE
    patterns = []
    for phrase, symbol in [("comma", ","), ("period", ".")]:
        pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
        patterns.append((pattern, symbol))

    start = time.perf_counter()
    for _ in range(iterations):
        result = text
        for pattern, symbol in patterns:
            result = pattern.sub(symbol, result)
    end = time.perf_counter()
    return (end - start) * 1000  # milliseconds


# Run benchmark
print("🔬 Regex Pre-compilation Benchmark")
print("=" * 50)
print(f"Processing: '{text}'")
print(f"Iterations: 10,000")
print()

old_time = method_old(text)
new_time = method_new(text)
speedup = old_time / new_time

print(f"❌ OLD (compile every time): {old_time:.1f}ms")
print(f"✅ NEW (pre-compile once):   {new_time:.1f}ms")
print()
print(f"🚀 Speedup: {speedup:.1f}x faster!")
print(f"💾 Time saved: {old_time - new_time:.1f}ms per 10k operations")
