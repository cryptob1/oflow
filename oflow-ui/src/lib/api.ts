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
    elevenlabsApiKey?: string;
    deepgramApiKey?: string;
    provider?: 'openai' | 'groq' | 'elevenlabs' | 'deepgram';
    dictationHotkey?: 'copilot' | 'f8';
    enableOverlay?: boolean;
    pauseMediaWhileRecording?: boolean;
    enableSpokenPunctuation?: boolean;
    enableSpokenActions?: boolean;
    commandWakeWord?: string;
    fastModeMaxWords?: number;
    audioFeedbackTheme?: string;
    submitKeywords?: string[];
    // Second brain (note & meeting capture)
    brainVaultPath?: string;
    brainGit?: boolean;
    brainGitPush?: boolean;
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
 * One Markdown entry in the second-brain vault (a note-day or a meeting).
 */
export interface VaultEntry {
    name: string;
    content: string;
    modified: number; // unix seconds
}

/**
 * Reads the vault's notes/ or meetings/ folder (via the Rust backend, which
 * resolves the configured vault path). Newest first; [] if none yet.
 */
export async function readVault(kind: 'notes' | 'meetings' | 'initiatives' | 'dreams' | 'reminders' | 'journal'): Promise<VaultEntry[]> {
    try {
        return await invoke<VaultEntry[]>('read_vault', { kind });
    } catch (error) {
        console.error(`Failed to read vault ${kind}:`, error);
        return [];
    }
}

/**
 * Answer + cited sources from an "ask my brain" query.
 */
export interface AskResult {
    answer: string;
    sources: string[];
}

/**
 * Ask a natural-language question over the vault (runs the brain_search RAG
 * via the Rust backend and returns a synthesized, cited answer).
 */
export async function askBrain(query: string): Promise<AskResult> {
    return await invoke<AskResult>('ask_brain', { query });
}

/** An initiative (goal/project) with its goals and how many captures link to it. */
export interface Initiative {
    slug: string;
    title: string;
    status: string;
    goals: string[];
    linked: number;
}

export async function listInitiatives(): Promise<Initiative[]> {
    return await invoke<Initiative[]>('list_initiatives');
}

/** A synthesized coach-style status for one initiative. */
export interface InitiativeStatus {
    title: string;
    status: string;
    linked: number;
}

export async function initiativeStatus(name: string): Promise<InitiativeStatus> {
    return await invoke<InitiativeStatus>('initiative_status', { name });
}

/** Result of a consolidation "dream". */
export interface DreamResult {
    relinked: number;
    initiatives: number;
    stale: string[];
    suggestions: { name: string; why: string }[];
    journal: string;
}

export async function runDream(): Promise<DreamResult> {
    return await invoke<DreamResult>('run_dream');
}

/** Result of synthesizing today's journal from the dictation stream. */
export interface JournalResult {
    date: string;
    dictations: number;
    journal: string;
    skipped: boolean;
    reason: string;
}

export async function runJournal(): Promise<JournalResult> {
    return await invoke<JournalResult>('run_journal');
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
