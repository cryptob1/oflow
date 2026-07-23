import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Sparkles, Loader2, AlertCircle } from "lucide-react";
import { Markdown } from "@/components/ui/markdown";
import { askBrain } from "@/lib/api";

/**
 * "Ask my brain" — natural-language Q&A over the vault (notes + meetings).
 * Calls the ask_brain Tauri command, which runs the local RAG.
 */
export function AskView() {
    const [query, setQuery] = useState("");
    const [answer, setAnswer] = useState<string | null>(null);
    const [sources, setSources] = useState<string[]>([]);
    const [asked, setAsked] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const ask = async () => {
        const q = query.trim();
        if (!q || isLoading) return;
        setIsLoading(true);
        setError(null);
        setAnswer(null);
        setSources([]);
        setAsked(q);
        try {
            const res = await askBrain(q);
            setAnswer(res.answer);
            setSources(res.sources || []);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="space-y-6 h-full flex flex-col">
            <div>
                <h2 className="text-3xl font-bold tracking-tight">Ask your brain</h2>
                <p className="text-muted-foreground">
                    Ask a question and get an answer synthesized from your notes and meetings.
                </p>
            </div>

            <div className="flex gap-2">
                <div className="relative flex-1">
                    <Sparkles className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                        className="pl-9"
                        placeholder="e.g. what did we decide about onboarding?"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Enter") ask(); }}
                        disabled={isLoading}
                    />
                </div>
                <Button onClick={ask} disabled={isLoading || !query.trim()}>
                    {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Ask"}
                </Button>
            </div>

            {(isLoading || answer || error) && (
                <Card className="flex-1 overflow-hidden flex flex-col">
                    <CardHeader>
                        <CardTitle className="text-base font-medium text-muted-foreground">
                            {asked}
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="flex-1 overflow-auto">
                        {isLoading ? (
                            <div className="flex items-center gap-2 text-muted-foreground">
                                <Loader2 className="h-5 w-5 animate-spin" />
                                <span className="text-sm">Searching your brain…</span>
                            </div>
                        ) : error ? (
                            <div className="flex items-center gap-2 text-destructive">
                                <AlertCircle className="h-5 w-5" />
                                <span className="text-sm">{error}</span>
                            </div>
                        ) : (
                            <>
                                <Markdown>{answer ?? ""}</Markdown>
                                {sources.length > 0 && (
                                    <div className="mt-4 flex flex-wrap gap-2">
                                        {sources.map((s) => (
                                            <span key={s} className="rounded-full bg-muted px-2.5 py-1 text-xs font-mono text-muted-foreground">
                                                {s}
                                            </span>
                                        ))}
                                    </div>
                                )}
                            </>
                        )}
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
