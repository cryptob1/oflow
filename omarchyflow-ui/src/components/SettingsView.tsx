import { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { loadSettings, saveSettings, clearHistory, type Settings } from "@/lib/api";
import { CheckCircle2, AlertCircle } from "lucide-react";

export function SettingsView() {
    const [settings, setSettings] = useState<Settings>({
        enableCleanup: true,
        enableMemory: false
    });
    const [isLoading, setIsLoading] = useState(true);
    const [saveStatus, setSaveStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
    const [clearStatus, setClearStatus] = useState<"idle" | "clearing" | "cleared" | "error">("idle");

    useEffect(() => {
        const load = async () => {
            setIsLoading(true);
            try {
                const loadedSettings = await loadSettings();
                setSettings(loadedSettings);
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

    return (
        <div className="space-y-6">
            <div>
                <h2 className="text-3xl font-bold tracking-tight">Settings</h2>
                <p className="text-muted-foreground">Manage your voice assistant preferences.</p>
            </div>

            <div className="grid gap-6">
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
                        <CardTitle>Storage</CardTitle>
                        <CardDescription>Manage your local data.</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="space-y-1">
                            <Label>Data Location</Label>
                            <div className="p-2 bg-muted rounded-md text-sm font-mono">
                                ~/.omarchyflow/transcripts.jsonl
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
