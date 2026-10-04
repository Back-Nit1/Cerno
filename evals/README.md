# Cerno review benchmark

This benchmark measures whether review findings land on known production-risk defects without flagging
correct changes. Its ground truth is the combination of human labels and executable regression tests, not a
model's judgment.

## Cases and fixture validation

The 24 cases under `evals/cases/` contain 8 correct changes, 10 violations, and 6 changes whose safety depends
on unavailable external code or configuration. Sixteen cases are in the development split and eight are held
out. Both JavaScript and Python projects are represented. `v01_demo_session_ttl` ports the repository's demo
session-lifetime regression; other violations include a removed expiry filter, an API return-shape break visible
from an unchanged caller, duplicate pagination, a removed permission check, personal data in a log, and a
destructive migration.

Validate every fixture without a model or network access:

```console
python -m evals.check_fixtures
```

For correct cases, both the base and changed project tests must pass. For violations, the case's
`regress_test.*` is injected into the base and changed trees: it must pass against the corrected base and fail
against the seeded defect. The validator also checks the label schema, case counts, split balance, language mix,
and that every expected line range is wholly inside modified lines. CI runs the same validation through
`tests/test_evals_fixtures.py`.

## Running arms

Set `CERNO_MODEL` to choose the model and `OPENAI_API_KEY` for the model-backed arms. Run each arm with the same
model and number of repetitions:

```console
python -m evals.run --arm diff --split dev --runs 3 --out evals/results/
python -m evals.run --arm rules --split dev --runs 3 --out evals/results/
python -m evals.run --arm cerno --split dev --runs 3 --out evals/results/ \
  --cerno-cmd '/path/to/other/worktree/.venv/bin/cerno'
```

The `diff` arm gives one model call only the unified diff. The `rules` arm makes the same call with the built-in
rule descriptions and any case-specific `rules.toml`. Both use `cerno.llm.openai.LLM` and the same strict
function schema. The `cerno` arm materializes a two-commit repository and invokes the supplied command as
`cerno review --base <base> --head <head> --format json`; a finding with `shown: false` remains in the raw result
but is not scored as surfaced.

Every result includes raw outputs, per-case findings and labels, wall time, token counts, nullable cost, the
latest commit affecting `evals/cases`, and a content digest. Files under `evals/results/` are ignored by Git.
Costs remain null unless the reviewed command supplies one; the benchmark never invents prices.

Held-out runs are deliberately gated and should happen only once tuning is finished:

```console
python -m evals.run --arm cerno --split heldout --runs 3 --out evals/results/ \
  --cerno-cmd cerno --i-am-done-tuning
```

Without the acknowledgement, the runner refuses the held-out split. Do not inspect held-out outputs while
tuning prompts, rules, thresholds, or scoring.

## Reporting and metrics

Render every compatible result file below a file or directory as Markdown:

```console
python -m evals.report evals/results/
```

A violation is detected only when a surfaced finding names the labelled file and its starting line falls inside
the labelled range. Rule-id agreement is reported separately, so a right location with the wrong rule can count
as detection without being presented as full agreement. The report contains:

- violations detected and missed;
- agreement with the labelled rule id among located detections;
- correct cases with at least one false positive and findings per correct case;
- insufficient cases on which the arm correctly abstained by surfacing no finding;
- location accuracy: correctly located findings divided by all surfaced findings on violation cases;
- for Cerno only, true violation cases missed from shown findings but present at the right location among
  suppressed findings;
- median per-case wall time, input-plus-output tokens, and cost.

Every metric is shown as a minimum–maximum range over runs rather than only a mean. A single run therefore has
the same value at both ends. `n/a` cost means at least one scored case did not provide a price.

## Limits

These are synthetic cases written by Cerno's own authors. The projects are deliberately small, cover only
Python and JavaScript, and do not reproduce the dependency graphs, operating conditions, or ambiguity of large
production systems. A run evaluates one configured model, and 24 cases is a small sample, so run-to-run ranges
are wide. The development split is especially vulnerable to tuning overfit. Results support comparisons only
for the exact checked-in cases, model, runner revision, and settings recorded in a results file. This document
makes no accuracy, latency, or cost claim without such a file.
