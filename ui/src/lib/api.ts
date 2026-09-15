// Thin fetch wrapper over the FastAPI endpoints in prompt.md §16.
// No caching/business logic here — that lives in the backend.
import type {
  CommitPage,
  CommitRecord,
  DiagnosisRequest,
  DiagnosisResult,
  DiffSegmentSummary,
  Evidence,
  FileChange,
  Marker,
  MarkerKind,
  PromptTimelineEntry,
  RepoIdentity,
  Requirement,
  SessionCommitLink,
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

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: POST ${path}`);
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

export const historyApi = {
  status: () => get<RepoIdentity>("/history/status"),
  commits: (beforeSeq?: number, limit = 50) =>
    get<CommitPage>(`/history/commits?limit=${limit}${beforeSeq != null ? `&before_seq=${beforeSeq}` : ""}`),
  commit: (hash: string) => get<CommitRecord & { session_id: string | null }>(`/history/commits/${hash}`),
  path: (path: string) =>
    get<{ path: string; commits: CommitRecord[]; created: unknown; deleted: unknown }>(
      `/history/path?path=${encodeURIComponent(path)}`,
    ),
  sessionLink: (sessionId: string) => get<SessionCommitLink>(`/history/sessions/${sessionId}`),
  markers: (kind?: MarkerKind) => get<Marker[]>(`/history/markers${kind ? `?kind=${kind}` : ""}`),
  addMarker: (body: { kind: MarkerKind; commit?: string; description?: string }) =>
    post<Marker>("/history/markers", body),
  diagnose: (body: DiagnosisRequest) => post<DiagnosisResult>("/history/diagnose", body),
};
