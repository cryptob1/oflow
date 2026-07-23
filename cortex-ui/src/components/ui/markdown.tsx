import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

/** Renders Markdown (LLM output — statuses, answers) with tidy, themed styling. */
export function Markdown({ children, className }: { children: string; className?: string }) {
    return (
        <div className={cn("text-sm leading-relaxed", className)}>
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                    h1: (p) => <h4 className="mt-4 mb-1.5 font-semibold text-foreground first:mt-0" {...p} />,
                    h2: (p) => <h4 className="mt-4 mb-1.5 font-semibold text-foreground first:mt-0" {...p} />,
                    h3: (p) => <h5 className="mt-3 mb-1 font-medium text-foreground first:mt-0" {...p} />,
                    p: (p) => <p className="my-1.5 text-muted-foreground" {...p} />,
                    ul: (p) => <ul className="list-disc pl-5 space-y-1 my-1.5 marker:text-muted-foreground/50" {...p} />,
                    ol: (p) => <ol className="list-decimal pl-5 space-y-1 my-1.5 marker:text-muted-foreground/50" {...p} />,
                    li: (p) => <li className="text-muted-foreground" {...p} />,
                    strong: (p) => <strong className="text-foreground font-semibold" {...p} />,
                    a: (p) => <a className="text-primary underline underline-offset-2" {...p} />,
                    code: (p) => <code className="rounded bg-muted px-1 py-0.5 text-xs font-mono" {...p} />,
                    input: (p) => <input {...p} disabled className="mr-1.5 align-middle accent-primary" />,
                }}
            >
                {children}
            </ReactMarkdown>
        </div>
    );
}
