import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Moon, Loader2, Sparkles } from "lucide-react";
import { readVault, runDream, type VaultEntry } from "@/lib/api";

/**
 * Dreams tab — read the nightly consolidation journals and trigger one on demand.
 * A dream re-links captures to initiatives, refreshes their status, and surfaces
 * emergent themes.
 */
export function DreamsView() {
    const [journals, setJournals] = useState<VaultEntry[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [dreaming, setDreaming] = useState(false);
    const [note, setNote] = useState<string | null>(null);

    const load = async () => {
        try {
            setJournals(await readVault("dreams"));
        } catch (e) {
            console.error("Failed to load dreams:", e);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => { load(); }, []);

    const dreamNow = async () => {
        setDreaming(true);
        setNote(null);
        try {
            const r = await runDream();
            const suggestions = r.suggestions.length
                ? ` · ${r.suggestions.length} new idea${r.suggestions.length === 1 ? "" : "s"}`
                : "";
            setNote(`Re-linked ${r.relinked} capture${r.relinked === 1 ? "" : "s"} across ${r.initiatives} initiative${r.initiatives === 1 ? "" : "s"}${suggestions}.`);
            await load();
        } catch (e) {
            setNote(`Dream failed: ${e instanceof Error ? e.message : String(e)}`);
        } finally {
            setDreaming(false);
        }
    };

    return (
        <div className="space-y-6 h-full flex flex-col">
            <div className="flex items-start justify-between gap-4">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Dreams</h2>
                    <p className="text-muted-foreground">
                        While you sleep, cortex consolidates your notes & meetings into your initiatives. Runs nightly.
                    </p>
                </div>
                <Button onClick={dreamNow} disabled={dreaming}>
                    {dreaming
                        ? <><Loader2 className="h-4 w-4 mr-1 animate-spin" /> Dreaming…</>
                        : <><Sparkles className="h-4 w-4 mr-1" /> Dream now</>}
                </Button>
            </div>

            {note && (
                <div className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 text-sm">{note}</div>
            )}

            <Card className="flex-1 overflow-hidden flex flex-col">
                <CardHeader><CardTitle>Dream journal</CardTitle></CardHeader>
                <CardContent className="flex-1 overflow-hidden p-0">
                    <ScrollArea className="h-full px-6 pb-6">
                        {isLoading ? (
                            <div className="flex items-center justify-center h-48">
                                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                            </div>
                        ) : journals.length === 0 ? (
                            <div className="flex flex-col items-center justify-center h-48 gap-2 text-muted-foreground">
                                <Moon className="h-8 w-8" />
                                <p className="text-sm">No dreams yet — hit "Dream now" or wait for tonight.</p>
                            </div>
                        ) : (
                            <div className="space-y-4">
                                {journals.map((j) => (
                                    <div key={j.name} className="rounded-lg border bg-card p-4">
                                        <div className="text-sm font-medium mb-2">{j.name}</div>
                                        <pre className="text-sm whitespace-pre-wrap font-sans text-muted-foreground">{j.content}</pre>
                                    </div>
                                ))}
                            </div>
                        )}
                    </ScrollArea>
                </CardContent>
            </Card>
        </div>
    );
}
