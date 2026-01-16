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
    openaiApiKey?: string;
    groqApiKey?: string;
    provider?: 'openai' | 'groq';
}

/**
 * Checks if the backend is running.
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
        return [];
    }
}

/**
 * Gets statistics from transcripts.
 */
export interface TranscriptStats {
    totalTranscripts: number;
    totalWords: number;
    estimatedTimeSaved: number; // in hours
}

export async function getTranscriptStats(): Promise<TranscriptStats> {
    try {
        const transcripts = await getTranscripts();
        
        const totalTranscripts = transcripts.length;
        
        const totalWords = transcripts.reduce((sum, t) => {
            return sum + (t.cleaned?.split(/\s+/).filter(w => w.length > 0).length || 0);
        }, 0);
        
        // Estimate time saved: assume 40 WPM typing speed
        const wordsPerMinute = 40;
        const minutesSaved = totalWords / wordsPerMinute;
        const estimatedTimeSaved = minutesSaved / 60;
        
        return {
            totalTranscripts,
            totalWords,
            estimatedTimeSaved,
        };
    } catch (error) {
        console.error("Failed to calculate stats:", error);
        return {
            totalTranscripts: 0,
            totalWords: 0,
            estimatedTimeSaved: 0,
        };
    }
}

/**
 * Loads settings from file.
 */
export async function loadSettings(): Promise<Settings> {
    try {
        const contents = await readTextFile('.oflow/settings.json', {
            baseDir: BaseDirectory.Home
        });
        return JSON.parse(contents) as Settings;
    } catch (error) {
        return {
            enableCleanup: true,
            provider: 'groq',
        };
    }
}

/**
 * Saves settings to file.
 */
export async function saveSettings(settings: Settings): Promise<void> {
    try {
        const dirExists = await exists('.oflow', { baseDir: BaseDirectory.Home });
        if (!dirExists) {
            await mkdir('.oflow', { baseDir: BaseDirectory.Home });
        }
        await writeTextFile('.oflow/settings.json', JSON.stringify(settings, null, 2), {
            baseDir: BaseDirectory.Home
        });
    } catch (error) {
        throw new Error(`Failed to save settings: ${error}`);
    }
}

/**
 * Clears all transcript history.
 */
export async function clearHistory(): Promise<void> {
    try {
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
