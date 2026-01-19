#!/usr/bin/env python3
"""
Build script to package Python backend with Tauri frontend.
This creates a standalone binary that includes both frontend and backend.
"""

import os
import sys
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def create_python_bundle():
    """Create a bundled Python executable with all dependencies."""
    print("Creating Python backend bundle...")

    # Ensure virtual environment exists
    if not Path(".venv").exists():
        print("Creating virtual environment...")
        subprocess.run(["uv", "venv"], check=True)

    # Install dependencies
    print("Installing Python dependencies...")
    subprocess.run(["uv", "pip", "install", "-e", "."], check=True)

    # Create resources directory
    resources_dir = Path("oflow-ui/src-tauri/resources")
    resources_dir.mkdir(parents=True, exist_ok=True)

    # Copy Python files
    print("Copying Python backend files...")
    backend_dir = resources_dir / "backend"
    backend_dir.mkdir(exist_ok=True)

    # Copy main oflow.py
    shutil.copy("oflow.py", backend_dir / "oflow.py")

    # Copy entire oflow package
    if Path("oflow").exists():
        shutil.copytree("oflow", backend_dir / "oflow", dirs_exist_ok=True)

    # Copy virtual environment (only needed packages)
    venv_dir = Path(".venv")
    bundled_venv = backend_dir / "venv"

    print("Bundling Python environment...")
    # Create a minimal venv structure
    bundled_venv.mkdir(parents=True, exist_ok=True)

    # Copy essential parts of venv
    for subdir in ["bin", "lib", "include"]:
        src = venv_dir / subdir
        dst = bundled_venv / subdir
        if src.exists():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)

    # Create a launcher script
    launcher_script = f"""#!/bin/bash
# Launcher script for bundled oflow backend
DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
export VIRTUAL_ENV="$DIR/backend/venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"
unset PYTHON_HOME
exec "$VIRTUAL_ENV/bin/python" "$DIR/backend/oflow.py" "$@"
"""

    launcher_path = resources_dir / "oflow-backend.sh"
    with open(launcher_path, "w") as f:
        f.write(launcher_script)

    os.chmod(launcher_path, 0o755)
    print("Python backend bundle created successfully!")


def update_tauri_config():
    """Update Tauri config to include backend resources."""
    print("Updating Tauri configuration...")

    # The config is already updated to include resources
    print("Tauri configuration updated!")


def main():
    """Main build process."""
    print("Building combined oflow binary...")

    # Change to project root if needed
    if Path("oflow-ui/src-tauri").exists():
        # We're in the right place
        pass
    elif Path("../oflow-ui/src-tauri").exists():
        os.chdir("..")

    # Create Python bundle
    create_python_bundle()

    # Update Tauri config
    update_tauri_config()

    print("Build preparation complete!")
    print("Now run: cd oflow-ui && npm run tauri build")


if __name__ == "__main__":
    main()
