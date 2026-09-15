# Cerno dashboard

This directory contains the React dashboard for Cerno. It lists captured verification sessions and presents each
session's requirements, verdicts, evidence, changed files and symbols, unexpected changes, project checks, agent
claims, and usage metrics. FastAPI owns the data and analysis logic; the UI is a client of its `/api` endpoints.

## Run locally

From the repository root, start the API against a repository containing Cerno sessions:

```bash
uv run cerno dashboard --repo /path/to/repository
```

Then start the Vite development server in another terminal:

```bash
cd ui
npm ci
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` requests to `http://localhost:8000`, as configured in
`vite.config.ts`.

To create sample session data first, run `uv run cerno demo`; the default demo repository is `~/.cerno-demo`.

## Commands

| Command | Purpose |
| --- | --- |
| `npm run dev` | Start Vite with hot module replacement |
| `npm test` | Run the Vitest unit suite once |
| `npm run lint` | Check the UI with Oxlint |
| `npm run build` | Type-check and create the production bundle in `dist/` |
| `npm run preview` | Preview the production bundle locally |

The backend serves `ui/dist` from the dashboard command when a production bundle exists.

## Current tests

`src/lib/session-view.test.ts` contains three unit cases:

- A capturing session marks the first analysis stage active and later stages pending.
- A complete session marks every analysis stage done.
- Verdicts are counted by `VERIFIED`, `LIKELY`, `UNCERTAIN`, and `VIOLATED`, including zero defaults for statuses that
  are absent.

Run them with:

```bash
npm test
```

The API contract itself is covered by the Python test suite in `../tests/test_agent_api.py`.

## Structure

```text
src/pages/       session list and session detail pages
src/components/  verdict, evidence, requirement, and progress UI
src/lib/api.ts   typed wrapper around the FastAPI endpoints
src/lib/session-view.ts  derived progress and verdict-count helpers
src/types.ts     API response types used by the dashboard
```

The app uses React 19, React Router, TypeScript, Vite, Tailwind CSS, Base UI, and Vitest.
