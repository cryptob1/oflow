#!/bin/bash
set -e

# Detect target triple (simplified for Linux)
TARGET="x86_64-unknown-linux-gnu"
SIDE_CAR_NAME="omarchyflow-backend"
TAURI_BIN_DIR="omarchyflow-ui/src-tauri/binaries"

# Create binaries directory if not exists
mkdir -p "$TAURI_BIN_DIR"

echo "📦 Building Python sidecar..."
# Build standalone binary using PyInstaller
# --onefile: bundle everything into one executable
# --name: output name
# --hidden-import: ensure these are included
pyinstaller --clean --noconfirm --onefile --name "$SIDE_CAR_NAME" \
    --hidden-import=langchain_openai \
    --hidden-import=langgraph \
    --hidden-import=numpy \
    --hidden-import=sounddevice \
    omarchyflow.py

echo "🚚 Moving binary to Tauri..."
mv "dist/$SIDE_CAR_NAME" "$TAURI_BIN_DIR/$SIDE_CAR_NAME-$TARGET"

echo "✅ Sidecar built: $TAURI_BIN_DIR/$SIDE_CAR_NAME-$TARGET"
