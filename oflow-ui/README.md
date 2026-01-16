# Oflow UI

A modern system tray application for Oflow voice dictation. Built with Tauri v2, React, and Shadcn/UI.

## Features

- **Settings UI**: Configure API keys and preferences
- **System Tray**: Quick access from your menu bar
- **Live History**: Searchable transcript history
- **Native Performance**: Built with Rust and Tauri
- **Privacy Focus**: All data stays local in `~/.oflow`

## Tech Stack

- **Frontend**: React, TypeScript, TailwindCSS, Shadcn/UI
- **Backend**: Tauri (Rust)

## Usage

1. **Start the UI**:
   - Launch `oflow` from your application menu
   - It will sit in your system tray (Waybar shows ○ icon)

2. **Dictate**:
   - Press **Super+D** to start recording
   - Speak your text
   - Press **Super+D** again to stop and transcribe
   - The text will be typed into your active window

3. **Configure**:
   - Click the ○ icon in Waybar to open settings
   - Add your Groq API key (free at console.groq.com)
   - Toggle cleanup, choose provider, etc.

4. **Browse History**:
   - Open the dashboard to see past transcripts

## Building

```bash
# Development
npm run tauri dev

# Production build
npm run tauri build
```

The binary will be in `src-tauri/target/release/oflow-ui`.

## Hotkey Setup

The hotkey is configured via Hyprland, not the app. See [docs/hotkeys.md](../docs/hotkeys.md) for details.

Default: **Super+D** (toggle mode - press to start, press again to stop)
