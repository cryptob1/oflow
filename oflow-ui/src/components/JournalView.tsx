import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { BookText, Loader2, RefreshCw } from "lucide-react";
import { Markdown } from "@/components/ui/markdown";
import { readVault, runJournal, type VaultEntry } from "@/lib/api";

/** Daily journal — "what did I work on", synthesized from the day's dictations. */
export function JournalView() {
    const [entries, setEntries] = useState<VaultEntry[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState<string | null>(null);

    const load = async () => {
        try {
            setEntries(await readVault("journal"));
        } catch (e) {
            console.error("Failed to load journal:", e);
        } finally {
            setIsLoading(false);
        }
    };
    useEffect(() => { load(); }, []);

    const journalToday = async () => {
        setBusy(true);
        setNote(null);
        try {
            const r = await runJournal();
            setNote(r.skipped ? `Nothing to journal yet: ${r.reason}` : `Journaled ${r.date} from ${r.dictations} dictations.`);
            await load();
        } catch (e) {
            setNote(`Failed: ${e instanceof Error ? e.message : String(e)}`);
        } finally {
            setBusy(false);
        }
    };

    const strip = (c: string) => c.replace(/^---\n[\s\S]*?\n---\n?/, "").trim();

    return (
        <div className="space-y-6 h-full flex flex-col">
            <div className="flex items-start justify-between gap-4">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">Journal</h2>
                    <p className="text-muted-foreground">
                        What you worked on each day — reconstructed from your dictations. Written nightly.
                    </p>
                </div>
                <Button onClick={journalToday} disabled={busy}>
                    {busy
                        ? <><Loader2 className="h-4 w-4 mr-1 animate-spin" /> Writing…</>
                        : <><RefreshCw className="h-4 w-4 mr-1" /> Journal today</>}
                </Button>
            </div>

            {note && (
                <div className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 text-sm">{note}</div>
            )}

            <Card className="flex-1 overflow-hidden flex flex-col">
                <CardHeader><CardTitle>Daily journal</CardTitle></CardHeader>
                <CardContent className="flex-1 overflow-hidden p-0">
                    <ScrollArea className="h-full px-6 pb-6">
                        {isLoading ? (
                            <div className="flex items-center justify-center h-48">
                                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                            </div>
                        ) : entries.length === 0 ? (
                            <div className="flex flex-col items-center justify-center h-48 gap-2 text-muted-foreground">
                                <BookText className="h-8 w-8" />
                                <p className="text-sm">No journal yet — dictate through your day, then "Journal today".</p>
                            </div>
                        ) : (
                            <div className="space-y-4">
                                {entries.map((j) => (
                                    <div key={j.name} className="rounded-lg border bg-card p-4">
                                        <div className="text-sm font-medium mb-2">{j.name}</div>
                                        <Markdown>{strip(j.content)}</Markdown>
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
