#!/usr/bin/env bash
#
# cortex installer — fast, accurate voice-to-text for Linux (Wayland/Hyprland).
#
#   curl -fsSL https://raw.githubusercontent.com/cryptob1/oflow/master/install.sh | bash
#
# Installs dependencies, builds cortex, sets up the Copilot-key hotkey, the recording
# overlay, the ydotool paste daemon, and autostart. Arch / Omarchy.
set -euo pipefail

REPO="https://github.com/cryptob1/oflow.git"
DIR="${CORTEX_DIR:-$HOME/code/cortex}"

say() { printf '\n\033[1;36m==>\033[0m %s\n' "$1"; }

command -v pacman >/dev/null || { echo "This installer targets Arch/Omarchy (pacman). See the README for manual steps."; exit 1; }

say "Installing dependencies (you'll be asked for sudo once)…"
sudo pacman -S --needed --noconfirm \
  git base-devel uv nodejs npm rust \
  webkit2gtk-4.1 jq \
  ydotool playerctl gtk4-layer-shell python-gobject python-cairo

say "Getting the source → $DIR"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only
else
  mkdir -p "$(dirname "$DIR")"
  git clone --depth 1 "$REPO" "$DIR"
fi
cd "$DIR"

say "Building & installing cortex (this takes a few minutes the first time)…"
make install

say "Enabling the ydotool paste daemon…"
sudo usermod -aG input "$USER" 2>/dev/null || true
systemctl --user enable --now ydotool.service 2>/dev/null || true

cat <<'DONE'

  ✅ cortex installed.

  1. Click the  󰍬  mic icon in Waybar (or the tray) → paste a free Groq API key
     from https://console.groq.com/keys
  2. Hold the  Copilot key , speak, release — your words paste into whatever you're typing.

  Tip: end a dictation with "press enter" to submit (great for AI prompts).
DONE
