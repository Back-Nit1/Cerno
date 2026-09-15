// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { HistoryPage } from "./history-page";

const RESPONSES: Record<string, unknown> = {
  "/api/history/status": {
    repo_id: "r-abc123", path: "/repo", remote_url: null, remote_normalized: null,
    default_branch: "main", initial_commit: "aaa111", latest_indexed_commit: "bbb222", indexed_at: "2026-01-01T00:00:00Z",
  },
  "/api/history/markers": [{ id: "M-1", repo_id: "r-abc123", kind: "good", commit: "aaa111", timestamp: "2026-01-01T00:00:00Z",
    description: "last known good", metadata: {} }],
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/history/commits")) {
      return new Response(JSON.stringify({
        commits: [{
          hash: "bbb222bbb222bbb222", parents: ["aaa111"], author_name: "t", author_email: "t@t",
          author_date: "2026-01-02T00:00:00Z", committer_date: "2026-01-02T00:00:00Z", subject: "fix regression",
          refs: [], is_merge: false, is_root: false,
          files: [{ path: "a.py", old_path: null, change_type: "modified", additions: 3, deletions: 1, is_binary: false }],
          manifest_changed: false, schema_changed: false, test_changed: false, seq: 1,
        }],
        next_before_seq: null,
      }), { status: 200 });
    }
    for (const [path, body] of Object.entries(RESPONSES)) {
      if (url.includes(path)) return new Response(JSON.stringify(body), { status: 200 });
    }
    return new Response("{}", { status: 200 });
  }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("HistoryPage", () => {
  it("renders repo status, markers, and the commit timeline", async () => {
    render(<MemoryRouter><HistoryPage /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText(/fix regression/)).toBeTruthy());
    expect(screen.getByText(/last known good/)).toBeTruthy();
    expect(screen.getByText(/r-abc123/)).toBeTruthy();
  });
});
