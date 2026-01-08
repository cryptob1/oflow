# OmarchyFlow UI

A modern, beautiful system tray application for OmarchyFlow voice assistant. Built with Tauri v2, React, and Shadcn/UI.

![OmarchyFlow UI](https://raw.githubusercontent.com/omarchyflow/ui/main/screenshot.png)

## Features

- **Beautiful Dashboard**: visualize your voice activity
- **System Tray**: Quick access from your menu bar
- **Live History**: Searchable transcript history
- **Native Performance**: Built with Rust and Tauri
- **Privacy Focus**: All data stays local in `~/.omarchyflow`

## Tech Stack

- **Frontend**: React, TypeScript, TailwindCSS, Shadcn/UI
- **Backend**: Tauri (Rust)
- **State**: React Hooks (Zustand planned)

## Usage

1. **Start the UI**:
   - Launch `OmarchyFlow` from your system menu or run `omarchyflow-ui`.
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

OmarchyFlow UI reads your transcripts from `~/.omarchyflow/transcripts.jsonl`. Ensure your Python backend is running and writing to this location.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.
