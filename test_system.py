#!/usr/bin/env python3
"""
System check script for oflow.
Validates dependencies, configuration, and API connectivity.
"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import httpx

# Colors for terminal output
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def check_mark(passed: bool) -> str:
    """Return a colored check mark or X."""
    return f"{GREEN}✓{RESET}" if passed else f"{RED}✗{RESET}"


def print_header(text: str):
    """Print a section header."""
    print(f"\n{BOLD}{text}{RESET}")
    print("=" * len(text))


def check_dependencies() -> tuple[int, int]:
    """Check system dependencies."""
    print_header("System Dependencies")

    checks = {
        "wtype (Wayland text input)": "wtype",
        "xdotool (X11 text input)": "xdotool",
        "wl-copy (clipboard fallback)": "wl-copy",
        "pactl (audio control)": "pactl",
    }

    passed = 0
    total = 0
    has_text_input = False

    for name, cmd in checks.items():
        total += 1
        found = shutil.which(cmd) is not None
        if found:
            passed += 1
            if cmd in ("wtype", "xdotool"):
                has_text_input = True

        status = check_mark(found)
        print(f"  {status} {name}")

        if not found and cmd == "wtype":
            print(f"      {YELLOW}→ Install with: sudo pacman -S wtype{RESET}")

    if not has_text_input:
        print(f"\n  {YELLOW}⚠  Warning: No text input tool found!{RESET}")
        print(f"     Install wtype for Wayland: {BOLD}sudo pacman -S wtype{RESET}")

    return passed, total


def check_configuration() -> tuple[int, int]:
    """Check oflow configuration."""
    print_header("Configuration")

    passed = 0
    total = 3

    # Check settings file
    settings_file = Path.home() / ".oflow" / "settings.json"
    settings_exists = settings_file.exists()
    print(f"  {check_mark(settings_exists)} Settings file: {settings_file}")
    if settings_exists:
        passed += 1

    # Check API key
    api_key = None
    provider = "groq"
    if settings_exists:
        try:
            with open(settings_file) as f:
                settings = json.load(f)
                provider = settings.get("provider", "groq")
                api_key = settings.get("groqApiKey" if provider == "groq" else "openaiApiKey")
        except Exception as e:
            print(f"      {RED}Error reading settings: {e}{RESET}")

    has_api_key = bool(api_key)
    print(f"  {check_mark(has_api_key)} {provider.capitalize()} API key configured")
    if has_api_key:
        passed += 1
        # Check for duplicated key
        if len(api_key) > 60:
            print(
                f"      {YELLOW}⚠  API key looks duplicated (length: {len(api_key)}, expected ~56){RESET}"
            )
            print(f"      {YELLOW}   Check ~/.oflow/settings.json{RESET}")
    else:
        if provider == "groq":
            print(f"      {YELLOW}→ Get a free key at: https://console.groq.com/keys{RESET}")
        else:
            print(f"      {YELLOW}→ Get a key at: https://platform.openai.com/api-keys{RESET}")

    # Check socket
    socket_exists = os.path.exists("/tmp/voice-dictation.sock")
    print(f"  {check_mark(socket_exists)} Backend running (socket exists)")
    if socket_exists:
        passed += 1
    else:
        print(f"      {YELLOW}→ Start with: python oflow.py &{RESET}")

    return passed, total


async def check_api_connectivity(api_key: str, provider: str) -> bool:
    """Test API connectivity."""
    if not api_key:
        return False

    try:
        url = (
            "https://api.groq.com/openai/v1/models"
            if provider == "groq"
            else "https://api.openai.com/v1/models"
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
            return response.status_code == 200
    except Exception:
        return False


async def check_api() -> tuple[int, int]:
    """Check API configuration and connectivity."""
    print_header("API Connectivity")

    passed = 0
    total = 1

    # Load API key
    settings_file = Path.home() / ".oflow" / "settings.json"
    api_key = None
    provider = "groq"

    if settings_file.exists():
        try:
            with open(settings_file) as f:
                settings = json.load(f)
                provider = settings.get("provider", "groq")
                api_key = settings.get("groqApiKey" if provider == "groq" else "openaiApiKey")
        except Exception:
            pass

    if not api_key:
        print(f"  {check_mark(False)} API connectivity (no key configured)")
        return passed, total

    # Test connectivity
    print(f"  Testing {provider.capitalize()} API...", end=" ", flush=True)
    connected = await check_api_connectivity(api_key, provider)
    print(f"\r  {check_mark(connected)} {provider.capitalize()} API connectivity")

    if connected:
        passed += 1
    else:
        print(f"      {RED}Failed to connect. Check your API key.{RESET}")
        if provider == "groq":
            print(f"      {YELLOW}→ Verify at: https://console.groq.com/keys{RESET}")
        else:
            print(f"      {YELLOW}→ Verify at: https://platform.openai.com/api-keys{RESET}")

    return passed, total


def check_audio() -> tuple[int, int]:
    """Check audio configuration."""
    print_header("Audio System")

    passed = 0
    total = 1

    # Check for default audio source
    try:
        result = subprocess.run(
            ["pactl", "list", "sources", "short"], capture_output=True, text=True, timeout=5
        )
        has_sources = result.returncode == 0 and len(result.stdout.strip()) > 0
        print(f"  {check_mark(has_sources)} Audio input device available")
        if has_sources:
            passed += 1
        else:
            print(f"      {RED}No audio sources found{RESET}")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print(f"  {check_mark(False)} Audio input device (pactl not available)")

    return passed, total


async def main():
    """Run all system checks."""
    print(f"\n{BOLD}oflow System Check{RESET}")
    print(f"{'=' * 40}\n")

    total_passed = 0
    total_checks = 0

    # Run checks
    p, t = check_dependencies()
    total_passed += p
    total_checks += t

    p, t = check_configuration()
    total_passed += p
    total_checks += t

    p, t = await check_api()
    total_passed += p
    total_checks += t

    p, t = check_audio()
    total_passed += p
    total_checks += t

    # Summary
    print_header("Summary")
    percentage = (total_passed / total_checks * 100) if total_checks > 0 else 0

    if total_passed == total_checks:
        color = GREEN
        status = "All checks passed! ✓"
    elif total_passed >= total_checks * 0.7:
        color = YELLOW
        status = "Most checks passed"
    else:
        color = RED
        status = "Several issues found"

    print(f"  {color}{status}{RESET}")
    print(f"  {total_passed}/{total_checks} checks passed ({percentage:.0f}%)")

    if total_passed < total_checks:
        print(f"\n  {YELLOW}See messages above for how to fix issues.{RESET}")
        sys.exit(1)
    else:
        print(f"\n  {GREEN}Your system is ready to use oflow!{RESET}")
        print(f"  Press {BOLD}Super+D{RESET} to start recording.")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
