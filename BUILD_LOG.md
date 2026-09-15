# Build Log (backend + infrastructure)

The dashboard (`ui/`) is built separately by the frontend agent; this log covers everything else.

## Phase 1 — Foundation
- `uv` project, Python 3.12+, Typer CLI, Pydantic v2 models (`models.py`), env config (`config.py`).
- SQLite store (`storage/sqlite.py`): one table per spec entity, query columns + JSON payload, plus a content-hash `cache` table.
- FastAPI app with `/api/health`. `uv run pytest` and `uv run ruff check .` pass.

## Phase 2 — Claude Code capture
- `cerno init` merges hooks into `.claude/settings.json` (timestamped backup, idempotent, never removes user hooks), ignores `.cerno/`.
- `cerno hook <event>` for SessionStart, UserPromptSubmit, Pre/PostToolUse(+Failure), Stop, StopFailure. Raw payloads -> `events.jsonl`, normalized rows -> `events`. Fail-open: errors go to `.cerno/hook-errors.log`, exit 0.
- Snapshots are git tree objects of the full working tree written through a temporary index (real index untouched, pinned under `refs/cerno/`). Baseline on first meaningful prompt, final on every Stop, so pre-existing dirty work is never attributed to Claude.
- `cerno doctor`, `cerno status`.

## Phase 3 — Repository analysis
- `analysis/git.py`: changed files (create/modify/delete/rename, numstat, blob hashes) between baseline and final trees, hunks, manifests.
- `analysis/tree_sitter.py`: TS/TSX/JS/Python functions, methods, classes, top-level constants, imports, exports, calls, ranges.
- `analysis/symbols.py`: added/modified/deleted symbols by body hash. `analysis/graph.py`: adjacency maps, bounded BFS, approximate name-based call resolution, relative import resolution; parses cached by content hash.
- `analysis/retrieval.py`: transparent weighted ranking (diff, keywords, graph distance, tests, Claude events, risk) and compact evidence packets.

## Phase 4 — AI verification agent
- `llm/anthropic.py` adapter (usage recorded per call; retries without `strict` if rejected).
- `intent/compiler.py`: forced strict tool call -> validated `IntentContract` (capped); labeled sentence-level fallback without a key.
- `verification/tools.py`: 13 read-only tools + `submit_verdict`; no shell tool. `verification/agent.py`: bounded loop, old tool outputs elided, final turn forces a verdict, malformed output rejected, verdict cache keyed by trees + packet + check results.
- `verification/verdict.py`: VERIFIED requires cited test/runtime/rule evidence, otherwise downgraded; no valid citations -> UNCERTAIN.

## Phase 5 — Runtime verification
- `verification/runtime.py`: checks detected only from `package.json` scripts / `pyproject.toml`; argv arrays, `shell=False`, timeout, output tails.
- `verification/deterministic.py`: checks-pass, tests-added, dependency manifests, schema/new tables, protected paths, secret scan (labeled suspicious).

## Phase 7 — End-to-end demo
- `examples/demo-auth-app` (baseline + flawed change) and `cerno demo`, which drives the real hook handlers. See `DEMO.md`.
- Tests: 24 passing (git, capture, Tree-sitter, graph, retrieval, intent, rules, agent loop with mocked API, API).

## Known gaps
- Target-specific test commands are not derived (whole suite runs).
- Call graph is name-based and approximate; no type resolution.
- Checks run against the current working tree, which may drift from the final snapshot if files change after Stop.
- Live Haiku runs were not exercised in this environment (no API key); the agent loop is covered by scripted-client tests.
