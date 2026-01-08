# Oflow UI

A modern, beautiful system tray application for Oflow voice assistant. Built with Tauri v2, React, and Shadcn/UI.

![Oflow UI](https://raw.githubusercontent.com/oflow/ui/main/screenshot.png)

## Features

- **Beautiful Dashboard**: visualize your voice activity
- **System Tray**: Quick access from your menu bar
- **Live History**: Searchable transcript history
- **Native Performance**: Built with Rust and Tauri
- **Privacy Focus**: All data stays local in `~/.oflow`

## Tech Stack

- **Frontend**: React, TypeScript, TailwindCSS, Shadcn/UI
- **Backend**: Tauri (Rust)
- **State**: React Hooks (Zustand planned)

## Usage

1. **Start the UI**:
   - Launch `Oflow` from your system menu or run `oflow-ui`.
   - It will sit in your system tray.

2. **Dictate (Super+I)**:
   - Press **Super+I** (or your configured hotkey) to start recording.
   - Speak your text.
   - Press **Super+I** again to stop.
   - The text will be transcribed, cleaned by GPT-4o-mini, and typed into your active window.

3. **Browse History**:
   - Click the tray icon or open the dashboard to see past transcripts.

## Hotkey Setup

See [docs/hotkeys.md](docs/hotkeys.md) for instructions on setting up the `Super+I` trigger in Hyprland, i3, or GNOME.

## Building

To build the optimized binary:
```bash
npm run tauri build
```
The binary will be in `src-tauri/target/release/bundle`.

## Configuration

Oflow UI reads your transcripts from `~/.oflow/transcripts.jsonl`. Ensure your Python backend is running and writing to this location.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.
