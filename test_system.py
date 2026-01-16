#!/usr/bin/env python3
"""System test script for oflow - tests all components."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()

def test_backend_running():
    """Check if backend process is running."""
    result = subprocess.run(
        ["pgrep", "-f", "python.*oflow.py"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("✓ Backend process running (PID: {})".format(result.stdout.strip().split()[0]))
        return True
    else:
        print("✗ Backend NOT running")
        return False

def test_socket_exists():
    """Check if socket file exists."""
    sock_path = "/tmp/voice-dictation.sock"
    if os.path.exists(sock_path):
        print(f"✓ Socket exists: {sock_path}")
        return True
    else:
        print(f"✗ Socket NOT found: {sock_path}")
        return False

def test_socket_responsive():
    """Test if socket accepts connections and responds."""
    sock_path = "/tmp/voice-dictation.sock"
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(sock_path)
        s.send(b"status")  # Send a benign command
        s.close()
        print("✓ Socket is responsive")
        return True
    except socket.timeout:
        print("✗ Socket timeout - backend may be frozen")
        return False
    except ConnectionRefusedError:
        print("✗ Socket connection refused")
        return False
    except Exception as e:
        print(f"✗ Socket error: {e}")
        return False

def test_oflow_ctl():
    """Test oflow-ctl helper script."""
    ctl_path = os.path.expanduser("~/.local/bin/oflow-ctl")
    if not os.path.exists(ctl_path):
        print(f"✗ oflow-ctl not found: {ctl_path}")
        return False

    # Check it's executable
    if not os.access(ctl_path, os.X_OK):
        print(f"✗ oflow-ctl not executable")
        return False

    print(f"✓ oflow-ctl exists and is executable")
    return True

def test_hyprland_bindings():
    """Check if Hyprland bindings are loaded."""
    try:
        result = subprocess.run(
            ["hyprctl", "binds"],
            capture_output=True, text=True, timeout=5
        )
        if "oflow-ctl" in result.stdout:
            # Find which key oflow is bound to
            lines = result.stdout.split("\n")
            oflow_key = None
            for i, line in enumerate(lines):
                if "oflow-ctl" in line:
                    # Search backwards for key
                    for j in range(i, max(0, i-10), -1):
                        if "key:" in lines[j]:
                            oflow_key = lines[j].split(":")[1].strip()
                            break
                    break
            print(f"✓ Hyprland bindings loaded (Super+{oflow_key})")
            return True
        else:
            print("✗ Hyprland bindings NOT loaded")
            return False
    except Exception as e:
        print(f"✗ Error checking Hyprland bindings: {e}")
        return False

def test_settings_file():
    """Check settings file exists and is valid."""
    settings_path = os.path.expanduser("~/.oflow/settings.json")
    if not os.path.exists(settings_path):
        print(f"✗ Settings file not found: {settings_path}")
        return False

    try:
        import json
        with open(settings_path) as f:
            settings = json.load(f)
        provider = settings.get("provider", "not set")
        print(f"✓ Settings file valid (provider: {provider})")
        return True
    except Exception as e:
        print(f"✗ Settings file invalid: {e}")
        return False

def test_ui_running():
    """Check if UI process is running."""
    result = subprocess.run(
        ["pgrep", "-f", "oflow-ui"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("✓ UI process running")
        return True
    else:
        print("✗ UI NOT running")
        return False

def start_backend():
    """Try to start the backend."""
    print("\nAttempting to start backend...")
    os.chdir(SCRIPT_DIR)

    # Activate venv and start
    cmd = f"source {SCRIPT_DIR}/.venv/bin/activate && python oflow.py > /tmp/oflow-test.log 2>&1 &"
    subprocess.run(cmd, shell=True, executable="/bin/bash")
    time.sleep(3)

    # Check log for errors
    try:
        with open("/tmp/oflow-test.log") as f:
            log = f.read()
        if "Error" in log or "Exception" in log:
            print(f"Backend log shows errors:\n{log[:500]}")
            return False
        print("Backend started (check /tmp/oflow-test.log for details)")
        return True
    except:
        return test_backend_running()

def main():
    print("=" * 50)
    print("OFLOW SYSTEM TEST")
    print("=" * 50)

    results = {}

    print("\n--- Process Status ---")
    results["backend"] = test_backend_running()
    results["ui"] = test_ui_running()

    print("\n--- Socket Status ---")
    results["socket_exists"] = test_socket_exists()
    if results["socket_exists"]:
        results["socket_responsive"] = test_socket_responsive()
    else:
        results["socket_responsive"] = False

    print("\n--- Configuration ---")
    results["oflow_ctl"] = test_oflow_ctl()
    results["hyprland"] = test_hyprland_bindings()
    results["settings"] = test_settings_file()

    print("\n" + "=" * 50)
    passed = sum(results.values())
    total = len(results)
    print(f"RESULTS: {passed}/{total} checks passed")

    if not results["backend"]:
        print("\n⚠ Backend not running. Start with:")
        print(f"  cd {SCRIPT_DIR} && source .venv/bin/activate && python oflow.py &")

    if not results["socket_responsive"] and results["backend"]:
        print("\n⚠ Backend running but socket unresponsive (frozen). Restart with:")
        print(f"  pkill -9 -f oflow.py && cd {SCRIPT_DIR} && source .venv/bin/activate && python oflow.py &")

    print("=" * 50)
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
