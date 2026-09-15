import { cn } from "@/lib/utils";
import type { EvidenceLevel as Level } from "@/types";

// prompt.md §4: evidence is ranked, not binary. Render the rank directly
// instead of hiding it behind a single "confidence" word.
const LEVELS: Level[] = ["runtime", "test", "structural", "semantic", "insufficient"];
const RANK: Record<Level, number> = { runtime: 4, test: 3, structural: 2, semantic: 1, insufficient: 0 };

export function EvidenceLevel({ level, className }: { level: Level; className?: string }) {
  const filled = RANK[level];
  return (
    <span className={cn("inline-flex items-center gap-2 font-mono text-xs text-muted-foreground", className)}>
      <span className="flex gap-0.5" aria-hidden>
        {LEVELS.slice(0, 4).map((l, i) => (
          <span key={l} className={cn("size-1.5 rounded-full", i < filled ? "bg-accent" : "bg-border")} />
        ))}
      </span>
      L{filled} · {level}
    </span>
  );
}
