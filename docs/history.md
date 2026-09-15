# Repository history and diagnosis

Cerno's per-session capture (`capture/`, `analysis/segments.py`) answers "what happened during this
one Claude Code session, between a baseline and a final tree." This layer answers a different
question: "what happened across the whole repository, over many sessions and commits, and which of
those changes plausibly caused a later production breakage." It is additive: nothing here replaces
or duplicates the session/prompt-timeline/provenance structures added for revision-aware prompt
tracking (`models.PromptRecord`, `models.DiffSegment`, `models.ChangeAttribution`,
`capture/timeline.py`, `analysis/segments.py`) — it consumes them (see `file_provenance` below).

## Concepts

- **Repository identity** (`analysis/history.repo_id_for`): a `repo_id` derived from the git remote
  URL when one exists (normalized so `git@host:org/repo.git` and `https://host/org/repo` match), or
  a UUID persisted at `.cerno/repo_id` otherwise. Identity survives the repo being moved or re-cloned
  to a new path — only the `path` field on `RepoIdentity` changes.
- **Commit index** (`models.CommitRecord`, table `commits`): one row per indexed commit — hash, all
  parent hashes, author/committer info, subject, decorating refs, merge/root flags, changed files
  (path, old_path for renames, change type, +/-, binary flag), and manifest/schema/test flags. File
  contents are never copied into SQLite; only git object references (hashes) and metadata are stored,
  and blobs are read lazily via `git cat-file` when a query actually needs source text (symbol
  history).
- **First-parent vs. full graph**: `history.log()` returns every indexed commit, newest first
  (paginated). `history.first_parent_log()` walks only `parents[0]` from the latest indexed commit —
  the conventional "what actually shipped on this branch" chronological view. All parents of every
  commit are always stored, so the full merge graph is never discarded; first-parent is a read-time
  choice, not an indexing-time one.
- **Markers** (`models.Marker`, table `markers`): developer-provided facts — `good`, `bad`,
  `deployment`, `release`, `test_run`, `build`, `incident`. Never inferred from commit existence; a
  commit is not "good" just because it exists.
- **Session-commit link** (`models.SessionCommitLink`): for one session, the commits made between its
  captured baseline HEAD and final HEAD, with a confidence label:
  - `direct_tool_event` — a `git commit` Bash tool call was recorded during the session.
  - `session_attributed` — commits fall strictly between baseline and final HEAD, but no tool event
    proves Claude itself ran the commit (a human could have, in the same window).
  - `temporal_only` — a baseline/final HEAD was captured but no new commits exist between them.
  - `unknown` — no baseline/final HEAD was captured at all.
- **File provenance** (`analysis.history.file_provenance`): for one path within one session, answers
  which prompt preceded the change (via Agent 1's `capture.timeline.load_prompt_timeline` +
  `analysis.segments.build_segments`/`attribute_changes`), whether it was committed during the
  session, which requirement motivated it and whether that requirement was later superseded (via the
  `Requirement.origin_prompt_id`/`active`/`superseded_by` provenance fields), and what verification
  verdict existed for it.

## CLI

```bash
cerno history index --repo /path/to/repo [--limit 2000]
cerno history log --repo /path/to/repo [--first-parent/--no-first-parent] [--limit 30]
cerno history path src/auth.py --repo /path/to/repo
cerno history markers --repo /path/to/repo [--add good --commit HEAD --description "v1.0 shipped"]
cerno diagnose --repo /path/to/repo --good <commit> --bad <commit> --file src/auth.py --query "login accepts any password"
```

`cerno history index` is incremental and restartable: it only walks commits reachable from `HEAD` not
already indexed (`git log <last_indexed>..HEAD`), and durably advances `latest_indexed_commit` after
each commit is written — an interrupted run leaves no gap and creates no duplicate on the next run.

## Diagnosis

`cerno diagnose` / `POST /api/history/diagnose` narrows a commit range (explicit `--good`/`--bad`, a
time range, or recorded `good`/`bad` markers as defaults), then deterministically scores every
candidate commit against whatever evidence was given: a suspected file, symbol, or dependency; schema
or manifest changes; and keyword overlap between the query/incident text and commit subjects or
paths. Every suspect is labeled with an **evidence kind**:

- `fact` — a git-verifiable match (the commit actually touches the suspected file).
- `correlation` — a plausible link Cerno is inferring (keyword overlap, a schema change in the
  window).
- `model_inference` — OpenAI flagged this correlation as especially likely, after seeing only the
  bounded, redacted packet of already-scored candidates.

If `OPENAI_API_KEY` is set, a single bounded call may add a short narrative and relabel a
`correlation` it considers likely-causal as `model_inference` — it can never introduce a new suspect,
change a score, or see anything outside the packet (any ref it returns that isn't in the packet is
dropped). Without a key, diagnosis is fully deterministic and still ranks and links suspects. Cerno
never runs `git bisect`, checks out a commit, or resets anything; it prints a **manual bisect plan**
(the `git bisect start/good/bad` commands and a midpoint to try first) for the developer to run.

## Realistic example

`tests/test_regression_scenario.py` is the runnable version of this walkthrough:

1. **Session 1** ("Add a login endpoint that checks the password") commits a real password check.
   The commit is marked `good` (a shipped, verified release).
2. **Session 2**, later ("Refactor the login check to be a one-liner"), replaces the check with
   `return True` — a silent regression.
3. **Production incident**: "login accepts any password" is reported weeks later, with no known-bad
   commit in hand.
4. `cerno diagnose --file auth.py --query "login accepts any password"` starts from the `good` marker,
   defaults `--bad` to current `HEAD`, and returns exactly one candidate: session 2's commit, scored
   as a `fact` (it touches the suspected file).
5. `history.link_session` confirms that commit was made during session 2, and `file_provenance` links
   it back to the exact prompt ("Refactor the login check...") that preceded it.

## Migration

The storage engine (`storage/sqlite.py`) has always used one generic `(pk, session_id, item_id, seq,
data)` shape for every table — history-layer tables (`repo_identity`, `commits`,
`session_commit_links`, `markers`, `diagnoses`) reuse it verbatim, with `session_id` holding a
`repo_id` instead of a session id. Adding a table is therefore never destructive: `DB._migrate()`
gates `CREATE TABLE IF NOT EXISTS` behind `PRAGMA user_version < SCHEMA_VERSION` (now `2`), so an
older database gains the five new empty tables and nothing existing is touched or re-read.
`tests/test_history.py::test_migration_preserves_existing_rows_and_adds_history_tables` builds a
version-1 database by hand and asserts old rows survive the upgrade.

## Privacy

Commit subjects and any text sent to OpenAI in a diagnosis packet are passed through `privacy.redact`
first (the same secret patterns used everywhere else in Cerno). No file contents, remote URLs, or
commit bodies are ever included in a model call — only hash, subject, file count, and boolean flags
for the bounded set of already-scored candidates. `.env`, credential files, and `.cernoignore`
patterns are excluded from indexing exactly as they are from session capture (`privacy.is_excluded`).

## Known limitations

- Indexing follows `HEAD` only; other branches/tags are not separately walked (their commits are
  picked up once they're merged into what `HEAD` reaches, and their ref names are still recorded on
  any commit they decorate via `for-each-ref`).
- A force-push/rebase that discards the previously-indexed tip is detected (the repo doesn't crash),
  but the incremental range `old_tip..new_tip` can, for a truly diverged history, re-list commits that
  were already indexed under the old tip; `INSERT OR REPLACE` makes this idempotent (no duplicate
  rows), but it is not the most efficient possible reconciliation. A full re-index is always safe.
- Symbol history re-parses the repository tree (via Tree-sitter) for each commit in the query, bounded
  to the last 20 commits touching a path — appropriate for interactive queries, not a bulk scan.
- `--good`/`--bad` accept any git revision syntax (`HEAD~5`, a tag, etc.) on the command line, but
  markers resolve and store the concrete hash at record time so they stay meaningful as `HEAD` moves.
