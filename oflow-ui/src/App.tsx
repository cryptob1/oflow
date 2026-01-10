import { useState, useEffect } from "react";
import { LayoutDashboard, History, Settings, Mic } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ToastProvider } from "@/components/ui/toast";
import { Dashboard } from "@/components/Dashboard";
import { HistoryView } from "@/components/HistoryView";
import { SettingsView } from "@/components/SettingsView";
import { showWindow } from "@/lib/api";

// Check if running in Tauri
const isTauri = typeof window !== 'undefined' && '__TAURI__' in window;

export default function App() {
  const [activeTab, setActiveTab] = useState<"dashboard" | "history" | "settings">("dashboard");

  // Show window on mount (Tauri only)
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

  return (
    <ToastProvider>
      <div className="flex h-screen bg-background text-foreground overflow-hidden">
        {/* Sidebar */}
        <div className="w-64 border-r bg-card p-4 flex flex-col">
          <div className="flex items-center gap-2 mb-8 px-2">
            <div className="h-8 w-8 rounded-full bg-primary flex items-center justify-center">
              <Mic className="h-4 w-4 text-primary-foreground" />
            </div>
            <h1 className="font-bold text-xl tracking-tight">oflow</h1>
          </div>

          <nav className="space-y-2">
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

          <div className="mt-auto text-xs text-muted-foreground text-center">
            <p>Press <kbd className="px-1.5 py-0.5 bg-muted rounded text-[10px]">Super+I</kbd> to record</p>
          </div>
        </div>

        {/* Main Content */}
        <main className="flex-1 overflow-auto bg-muted/10 p-8">
          {activeTab === "dashboard" && <Dashboard />}
          {activeTab === "history" && <HistoryView />}
          {activeTab === "settings" && <SettingsView />}
        </main>
      </div>
    </ToastProvider>
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
