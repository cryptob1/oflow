# OmarchyFlow Implementation Status

## Overview
OmarchyFlow is a voice dictation app similar to WispFlow/Willow on Mac. It should run in the system tray, record audio on a global hotkey, transcribe to English, and paste into the active window.

## ✅ What's Implemented

### Python Backend (`omarchyflow.py`)
**Status: ✅ Fully Functional**

1. **Audio Recording**
   - Uses `sounddevice` for microphone input
   - Records at 16kHz, mono
   - Queue-based audio buffering
   - Audio validation (checks for empty/silent audio)

2. **Transcription Pipeline**
   - Whisper API integration (OpenAI)
   - GPT-4o-mini text cleanup (optional, enabled by default)
   - Memory system for learning user patterns (optional)
   - LangGraph workflow architecture
   - Async processing with retry logic

3. **Text Output**
   - Types text using `wtype` (Wayland) or `xdotool` (X11)
   - Falls back to clipboard if typing fails
   - Desktop notifications for status

4. **Storage**
   - Saves transcripts to `~/.omarchyflow/transcripts.jsonl`
   - Stores memories in `~/.omarchyflow/memories.json`
   - JSONL format for easy parsing

5. **Communication**
   - Unix socket server at `/tmp/voice-dictation.sock`
   - Accepts commands: `start`, `stop`, `toggle`
   - CLI interface: `./omarchyflow [start|stop|toggle]`

### Tauri UI Shell (`omarchyflow-ui/`)
**Status: ⚠️ Partially Implemented**

1. **Basic Structure**
   - React + TypeScript + TailwindCSS
   - Shadcn/UI components
   - Three views: Dashboard, History, Settings

2. **System Tray**
   - Tray icon created (basic implementation)
   - No menu or click handlers yet

3. **Backend Integration**
   - Sidecar binary configured (`omarchyflow-backend-x86_64-unknown-linux-gnu`)
   - Backend spawns on startup but not properly integrated
   - No Tauri commands to control recording

4. **UI Components**
   - Dashboard with stats cards (hardcoded data)
   - History view (mock data)
   - Settings view (UI only, no persistence)

## ❌ What's Missing

### Critical Features

1. **Global Hotkey Registration** 🔴 HIGH PRIORITY
   - Currently relies on external Hyprland config
   - Need Tauri global hotkey plugin (`tauri-plugin-global-shortcut`)
   - Should register hotkey (e.g., Super+I) within the app
   - Hotkey should trigger recording start/stop

2. **Tauri Commands for Backend Control** 🔴 HIGH PRIORITY
   - No Tauri commands exposed to frontend
   - Need commands like:
     - `start_recording()` - Send "start" to Unix socket
     - `stop_recording()` - Send "stop" to Unix socket
     - `toggle_recording()` - Send "toggle" to Unix socket
     - `get_recording_status()` - Check if recording
   - Frontend buttons don't actually control backend

3. **Unix Socket Communication from Tauri** 🔴 HIGH PRIORITY
   - Backend spawns but Tauri doesn't communicate with it
   - Need Rust code to send commands to `/tmp/voice-dictation.sock`
   - Should handle connection errors gracefully

4. **System Tray Menu** 🟡 MEDIUM PRIORITY
   - No tray menu on click
   - Should have: "Show Window", "Start Recording", "Stop Recording", "Settings", "Quit"
   - Tray icon should reflect recording state (red when recording)

5. **Window Management** 🟡 MEDIUM PRIORITY
   - Window should start hidden/minimized
   - Tray click should show/hide window
   - Window should minimize to tray on close

6. **Real Data Integration** 🟡 MEDIUM PRIORITY
   - History view uses mock data
   - Need to read from `~/.omarchyflow/transcripts.jsonl`
   - Dashboard stats should be calculated from real data
   - Need Tauri command to fetch transcripts

7. **Settings Persistence** 🟡 MEDIUM PRIORITY
   - Settings UI exists but doesn't save
   - Need to store settings (cleanup enabled, memory enabled, etc.)
   - Should update backend config or `.env` file

8. **Backend Status Monitoring** 🟡 MEDIUM PRIORITY
   - UI doesn't know if backend is actually recording
   - Need to poll or listen for backend status
   - Recording button state should reflect actual backend state

### Nice-to-Have Features

9. **Hotkey Configuration UI** 🟢 LOW PRIORITY
   - Allow users to change hotkey from Settings
   - Show current hotkey binding
   - Validate hotkey conflicts

10. **Real-time Transcript Display** 🟢 LOW PRIORITY
    - Show transcript as it's being processed
    - Display raw vs cleaned text side-by-side

11. **Transcript Search/Filter** 🟢 LOW PRIORITY
    - History view has search input but doesn't work
    - Filter by date, tags, etc.

12. **Export Transcripts** 🟢 LOW PRIORITY
    - Export to text file, markdown, etc.

## Architecture Gaps

### Current Flow (Broken)
```
User presses Super+I (Hyprland) 
  → Executes `./omarchyflow toggle` (external)
  → Python backend receives command
  → Records and transcribes
  → Types text
```

### Desired Flow (Not Implemented)
```
User presses Super+I (Tauri global hotkey)
  → Tauri command triggered
  → Sends command to Python backend via Unix socket
  → Python backend records and transcribes
  → Types text
  → Tauri UI updates status
```

## Required Dependencies

### Tauri Plugins Needed
1. `tauri-plugin-global-shortcut` - For global hotkey registration
2. Already have: `tauri-plugin-shell`, `tauri-plugin-fs`, `tauri-plugin-log`

### Rust Code Needed
1. Unix socket client to communicate with Python backend
2. Tauri commands for recording control
3. Tauri commands for reading transcripts
4. Tray menu implementation
5. Window show/hide logic

### Frontend Code Needed
1. API calls to Tauri commands
2. Real data fetching for History/Dashboard
3. Settings save/load
4. Recording status polling or event listening

## Testing Checklist

- [ ] App starts and shows in system tray
- [ ] Tray menu works (show/hide window, quit)
- [ ] Global hotkey (Super+I) triggers recording
- [ ] UI recording button controls backend
- [ ] Recording status updates in UI
- [ ] History view shows real transcripts
- [ ] Dashboard shows real statistics
- [ ] Settings save and persist
- [ ] Text gets typed into active window
- [ ] Window minimizes to tray on close

## Next Steps (Priority Order)

1. **Add global hotkey plugin** - Install and configure `tauri-plugin-global-shortcut`
2. **Create Tauri commands** - Implement Rust commands for backend control
3. **Implement Unix socket client** - Rust code to communicate with Python backend
4. **Connect frontend to backend** - Wire up UI buttons to Tauri commands
5. **Add tray menu** - Implement click handlers and context menu
6. **Window management** - Hide on start, show/hide from tray
7. **Real data integration** - Read transcripts from file system
8. **Settings persistence** - Save/load settings

## Files That Need Changes

### Rust (`omarchyflow-ui/src-tauri/src/lib.rs`)
- Add global hotkey registration
- Add Unix socket client
- Add Tauri commands
- Add tray menu
- Add window management

### TypeScript (`omarchyflow-ui/src/`)
- `lib/api.ts` - Add Tauri command wrappers
- `App.tsx` - Connect buttons to API
- `components/Dashboard.tsx` - Fetch real stats
- `components/HistoryView.tsx` - Fetch real transcripts
- `components/SettingsView.tsx` - Save/load settings

### Configuration
- `omarchyflow-ui/src-tauri/Cargo.toml` - Add `tauri-plugin-global-shortcut`
- `omarchyflow-ui/src-tauri/tauri.conf.json` - Configure window behavior

