import { useState, useEffect } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Markdown } from "@/components/ui/markdown";
import { Target, Loader2, AlertCircle, Sparkles, Link2, ChevronDown } from "lucide-react";
import { listInitiatives, initiativeStatus, type Initiative } from "@/lib/api";

/**
 * Initiatives tab — goals/projects the brain tracks, with an on-demand
 * coach-style status synthesized from the notes & meetings linked to each.
 */
export function InitiativesView() {
    const [initiatives, setInitiatives] = useState<Initiative[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [openSlug, setOpenSlug] = useState<string | null>(null);
    const [status, setStatus] = useState<string>("");
    const [statusLoading, setStatusLoading] = useState(false);

    useEffect(() => {
        (async () => {
            setIsLoading(true);
            setError(null);
            try {
                setInitiatives(await listInitiatives());
            } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
            } finally {
                setIsLoading(false);
            }
        })();
    }, []);

    const toggleStatus = async (it: Initiative) => {
        if (openSlug === it.slug) { setOpenSlug(null); return; }
        setOpenSlug(it.slug);
        setStatus("");
        setStatusLoading(true);
        try {
            setStatus((await initiativeStatus(it.slug)).status);
        } catch (e) {
            setStatus(`Couldn't get a status: ${e instanceof Error ? e.message : String(e)}`);
        } finally {
            setStatusLoading(false);
        }
    };

    return (
        <div className="space-y-6">
            <div>
                <h2 className="text-3xl font-bold tracking-tight">Initiatives</h2>
                <p className="text-muted-foreground">
                    Goals your brain tracks — say <span className="font-mono text-xs">"start an initiative…"</span> in a note.
                    Notes and meetings link to them automatically.
                </p>
            </div>

            {isLoading ? (
                <div className="flex items-center justify-center h-48">
                    <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                </div>
            ) : error ? (
                <div className="flex flex-col items-center justify-center h-48 gap-2 text-destructive">
                    <AlertCircle className="h-8 w-8" />
                    <p className="text-sm">{error}</p>
                </div>
            ) : initiatives.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-48 gap-2 text-muted-foreground">
                    <Target className="h-8 w-8" />
                    <p className="text-sm">No initiatives yet — capture a note saying "start an initiative to…".</p>
                </div>
            ) : (
                <div className="grid gap-4">
                    {initiatives.map((it) => {
                        const open = openSlug === it.slug;
                        return (
                            <Card key={it.slug} className="overflow-hidden">
                                {/* Header */}
                                <div className="flex items-start justify-between gap-4 p-5">
                                    <div className="flex items-start gap-3 min-w-0">
                                        <div className="mt-0.5 h-9 w-9 shrink-0 rounded-lg bg-primary/10 flex items-center justify-center">
                                            <Target className="h-5 w-5 text-primary" />
                                        </div>
                                        <div className="min-w-0">
                                            <h3 className="font-semibold text-lg leading-tight truncate">{it.title}</h3>
                                            <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                                                <Badge variant="secondary" className="capitalize">{it.status}</Badge>
                                                <span className="inline-flex items-center gap-1">
                                                    <Link2 className="h-3 w-3" />
                                                    {it.linked} linked
                                                </span>
                                            </div>
                                        </div>
                                    </div>
                                    <Button variant={open ? "secondary" : "outline"} size="sm" onClick={() => toggleStatus(it)}>
                                        <Sparkles className="h-4 w-4 mr-1" />
                                        Status
                                        <ChevronDown className={`h-4 w-4 ml-1 transition-transform ${open ? "rotate-180" : ""}`} />
                                    </Button>
                                </div>

                                {/* Goals */}
                                {it.goals.length > 0 && (
                                    <div className="px-5 pb-5 -mt-1">
                                        <div className="rounded-lg border bg-muted/20 p-4">
                                            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">Goals</div>
                                            <ul className="space-y-1.5">
                                                {it.goals.map((g, i) => (
                                                    <li key={i} className="flex items-start gap-2 text-sm">
                                                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary/60" />
                                                        <span>{g}</span>
                                                    </li>
                                                ))}
                                            </ul>
                                        </div>
                                    </div>
                                )}

                                {/* Status (rendered markdown) */}
                                {open && (
                                    <div className="border-t bg-muted/10 px-5 py-4">
                                        {statusLoading ? (
                                            <div className="flex items-center gap-2 text-muted-foreground py-4">
                                                <Loader2 className="h-4 w-4 animate-spin" />
                                                <span className="text-sm">Reviewing your progress…</span>
                                            </div>
                                        ) : (
                                            <Markdown>{status}</Markdown>
                                        )}
                                    </div>
                                )}
                            </Card>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
