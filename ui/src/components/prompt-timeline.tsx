import { Badge } from "@/components/ui/badge";
import { classificationLabel } from "@/lib/session-view";
import type { PromptTimelineEntry } from "@/types";

const AMBIGUOUS_CLASSIFICATIONS = new Set(["ambiguous", "correction", "replacement"]);

export function PromptTimeline({ prompts }: { prompts: PromptTimelineEntry[] }) {
  if (prompts.length === 0) return null;
  return (
    <div className="flex flex-col gap-3">
      {prompts.map((p) => (
        <div key={p.id} className="rounded-md border border-border p-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-mono text-xs text-muted-foreground">
              P{p.seq} · {new Date(p.timestamp).toLocaleString()}
            </span>
            <span className="flex items-center gap-2">
              {p.ambiguous && (
                <Badge variant="outline" className="text-uncertain">
                  needs review
                </Badge>
              )}
              <Badge variant={AMBIGUOUS_CLASSIFICATIONS.has(p.classification ?? "") ? "outline" : "secondary"}>
                {classificationLabel(p.classification)}
              </Badge>
            </span>
          </div>

          <p className="mt-1 text-foreground">{p.text.length > 200 ? `${p.text.slice(0, 200)}…` : p.text}</p>

          <p className="mt-1 font-mono text-xs text-muted-foreground">
            checkpoint {p.head ? p.head.slice(0, 8) : "—"} · tree {p.tree ? p.tree.slice(0, 8) : "—"}
            {p.confidence != null && ` · confidence ${p.confidence.toFixed(2)}`}
          </p>

          {(p.files_changed.length > 0 || p.symbols_changed.length > 0) && (
            <p className="mt-1 text-xs text-muted-foreground">
              {p.files_changed.length} file(s), {p.symbols_changed.length} symbol(s) changed afterward
            </p>
          )}

          {(p.requirements_introduced.length > 0 ||
            p.requirements_refined.length > 0 ||
            p.requirements_superseded.length > 0) && (
            <div className="mt-2 flex flex-wrap gap-2 text-xs">
              {p.requirements_introduced.map((id) => (
                <Badge key={`intro-${id}`} variant="outline" className="text-verified">
                  +{id}
                </Badge>
              ))}
              {p.requirements_refined.map((id) => (
                <Badge key={`refine-${id}`} variant="outline">
                  ~{id}
                </Badge>
              ))}
              {p.requirements_superseded.map((id) => (
                <Badge key={`supersede-${id}`} variant="outline" className="text-violated line-through">
                  {id}
                </Badge>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
