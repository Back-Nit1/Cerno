# Contributing to Cerno

## Setup

Cerno is a Python 3.12 CLI managed with [uv](https://docs.astral.sh/uv/), plus a Vite/React dashboard
under `ui/`. Node is required even for the Python suite: one test fixture builds a demo JavaScript
project and runs `node --test` against it.

```sh
uv sync                 # Python dependencies into .venv
cd ui && npm ci         # dashboard dependencies
```

Copy `.env.example` to `.env` if you want the model-backed path. Without `OPENAI_API_KEY` every command
still runs and reports deterministic results only — that path is covered by tests and is not a
degraded mode you need to avoid.

## The three checks

Run all three before you open a pull request. CI (`.github/workflows/ci.yml`) runs exactly these.

```sh
uv run pytest -q
uv run ruff check .
cd ui && npm test
```

Ruff is configured in `pyproject.toml`: 110-column lines, `E,F,I,B,UP`. Match the surrounding code —
heavy imports inside CLI commands, `subprocess` with argv lists and `shell=False`, pydantic v2 models,
terse docstrings. Sub-packages under `src/cerno/` are namespace packages: do not add `__init__.py`.

New dependencies need a reason in the pull request. `pyproject.toml` and `uv.lock` change together
(`uv lock`), never one without the other.

## Commits

One working step per commit, present tense, prefixed with a conventional type:

```
feat(review): suppress findings outside the changed hunks
fix(report): escape backticks in paths
docs(github-action): state the fork behaviour
```

Stage paths by name rather than `git add -A`, so unrelated local files do not ride along.

## Adding a review rule

A rule is a sentence about what must not happen in production, plus a severity. `cerno review` turns
each rule into one question about the diff and answers it from evidence.

Project rules live in `.cerno.toml` at the repository root:

```toml
[review]
builtin = true                    # keep the built-in rules on
disable = ["CERNO-BEHAVIOR"]      # built-in ids to switch off
min_severity = "high"             # findings below this are suppressed

[[rule]]
id = "PAY-001"
text = "Refunds must never exceed the captured amount."
severity = "critical"             # critical | high | medium | low
paths = ["src/payments/**"]       # optional: only applies if a changed file matches
```

Guidance that makes rules work:

- One checkable claim per rule. "Refunds must never exceed the captured amount" can be settled from
  the code; "payments code should be clean" cannot, and will land in `insufficient` every time.
- Name the symbols, files or values involved. The rule text is the only context the reviewer gets.
- Scope with `paths` when a rule is only meaningful in part of the tree. An unscoped rule is asked
  about every diff.
- Set `severity` to the production consequence, not to how annoyed you are. `min_severity` defaults to
  `high`, so a `medium` rule is written down and measured but not shown.

The built-in rule ids — `CERNO-CONTRACT`, `CERNO-AUTHZ`, `CERNO-DATA-EXPOSURE`, `CERNO-DESTRUCTIVE`,
`CERNO-BEHAVIOR` — are defined in the review engine. Changing one of those changes every repository
that uses Cerno, so it needs a test showing a real diff it catches and a real diff it stays quiet on.

Sample review results, used as fixtures and as documentation of the result schema, live in
`examples/review/` with their expected Markdown beside them. Regenerate the Markdown with
`uv run python -m cerno.report.markdown examples/review/<name>.json`.
