import { useState, useEffect } from "react";
import { LayoutDashboard, History, Settings, Mic, StickyNote, Users, Sparkles, Target, Moon, Bell, BookText } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ToastProvider } from "@/components/ui/toast";
import { Dashboard } from "@/components/Dashboard";
import { HistoryView } from "@/components/HistoryView";
import { VaultView } from "@/components/VaultView";
import { AskView } from "@/components/AskView";
import { InitiativesView } from "@/components/InitiativesView";
import { DreamsView } from "@/components/DreamsView";
import { JournalView } from "@/components/JournalView";
import { SettingsView } from "@/components/SettingsView";
import { showWindow } from "@/lib/api";

type Tab = "dashboard" | "ask" | "initiatives" | "dreams" | "journal" | "reminders" | "history" | "notes" | "meetings" | "settings";

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>("dashboard");

  // Show window on mount
  useEffect(() => {
    showWindow().catch(() => {});
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
            <h1 className="font-bold text-xl tracking-tight">cortex</h1>
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
              active={activeTab === "ask"}
              onClick={() => setActiveTab("ask")}
              icon={<Sparkles className="h-4 w-4" />}
            >
              Ask
            </NavButton>
            <NavButton
              active={activeTab === "initiatives"}
              onClick={() => setActiveTab("initiatives")}
              icon={<Target className="h-4 w-4" />}
            >
              Initiatives
            </NavButton>
            <NavButton
              active={activeTab === "dreams"}
              onClick={() => setActiveTab("dreams")}
              icon={<Moon className="h-4 w-4" />}
            >
              Dreams
            </NavButton>
            <NavButton
              active={activeTab === "journal"}
              onClick={() => setActiveTab("journal")}
              icon={<BookText className="h-4 w-4" />}
            >
              Journal
            </NavButton>
            <NavButton
              active={activeTab === "reminders"}
              onClick={() => setActiveTab("reminders")}
              icon={<Bell className="h-4 w-4" />}
            >
              Reminders
            </NavButton>
            <NavButton
              active={activeTab === "history"}
              onClick={() => setActiveTab("history")}
              icon={<History className="h-4 w-4" />}
            >
              History
            </NavButton>
            <NavButton
              active={activeTab === "notes"}
              onClick={() => setActiveTab("notes")}
              icon={<StickyNote className="h-4 w-4" />}
            >
              Notes
            </NavButton>
            <NavButton
              active={activeTab === "meetings"}
              onClick={() => setActiveTab("meetings")}
              icon={<Users className="h-4 w-4" />}
            >
              Meetings
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
            <p>Press <kbd className="px-1.5 py-0.5 bg-muted rounded text-[10px]">Super+D</kbd> to record</p>
          </div>
        </div>

        {/* Main Content */}
        <main className="flex-1 overflow-auto bg-muted/10 p-8">
          {activeTab === "dashboard" && <Dashboard />}
          {activeTab === "ask" && <AskView />}
          {activeTab === "initiatives" && <InitiativesView />}
          {activeTab === "dreams" && <DreamsView />}
          {activeTab === "journal" && <JournalView />}
          {activeTab === "reminders" && <VaultView kind="reminders" title="Reminders" subtitle="Say &quot;remind me to…&quot; in a note — cortex notifies you when it's due." />}
          {activeTab === "history" && <HistoryView />}
          {activeTab === "notes" && <VaultView kind="notes" title="Notes" subtitle="Quick notes you captured with Copilot+N." />}
          {activeTab === "meetings" && <VaultView kind="meetings" title="Meetings" subtitle="Recorded meetings, summarized with Copilot+M." />}
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
