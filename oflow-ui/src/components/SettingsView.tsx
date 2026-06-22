import { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { loadSettings, saveSettings, clearHistory, type Settings } from "@/lib/api";
import { Eye, EyeOff, Shield, Zap, Keyboard } from "lucide-react";
import { useToast } from "@/components/ui/toast";

// Mirrors SPOKEN_ACTIONS in oflow.py. Shown read-only so users can see what
// they can say; the backend is the source of truth for what actually fires.
// {w} is replaced with the configured wake word at render time.
const SPOKEN_COMMANDS: { say: string; does: string; key: string }[] = [
    { say: "“{w} enter” / “send it”", does: "Press Enter", key: "↵" },
    { say: "“{w} new line”", does: "Press Enter", key: "↵" },
    { say: "“{w} new paragraph”", does: "Press Enter twice", key: "¶" },
    { say: "“{w} tab”", does: "Press Tab", key: "⇥" },
    { say: "“{w} escape”", does: "Press Esc", key: "⎋" },
    { say: "“{w} scratch that”", does: "Delete last dictation", key: "⌫" },
    { say: "“{w} select all”", does: "Ctrl+A", key: "⌘" },
    { say: "“{w} undo” / “redo”", does: "Ctrl+Z / Ctrl+Shift+Z", key: "↶" },
    { say: "“{w} delete word”", does: "Delete previous word", key: "⌫" },
];

export function SettingsView() {
    const { showToast } = useToast();
    const [settings, setSettings] = useState<Settings>({
        enableCleanup: true,
        provider: 'groq'
    });
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    const [clearStatus, setClearStatus] = useState<"idle" | "clearing">("idle");
    const [showApiKey, setShowApiKey] = useState(false);
    const [showGroqKey, setShowGroqKey] = useState(false);
    const [apiKeyInput, setApiKeyInput] = useState("");
    const [groqKeyInput, setGroqKeyInput] = useState("");
    const [submitKeywordsInput, setSubmitKeywordsInput] = useState("press enter, hit enter");
    const [fastWordsInput, setFastWordsInput] = useState("8");
    const [wakeWordInput, setWakeWordInput] = useState("oflow");
    const wakeWord = (settings.commandWakeWord || "oflow").trim() || "oflow";

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
                if (loadedSettings.submitKeywords && loadedSettings.submitKeywords.length) {
                    setSubmitKeywordsInput(loadedSettings.submitKeywords.join(", "));
                }
                if (typeof loadedSettings.fastModeMaxWords === "number" && loadedSettings.fastModeMaxWords > 0) {
                    setFastWordsInput(String(loadedSettings.fastModeMaxWords));
                }
                if (loadedSettings.commandWakeWord) {
                    setWakeWordInput(loadedSettings.commandWakeWord);
                }
            } catch (error) {
                console.error("Failed to load settings:", error);
            } finally {
                setIsLoading(false);
            }
        };
        load();
    }, []);

    const handleSettingChange = async (key: keyof Settings, value: boolean | string | string[] | number) => {
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

    const handleSaveSubmitKeywords = async () => {
        const phrases = submitKeywordsInput.split(",").map(s => s.trim()).filter(Boolean);
        await handleSettingChange("submitKeywords", phrases);
    };

    const handleSaveFastWords = async () => {
        const n = parseInt(fastWordsInput, 10);
        const clamped = Number.isFinite(n) && n > 0 ? n : 8;
        setFastWordsInput(String(clamped));
        await handleSettingChange("fastModeMaxWords", clamped);
    };

    const handleSaveWakeWord = async () => {
        const w = wakeWordInput.trim().toLowerCase() || "oflow";
        setWakeWordInput(w);
        await handleSettingChange("commandWakeWord", w);
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
                        <CardTitle>Pipeline Configuration</CardTitle>
                        <CardDescription>Control how your audio is processed.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-6">
                        <div className="flex items-center justify-between space-x-2">
                            <div className="space-y-1">
                                <Label htmlFor="cleanup">AI Cleanup</Label>
                                <p className="text-sm text-muted-foreground">Automatically fix grammar and punctuation.</p>
                            </div>
                            <Switch
                                id="cleanup"
                                checked={settings.enableCleanup}
                                onCheckedChange={(checked) => handleSettingChange("enableCleanup", checked)}
                                disabled={isLoading}
                            />
                        </div>

                        <div className={`flex items-center justify-between space-x-2 ${settings.enableCleanup ? '' : 'opacity-50'}`}>
                            <div className="space-y-1">
                                <Label htmlFor="fastmode">Fast mode</Label>
                                <p className="text-sm text-muted-foreground">Skip AI cleanup on short dictations (≤ {settings.fastModeMaxWords ?? 8} words) for instant output. Saves ~200&nbsp;ms.</p>
                            </div>
                            <Switch
                                id="fastmode"
                                checked={(settings.fastModeMaxWords ?? 8) > 0}
                                onCheckedChange={(c) => handleSettingChange("fastModeMaxWords", c ? (parseInt(fastWordsInput, 10) || 8) : 0)}
                                disabled={isLoading || !settings.enableCleanup}
                            />
                        </div>

                        {settings.enableCleanup && (settings.fastModeMaxWords ?? 8) > 0 && (
                            <div className="space-y-2">
                                <Label htmlFor="fastwords">Fast mode word limit</Label>
                                <p className="text-sm text-muted-foreground">Dictations with this many words or fewer skip cleanup.</p>
                                <div className="flex gap-2">
                                    <Input id="fastwords" type="number" min={1} className="w-24"
                                        value={fastWordsInput}
                                        onChange={(e) => setFastWordsInput(e.target.value)}
                                        disabled={isLoading} />
                                    <Button onClick={handleSaveFastWords} disabled={isLoading}>Save</Button>
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Dictation &amp; Feedback</CardTitle>
                        <CardDescription>Recording feedback and on-screen behavior.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-6">
                        <div className="flex items-center justify-between space-x-2">
                            <div className="space-y-1">
                                <Label htmlFor="overlay">Recording overlay</Label>
                                <p className="text-sm text-muted-foreground">Show the on-screen audio level meter while recording.</p>
                            </div>
                            <Switch id="overlay" checked={settings.enableOverlay ?? true}
                                onCheckedChange={(c) => handleSettingChange("enableOverlay", c)} disabled={isLoading} />
                        </div>
                        <div className="flex items-center justify-between space-x-2">
                            <div className="space-y-1">
                                <Label htmlFor="pausemedia">Pause media while recording</Label>
                                <p className="text-sm text-muted-foreground">Pause playing music/video so it doesn&apos;t bleed into the mic.</p>
                            </div>
                            <Switch id="pausemedia" checked={settings.pauseMediaWhileRecording ?? true}
                                onCheckedChange={(c) => handleSettingChange("pauseMediaWhileRecording", c)} disabled={isLoading} />
                        </div>
                        <div className="flex items-center justify-between space-x-2">
                            <div className="space-y-1">
                                <Label htmlFor="sounds">Sound effects</Label>
                                <p className="text-sm text-muted-foreground">Play start/stop beeps. Off = silent (rely on the overlay).</p>
                            </div>
                            <Switch id="sounds" checked={(settings.audioFeedbackTheme ?? "default") !== "silent"}
                                onCheckedChange={(c) => handleSettingChange("audioFeedbackTheme", c ? "default" : "silent")} disabled={isLoading} />
                        </div>
                        <div className="flex items-center justify-between space-x-2">
                            <div className="space-y-1">
                                <Label htmlFor="punct">Spoken punctuation</Label>
                                <p className="text-sm text-muted-foreground">Say &quot;period&quot; or &quot;new line&quot; to insert symbols.</p>
                            </div>
                            <Switch id="punct" checked={settings.enableSpokenPunctuation ?? false}
                                onCheckedChange={(c) => handleSettingChange("enableSpokenPunctuation", c)} disabled={isLoading} />
                        </div>
                        <div className={`space-y-2 ${(settings.enableSpokenActions ?? true) ? 'opacity-50' : ''}`}>
                            <Label htmlFor="submitkw">Submit phrases</Label>
                            <p className="text-sm text-muted-foreground">Legacy: saying one of these at the end presses Enter after pasting. Comma-separated. Only used when <strong>Spoken commands</strong> is off.</p>
                            <div className="flex gap-2">
                                <Input id="submitkw" value={submitKeywordsInput}
                                    onChange={(e) => setSubmitKeywordsInput(e.target.value)}
                                    placeholder="press enter, hit enter" disabled={isLoading} />
                                <Button onClick={handleSaveSubmitKeywords} disabled={isLoading}>Save</Button>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <Keyboard className="h-5 w-5" />
                            Spoken Commands
                        </CardTitle>
                        <CardDescription>Say the wake word + a command and oflow presses the real key — anywhere in a dictation.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex items-center justify-between space-x-2">
                            <div className="space-y-1">
                                <Label htmlFor="actions">Enable spoken commands</Label>
                                <p className="text-sm text-muted-foreground">Turn “{wakeWord} …” phrases into real keystrokes. Needs ydotool.</p>
                            </div>
                            <Switch id="actions" checked={settings.enableSpokenActions ?? true}
                                onCheckedChange={(c) => handleSettingChange("enableSpokenActions", c)} disabled={isLoading} />
                        </div>

                        <div className={`space-y-2 ${(settings.enableSpokenActions ?? true) ? '' : 'opacity-50'}`}>
                            <Label htmlFor="wakeword">Wake word</Label>
                            <p className="text-sm text-muted-foreground">Every command starts with this word, so normal speech is never mistaken for a command. Default “oflow”; pick something Whisper hears reliably.</p>
                            <div className="flex gap-2">
                                <Input id="wakeword" className="w-40" value={wakeWordInput}
                                    onChange={(e) => setWakeWordInput(e.target.value)}
                                    placeholder="oflow" disabled={isLoading || !(settings.enableSpokenActions ?? true)} />
                                <Button onClick={handleSaveWakeWord} disabled={isLoading || !(settings.enableSpokenActions ?? true)}>Save</Button>
                            </div>
                        </div>

                        <div className={`rounded-lg border divide-y ${(settings.enableSpokenActions ?? true) ? '' : 'opacity-50'}`}>
                            {SPOKEN_COMMANDS.map((cmd) => (
                                <div key={cmd.say} className="flex items-center justify-between gap-3 px-3 py-2">
                                    <span className="text-sm">{cmd.say.replace(/\{w\}/g, wakeWord)}</span>
                                    <span className="flex items-center gap-2 text-sm text-muted-foreground">
                                        {cmd.does}
                                        <kbd className="px-2 py-0.5 rounded bg-muted font-mono text-foreground">{cmd.key}</kbd>
                                    </span>
                                </div>
                            ))}
                        </div>
                        <p className="text-xs text-muted-foreground">
                            Requiring “{wakeWord}” first means ordinary speech like “select all the files” stays literal text — only “{wakeWord} select all” fires the command.
                        </p>
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
                        <div className="space-y-1">
                            <Label>Hotkey</Label>
                            <div className="p-2 bg-muted rounded-md text-sm font-mono">
                                F8 (push-to-talk: hold to record)
                            </div>
                            <p className="text-xs text-muted-foreground">
                                Configured via Hyprland. Edit ~/.config/hypr/bindings.conf to change.
                            </p>
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
