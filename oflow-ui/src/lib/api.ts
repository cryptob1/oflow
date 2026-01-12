import { readTextFile, writeTextFile, mkdir, exists, BaseDirectory } from '@tauri-apps/plugin-fs';
import { invoke } from '@tauri-apps/api/core';
import { getCurrentWindow } from '@tauri-apps/api/window';

export interface Transcript {
    timestamp: string;
    raw: string;
    cleaned: string;
}

export interface Settings {
    enableCleanup: boolean;
    enableMemory: boolean;
    openaiApiKey?: string;
    groqApiKey?: string;
    provider?: 'openai' | 'groq';  // Groq is ~200x faster
    shortcut?: string;  // e.g., "Super+I", "Ctrl+Shift+Space"
    transcriptionMode?: 'single' | 'streaming';  // single = wait until done, streaming = upload during recording
}

// Default shortcut
export const DEFAULT_SHORTCUT = "Super+I";

/**
 * Starts recording audio.
 * @returns Promise that resolves when recording starts, or rejects with an error message.
 */
export async function startRecording(): Promise<void> {
    try {
        await invoke('start_recording');
    } catch (error) {
        throw new Error(`Failed to start recording: ${error}`);
    }
}

/**
 * Stops recording audio.
 * @returns Promise that resolves when recording stops, or rejects with an error message.
 */
export async function stopRecording(): Promise<void> {
    try {
        await invoke('stop_recording');
    } catch (error) {
        throw new Error(`Failed to stop recording: ${error}`);
    }
}

/**
 * Toggles recording state.
 * @returns Promise that resolves with the new recording state (true if recording, false if stopped).
 */
export async function toggleRecording(): Promise<boolean> {
    try {
        return await invoke<boolean>('toggle_recording');
    } catch (error) {
        throw new Error(`Failed to toggle recording: ${error}`);
    }
}

/**
 * Gets the current recording status.
 * @returns Promise that resolves with true if recording, false otherwise.
 */
export async function getRecordingStatus(): Promise<boolean> {
    try {
        return await invoke<boolean>('get_recording_status');
    } catch (error) {
        throw new Error(`Failed to get recording status: ${error}`);
    }
}

/**
 * Checks if the backend is running.
 * @returns Promise that resolves with true if backend is running, false otherwise.
 */
export async function checkBackendStatus(): Promise<boolean> {
    try {
        return await invoke<boolean>('check_backend_status');
    } catch (error) {
        console.error('Failed to check backend status:', error);
        return false;
    }
}

/**
 * Shows the main window.
 */
export async function showWindow(): Promise<void> {
    try {
        const window = getCurrentWindow();
        await window.show();
        await window.setFocus();
    } catch (error) {
        throw new Error(`Failed to show window: ${error}`);
    }
}

/**
 * Hides the main window.
 */
export async function hideWindow(): Promise<void> {
    try {
        await invoke('hide_window');
    } catch (error) {
        throw new Error(`Failed to hide window: ${error}`);
    }
}

/**
 * Gets all transcripts from the storage file.
 * @returns Promise that resolves with an array of transcripts, newest first.
 */
export async function getTranscripts(): Promise<Transcript[]> {
    try {
        const contents = await readTextFile('.oflow/transcripts.jsonl', {
            baseDir: BaseDirectory.Home
        });

        const lines = contents.trim().split('\n');
        return lines
            .filter(line => line.trim())
            .map(line => {
                try {
                    return JSON.parse(line) as Transcript;
                } catch (e) {
                    console.error("Failed to parse line:", line, e);
                    return null;
                }
            })
            .filter((t): t is Transcript => t !== null)
            .reverse(); // Newest first
    } catch (error) {
        console.error("Failed to read transcripts:", error);
        return []; // Return empty array instead of mock data
    }
}

/**
 * Gets statistics from transcripts.
 */
export interface TranscriptStats {
    totalTranscripts: number;
    totalWords: number;
    estimatedTimeSaved: number; // in hours
    cleanupQuality: number; // percentage
}

/**
 * Calculates statistics from transcripts.
 * @returns Promise that resolves with transcript statistics.
 */
export async function getTranscriptStats(): Promise<TranscriptStats> {
    try {
        const transcripts = await getTranscripts();
        
        const totalTranscripts = transcripts.length;
        
        // Count words in cleaned transcripts
        const totalWords = transcripts.reduce((sum, t) => {
            return sum + (t.cleaned?.split(/\s+/).filter(w => w.length > 0).length || 0);
        }, 0);
        
        // Estimate time saved: assume 40 WPM typing speed
        const wordsPerMinute = 40;
        const minutesSaved = totalWords / wordsPerMinute;
        const estimatedTimeSaved = minutesSaved / 60;
        
        // Calculate cleanup quality: compare raw vs cleaned word counts
        let cleanupQuality = 100;
        if (transcripts.length > 0) {
            const totalRawWords = transcripts.reduce((sum, t) => {
                return sum + (t.raw?.split(/\s+/).filter(w => w.length > 0).length || 0);
            }, 0);
            if (totalRawWords > 0) {
                // Quality is how much the cleaned version improved (removed filler words, etc.)
                // This is a simple heuristic
                const improvement = Math.max(0, (totalRawWords - totalWords) / totalRawWords * 100);
                cleanupQuality = Math.min(100, 85 + improvement); // Base 85%, up to 100%
            }
        }
        
        return {
            totalTranscripts,
            totalWords,
            estimatedTimeSaved,
            cleanupQuality
        };
    } catch (error) {
        console.error("Failed to calculate stats:", error);
        return {
            totalTranscripts: 0,
            totalWords: 0,
            estimatedTimeSaved: 0,
            cleanupQuality: 0
        };
    }
}

/**
 * Loads settings from file.
 * @returns Promise that resolves with settings, or default settings if file doesn't exist.
 */
export async function loadSettings(): Promise<Settings> {
    try {
        const contents = await readTextFile('.oflow/settings.json', {
            baseDir: BaseDirectory.Home
        });
        return JSON.parse(contents) as Settings;
    } catch (error) {
        // Return default settings if file doesn't exist
        return {
            enableCleanup: true,
            enableMemory: false,
            provider: 'groq',  // Default to Groq (faster)
            shortcut: DEFAULT_SHORTCUT
        };
    }
}

/**
 * Saves settings to file.
 * @param settings - The settings to save.
 */
export async function saveSettings(settings: Settings): Promise<void> {
    try {
        console.log('[saveSettings] Checking if .oflow exists...');
        const dirExists = await exists('.oflow', { baseDir: BaseDirectory.Home });
        console.log('[saveSettings] Directory exists:', dirExists);

        if (!dirExists) {
            console.log('[saveSettings] Creating .oflow directory...');
            await mkdir('.oflow', { baseDir: BaseDirectory.Home });
        }

        console.log('[saveSettings] Writing settings.json...');
        await writeTextFile('.oflow/settings.json', JSON.stringify(settings, null, 2), {
            baseDir: BaseDirectory.Home
        });
        console.log('[saveSettings] Success!');
    } catch (error) {
        console.error('[saveSettings] Error:', error);
        throw new Error(`Failed to save settings: ${error}`);
    }
}

/**
 * Clears all transcript history.
 */
export async function clearHistory(): Promise<void> {
    try {
        // Ensure .oflow directory exists
        const dirExists = await exists('.oflow', { baseDir: BaseDirectory.Home });
        if (!dirExists) {
            await mkdir('.oflow', { baseDir: BaseDirectory.Home });
        }
        await writeTextFile('.oflow/transcripts.jsonl', '', {
            baseDir: BaseDirectory.Home
        });
    } catch (error) {
        throw new Error(`Failed to clear history: ${error}`);
    }
}

/**
 * Gets the current global shortcut.
 * @returns Promise that resolves with the current shortcut string.
 */
export async function getShortcut(): Promise<string> {
    try {
        return await invoke<string>('get_shortcut');
    } catch (error) {
        console.error('Failed to get shortcut:', error);
        return DEFAULT_SHORTCUT;
    }
}

/**
 * Sets and registers a new global shortcut.
 * @param shortcut - The shortcut string (e.g., "Super+I", "Ctrl+Shift+Space").
 */
export async function setShortcut(shortcut: string): Promise<void> {
    try {
        console.log('[setShortcut] Setting shortcut to:', shortcut);
        await invoke('set_shortcut', { shortcut });
        console.log('[setShortcut] Success!');
    } catch (error) {
        console.error('[setShortcut] Error:', error);
        throw new Error(`Failed to set shortcut: ${error}`);
    }
}

/**
 * Available shortcut presets.
 */
export const SHORTCUT_PRESETS = [
    { value: "Super+I", label: "Super + I (Default)" },
    { value: "Ctrl+Shift+Space", label: "Ctrl + Shift + Space" },
    { value: "Alt+Space", label: "Alt + Space" },
    { value: "F9", label: "F9" },
    { value: "Ctrl+Shift+R", label: "Ctrl + Shift + R" },
] as const;
