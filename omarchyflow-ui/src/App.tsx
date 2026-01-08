import { useState, useEffect } from "react";
import { LayoutDashboard, History, Settings, Mic, Play, Pause, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Dashboard } from "@/components/Dashboard";
import { HistoryView } from "@/components/HistoryView";
import { SettingsView } from "@/components/SettingsView";
import { toggleRecording, getRecordingStatus, checkBackendStatus, showWindow } from "@/lib/api";

// Check if running in Tauri
const isTauri = typeof window !== 'undefined' && '__TAURI__' in window;

export default function App() {
  const [activeTab, setActiveTab] = useState<"dashboard" | "history" | "settings">("dashboard");
  const [isRecording, setIsRecording] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [backendRunning, setBackendRunning] = useState(true);

  // Show window on mount (Tauri only)
  // Note: Global hotkey (Super+I) is handled by Hyprland bindings, not Tauri
  useEffect(() => {
    if (!isTauri) return;

    const setupTauri = async () => {
      try {
        await showWindow();
      } catch (e) {
        console.error("Failed to show window:", e);
      }
    };

    setupTauri();
  }, []);

  // Check backend status and recording status on mount (Tauri only)
  useEffect(() => {
    if (!isTauri) {
      setBackendRunning(false);
      return;
    }

    const checkStatus = async () => {
      const running = await checkBackendStatus();
      setBackendRunning(running);

      if (running) {
        try {
          const status = await getRecordingStatus();
          setIsRecording(status);
        } catch (e) {
          console.error("Failed to get recording status:", e);
        }
      }
    };

    checkStatus();
    // Poll backend status every 5 seconds
    const interval = setInterval(checkStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleToggleRecording = async () => {
    setIsLoading(true);
    setError(null);
    
    try {
      const newStatus = await toggleRecording();
      setIsRecording(newStatus);
    } catch (e) {
      const errorMessage = e instanceof Error ? e.message : "Failed to toggle recording";
      setError(errorMessage);
      console.error("Recording error:", e);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex h-screen bg-background text-foreground overflow-hidden">
      {/* Sidebar */}
      <div className="w-64 border-r bg-card p-4 flex flex-col">
        <div className="flex items-center gap-2 mb-8 px-2">
          <div className="h-8 w-8 rounded-full bg-primary flex items-center justify-center">
            <Mic className="h-4 w-4 text-primary-foreground" />
          </div>
          <h1 className="font-bold text-xl tracking-tight">OmarchyFlow</h1>
        </div>

        <nav className="space-y-2 flex-1">
          <NavButton
            active={activeTab === "dashboard"}
            onClick={() => setActiveTab("dashboard")}
            icon={<LayoutDashboard className="h-4 w-4" />}
          >
            Dashboard
          </NavButton>
          <NavButton
            active={activeTab === "history"}
            onClick={() => setActiveTab("history")}
            icon={<History className="h-4 w-4" />}
          >
            History
          </NavButton>
          <NavButton
            active={activeTab === "settings"}
            onClick={() => setActiveTab("settings")}
            icon={<Settings className="h-4 w-4" />}
          >
            Settings
          </NavButton>
        </nav>

        <div className="mt-auto">
          <div className="bg-muted/50 rounded-lg p-4 border">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium">Status</span>
              <span className={cn(
                "h-2 w-2 rounded-full",
                !backendRunning ? "bg-yellow-500" : isRecording ? "bg-red-500 animate-pulse" : "bg-green-500"
              )} />
            </div>
            <p className="text-xs text-muted-foreground mb-3">
              {!backendRunning 
                ? "Backend not running" 
                : isRecording 
                  ? "Listening..." 
                  : "Ready to transcribe"}
            </p>
            {error && (
              <div className="mb-2 p-2 bg-destructive/10 border border-destructive/20 rounded text-xs text-destructive flex items-center gap-1">
                <AlertCircle className="h-3 w-3" />
                {error}
              </div>
            )}
            <Button
              size="sm"
              className={cn("w-full", isRecording ? "bg-red-500 hover:bg-red-600" : "")}
              onClick={handleToggleRecording}
              disabled={isLoading || !backendRunning}
            >
              {isLoading ? (
                <>Loading...</>
              ) : (
                <>
                  {isRecording ? <Pause className="h-3 w-3 mr-2" /> : <Play className="h-3 w-3 mr-2" />}
                  {isRecording ? "Stop" : "Start"}
                </>
              )}
            </Button>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <main className="flex-1 overflow-auto bg-muted/10 p-8">
        {activeTab === "dashboard" && <Dashboard />}
        {activeTab === "history" && <HistoryView />}
        {activeTab === "settings" && <SettingsView />}
      </main>
    </div>
  );
}

function NavButton({ active, onClick, icon, children }: { active: boolean, onClick: () => void, icon: React.ReactNode, children: React.ReactNode }) {
  return (
    <Button
      variant={active ? "secondary" : "ghost"}
      className={cn("w-full justify-start font-medium", active ? "bg-primary/10 text-primary hover:bg-primary/20" : "")}
      onClick={onClick}
    >
      {icon}
      <span className="ml-2">{children}</span>
    </Button>
  );
}
