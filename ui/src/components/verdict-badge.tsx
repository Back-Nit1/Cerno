import { CheckCircle2, CircleHelp, TrendingUp, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { VerdictStatus } from "@/types";

const STYLE: Record<VerdictStatus, { icon: typeof CheckCircle2; color: string }> = {
  VERIFIED: { icon: CheckCircle2, color: "text-verified border-verified/40 bg-verified/10" },
  LIKELY: { icon: TrendingUp, color: "text-likely border-likely/40 bg-likely/10" },
  UNCERTAIN: { icon: CircleHelp, color: "text-uncertain border-uncertain/40 bg-uncertain/10" },
  VIOLATED: { icon: XCircle, color: "text-violated border-violated/40 bg-violated/10" },
};

// The "stamp": Cerno's core claim is evidence over confidence, so every
// verdict renders the same way everywhere — mono, uppercase, bordered.
export function VerdictBadge({ status, className }: { status: VerdictStatus; className?: string }) {
  const { icon: Icon, color } = STYLE[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono text-xs font-medium tracking-wide",
        color,
        className,
      )}
    >
      <Icon className="size-3.5" strokeWidth={2.5} />
      {status}
    </span>
  );
}
