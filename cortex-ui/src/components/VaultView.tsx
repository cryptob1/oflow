import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Search, Loader2, AlertCircle, Copy, Check } from "lucide-react";
import { Markdown } from "@/components/ui/markdown";
import { readVault, type VaultEntry } from "@/lib/api";

/** Split an item's YAML frontmatter from its body, pulling out the title. */
function parseEntry(content: string): { title?: string; body: string } {
    const m = content.match(/^---\n([\s\S]*?)\n---\n?/);
    if (!m) return { body: content };
    const title = m[1].match(/^title:\s*(.+)$/m)?.[1]?.trim();
    return { title, body: content.slice(m[0].length).trim() };
}

/**
 * Lists Markdown entries from the second-brain vault (notes/ or meetings/).
 * Reused for both the Notes and Meetings tabs.
 */
const CAPTURE_HINT: Record<string, string> = {
    notes: "Copilot+N",
    meetings: "Copilot+M",
    initiatives: 'a note saying "start an initiative…"',
    reminders: 'a note saying "remind me to…"',
};

export function VaultView({ kind, title, subtitle }: {
    kind: "notes" | "meetings" | "initiatives" | "reminders";
    title: string;
    subtitle: string;
}) {
    const [entries, setEntries] = useState<VaultEntry[]>([]);
    const [searchQuery, setSearchQuery] = useState("");
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

    const copyToClipboard = async (text: string, index: number) => {
        try {
            await navigator.clipboard.writeText(text);
            setCopiedIndex(index);
            setTimeout(() => setCopiedIndex(null), 2000);
        } catch (err) {
            console.error("Failed to copy:", err);
        }
    };

    useEffect(() => {
        const load = async () => {
            setIsLoading(true);
            setError(null);
            try {
                setEntries(await readVault(kind));
            } catch (e) {
                setError(e instanceof Error ? e.message : `Failed to load ${kind}`);
            } finally {
                setIsLoading(false);
            }
        };
        load();
    }, [kind]);

    const query = searchQuery.trim().toLowerCase();
    const filtered = query
        ? entries.filter(e => e.name.toLowerCase().includes(query) || e.content.toLowerCase().includes(query))
        : entries;

    return (
        <div className="space-y-6 h-full flex flex-col">
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">{title}</h2>
                    <p className="text-muted-foreground">{subtitle}</p>
                </div>
                <div className="relative w-64">
                    <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                        type="search"
                        placeholder={`Search ${kind}...`}
                        className="pl-9"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                    />
                </div>
            </div>

            <Card className="flex-1 overflow-hidden flex flex-col">
                <CardHeader>
                    <CardTitle>{title}</CardTitle>
                </CardHeader>
                <CardContent className="flex-1 overflow-hidden p-0">
                    <ScrollArea className="h-full px-6 pb-6">
                        {isLoading ? (
                            <div className="flex items-center justify-center h-64">
                                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                            </div>
                        ) : error ? (
                            <div className="flex flex-col items-center justify-center h-64 gap-2 text-destructive">
                                <AlertCircle className="h-8 w-8" />
                                <p className="text-sm">{error}</p>
                            </div>
                        ) : filtered.length === 0 ? (
                            <div className="flex items-center justify-center h-64 text-muted-foreground">
                                <p className="text-sm">
                                    {searchQuery
                                        ? `No ${kind} match your search.`
                                        : `No ${kind} yet — capture one with ${CAPTURE_HINT[kind] ?? "voice"}.`}
                                </p>
                            </div>
                        ) : (
                            <div className="space-y-4">
                                {filtered.map((item, index) => {
                                    const { title, body } = parseEntry(item.content);
                                    return (
                                        <div key={item.name} className="flex flex-col gap-2 p-4 rounded-lg border bg-card group">
                                            <div className="flex items-start justify-between gap-2">
                                                <span className="font-medium leading-snug">{title || item.name}</span>
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="h-8 w-8 p-0 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
                                                    onClick={() => copyToClipboard(body, index)}
                                                    title="Copy to clipboard"
                                                >
                                                    {copiedIndex === index ? (
                                                        <Check className="h-4 w-4 text-green-500" />
                                                    ) : (
                                                        <Copy className="h-4 w-4" />
                                                    )}
                                                </Button>
                                            </div>
                                            <div className="max-h-96 overflow-auto">
                                                <Markdown>{body}</Markdown>
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </ScrollArea>
                </CardContent>
            </Card>
        </div>
    );
}
