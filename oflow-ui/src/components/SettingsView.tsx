import { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { loadSettings, saveSettings, clearHistory, type Settings } from "@/lib/api";
import { CheckCircle2, AlertCircle, Eye, EyeOff, Shield, Keyboard, Zap } from "lucide-react";

export function SettingsView() {
    const [settings, setSettings] = useState<Settings>({
        enableCleanup: true,
        enableMemory: false,
        provider: 'groq'
    });
    const [isLoading, setIsLoading] = useState(true);
    const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
    const [clearStatus, setClearStatus] = useState<"idle" | "clearing" | "cleared" | "error">("idle");
    const [showApiKey, setShowApiKey] = useState(false);
    const [showGroqKey, setShowGroqKey] = useState(false);
    const [apiKeyInput, setApiKeyInput] = useState("");
    const [groqKeyInput, setGroqKeyInput] = useState("");

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
            } catch (error) {
                console.error("Failed to load settings:", error);
            } finally {
                setIsLoading(false);
            }
        };
        load();
    }, []);

    const handleSettingChange = async (key: keyof Settings, value: boolean) => {
        const newSettings = { ...settings, [key]: value };
        setSettings(newSettings);
        
        setSaveStatus("saving");
        try {
            await saveSettings(newSettings);
            setSaveStatus("saved");
            setTimeout(() => setSaveStatus("idle"), 2000);
        } catch (error) {
            console.error("Failed to save settings:", error);
            setSaveStatus("error");
            setTimeout(() => setSaveStatus("idle"), 3000);
        }
    };

    const handleClearHistory = async () => {
        if (!confirm("Are you sure you want to clear all transcript history? This cannot be undone.")) {
            return;
        }

        setClearStatus("clearing");
        try {
            await clearHistory();
            setClearStatus("cleared");
            setTimeout(() => setClearStatus("idle"), 2000);
        } catch (error) {
            console.error("Failed to clear history:", error);
            setClearStatus("error");
            setTimeout(() => setClearStatus("idle"), 3000);
        }
    };

    const handleSaveApiKey = async () => {
        const newSettings = { ...settings, openaiApiKey: apiKeyInput };
        setSettings(newSettings);

        setSaveStatus("saving");
        try {
            await saveSettings(newSettings);
            setSaveStatus("saved");
            setTimeout(() => setSaveStatus("idle"), 2000);
        } catch (error) {
            console.error("Failed to save API key:", error);
            setSaveStatus("error");
            setTimeout(() => setSaveStatus("idle"), 3000);
        }
    };

    const handleSaveGroqKey = async () => {
        const newSettings = { ...settings, groqApiKey: groqKeyInput };
        setSettings(newSettings);

        setSaveStatus("saving");
        try {
            await saveSettings(newSettings);
            setSaveStatus("saved");
            setTimeout(() => setSaveStatus("idle"), 2000);
        } catch (error) {
            console.error("Failed to save Groq API key:", error);
            setSaveStatus("error");
            setTimeout(() => setSaveStatus("idle"), 3000);
        }
    };

    const handleProviderChange = async (provider: 'openai' | 'groq') => {
        const newSettings = { ...settings, provider };
        setSettings(newSettings);

        setSaveStatus("saving");
        try {
            await saveSettings(newSettings);
            setSaveStatus("saved");
            setTimeout(() => setSaveStatus("idle"), 2000);
        } catch (error) {
            console.error("Failed to save provider:", error);
            setSaveStatus("error");
            setTimeout(() => setSaveStatus("idle"), 3000);
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
                                <Button onClick={handleSaveGroqKey} disabled={isLoading || saveStatus === "saving"}>
                                    {saveStatus === "saving" ? "Saving..." : "Save"}
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
                                <Button onClick={handleSaveApiKey} disabled={isLoading || saveStatus === "saving"}>
                                    {saveStatus === "saving" ? "Saving..." : "Save"}
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
                        <div className="p-3 bg-muted rounded-lg">
                            <p className="text-sm font-medium mb-2">Current Shortcut: <code className="px-2 py-1 bg-background rounded">Super + I</code></p>
                            <p className="text-xs text-muted-foreground">
                                Hold to record, release to stop. On Hyprland/Wayland, this is configured via your window manager bindings.
                            </p>
                        </div>
                        <div className="text-xs text-muted-foreground">
                            <p className="font-medium mb-1">To change the shortcut:</p>
                            <p>Edit <code className="px-1 bg-muted rounded">~/.config/hypr/bindings.conf</code></p>
                        </div>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle>Pipeline Configuration</CardTitle>
                        <CardDescription>Control how your audio is processed.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-6">
                        {saveStatus === "saved" && (
                            <div className="flex items-center gap-2 text-sm text-green-600 dark:text-green-400">
                                <CheckCircle2 className="h-4 w-4" />
                                Settings saved
                            </div>
                        )}
                        {saveStatus === "error" && (
                            <div className="flex items-center gap-2 text-sm text-destructive">
                                <AlertCircle className="h-4 w-4" />
                                Failed to save settings
                            </div>
                        )}
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
                        <div className="pt-2 space-y-2">
                            {clearStatus === "cleared" && (
                                <div className="flex items-center gap-2 text-sm text-green-600 dark:text-green-400">
                                    <CheckCircle2 className="h-4 w-4" />
                                    History cleared
                                </div>
                            )}
                            {clearStatus === "error" && (
                                <div className="flex items-center gap-2 text-sm text-destructive">
                                    <AlertCircle className="h-4 w-4" />
                                    Failed to clear history
                                </div>
                            )}
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
