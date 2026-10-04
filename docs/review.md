# `cerno review`

Review the diff between a base and a head revision and report only problems that could hurt
production: broken callers or API contracts, weakened auth or validation, data exposure,
destructive data changes, unintended behaviour changes.

The product goal is high signal. A style nit or a speculative comment costs a reviewer more
than a missed finding, so anything that is not a high-severity, evidence-backed finding located
in the changed lines is suppressed. **Silence on a clean diff is the correct output.**

## Usage

```
cerno review [--repo PATH] [--base REF] [--head REF] [--staged] [--pr N]
             [--rules PATH] [--no-checks] [--format json|md] [--out FILE]
```

| Flag | Meaning |
| --- | --- |
| `--repo` | Repository root. Defaults to the current git repository. |
| `--base` | Base ref, default `main`. The diff runs from `merge-base(base, head)` to head. |
| `--head` | Head ref. Default: the current working tree, including uncommitted changes. |
| `--staged` | Review the index against `HEAD`. Ignores `--base` / `--head`. |
| `--pr N` | Resolve base and head through `cerno.github.pr.resolve_pr`. |
| `--rules` | Rules file. Defaults to `.cerno.toml` at the repository root, if present. |
| `--no-checks` | Never execute repository scripts (tests, build, lint, typecheck). |
| `--format` | `json` (default) or `md`, rendered by `cerno.report.markdown.render_markdown`. |
| `--out` | Write the report to a file instead of stdout. Progress always goes to stderr. |

Exit codes: **0** no shown findings, **1** at least one shown finding, **2** error (bad ref,
unreadable rules file, provider authentication or connection failure).

Without `OPENAI_API_KEY` the command still runs: deterministic rules supply evidence, every rule
is reported under `insufficient`, and the exit code is 0.

```bash
cerno review --base main                       # the working tree against main
cerno review --base main --head feature-x      # two committed revisions
cerno review --staged --no-checks              # what you are about to commit, nothing executed
cerno review --format md --out review.md
```

## Rules file

`.cerno.toml` at the repository root, or any file named with `--rules`:

```toml
[review]
builtin = true                    # default true
disable = ["CERNO-BEHAVIOR"]      # rule ids to switch off
min_severity = "high"             # default "high": findings below this are suppressed

[[rule]]
id = "PAY-001"
text = "Refunds must never exceed the captured amount."
severity = "critical"             # critical | high | medium | low
paths = ["src/payments/**"]       # optional; the rule applies only if a changed file matches
```

A rule whose `paths` match no changed file gets verdict `SKIPPED` and costs no model call.
A malformed file is exit 2 and the message names the offending key.

### Built-in rules

| id | Harm | Default severity |
| --- | --- | --- |
| `CERNO-CONTRACT` | An existing caller or published API contract is broken | high |
| `CERNO-AUTHZ` | An authentication, permission or validation check is removed or weakened | critical |
| `CERNO-DATA-EXPOSURE` | Secrets, personal data or other tenants' records reach logs, responses or files | critical |
| `CERNO-DESTRUCTIVE` | An irreversible data or schema change | critical |
| `CERNO-BEHAVIOR` | Existing behaviour changes outside the apparent purpose of the diff | high |

Each built-in text names the concrete harm, states what must be true before it may be reported,
and explicitly rules out style, naming, formatting, comments, dead code, type annotations, test
coverage, performance and refactor suggestions. Rule wording is the main precision control here:
if you add your own rules, describe a harm, not a preference.

## The gate

A finding is `shown: true` only when **all four** hold:

1. the rule's verdict is `VIOLATED`;
2. its severity is at or above `min_severity`;
3. its path and line fall inside a hunk this diff changed;
4. it cites at least one evidence item that is not merely the diff: a caller (`graph`), a test
   (`tests`), a check result (`check_*`), or a deterministic rule hit (`rule`).

Otherwise the finding stays in the JSON with `shown: false` and a short `suppressed_reason`, so
precision can be measured instead of guessed. The conditions are enforced in code, in
`cerno/review/engine.py:gate`, whatever the model claims.

`status` is `confirmed` when the cited evidence includes level `test`, `runtime` or `structural`;
otherwise `suspected`. A model's opinion alone never yields `confirmed`.

When a `VIOLATED` verdict cites only the diff, Cerno makes **one** further bounded attempt asking
the agent to confirm it with a caller or a test, or retract. If nothing turns up, the finding is
suppressed with `cited evidence is the diff alone`.

Locations come from the model but are never trusted: a location outside the changed hunks is
dropped before the gate runs (`cerno/verification/agent.py:valid_locations`).

`insufficient` lists rules whose evidence could not settle the question. That is a first-class
outcome, not a failure.

## What is sent to the model provider

Per rule, one evidence packet plus the results of the tools the agent chooses to call. The packet
contains: the rule text; summaries of deterministic rule hits; up to 12 ranked changed symbols and
files with up to four diff hunks (40 lines each); approximate caller paths; the exit codes of any
project checks that ran; and the list of changed file paths.

Tools can additionally return file diffs, symbol source, search hits, caller lists, test file names
and check output. Every string bound for the model passes through `Context.send`, which applies the
secret redaction and path exclusion in `cerno/privacy.py` (`.env`, keys, certificates, `.cerno/`,
`.git/`, plus anything in `.cernoignore`) and counts the lines sent.

Nothing is written to the repository, no shell command from the model is ever executed, and
`store=False` is set on every request so the provider does not retain the conversation.

Project checks are the only subprocesses, they come from the project's own configuration
(`package.json` scripts, `pyproject.toml` tools), and they run with `*_API_KEY`, `*_TOKEN`,
`*_SECRET` and `*_PASSWORD` stripped from the environment, so a reviewed branch's test script
cannot read the reviewer's credentials. `--no-checks` disables them entirely.

## Cost

`usage.cost_usd` is `null` unless you supply prices in USD per million tokens:

```
CERNO_PRICE_INPUT_PER_MTOK=...
CERNO_PRICE_OUTPUT_PER_MTOK=...
```

Cerno never invents a price. `usage` also reports wall time, model calls and token counts.

## Known limits

- **Call graph is name-based.** `find_callers` and the ranking matches symbols by name, not by
  resolved imports. Same-named methods on different classes are conflated, and dynamic dispatch
  is invisible.
- **Languages.** Symbol-level analysis covers JavaScript, TypeScript and Python (tree-sitter).
  Other languages are visible only as file diffs and text search.
- **Checks run against the working tree**, not against the revision under review. With
  `--head <ref>` the check result describes your checkout, not that ref. Use `--no-checks` if
  that would mislead.
- **Deterministic rules never settle a review rule.** They seed evidence; the agent decides. The
  heuristics in `verification/deterministic.py` were written for session requirements and are not
  safe arbiters of a review rule.
- A deterministic rule can produce a `VIOLATED` verdict with no location, which the gate then
  suppresses. Such findings appear in the JSON, never in the shown set.
- **One diff, no history.** The review looks at two trees. It does not read the branch's commit
  messages, its pull-request description, or the sessions that produced it.
