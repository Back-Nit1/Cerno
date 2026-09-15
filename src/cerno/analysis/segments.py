"""Prompt-segment git diffs: reuse the same tree snapshots hooks already captured (no repository
duplication) to slice the session into baseline -> P1 -> P2 -> ... -> Pn -> final boundaries, and map
each changed file to the prompt whose checkpoint opened the segment it changed in.

This is temporal attribution: correlation in time, not proof that a prompt *caused* a change. A
PostToolUse event on the same path upgrades a file's attribution to "direct" evidence.
"""

from pathlib import Path

from cerno.analysis import git
from cerno.analysis.graph import RepoIndex
from cerno.analysis.symbols import changed_symbols
from cerno.capture.snapshots import load_snapshot
from cerno.models import ChangeAttribution, DiffSegment, HookEvent, PromptRecord
from cerno.storage.sqlite import DB


def build_segments(root: Path, session_id: str, prompts: list[PromptRecord], db: DB) -> list[DiffSegment]:
    baseline = load_snapshot(root, session_id, "baseline")
    final = load_snapshot(root, session_id, "final")
    checkpoints: list[tuple[str, str | None, str]] = []  # (label, owning prompt id, tree)
    if baseline and baseline.tree:
        checkpoints.append(("baseline", None, baseline.tree))
    for p in sorted(prompts, key=lambda p: p.seq):
        if p.tree:
            checkpoints.append((f"prompt-{p.seq}", p.id, p.tree))
    if final and final.tree:
        checkpoints.append(("final", None, final.tree))
    if len(checkpoints) < 2:
        return []

    index_cache: dict[str, RepoIndex] = {}

    def index_for(tree: str) -> RepoIndex:
        if tree not in index_cache:
            index_cache[tree] = RepoIndex.from_tree(root, tree, db)
        return index_cache[tree]

    segments = []
    for (from_label, from_pid, from_tree), (to_label, _, to_tree) in zip(checkpoints, checkpoints[1:], strict=False):
        changes = git.get_changed_files(root, from_tree, to_tree) if from_tree != to_tree else []
        symbols = changed_symbols(changes, index_for(from_tree), index_for(to_tree)) if changes else []
        segments.append(DiffSegment(
            id=f"{from_label}->{to_label}", from_label=from_label, to_label=to_label,
            from_tree=from_tree, to_tree=to_tree, prompt_id=from_pid, files=changes, symbols=symbols,
        ))
    return segments


def attribute_changes(segments: list[DiffSegment], events: list[HookEvent]) -> list[ChangeAttribution]:
    """Attribute each changed path to the prompt whose segment most recently touched it. A PostToolUse
    event recorded for that path is direct evidence; absent that, attribution is temporal ordering only."""
    out: dict[str, ChangeAttribution] = {}
    for seg in segments:
        for c in seg.files:
            direct = any(e.path == c.path and e.event_type == "post_tool_use" for e in events)
            prompt_label = seg.prompt_id or "(session start)"
            out[c.path] = ChangeAttribution(
                path=c.path, prompt_id=seg.prompt_id, segment_id=seg.id,
                confidence="direct" if direct else "temporal",
                basis=(f"Claude tool event on {c.path} recorded after prompt {prompt_label}" if direct
                       else f"{c.path} changed between checkpoints {seg.from_label} and {seg.to_label}, "
                            f"following prompt {prompt_label} (temporal ordering, not proven causality)"),
            )
    return list(out.values())
