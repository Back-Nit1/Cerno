# Cerno Demo (under 5 minutes)

**Point: tests can pass while the developer's intent is still violated.**

## Setup (once)

```bash
uv sync
cp .env.example .env        # add OPENAI_API_KEY for semantic verdicts
(cd ui && npm install && npm run build)   # dashboard, optional
```

Node 18+ must be on PATH (the demo app's tests use `node --test`).

## 1. Create the scenario

```bash
uv run cerno demo --target ~/.cerno-demo
```

This builds a small auth app, commits it, adds some **uncommitted developer work** (a README edit), then replays a
Claude Code session through Cerno's real hook handlers:

> Add password-reset support. Reset tokens must expire after 30 minutes. Do not change existing login/session
> behavior. Use environment variables for credentials. Add tests.

The simulated Claude change adds `reset.js`, `mailer.js`, and `reset.test.js` correctly, but also:
- changes `SESSION_TTL_MS` in `session.js` from 24 hours to 30 minutes (breaks the login/session invariant);
- raises `MAX_FAILED_ATTEMPTS` in `rateLimit.js` from 5 to 50 (unexplained).

Claude's final message claims "All tests pass. Existing login and session behavior is unchanged." Both test files do pass.

`cerno demo` runs the analysis immediately. Re-run it with `uv run cerno analyze --repo ~/.cerno-demo`.

## 2. What to show

| Requirement | Expected |
| --- | --- |
| Add password-reset support | VERIFIED/LIKELY (with key) |
| Reset tokens expire after 30 minutes | VERIFIED/LIKELY (with key) |
| Do not change existing login/session behavior | **VIOLATED** (with key): `SESSION_TTL_MS` 24h -> 30m, reachable from `login()` |
| Use environment variables for credentials | VERIFIED/LIKELY (with key), secret scan evidence |
| Add tests | **VERIFIED** deterministically: `test/reset.test.js` added, `npm run test` exit 0 |

With or without a key:
- `README.md` is **not** listed as a Claude change (baseline attribution).
- `src/auth/session.js` and `src/auth/rateLimit.js` are flagged UNEXPLAINED/CONTRADICTORY, HIGH impact.
- Claim "All tests pass" CONFIRMED; claim "login and session behavior is unchanged" CONTRADICTED once R3 is VIOLATED.
- Efficiency line: repository files vs files inspected, tokens, source lines sent.

Without `OPENAI_API_KEY`, semantic requirements are UNCERTAIN (never guessed) and the intent contract uses the
labeled sentence fallback.

## 3. Dashboard

```bash
uv run cerno dashboard --repo ~/.cerno-demo     # http://127.0.0.1:8000 (serves ui/dist if built)
```

For UI development: `cd ui && npm run dev` (Vite proxies `/api` to the backend).

## 4. Multi-prompt sessions: revision-aware timeline

The scripted demo above is one prompt. Real sessions usually aren't — a developer refines, corrects, or
replaces earlier instructions. Cerno tracks that instead of flattening every prompt into one blob of intent.
This mini-scenario shows it directly, using the same hook entrypoints Claude Code calls, against a throwaway
repo:

```bash
mkdir /tmp/cerno-revision-demo && cd /tmp/cerno-revision-demo
git init -q && git commit -q --allow-empty -m init
uv run --project /path/to/Cerno cerno init

# Prompt 1
echo '{"session_id":"demo2","cwd":"'"$PWD"'","prompt":"Use JWT reset tokens."}' \
  | uv run --project /path/to/Cerno cerno hook user-prompt
echo "export function resetJwt() { return 'jwt'; }" > auth.js

# Prompt 2: a correction, not a refinement
echo '{"session_id":"demo2","cwd":"'"$PWD"'","prompt":"Actually, do not use JWT. Store opaque reset tokens in the database instead."}' \
  | uv run --project /path/to/Cerno cerno hook user-prompt
echo "export function resetOpaque() { return 'opaque'; }" > auth.js

echo '{"session_id":"demo2","cwd":"'"$PWD"'"}' | uv run --project /path/to/Cerno cerno hook stop

uv run --project /path/to/Cerno cerno analyze demo2 --repo .
uv run --project /path/to/Cerno cerno timeline demo2 --repo .
```

Expect:

- **P1** classified `new_task`; introduces a requirement "Use JWT reset tokens."
- **P2** classified `correction`, `supersedes: [demo2-P1]`. The JWT requirement is still listed by
  `cerno timeline` / in the dashboard, struck through, marked superseded by P2 — but it does **not** receive
  a verdict and is not counted in `VERIFIED/LIKELY/UNCERTAIN/VIOLATED` totals. A new, active requirement for
  opaque tokens records P2 as its origin.
- Each prompt's git checkpoint (`head`/`tree`) is real, even though nothing was ever committed — checkpoints
  are worktree tree snapshots, independent of the commit graph.
- `auth.js`'s change is attributed to P2 (the last prompt whose segment touched it) — **temporal**
  attribution unless a `PostToolUse` hook event on the same path makes it **direct**; see "Known limitations"
  in the README. It is evidence the change followed that prompt, not proof the prompt caused it.

Run without `OPENAI_API_KEY` and the classification above comes from the deterministic fallback (cue phrases
like "actually"/"do not use" plus requirement-keyword overlap) — same result, no model call, labeled
`fallback` in the session's `intent_source`.

## 5. Live Claude Code (optional)

```bash
cd your-repo && uv run --project /path/to/Cerno cerno init
claude            # work normally; hooks capture intent, tool events, snapshots
uv run --project /path/to/Cerno cerno analyze --repo .
```
