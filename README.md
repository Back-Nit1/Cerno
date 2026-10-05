# Cerno

> **Coding agents shouldn't grade their own homework.**

Cerno is a local-first verification tool for code that AI agents write. It does two things:

1. **Reviews a diff before it merges** and reports only problems that could hurt production: a broken
   caller, a weakened permission check, leaked data, a destructive migration, an unintended behaviour
   change. Everything else is suppressed.
2. **Tracks a Claude Code session** — what the developer asked for, what Claude changed, and whether the
   evidence supports Claude's claim that the work is done.

Both are built on one rule, enforced in code: a model's opinion alone is never proof. A finding is only
`confirmed`, and a requirement only `VERIFIED`, when a test, a runtime check or a deterministic rule backs it.

*Cerno* is Latin for "to discern, to decide."

## Status

Early, pre-release software (`0.1.0`, no tagged release, not on PyPI).

- The deterministic parts — git analysis, symbol extraction, rules, the finding gate, reports — are covered by
  the test suite and work without an API key.
- The model-backed parts are tested against a scripted client. A benchmark exists in [`evals/`](evals/README.md),
  but **no results have been published yet**, so this README makes no accuracy, cost or token-saving claims.
- Symbol-level analysis covers JavaScript, TypeScript and Python.

## Install

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and git. Node is needed only for the demo and the dashboard.

```bash
git clone https://github.com/N1tu-Mar/Cerno.git
cd Cerno
uv sync
cp .env.example .env     # optional: set OPENAI_API_KEY and CERNO_MODEL
uv run cerno --help
```

To use it in another repository, point uv at the clone:

```bash
uv run --project /path/to/Cerno cerno review --base main
```

Without `OPENAI_API_KEY`, every command still runs: deterministic evidence is collected and anything a model
would have judged is reported as insufficient evidence instead of guessed.

## Review a diff

```bash
cerno review --base main                      # your working tree against main
cerno review --base main --head feature-x     # two committed revisions
cerno review --staged --no-checks             # what you are about to commit; nothing is executed
cerno review --pr 42 --format md              # a GitHub pull request, as Markdown (needs the gh CLI)
```

Exit code `0` means nothing to show, `1` means at least one shown finding, `2` means the review itself failed.

### What gets reported

Five built-in rules, each describing a concrete harm:

| Rule | Harm | Severity |
| --- | --- | --- |
| `CERNO-CONTRACT` | An existing caller or published API contract is broken | high |
| `CERNO-AUTHZ` | An authentication, permission or validation check is removed or weakened | critical |
| `CERNO-DATA-EXPOSURE` | Secrets, personal data or another tenant's records reach logs, responses or files | critical |
| `CERNO-DESTRUCTIVE` | An irreversible data or schema change | critical |
| `CERNO-BEHAVIOR` | Existing behaviour changes outside the apparent purpose of the diff | high |

Add your own, or switch built-ins off, in `.cerno.toml`:

```toml
[review]
disable = ["CERNO-BEHAVIOR"]
min_severity = "high"

[[rule]]
id = "PAY-001"
text = "Refunds must never exceed the captured amount."
severity = "critical"
paths = ["src/payments/**"]
```

### What gets suppressed

A finding is shown only when all four hold:

1. the rule's verdict is `VIOLATED`;
2. its severity is at or above `min_severity`;
3. its location is inside lines the diff changed;
4. it cites evidence beyond the diff itself — a caller, a test, a check result or a deterministic rule hit.

Anything else stays in the JSON with `shown: false` and a reason, so precision can be measured. Style, naming,
formatting and refactor suggestions are never reported. Silence on a clean diff is the correct output.

Full reference: [`docs/review.md`](docs/review.md).

## GitHub Action

A report-only Action runs the same review on pull requests and writes the result to the job summary:

```yaml
on:
  pull_request:

permissions:
  contents: read

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0
          persist-credentials: false
      - uses: N1tu-Mar/Cerno@main
        with:
          openai-api-key: ${{ secrets.OPENAI_API_KEY }}
```

It never executes code from the pull request, never comments, approves or merges, and needs only read access.
Pull requests from forks run without secrets and report deterministic results only. Inputs, outputs and the
security model are in [`docs/github-action.md`](docs/github-action.md).

## Track a Claude Code session

```bash
cerno init       # merge hooks into .claude/settings.json (a backup is kept) and ignore .cerno/
cerno doctor     # check the setup
# ... use Claude Code as usual ...
cerno status     # list captured sessions
cerno analyze    # verify the latest session
```

Hooks record each prompt, each tool event and a git tree snapshot per prompt. They make no network calls and
never block Claude Code; a hook failure exits 0. At `cerno analyze`:

```text
Claude Code hooks ──> prompts, tool events, a git checkpoint per prompt
                        │
                        ▼
       Intent compiler: prompts ─> requirements R1..Rn (active and superseded)
                        │
        ┌───────────────┴────────────────┐
        ▼                                ▼
 Change analysis                   Project checks
 git trees + Tree-sitter symbols   the project's own test/build/lint commands
 + per-prompt diff segments
        └───────────────┬────────────────┘
                        ▼
      Deterministic rules ──(settled)──────────────┐
                        │ (not settled)            │
                        ▼                          │
      Ranked, bounded evidence packet              │
                        ▼                          │
      Verification agent (read-only tools,         │
      bounded steps, must cite evidence ids)       │
                        ▼                          ▼
      Verdict rules ──> drift analysis · claim checks · usage ──> SQLite ──> API ──> dashboard
```

Each active requirement gets one verdict:

| Verdict | Meaning |
| --- | --- |
| **VERIFIED** | Test, runtime or deterministic proof |
| **LIKELY** | Strong code evidence, no proof |
| **UNCERTAIN** | Missing or conflicting evidence, or the budget was spent |
| **VIOLATED** | Evidence contradicts the requirement |

Cerno also flags changed files no requirement explains, and checks statements in Claude's final message
("all tests pass", "no schema changes") against what the evidence shows.

### Multi-prompt sessions

Developers refine, correct and replace their own requests. Cerno classifies each prompt's relationship to the
earlier ones — `new_task`, `refinement`, `correction`, `replacement`, `continuation` or `ambiguous` — and
threads that through the requirements:

> **P1:** "Use JWT reset tokens."
> **P2:** "Actually, do not use JWT. Store opaque reset tokens in the database instead."

P2 is a `correction`. The JWT requirement stays visible for history but is marked superseded and receives no
verdict. A relationship Cerno cannot classify with a clear signal is reported `ambiguous` for the developer to
resolve; it is never silently reconciled.

Each changed file is attributed to the prompt whose segment it changed in. This is correlation in time, not
proof of cause; a matching tool event on the same path upgrades the attribution from `temporal` to `direct`.

```bash
cerno timeline [session-id] [--json]
```

A reproducible walkthrough is in [`DEMO.md`](DEMO.md): `uv run cerno demo`.

## Repository history and diagnosis

Cerno can index a repository's commit history, link commits back to the sessions and prompts that produced
them, and rank suspect commits for a breakage between a known-good and a known-bad point:

```bash
cerno history index
cerno history path src/auth/session.js
cerno diagnose --good v1.2.0 --bad HEAD --file src/auth/session.js
```

See [`docs/history.md`](docs/history.md).

## Commands

| Command | Purpose |
| --- | --- |
| `cerno review` | Review a diff for production risk |
| `cerno init` | Install Claude Code hooks, ignore `.cerno/` |
| `cerno doctor` | Check git, hooks, key, Tree-sitter, database, detected checks |
| `cerno status` | List captured sessions |
| `cerno analyze [session-id] [--json]` | Verify a session |
| `cerno timeline [session-id] [--json]` | Show the structured prompt timeline |
| `cerno history index \| log \| path \| markers` | Index and query commit history |
| `cerno diagnose` | Rank suspect commits for a breakage |
| `cerno dashboard` | Serve the API and, if built, the web UI on `127.0.0.1:8000` |
| `cerno demo` | Build and analyze the demo repository |

The dashboard UI is a separate build: `cd ui && npm ci && npm run build`, then `cerno dashboard`. Its HTTP API
is listed in [`src/cerno/api/app.py`](src/cerno/api/app.py) and [`src/cerno/api/history.py`](src/cerno/api/history.py).

## Configuration

Set through environment variables or a `.env` file in the repository root; see [`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | unset | Enables model-backed verification |
| `CERNO_MODEL` | see `.env.example` | Model id; set one your account can use |
| `CERNO_MAX_TOOL_STEPS` | 8 | Agent turns per requirement or rule |
| `CERNO_MAX_MODEL_CALLS` | 200 | Model calls per run |
| `CERNO_MAX_TOTAL_TOKENS` | 3000000 | Input plus output tokens per run |
| `CERNO_MAX_CALLS_PER_MINUTE` | 60 | Client-side rate limit |
| `CERNO_CHECK_TIMEOUT` | 300 | Seconds per project check |
| `CERNO_PRICE_INPUT_PER_MTOK`, `CERNO_PRICE_OUTPUT_PER_MTOK` | unset | Your prices; cost is reported only when set |
| `CERNO_DB_PATH` | `.cerno/cerno.db` | Local state |

A run that reaches a budget stops with an error. Every numeric setting has a hard ceiling in code.

## Privacy and security

- **What leaves your machine.** Only the evidence selected for a requirement or rule — ranked diff hunks,
  symbol source, search hits, check output — is sent to the OpenAI Responses API, with `store=False`. The whole
  repository is never sent.
- **What is excluded.** `.env`, key and credential files, `.gitignore`d paths and anything in `.cernoignore`.
  Text sent to the model is pattern-redacted for common secret formats; this is best-effort.
- **What the model can do.** Read through a fixed set of repository-scoped tools. It has no shell and no write
  access.
- **What Cerno executes.** Only the project's own test, build, lint and typecheck commands, with credential
  variables removed from their environment. `--no-checks` disables them, and they are skipped when reviewing
  a revision other than your working tree.
- **The dashboard has no authentication.** It binds to loopback and refuses cross-origin writes; do not expose
  it to a network.

The threat model, the known limits and how to report a vulnerability are in [`SECURITY.md`](SECURITY.md).

## Evaluation

[`evals/`](evals/README.md) holds 24 labelled changes — 8 correct, 10 violations, 6 where the honest answer is
insufficient evidence — with a regression test behind every seeded defect. It compares three arms on the same
model: a diff-only review, a diff-plus-rules review, and `cerno review`.

```bash
python -m evals.check_fixtures                                      # validate the cases; no model, no network
python -m evals.run --arm cerno --split dev --runs 3 --out evals/results/ --cerno-cmd cerno
python -m evals.report evals/results/
```

The cases are synthetic, small, and written by this project's authors; the sample is too small for narrow
confidence intervals. Results will be published here once the held-out split has been run.

## Development

```bash
uv run pytest -q          # backend, capture, analysis, review, reports, evals fixtures
uv run ruff check .
cd ui && npm ci && npm test && npm run lint
```

The Python suite needs Node on the path: the demo fixture runs `node --test`. Model calls are scripted, so no
API key is required. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

```text
src/cerno/  cli.py config.py models.py privacy.py pipeline.py demo.py
            capture/       hooks, snapshots, prompt timeline, transcript
            intent/        prompts -> requirements
            analysis/      git, Tree-sitter symbols, call graph, retrieval, segments, history
            verification/  rules, project checks, agent, tools, verdict rules, diagnosis
            review/        diff review: revisions, rules file, engine and gate
            report/        Markdown rendering        github/  pull-request resolution
            llm/ storage/ evidence/ api/
ui/         React + Vite dashboard
evals/      benchmark cases, runner and report
docs/       review.md, github-action.md, history.md
```

## Known limitations

- The call graph is name-based and approximate; there is no type resolution.
- The whole test suite runs; tests are not selected per change.
- Project checks run against the working tree, not against a stored snapshot.
- Prompt-to-change attribution is temporal, not causal.
- Without an API key, prompt classification falls back to a conservative heuristic that reports `ambiguous`
  whenever it is unsure.
- A review sees two trees. It does not read commit messages or the pull-request description.
- Repository history is indexed from `HEAD` only.

## Roadmap

Posting findings as pull-request comments with tracking across new commits · preparing fixes on a separate
branch · targeted test selection · more languages.

## License

No license has been chosen yet. Until a `LICENSE` file is added, the code is published for reading and
evaluation only and normal copyright applies.
