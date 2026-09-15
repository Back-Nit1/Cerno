import { Check, Circle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AnalysisProgressStep } from "@/types";

const ICON = { done: Check, active: Loader2, pending: Circle };

export function AnalysisProgress({ steps }: { steps: AnalysisProgressStep[] }) {
  return (
    <ol className="flex flex-col gap-1.5">
      {steps.map((step) => {
        const Icon = ICON[step.state];
        return (
          <li
            key={step.label}
            className={cn(
              "flex items-center gap-2 text-sm",
              step.state === "pending" ? "text-muted-foreground" : "text-foreground",
            )}
          >
            <Icon className={cn("size-3.5", step.state === "active" && "animate-spin")} strokeWidth={2.5} />
            {step.label}
          </li>
        );
      })}
    </ol>
  );
}
