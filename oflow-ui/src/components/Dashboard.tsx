import { useState, useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Activity, Clock, FileText, Zap, Loader2 } from "lucide-react";
import { getTranscriptStats, getTranscripts, type Transcript } from "@/lib/api";

export function Dashboard() {
    const [stats, setStats] = useState({
        totalTranscripts: 0,
        totalWords: 0,
        estimatedTimeSaved: 0,
        cleanupQuality: 0
    });
    const [recentTranscripts, setRecentTranscripts] = useState<Transcript[]>([]);
    const [isLoading, setIsLoading] = useState(true);

    useEffect(() => {
        const loadData = async () => {
            setIsLoading(true);
            try {
                const [statsData, transcripts] = await Promise.all([
                    getTranscriptStats(),
                    getTranscripts()
                ]);
                setStats(statsData);
                setRecentTranscripts(transcripts.slice(0, 5)); // Get 5 most recent
            } catch (error) {
                console.error("Failed to load dashboard data:", error);
            } finally {
                setIsLoading(false);
            }
        };
        
        loadData();
    }, []);

    const formatTime = (hours: number): string => {
        if (hours < 1) {
            const minutes = Math.round(hours * 60);
            return `${minutes}m`;
        }
        return `${hours.toFixed(1)}h`;
    };

    const formatNumber = (num: number): string => {
        if (num >= 1000) {
            return (num / 1000).toFixed(1) + "k";
        }
        return num.toString();
    };

    const getTimeAgo = (timestamp: string): string => {
        const date = new Date(timestamp);
        const now = new Date();
        const diffMs = now.getTime() - date.getTime();
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);

        if (diffMins < 1) return "Just now";
        if (diffMins < 60) return `${diffMins} min${diffMins > 1 ? 's' : ''} ago`;
        if (diffHours < 24) return `${diffHours} hour${diffHours > 1 ? 's' : ''} ago`;
        return `${diffDays} day${diffDays > 1 ? 's' : ''} ago`;
    };

    return (
        <div className="space-y-6">
            <div>
                <h2 className="text-3xl font-bold tracking-tight">Dashboard</h2>
                <p className="text-muted-foreground">Overview of your voice transcription activity.</p>
            </div>

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Total Transcripts</CardTitle>
                        <FileText className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        {isLoading ? (
                            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                        ) : (
                            <>
                                <div className="text-2xl font-bold">{stats.totalTranscripts}</div>
                                <p className="text-xs text-muted-foreground">All time</p>
                            </>
                        )}
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Words Dictated</CardTitle>
                        <Activity className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        {isLoading ? (
                            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                        ) : (
                            <>
                                <div className="text-2xl font-bold">{formatNumber(stats.totalWords)}</div>
                                <p className="text-xs text-muted-foreground">Total words</p>
                            </>
                        )}
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Saved Time</CardTitle>
                        <Clock className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        {isLoading ? (
                            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                        ) : (
                            <>
                                <div className="text-2xl font-bold">{formatTime(stats.estimatedTimeSaved)}</div>
                                <p className="text-xs text-muted-foreground">vs typing manually</p>
                            </>
                        )}
                    </CardContent>
                </Card>
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Cleanup Quality</CardTitle>
                        <Zap className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        {isLoading ? (
                            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                        ) : (
                            <>
                                <div className="text-2xl font-bold">{stats.cleanupQuality.toFixed(1)}%</div>
                                <p className="text-xs text-muted-foreground">GPT-4o-mini optimization</p>
                            </>
                        )}
                    </CardContent>
                </Card>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>Recent Transcripts</CardTitle>
                </CardHeader>
                <CardContent>
                    {isLoading ? (
                        <div className="flex items-center justify-center h-[200px]">
                            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                        </div>
                    ) : recentTranscripts.length === 0 ? (
                        <div className="flex items-center justify-center h-[200px] text-muted-foreground">
                            <p className="text-sm">No transcripts yet. Press Super+I to start recording.</p>
                        </div>
                    ) : (
                        <div className="grid gap-3 md:grid-cols-2">
                            {recentTranscripts.map((transcript, index) => {
                                const preview = transcript.cleaned.length > 80
                                    ? transcript.cleaned.substring(0, 80) + "..."
                                    : transcript.cleaned;

                                return (
                                    <div key={index} className="flex items-start gap-4 p-3 rounded-lg hover:bg-muted/50 transition-colors border bg-card/50">
                                        <div className="space-y-1 flex-1">
                                            <p className="text-sm font-medium leading-none">{preview}</p>
                                            <p className="text-xs text-muted-foreground">{getTimeAgo(transcript.timestamp)}</p>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
