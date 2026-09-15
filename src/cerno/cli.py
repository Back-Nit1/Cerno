"""Cerno CLI. Heavy modules are imported inside commands so hook invocations stay fast."""

import json
import sys
from pathlib import Path

import typer

from cerno.analysis import git
from cerno.capture.hooks import HOOK_EVENTS, run_hook_safely
from cerno.config import Settings, db_file

app = typer.Typer(no_args_is_help=True, help="Cerno: evidence-backed verification for Claude Code.")
hook_app = typer.Typer(help="Internal Claude Code hook entrypoints (read JSON from stdin).")
history_app = typer.Typer(help="Repository history: commits, file/symbol history, markers.")
app.add_typer(hook_app, name="hook")
app.add_typer(history_app, name="history")
REPO = typer.Option(None, "--repo", help="Repository root (defaults to the current git repository).")
MARK = {"VERIFIED": "✓", "LIKELY": "~", "UNCERTAIN": "?", "VIOLATED": "✕"}


def _root(repo: Path | None) -> Path:
    start = (repo or Path.cwd()).resolve()
    return git.get_repository_root(start) or start


def _db(root: Path):
    from cerno.storage.sqlite import DB

    return DB(db_file(root, Settings.load(root)))


@app.command()
def init(repo: Path | None = REPO):
    """Install Cerno hooks into .claude/settings.json (merged, with a backup) and ignore .cerno/."""
    from cerno.capture.hooks import install_hooks

    root = _root(repo)
    if git.get_repository_root(root) is None:
        typer.secho(f"{root} is not a git repository; Cerno needs git for attribution.", fg="red")
        raise typer.Exit(1)
    result = install_hooks(root)
    typer.echo(f"settings: {result['settings']}")
    typer.echo(f"backup:   {result['backup'] or '(no previous settings)'}")
    typer.echo(f"hooks added: {', '.join(result['added']) or 'none (already installed)'}")
    if result["gitignore_updated"]:
        typer.echo("added .cerno/ to .gitignore")
    typer.echo("Run `cerno doctor` to check the setup.")


@app.command()
def doctor(repo: Path | None = REPO):
    """Check environment, hooks, storage, and detected project checks."""
    from cerno.capture.hooks import _is_ignored
    from cerno.verification.runtime import detect_checks

    root = _root(repo)
    settings = Settings.load(root)
    settings_file = root / ".claude" / "settings.json"
    hooks_text = settings_file.read_text() if settings_file.exists() else ""
    rows = [
        ("python >= 3.12", sys.version_info >= (3, 12), sys.version.split()[0]),
        ("git repository", git.get_repository_root(root) is not None, str(root)),
        ("hooks installed", all(f"cerno hook {h}" in hooks_text for h in HOOK_EVENTS), str(settings_file)),
        (".cerno ignored", git.get_repository_root(root) is not None and _is_ignored(root), ".gitignore"),
        ("OPENAI_API_KEY", bool(settings.api_key), f"model {settings.model}" if settings.api_key
         else "absent: deterministic analysis only"),
    ]
    try:
        from cerno.analysis.tree_sitter import parse_source

        rows.append(("tree-sitter", parse_source("x.ts", "function a() {}") is not None, "ts/tsx/js/py"))
    except Exception as exc:  # noqa: BLE001
        rows.append(("tree-sitter", False, str(exc)))
    try:
        _db(root)
        rows.append(("database", True, str(db_file(root, settings))))
    except Exception as exc:  # noqa: BLE001
        rows.append(("database", False, str(exc)))
    checks = detect_checks(root)
    rows.append(("project checks", bool(checks), ", ".join(f"{k}: {' '.join(v)}" for k, v in checks.items()) or "none"))
    for name, ok, detail in rows:
        typer.echo(f"{'✓' if ok else '✕'} {name:<18} {detail}")


@app.command()
def status(repo: Path | None = REPO):
    """List captured sessions."""
    from cerno.capture.snapshots import load_snapshot

    root = _root(repo)
    db = _db(root)
    sessions = sorted((s for s in db.list("sessions") if "prompts" in s), key=lambda s: s["updated_at"], reverse=True)
    if not sessions:
        typer.echo("No sessions captured yet. Run `cerno init`, then use Claude Code in this repository.")
    for s in sessions:
        snaps = "".join(k[0].upper() if load_snapshot(root, s["id"], k) else "-" for k in ("baseline", "final"))
        counts = " ".join(f"{k}:{v}" for k, v in (s.get("counts") or {}).items())
        typer.echo(f"{s['id'][:36]:<37} {s['status']:<10} snapshots[{snaps}] events:{db.count('events', s['id'])} "
                   f"prompts:{len(s['prompts'])} {counts}")


@app.command()
def analyze(session_id: str | None = typer.Argument(None), repo: Path | None = REPO,
            as_json: bool = typer.Option(False, "--json", help="Print verdicts as JSON.")):
    """Analyze a session (default: most recent)."""
    from cerno.pipeline import analyze as run
    from cerno.pipeline import latest_session_id

    root = _root(repo)
    db = _db(root)
    session_id = session_id or latest_session_id(db)
    if session_id is None:
        typer.secho("No sessions found.", fg="red")
        raise typer.Exit(1)
    settings = Settings.load(root)
    if not settings.api_key:
        typer.secho("OPENAI_API_KEY not set: semantic requirements will be UNCERTAIN.", fg="yellow")
    session = run(root, session_id, settings, on_progress=lambda label: typer.echo(f"● {label}", err=as_json))
    verdicts = db.list("verdicts", session_id)
    if as_json:
        typer.echo(json.dumps(verdicts, indent=2))
        return
    typer.echo(f"\n{session.task_summary}  (intent: {session.intent_source})")
    typer.echo(" · ".join(f"{v} {k}" for k, v in session.counts.items()))
    reqs = {r["id"]: r for r in db.list("requirements", session_id)}
    for v in verdicts:
        typer.echo(f"{MARK[v['status']]} {v['requirement_id']} {reqs[v['requirement_id']]['text'][:60]:<60} "
                   f"{v['status']:<9} [{v['evidence_level']}]\n    {v['summary']}")
    for a in db.list("change_assessments", session_id):
        if a["classification"] in {"UNEXPLAINED", "CONTRADICTORY"}:
            typer.echo(f"! {a['path']:<40} {a['classification']:<13} {a['impact']}  {a['why_impact']}")
    for c in db.list("agent_claims", session_id):
        typer.echo(f"claim {c['result']:<12} \"{c['text'][:80]}\"  {c['summary'][:80]}")
    u = db.get("model_usage", session_id, "__summary__") or {}
    typer.echo(f"\n{u.get('repo_files')} repo files · {u.get('files_inspected')} inspected · "
               f"{u.get('model_input_tokens')} input tokens · {u.get('model_calls')} model calls · "
               f"{u.get('lines_sent_to_model')}/{u.get('repo_lines')} source lines sent")


@app.command()
def dashboard(repo: Path | None = REPO, host: str = "127.0.0.1", port: int = 8000):
    """Serve the API (and the built UI from ui/dist if present)."""
    import uvicorn

    from cerno.api.app import create_app

    uvicorn.run(create_app(_root(repo)), host=host, port=port)


@app.command()
def demo(target: Path = typer.Option(Path.home() / ".cerno-demo", help="Where to create the demo repository."),
         run_analysis: bool = typer.Option(True, "--analyze/--no-analyze")):
    """Create the reproducible password-reset demo repository and capture a simulated Claude session."""
    from cerno.demo import build_demo

    session_id = build_demo(target)
    typer.echo(f"demo repo: {target}\nsession:   {session_id}")
    if run_analysis:
        analyze(session_id, target, False)
    typer.echo(f"\nDashboard: cerno dashboard --repo {target}")


@history_app.command("index")
def history_index(repo: Path | None = REPO,
                  limit: int = typer.Option(2000, help="Max new commits to index this run.")):
    """Incrementally index reachable git history (restartable; safe to re-run)."""
    from cerno.analysis import history as hist

    root = _root(repo)
    db = _db(root)
    identity = hist.index(root, db, limit=limit,
                          on_progress=lambda i, n: typer.echo(f"  indexed {i}/{n}", err=True))
    typer.echo(f"repo_id:          {identity.repo_id}")
    typer.echo(f"initial commit:   {identity.initial_commit}")
    typer.echo(f"latest indexed:   {identity.latest_indexed_commit}")
    typer.echo(f"indexed at:       {identity.indexed_at}")


@history_app.command("log")
def history_log(repo: Path | None = REPO, limit: int = 30,
                first_parent: bool = typer.Option(True, help="Chronological product-history view.")):
    """Show indexed commit history."""
    from cerno.analysis import history as hist

    root = _root(repo)
    db = _db(root)
    repo_id = hist.repo_id_for(root)
    commits = hist.first_parent_log(db, repo_id, limit=limit) if first_parent else hist.log(db, repo_id, limit=limit)
    for c in commits:
        merge = "M " if c.is_merge else "  "
        typer.echo(f"{c.hash[:10]} {merge}{c.author_date[:10]} {c.subject[:70]:<70} "
                   f"+{sum(f.additions for f in c.files)}/-{sum(f.deletions for f in c.files)}")


@history_app.command("path")
def history_path(path: str, repo: Path | None = REPO):
    """Show every indexed commit that touched `path` (follows renames)."""
    from cerno.analysis import history as hist

    root = _root(repo)
    db = _db(root)
    repo_id = hist.repo_id_for(root)
    commits = hist.path_history(db, repo_id, path)
    if not commits:
        typer.echo(f"No indexed commits touch {path}. Run `cerno history index` first?")
        raise typer.Exit(1)
    edges = hist.creation_and_deletion(commits, path)
    typer.echo(f"created: {edges['created']}")
    typer.echo(f"deleted: {edges['deleted']}")
    for c in commits:
        f = next(f for f in c.files if f.path == path or f.old_path == path)
        typer.echo(f"{c.hash[:10]} {c.author_date[:10]} {f.change_type:<9} {c.subject[:60]}")


@history_app.command("markers")
def history_markers(repo: Path | None = REPO,
                    add: str = typer.Option(None, help="Kind to record: good|bad|deployment|release|test_run|build|incident."),
                    commit: str = typer.Option(None), description: str = typer.Option("")):
    """List markers, or record one with --add."""
    from cerno.analysis import history as hist

    root = _root(repo)
    db = _db(root)
    repo_id = hist.repo_id_for(root)
    if add:
        resolved = git.git(root, "rev-parse", commit, check=False).strip() or commit if commit else None
        marker = hist.add_marker(db, repo_id, add, resolved, description or add)
        typer.echo(f"recorded {marker.id}: {marker.kind} @ {marker.commit or '(no commit)'}")
        return
    for m in hist.list_markers(db, repo_id):
        typer.echo(f"{m.id} {m.kind:<10} {(m.commit or '')[:10]:<10} {m.timestamp[:19]} {m.description}")


@app.command()
def diagnose(repo: Path | None = REPO, good: str = typer.Option(None), bad: str = typer.Option(None),
            since: str = typer.Option(None), until: str = typer.Option(None), query: str = typer.Option(None),
            file: str = typer.Option(None, "--file", help="Suspected file path."),
            symbol: str = typer.Option(None, help="Suspected symbol name."),
            dependency: str = typer.Option(None, help="Suspected dependency name."),
            incident: str = typer.Option(None, help="Free-text incident description."),
            as_json: bool = typer.Option(False, "--json")):
    """Diagnose a production breakage: rank suspect commits between a known-good and known-bad point."""
    from cerno.verification.diagnosis import diagnose as run_diagnosis

    root = _root(repo)
    db = _db(root)
    settings = Settings.load(root)
    result = run_diagnosis(root, db, settings, good=good, bad=bad, since=since, until=until, query=query,
                           suspected_path=file, suspected_symbol=symbol, suspected_dependency=dependency,
                           incident_description=incident)
    if as_json:
        typer.echo(json.dumps(result.model_dump(), indent=2))
        return
    typer.echo(result.narrative)
    typer.echo(f"model used: {result.model_used}\n")
    for s in result.suspects[:15]:
        typer.echo(f"[{s.evidence:<15}] {s.score:>5.1f}  {s.ref[:10] if s.kind == 'commit' else s.ref:<40} {s.reason}")
    if result.bisect_plan:
        typer.echo("\nSuggested manual bisect (not run automatically):")
        for line in result.bisect_plan:
            typer.echo(f"  {line}")


def _register(hook: str) -> None:
    @hook_app.command(hook)
    def _command():
        run_hook_safely(hook, sys.stdin.read())


for _hook in HOOK_EVENTS:
    _register(_hook)
