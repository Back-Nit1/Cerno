# The Cerno review Action

`action.yml` at the repository root is a composite GitHub Action that runs `cerno review` on a pull
request's diff and writes the result to the job summary. It reports; it does not gate, comment or
change anything, unless you ask it to fail the job.

## Usage

```yaml
name: cerno review

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
          rules: .cerno.toml
          fail-on-findings: "false"

      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: cerno-review
          path: cerno-review.json
          if-no-files-found: ignore
```

`fetch-depth: 0` is required: the review runs on the diff from `merge-base(base, head)` to `head`, and
a shallow clone has no merge base. `ref:` pins the head commit itself rather than the merge commit
GitHub synthesises, so the reported lines match the pull request's own files.

This repository's own copy of that workflow is `.github/workflows/cerno-review.yml`, which uses the
action locally (`uses: ./`).

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `openai-api-key` | `""` | OpenAI API key. Optional — see below. |
| `rules` | `.cerno.toml` | Rules file, relative to the repository root. Ignored when absent. |
| `fail-on-findings` | `"false"` | Fail the job when the review shows at least one finding. |
| `output` | `cerno-review.json` | Where the JSON result is written, relative to the repository root. |

Outputs: `result-file` (absolute path of the JSON result) and `exit-code` (the exit code of
`cerno review`).

Exit codes map to the job like this: `0` (no shown findings) and `1` (at least one shown finding)
both succeed by default, and `1` fails the job only when `fail-on-findings` is `"true"`. `2` means the
review itself failed — a bad ref, an unreadable rules file, a provider auth or connection failure —
and always fails the job, because a review that did not run is not a clean review.

## Security model

The Action is report-only by construction, not by convention:

- **`pull_request`, never `pull_request_target`.** A pull request from a fork therefore runs with a
  read-only token and no access to repository secrets. `pull_request_target` would hand secrets and a
  write token to a workflow evaluating code the fork controls; Cerno does not use it, and neither
  should a workflow wrapping it.
- **`permissions: contents: read` and nothing else.** The job cannot comment, approve, merge, push, or
  write a check. Findings land in `$GITHUB_STEP_SUMMARY` and in an uploaded artifact.
- **`--no-checks` is fixed on inside the Action.** `cerno review` can run a project's own test, lint
  and build commands when you invoke it locally, which means executing code from the branch under
  review. In CI on a pull request that code is untrusted, so the Action never passes a mode that would
  run it. There is no input to turn this off.
- **Event values reach the shell through `env:`, never through `${{ }}` inside a `run:` script.** A
  branch, title or author name is attacker-controlled text; interpolating it into a script body is a
  shell injection. Keep that discipline in any workflow you add.
- **Nothing writes to GitHub.** `cerno.github.pr.resolve_pr`, the `--pr N` path of the CLI, only runs
  `gh pr view`, `git cat-file` and `git fetch`. It never comments, pushes, approves or merges.

## What is sent to the model provider

With `openai-api-key` set, the review sends repository content to the OpenAI API: the changed hunks of
the diff, the text of the rules, and whatever excerpts the agent asks for while investigating. The
tools it can ask with are listed in `src/cerno/verification/tools.py` — symbol bodies before and after
the change, callers and references of a changed symbol, code search hits, related tests, and the
stdout tails of checks that ran (none in CI, since `--no-checks` is fixed). That is source code
leaving your CI environment for a third party. On a public repository the diff is already public; on a
private one, decide deliberately.

Cerno does not send `.env` files, keys or similar: `src/cerno/privacy.py` excludes those paths
outright and redacts secret-shaped strings it recognises elsewhere (`privacy.redact`,
`privacy.SECRET_PATTERNS`). Treat that as defence in depth, not as a guarantee — if your source holds
embedded credentials in a shape those patterns do not match, an agent reading that source reads them
too.

## Fork behaviour, and what a run without a key looks like

`secrets.OPENAI_API_KEY` is empty for a pull request from a fork. The Action handles that as a normal
outcome rather than an error:

- the job succeeds;
- deterministic rules still run and can still produce findings;
- every rule that needed a model is reported under `insufficient` with `OPENAI_API_KEY` named as the
  missing evidence;
- the summary footer reads `model none`.

`examples/review/no-api-key.json` and its rendered `no-api-key.md` are exactly that output. The
consequence worth knowing: a fork's pull request gets a weaker review than a branch in this
repository. That is the deliberate trade — a full review of a fork's code would require handing
secrets to a workflow that the fork controls.
