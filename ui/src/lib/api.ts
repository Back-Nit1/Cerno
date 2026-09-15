// Thin fetch wrapper over the FastAPI endpoints in prompt.md §16.
// No caching/business logic here — that lives in the backend.
import type {
  DiffSegmentSummary,
  Evidence,
  FileChange,
  PromptTimelineEntry,
  Requirement,
  SessionDetail,
  SessionSummary,
  SymbolChange,
  UsageMetrics,
} from "@/types";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: GET ${path}`);
  return res.json() as Promise<T>;
}

export const api = {
  health: () => get<{ status: string }>("/health"),
  sessions: () => get<SessionSummary[]>("/sessions"),
  session: (id: string) => get<SessionDetail>(`/sessions/${id}`),
  requirements: (id: string) => get<Requirement[]>(`/sessions/${id}/requirements`),
  changes: (id: string) => get<{ files: FileChange[]; symbols: SymbolChange[] }>(`/sessions/${id}/changes`),
  evidence: (id: string) => get<Evidence[]>(`/sessions/${id}/evidence`),
  usage: (id: string) => get<UsageMetrics>(`/sessions/${id}/usage`),
  timeline: (id: string) => get<PromptTimelineEntry[]>(`/sessions/${id}/timeline`),
  segments: (id: string) => get<DiffSegmentSummary[]>(`/sessions/${id}/segments`),
  analyze: async (id: string) => {
    const res = await fetch(`/api/sessions/${id}/analyze`, { method: "POST" });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}: POST /sessions/${id}/analyze`);
    return res.json() as Promise<{ started: boolean }>;
  },
};
