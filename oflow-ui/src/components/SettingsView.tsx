import { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { loadSettings, saveSettings, clearHistory, getShortcut, setShortcut, SHORTCUT_PRESETS, DEFAULT_SHORTCUT, type Settings } from "@/lib/api";
import { Eye, EyeOff, Shield, Keyboard, Zap, Loader2 } from "lucide-react";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/toast";

interface SettingsViewProps {
    onShortcutChange?: (shortcut: string) => void;
}

export function SettingsView({ onShortcutChange }: SettingsViewProps) {
    const { showToast } = useToast();
    const [settings, setSettings] = useState<Settings>({
        enableCleanup: true,
        enableMemory: false,
        provider: 'groq',
        transcriptionMode: 'single'
    });
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [clearStatus, setClearStatus] = useState<"idle" | "clearing" | "cleared" | "error">("idle");
    const [showApiKey, setShowApiKey] = useState(false);
    const [showGroqKey, setShowGroqKey] = useState(false);
    const [apiKeyInput, setApiKeyInput] = useState("");
    const [groqKeyInput, setGroqKeyInput] = useState("");
    const [currentShortcut, setCurrentShortcut] = useState(DEFAULT_SHORTCUT);
    const [shortcutSaving, setShortcutSaving] = useState(false);

    useEffect(() => {
        const load = async () => {
            setIsLoading(true);
            try {
                const loadedSettings = await loadSettings();
                setSettings(loadedSettings);
                if (loadedSettings.openaiApiKey) {
                    setApiKeyInput(loadedSettings.openaiApiKey);
                }
                if (loadedSettings.groqApiKey) {
                    setGroqKeyInput(loadedSettings.groqApiKey);
                }
                // Load current shortcut
                const shortcut = await getShortcut();
                setCurrentShortcut(shortcut);
                onShortcutChange?.(shortcut);
            } catch (error) {
                console.error("Failed to load settings:", error);
            } finally {
                setIsLoading(false);
            }
        };
        load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const handleSettingChange = async (key: keyof Settings, value: boolean) => {
        const newSettings = { ...settings, [key]: value };
        setSettings(newSettings);

        try {
            await saveSettings(newSettings);
            showToast("Settings saved", "success");
        } catch (error) {
            console.error("Failed to save settings:", error);
            showToast("Failed to save settings", "error");
        }
    };

    const handleClearHistory = async () => {
        if (!confirm("Are you sure you want to clear all transcript history? This cannot be undone.")) {
            return;
        }

        setClearStatus("clearing");
        try {
            await clearHistory();
            setClearStatus("idle");
            showToast("History cleared", "success");
        } catch (error) {
            console.error("Failed to clear history:", error);
            setClearStatus("idle");
            showToast("Failed to clear history", "error");
        }
    };

    const handleSaveApiKey = async () => {
        const newSettings = { ...settings, openaiApiKey: apiKeyInput };
        setSettings(newSettings);
        setIsSaving(true);

        try {
            await saveSettings(newSettings);
            showToast("OpenAI API key saved", "success");
        } catch (error) {
            console.error("Failed to save API key:", error);
            showToast("Failed to save API key", "error");
        } finally {
            setIsSaving(false);
        }
    };

    const handleSaveGroqKey = async () => {
        const newSettings = { ...settings, groqApiKey: groqKeyInput };
        setSettings(newSettings);
        setIsSaving(true);

        try {
            await saveSettings(newSettings);
            showToast("Groq API key saved", "success");
        } catch (error) {
            console.error("Failed to save Groq API key:", error);
            showToast("Failed to save API key", "error");
        } finally {
            setIsSaving(false);
        }
    };

    const handleProviderChange = async (provider: 'openai' | 'groq') => {
        const newSettings = { ...settings, provider };
        setSettings(newSettings);

        try {
            await saveSettings(newSettings);
            showToast(`Switched to ${provider === 'groq' ? 'Groq' : 'OpenAI'}`, "success");
        } catch (error) {
            console.error("Failed to save provider:", error);
            showToast("Failed to change provider", "error");
        }
    };

    const handleShortcutChange = async (shortcut: string) => {
        setShortcutSaving(true);
        try {
            await setShortcut(shortcut);
            setCurrentShortcut(shortcut);
            onShortcutChange?.(shortcut);
            const newSettings = { ...settings, shortcut };
            await saveSettings(newSettings);
            showToast(`Shortcut changed to ${shortcut}`, "success");
        } catch (error) {
            console.error("Failed to set shortcut:", error);
            showToast("Failed to change shortcut", "error");
        } finally {
            setShortcutSaving(false);
        }
    };

    const handleTranscriptionModeChange = async (mode: 'single' | 'streaming') => {
        const newSettings = { ...settings, transcriptionMode: mode };
        setSettings(newSettings);

        try {
            await saveSettings(newSettings);
            showToast(`Transcription mode: ${mode === 'streaming' ? 'Streaming (faster)' : 'Single request'}`, "success");
        } catch (error) {
            console.error("Failed to save transcription mode:", error);
            showToast("Failed to change mode", "error");
        }
    };

    return (
        <div className="space-y-6">
            <div>
                <h2 className="text-3xl font-bold tracking-tight">Settings</h2>
                <p className="text-muted-foreground">Manage your voice assistant preferences.</p>
            </div>

            <div className="grid gap-6">
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <Zap className="h-5 w-5" />
                            AI Provider
                        </CardTitle>
                        <CardDescription>Choose your transcription and cleanup provider.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-6">
                        {/* Provider Selection */}
                        <div className="space-y-3">
                            <Label>Provider</Label>
                            <div className="grid grid-cols-2 gap-3">
                                <button
                                    onClick={() => handleProviderChange('groq')}
                                    className={`p-4 rounded-lg border-2 text-left transition-all ${
                                        settings.provider === 'groq'
                                            ? 'border-primary bg-primary/5'
                                            : 'border-muted hover:border-muted-foreground/50'
                                    }`}
                                    disabled={isLoading}
                                >
                                    <div className="font-medium flex items-center gap-2">
                                        Groq
                                        <span className="text-xs px-2 py-0.5 bg-green-500/20 text-green-600 dark:text-green-400 rounded-full">
                                            200x faster
                                        </span>
                                    </div>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        Whisper Turbo + Llama 3.1 8B
                                    </p>
                                </button>
                                <button
                                    onClick={() => handleProviderChange('openai')}
                                    className={`p-4 rounded-lg border-2 text-left transition-all ${
                                        settings.provider === 'openai'
                                            ? 'border-primary bg-primary/5'
                                            : 'border-muted hover:border-muted-foreground/50'
                                    }`}
                                    disabled={isLoading}
                                >
                                    <div className="font-medium">OpenAI</div>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        Whisper + GPT-4o-mini
                                    </p>
                                </button>
                            </div>
                        </div>

                        {/* Groq API Key */}
                        <div className={`space-y-2 ${settings.provider !== 'groq' ? 'opacity-50' : ''}`}>
                            <Label htmlFor="groqKey">Groq API Key</Label>
                            <div className="flex gap-2">
                                <div className="relative flex-1">
                                    <Input
                                        id="groqKey"
                                        type={showGroqKey ? "text" : "password"}
                                        placeholder="gsk_..."
                                        value={groqKeyInput}
                                        onChange={(e) => setGroqKeyInput(e.target.value)}
                                        disabled={isLoading}
                                    />
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="sm"
                                        className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
                                        onClick={() => setShowGroqKey(!showGroqKey)}
                                    >
                                        {showGroqKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                    </Button>
                                </div>
                                <Button onClick={handleSaveGroqKey} disabled={isLoading || isSaving}>
                                    {isSaving ? "Saving..." : "Save"}
                                </Button>
                            </div>
                            <p className="text-xs text-muted-foreground">
                                Get your free API key from <a href="https://console.groq.com/keys" target="_blank" rel="noopener noreferrer" className="underline">console.groq.com</a>
                            </p>
                        </div>

                        {/* OpenAI API Key */}
                        <div className={`space-y-2 ${settings.provider !== 'openai' ? 'opacity-50' : ''}`}>
                            <Label htmlFor="apiKey">OpenAI API Key</Label>
                            <div className="flex gap-2">
                                <div className="relative flex-1">
                                    <Input
                                        id="apiKey"
                                        type={showApiKey ? "text" : "password"}
                                        placeholder="sk-..."
                                        value={apiKeyInput}
                                        onChange={(e) => setApiKeyInput(e.target.value)}
                                        disabled={isLoading}
                                    />
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="sm"
                                        className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
                                        onClick={() => setShowApiKey(!showApiKey)}
                                    >
                                        {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                    </Button>
                                </div>
                                <Button onClick={handleSaveApiKey} disabled={isLoading || isSaving}>
                                    {isSaving ? "Saving..." : "Save"}
                                </Button>
                            </div>
                            <p className="text-xs text-muted-foreground">
                                Get your API key from <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener noreferrer" className="underline">platform.openai.com</a>
                            </p>
                        </div>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <Keyboard className="h-5 w-5" />
                            Keyboard Shortcut
                        </CardTitle>
                        <CardDescription>Push-to-talk hotkey for voice recording.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="space-y-2">
                            <Label>Global Hotkey</Label>
                            <Select
                                value={currentShortcut}
                                onValueChange={handleShortcutChange}
                                disabled={isLoading || shortcutSaving}
                            >
                                <SelectTrigger className="w-full">
                                    {shortcutSaving ? (
                                        <div className="flex items-center gap-2">
                                            <Loader2 className="h-4 w-4 animate-spin" />
                                            Updating...
                                        </div>
                                    ) : (
                                        <SelectValue placeholder="Select a shortcut" />
                                    )}
                                </SelectTrigger>
                                <SelectContent>
                                    {SHORTCUT_PRESETS.map((preset) => (
                                        <SelectItem key={preset.value} value={preset.value}>
                                            {preset.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <p className="text-xs text-muted-foreground">
                            Hold the key to record, release to stop and transcribe.
                        </p>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Pipeline Configuration</CardTitle>
                        <CardDescription>Control how your audio is processed.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-6">
                        {/* Transcription Mode */}
                        <div className="space-y-3">
                            <Label>Transcription Mode</Label>
                            <div className="grid grid-cols-2 gap-3">
                                <button
                                    onClick={() => handleTranscriptionModeChange('single')}
                                    className={`p-4 rounded-lg border-2 text-left transition-all ${
                                        settings.transcriptionMode === 'single'
                                            ? 'border-primary bg-primary/5'
                                            : 'border-muted hover:border-muted-foreground/50'
                                    }`}
                                    disabled={isLoading}
                                >
                                    <div className="font-medium">Single Request</div>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        Send audio after recording stops. More reliable.
                                    </p>
                                </button>
                                <button
                                    onClick={() => handleTranscriptionModeChange('streaming')}
                                    className={`p-4 rounded-lg border-2 text-left transition-all ${
                                        settings.transcriptionMode === 'streaming'
                                            ? 'border-primary bg-primary/5'
                                            : 'border-muted hover:border-muted-foreground/50'
                                    }`}
                                    disabled={isLoading}
                                >
                                    <div className="font-medium flex items-center gap-2">
                                        Streaming
                                        <span className="text-xs px-2 py-0.5 bg-blue-500/20 text-blue-600 dark:text-blue-400 rounded-full">
                                            faster
                                        </span>
                                    </div>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        Upload chunks during recording. Lower latency.
                                    </p>
                                </button>
                            </div>
                        </div>

                        <div className="flex items-center justify-between space-x-2">
                            <div className="space-y-1">
                                <Label htmlFor="cleanup">AI Cleanup (GPT-4o-mini)</Label>
                                <p className="text-sm text-muted-foreground">Automatically fix grammar and punctuation.</p>
                            </div>
                            <Switch
                                id="cleanup"
                                checked={settings.enableCleanup}
                                onCheckedChange={(checked) => handleSettingChange("enableCleanup", checked)}
                                disabled={isLoading}
                            />
                        </div>
                        <div className="flex items-center justify-between space-x-2">
                            <div className="space-y-1">
                                <Label htmlFor="memory">Memory System</Label>
                                <p className="text-sm text-muted-foreground">Learn from your past corrections over time.</p>
                            </div>
                            <Switch
                                id="memory"
                                checked={settings.enableMemory}
                                onCheckedChange={(checked) => handleSettingChange("enableMemory", checked)}
                                disabled={isLoading}
                            />
                        </div>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <Shield className="h-5 w-5" />
                            Storage & Privacy
                        </CardTitle>
                        <CardDescription>Your data stays on your device.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="p-3 bg-green-500/10 border border-green-500/20 rounded-lg">
                            <p className="text-sm text-green-700 dark:text-green-400">
                                <strong>Your Data, Your Control</strong> - All transcripts and settings are stored locally on your machine.
                                oflow has no cloud backend or analytics. Audio is only sent to your chosen provider (Groq/OpenAI) for transcription.
                            </p>
                        </div>
                        <div className="space-y-1">
                            <Label>Data Location</Label>
                            <div className="p-2 bg-muted rounded-md text-sm font-mono">
                                ~/.oflow/transcripts.jsonl
                            </div>
                        </div>
                        <div className="pt-2">
                            <Button
                                variant="outline"
                                className="text-destructive hover:text-destructive"
                                onClick={handleClearHistory}
                                disabled={clearStatus === "clearing"}
                            >
                                {clearStatus === "clearing" ? "Clearing..." : "Clear All History"}
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}
