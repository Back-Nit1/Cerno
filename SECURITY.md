# Security

## Reporting a vulnerability

Please report privately through GitHub: **Security → Report a vulnerability** on this repository. Do not
open a public issue for something exploitable. Include the version or commit, what you ran, and what
happened.

## What Cerno trusts, and what it does not

Cerno reads code it did not write and sends parts of it to a model. It treats the following as untrusted:
the diff under review, everything in the repository checkout, and everything the model returns.

| Risk | What Cerno does |
| --- | --- |
| A pull request runs code with the reviewer's credentials | The GitHub Action always passes `--no-checks`. `cerno review --head/--pr` skips project checks. When checks do run, `*_API_KEY`, `*_TOKEN` and similar variables are removed from the child environment. |
| A pull request weakens the rules it is judged by | With `--head` or `--pr`, `.cerno.toml` is read from the base revision, not from the change. |
| A pull request plants configuration or cached verdicts | The Action sets `CERNO_NO_DOTENV=1` and keeps its database outside the checkout. `cerno review` refuses a database that is committed to the repository. |
| Runaway spend on the API key | Per run: `CERNO_MAX_MODEL_CALLS`, `CERNO_MAX_TOTAL_TOKENS`, and a client-side `CERNO_MAX_CALLS_PER_MINUTE`. Every numeric setting has a hard ceiling in code, and a rules file may define at most 50 rules. |
| Prompt injection in reviewed code | The model has read-only tools scoped to the repository and no shell. A verdict cannot be `confirmed` without test, runtime or rule evidence, and a location outside the diff is dropped. Injection can still cause a wrong `UNCERTAIN`; it cannot make Cerno write or execute anything. |
| Secrets leaving the machine | Credential files and `.cernoignore` paths are never read for the model; text sent to the model is pattern-redacted. Redaction is best-effort: do not rely on it for a repository that stores secrets in source. |
| A web page driving the local dashboard | The dashboard binds to loopback unless `--allow-remote` is passed, rejects requests whose `Host` is not this machine, and refuses cross-origin writes. It has **no authentication**: do not expose it to a network. |
| Option injection into git | Refs from flags and HTTP bodies are rejected if they could be read as options. All subprocesses use argument arrays, never a shell. |
| Compromised CI dependencies | Workflow actions are pinned to commit SHAs; Dependabot proposes updates. |

## Known limits

- The dashboard has no authentication.
- Secret redaction is pattern-based and will miss unusual formats.
- On a same-repository pull request, the workflow in this repository runs the Action from the pull
  request's own checkout with the repository's `OPENAI_API_KEY`. Anyone who can push a branch here can
  therefore use that key. Pull requests from forks receive no secrets.
- Repository content selected as evidence is sent to the configured model provider.
