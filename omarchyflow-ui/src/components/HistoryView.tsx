import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Input } from "@/components/ui/input";
import { Search, Loader2, AlertCircle } from "lucide-react";
import { getTranscripts, type Transcript } from "@/lib/api";

export function HistoryView() {
    const [transcripts, setTranscripts] = useState<Transcript[]>([]);
    const [filteredTranscripts, setFilteredTranscripts] = useState<Transcript[]>([]);
    const [searchQuery, setSearchQuery] = useState("");
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const loadTranscripts = async () => {
            setIsLoading(true);
            setError(null);
            try {
                const data = await getTranscripts();
                setTranscripts(data);
                setFilteredTranscripts(data);
            } catch (e) {
                const errorMessage = e instanceof Error ? e.message : "Failed to load transcripts";
                setError(errorMessage);
                console.error("Error loading transcripts:", e);
            } finally {
                setIsLoading(false);
            }
        };
        
        loadTranscripts();
    }, []);

    useEffect(() => {
        if (!searchQuery.trim()) {
            setFilteredTranscripts(transcripts);
            return;
        }

        const query = searchQuery.toLowerCase();
        const filtered = transcripts.filter(t => 
            t.raw.toLowerCase().includes(query) || 
            t.cleaned.toLowerCase().includes(query)
        );
        setFilteredTranscripts(filtered);
    }, [searchQuery, transcripts]);

    return (
        <div className="space-y-6 h-full flex flex-col">
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-3xl font-bold tracking-tight">History</h2>
                    <p className="text-muted-foreground">Search and browse your past transcripts.</p>
                </div>
                <div className="relative w-64">
                    <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input 
                        type="search" 
                        placeholder="Search transcripts..." 
                        className="pl-9"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                    />
                </div>
            </div>

            <Card className="flex-1 overflow-hidden flex flex-col">
                <CardHeader>
                    <CardTitle>Recent Transcripts</CardTitle>
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
                        ) : filteredTranscripts.length === 0 ? (
                            <div className="flex items-center justify-center h-64 text-muted-foreground">
                                <p className="text-sm">
                                    {searchQuery ? "No transcripts match your search." : "No transcripts yet."}
                                </p>
                            </div>
                        ) : (
                            <div className="space-y-4">
                                {filteredTranscripts.map((item, index) => {
                                    const date = new Date(item.timestamp);
                                    const formattedDate = date.toLocaleString();
                                    
                                    return (
                                        <div key={index} className="flex flex-col gap-2 p-4 rounded-lg border bg-card hover:bg-accent/50 transition-colors">
                                            <div className="flex items-center justify-between">
                                                <span className="text-sm text-muted-foreground">{formattedDate}</span>
                                            </div>
                                            <div>
                                                <p className="font-medium">{item.cleaned}</p>
                                                {item.raw !== item.cleaned && (
                                                    <p className="text-sm text-muted-foreground mt-1 line-through opacity-50">{item.raw}</p>
                                                )}
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
