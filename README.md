# Cerno

> **Coding agents shouldn't grade their own homework.**

Cerno is an autonomous verification agent for Claude Code. It tracks what a developer asked for, observes what Claude
changed, and investigates the resulting codebase using Git, structural analysis, runtime checks, and targeted AI
reasoning. Instead of trusting an AI coding agent to grade its own work, Cerno produces evidence-backed verdicts for
every requirement.

*Cerno* is Latin for "to discern, to decide."

## How it works

```text
Claude Code hooks ──> Capture (prompts, tool events, baseline/final git tree snapshots)
                        │
                        ▼
                 Intent Compiler (OpenAI, strict schema) ─> Intent Contract (R1..Rn)
                        │
        ┌───────────────┴────────────────┐
        ▼                                ▼
 Change Analyzer                   Runtime Observer
 git trees + Tree-sitter           allowlisted test/build/lint/typecheck
 symbols + impact graph                  │
        └───────────────┬────────────────┘
                        ▼
      Deterministic rules ──(settled)──────────────┐
                        │ (not settled)            │
                        ▼                          │
      Evidence retrieval: rank + compact packet    │
                        ▼                          │
      Verification agent (OpenAI, read-only tools, │
      bounded steps, cites evidence ids)           │
                        ▼                          ▼
      Verdict rules (VERIFIED needs test/runtime/rule evidence)
                        ▼
      Drift analysis · claim checks · usage ──> SQLite ──> FastAPI ──> React dashboard
```

Verdicts: **VERIFIED** (test, runtime, or deterministic proof), **LIKELY** (strong code evidence, no proof),
**UNCERTAIN** (missing or conflicting evidence, or budget spent), **VIOLATED** (evidence contradicts it).
A model's opinion alone can never produce VERIFIED; that rule is enforced in code.

## Quick start

```bash
uv sync
cp .env.example .env                 # set OPENAI_API_KEY (optional; deterministic analysis works without it)
uv run cerno demo                    # reproducible scenario, see DEMO.md
uv run cerno dashboard --repo ~/.cerno-demo
```

In your own repository:

```bash
uv run --project /path/to/Cerno cerno init      # merges hooks into .claude/settings.json (backup kept)
uv run --project /path/to/Cerno cerno doctor
# ... use Claude Code as usual ...
uv run --project /path/to/Cerno cerno status
uv run --project /path/to/Cerno cerno analyze   # latest session
```

## CLI

| Command | Purpose |
| --- | --- |
| `cerno init` | Install hooks, ignore `.cerno/` |
| `cerno doctor` | Check git, hooks, key, Tree-sitter, DB, detected checks |
| `cerno status` | List captured sessions |
| `cerno analyze [session-id] [--json]` | Run verification |
| `cerno dashboard` | Serve API (+ `ui/dist`) on :8000 |
| `cerno demo` | Build and analyze the demo repository |
| `cerno history index` | Incrementally index reachable git history |
| `cerno history log [--first-parent]` | Show indexed commits |
| `cerno history path <path>` | Commits touching a path, following renames |
| `cerno history markers [--add <kind> --commit <ref>]` | Record/list good/bad/deployment/release/incident markers |
| `cerno diagnose --good/--bad/--query/--file ...` | Rank suspect commits for a production breakage |
| `cerno hook <event>` | Internal hook entrypoints (stdin JSON, fail-open) |

See [`docs/history.md`](docs/history.md) for the repository-history layer and diagnosis workflow in depth.

## API

`GET /api/health` · `GET /api/sessions` · `GET /api/sessions/{id}` · `GET /api/sessions/{id}/requirements` ·
`GET /api/sessions/{id}/requirements/{rid}` (trace + evidence) · `GET /api/sessions/{id}/changes` ·
`GET /api/sessions/{id}/evidence[/{eid}]` · `GET /api/sessions/{id}/checks` · `GET /api/sessions/{id}/claims` ·
`GET /api/sessions/{id}/events` · `GET /api/sessions/{id}/usage` · `POST /api/sessions/{id}/analyze` ·
`GET /api/sessions/{id}/stream` (SSE progress). Response shapes follow `ui/src/types.ts`, with extra fields added.

Repository history (see `docs/history.md`): `GET /api/history/status` · `GET /api/history/commits` (paginated,
`?before_seq=&limit=&first_parent=`) · `GET /api/history/commits/{hash}` · `GET /api/history/path?path=` ·
`GET /api/history/symbol?path=&name=` · `GET /api/history/sessions/{id}[/file?path=]` ·
`GET/POST /api/history/markers` · `POST /api/history/diagnose` · `GET /api/history/diagnose/{id}`.

## Configuration

See `.env.example`: `CERNO_MODEL` (default `gpt-5.6-luna`), `CERNO_MAX_TOOL_STEPS`, `CERNO_MAX_GRAPH_DEPTH`,
`CERNO_MAX_SNIPPET_LINES`, `CERNO_MAX_SEARCH_RESULTS`, `CERNO_CHECK_TIMEOUT`, `CERNO_DB_PATH`.

## Privacy and safety

- Local-first. Only small, redacted evidence snippets are sent to the OpenAI Responses API, never the whole repository.
- `.env`, key/credential files, `.gitignore`d paths, and `.cernoignore` patterns are excluded.
- The model has no shell and no write access. Project checks come only from `package.json` scripts or `pyproject.toml`.
- OBSERVE mode: Cerno never blocks Claude Code, and hook failures exit 0.

## Layout

```text
src/cerno/  cli.py config.py models.py privacy.py pipeline.py demo.py
            capture/ (hooks, snapshots, transcript, timeline)   intent/compiler.py
            analysis/ (git, tree_sitter, symbols, graph, retrieval, segments, history)
            evidence/store.py   verification/ (context, deterministic, runtime, tools, agent, verdict, diagnosis)
            llm/openai.py       storage/sqlite.py    api/ (app.py, history.py)
ui/         React + Vite dashboard
tests/      pytest suite (API mocked for the agent loop)
examples/demo-auth-app/
docs/       history.md (repository-history layer + diagnosis workflow)
```

## Development and tests

The repository has two test suites:

```bash
# Backend, capture pipeline, analysis, verification, and API
uv run pytest

# Dashboard utilities
cd ui
npm ci
npm test
```

The Python suite currently contains 46 collected cases across eight files. The first four cover the per-session
capture/verification pipeline; the last four cover the repository-history layer (`docs/history.md`):

- `tests/test_capture_git.py` checks Git snapshots and diffs, dirty-work attribution, hook event persistence and
  normalization, fail-open handling of invalid hook input, and non-destructive/idempotent hook installation.
- `tests/test_analysis.py` checks JavaScript/TypeScript and Python symbol extraction, changed-symbol classification,
  import/caller relationships, bounded graph traversal, and ranked evidence retrieval with `.cernoignore` filtering.
- `tests/test_intent_rules.py` checks model-produced and fallback intent contracts, the requirement cap, malformed model
  output, evidence requirements for verdicts, and deterministic rules for dependencies, schema tables, and tests.
- `tests/test_agent_api.py` runs the demo analysis once per test session, then checks deterministic results, the mocked
  verification-agent tool loop and cache, budget/malformed-output fallbacks, the rule preventing opinion-only
  `VERIFIED` verdicts, and the FastAPI response contract. OpenAI calls are scripted with `FakeClient`, so the suite does
  not require an API key.
- `tests/test_history.py` checks git indexing (linear/merge/root commits, renames, deletions, binary files,
  schema/manifest flags), incremental re-indexing, interrupted-indexing recovery, shallow clones, rewritten/diverged
  history, session- and prompt-to-commit linkage, developer-only markers, and backward-compatible DB migration.
- `tests/test_diagnosis.py` checks deterministic suspect ranking without OpenAI, `--good`/`--bad` range narrowing, a
  fake-OpenAI narrative pass (`FakeClient`, no live API calls), that a model-suggested ref outside the candidate set
  is dropped rather than trusted, and secret redaction in the evidence packet.
- `tests/test_history_api.py` checks commit-timeline pagination, commit/path/marker/diagnose endpoints, and 404s.
- `tests/test_regression_scenario.py` is the realistic example from `docs/history.md`: two sessions, a known-good
  release, a later regression, and a diagnosis that ranks and links the suspect commit back to its session and prompt.

The UI suite currently contains four Vitest cases: `ui/src/lib/session-view.test.ts` (analysis-progress stage states,
verdict-status counting) and `ui/src/pages/history-page.test.tsx` (renders the repository-history dashboard page
against a mocked `fetch`). The shared `demo` pytest fixture invokes the demo app's real `node --test` command.

Useful additional checks are:

```bash
uv run ruff check .
cd ui && npm run lint && npm run build
```

## Known limitations

- The call graph is name-based and approximate. There is no type resolution.
- The whole test suite runs; per-target test selection is not derived.
- Checks run on the working tree at analysis time.
- JavaScript/TypeScript and Python only.
- Repository history is indexed from `HEAD` only (not every branch/tag); incremental indexing after a
  force-push/rebase that rewrites already-indexed commits may briefly re-walk more than the new tip alone (see
  `docs/history.md#known-limitations`).
- Per-commit symbol history re-parses the repository tree for each commit in the query, bounded to the last 20
  commits touching a path by default — fine for interactive queries, not for scanning whole-history symbol churn.

## Roadmap

GUARD mode (warn on risky tool calls) · ENFORCE mode (block explicit prohibitions) · targeted test selection ·
richer claim extraction from transcripts · more languages.

<!-- Screenshots: docs/screenshots/overview.png, docs/screenshots/trace.png (placeholders) -->
