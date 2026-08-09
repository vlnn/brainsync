import argparse
import os
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import requests

from brainsync.api import ApiClient, load_config
from brainsync.brain2notes import (
    MergePlan,
    adopt_text,
    plan_merge,
    push_body,
    related_of,
    strip_frontmatter,
    write_images,
)
from brainsync.garden import load_garden
from brainsync.sources import ApiSource
from brainsync.garden import parse_frontmatter


@dataclass(frozen=True)
class NotePair:
    filename: str
    base: str | None
    garden: str | None
    brain: str | None


@dataclass(frozen=True)
class SyncPlan:
    pushes: tuple[str, ...] = ()
    pulls: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    orphans: tuple[str, ...] = ()
    adoptions: tuple[str, ...] = ()


def classify(pair: NotePair) -> str:
    if pair.brain is None:
        return "orphan"
    if pair.garden is None:
        return "pull" if pair.base is None else "deleted"
    if pair.garden == pair.brain:
        return "noop"
    dirty = pair.garden != pair.base
    changed = pair.brain != pair.base
    if dirty and changed:
        return "conflict"
    if dirty:
        return "push"
    return "pull"


def plan_sync(pairs: list[NotePair]) -> SyncPlan:
    buckets: dict[str, list[str]] = {"push": [], "pull": [], "conflict": [], "deleted": [], "orphan": []}
    for pair in pairs:
        action = classify(pair)
        if action != "noop":
            buckets[action].append(pair.filename)
    return SyncPlan(
        pushes=tuple(sorted(buckets["push"])),
        pulls=tuple(sorted(buckets["pull"])),
        conflicts=tuple(sorted(buckets["conflict"])),
        deleted=tuple(sorted(buckets["deleted"])),
        orphans=tuple(sorted(buckets["orphan"])),
    )


def is_git_repo(repo: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--git-dir"], capture_output=True, text=True
    )
    return result.returncode == 0


def head_content(repo: Path, relative: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"HEAD:{relative}"], capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else None


def _file_content(path: Path) -> str | None:
    return path.read_text() if path.is_file() else None


def build_pairs(writes: dict[str, str], orphans: tuple[str, ...], notes_dir: Path) -> list[NotePair]:
    repo = notes_dir.parent
    filenames = sorted({*writes, *orphans})
    return [
        NotePair(
            filename=filename,
            base=head_content(repo, f"{notes_dir.name}/{filename}"),
            garden=_file_content(notes_dir / filename),
            brain=writes.get(filename),
        )
        for filename in filenames
    ]


def report_plan(plan: SyncPlan) -> None:
    lines = [
        *(f"adopt {name} (linking to its brain thought — garden body wins)" for name in plan.adoptions),
        *(f"push  {name}" for name in plan.pushes),
        *(f"pull  {name}" for name in plan.pulls),
        *(f"conflict {name} (both sides changed — resolve by hand, then commit)" for name in plan.conflicts),
        *(f"deleted-locally {name} (brain untouched — restore the file or delete the thought)" for name in plan.deleted),
        *(f"orphan {name} (thought gone from the brain)" for name in plan.orphans),
    ]
    print("\n".join(lines) if lines else "garden and brain are in sync")


def _push_note(filename: str, notes_dir: Path, client, names: dict[str, str], ids_by_slug: dict[str, str], rendered: str) -> None:
    garden_text = (notes_dir / filename).read_text()
    brain_id = parse_frontmatter(garden_text)["brain-id"]
    if related_of(strip_frontmatter(garden_text)) != related_of(strip_frontmatter(rendered)):
        print(
            f"{filename}: edits under ## Related are ignored — Related is derived from the plex",
            file=sys.stderr,
        )
    client.update_note_markdown(brain_id, push_body(garden_text, names[brain_id], ids_by_slug))


def _adopt_note(filename: str, thought_id: str, notes_dir: Path, client, title: str, ids_by_slug: dict[str, str], rendered: str) -> None:
    garden_text = (notes_dir / filename).read_text()
    client.update_note_markdown(thought_id, push_body(garden_text, title, ids_by_slug))
    (notes_dir / filename).write_text(adopt_text(rendered, garden_text, title))


def execute_plan(
    plan: SyncPlan,
    merge: MergePlan,
    notes_dir: Path,
    client,
    names: dict[str, str],
    ids_by_slug: dict[str, str],
) -> None:
    for filename in plan.adoptions:
        thought_id = merge.adopted[filename]
        _adopt_note(filename, thought_id, notes_dir, client, names[thought_id], ids_by_slug, merge.writes[filename])
    for filename in plan.pushes:
        _push_note(filename, notes_dir, client, names, ids_by_slug, merge.writes[filename])
    pulled_slugs = {Path(filename).stem for filename in plan.pulls}
    for filename in plan.pulls:
        (notes_dir / filename).write_text(merge.writes[filename])
    write_images(
        {key: data for key, data in merge.images.items() if key.split("/", 1)[0] in pulled_slugs},
        notes_dir,
    )


def make_api_source(notes_dir: Path):
    config = load_config(notes_dir.parent, os.environ)
    client = ApiClient(config=config, session=requests.Session())
    return client, ApiSource(client)


def names_by_id(snapshot) -> dict[str, str]:
    return {thought.id: thought.name for thought in snapshot.thoughts.values()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Two-way sync between the garden and TheBrain via the Local API")
    parser.add_argument("--notes-dir", type=Path, required=True)
    parser.add_argument("--tag", default=None)
    parser.add_argument(
        "--adopt-all",
        action="store_true",
        help="link brain-id-less md notes to same-named thoughts and push their bodies",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.notes_dir.is_dir():
        raise SystemExit(f"notes dir not found: {args.notes_dir}")
    if not is_git_repo(args.notes_dir.parent):
        raise SystemExit(
            f"{args.notes_dir.parent} is not a git checkout — git history is the sync base, "
            "so sync only works inside the garden repo (commit after each sync)"
        )
    client, source = make_api_source(args.notes_dir)
    try:
        snapshot = source.snapshot(args.tag)
    except requests.HTTPError as error:
        raise SystemExit(str(error))
    merge = plan_merge(snapshot, load_garden(args.notes_dir), args.tag, adopt=args.adopt_all)
    pairs = [
        pair
        for pair in build_pairs(merge.writes, merge.orphans, args.notes_dir)
        if pair.filename not in merge.adopted
    ]
    plan = replace(plan_sync(pairs), adoptions=tuple(sorted(merge.adopted)))
    for name in merge.skipped:
        print(f"skip  {name} (hand-written note with the same title exists)", file=sys.stderr)
    report_plan(plan)
    if args.dry_run:
        return
    ids_by_slug = {
        Path(filename).stem: brain_id
        for filename, brain_id in (
            (filename, parse_frontmatter(text).get("brain-id", "")) for filename, text in merge.writes.items()
        )
        if brain_id
    }
    execute_plan(plan, merge, args.notes_dir, client, names_by_id(snapshot), ids_by_slug)
    if plan.pushes or plan.pulls or plan.adoptions:
        print(
            f"synced: {len(plan.adoptions)} adopted, {len(plan.pushes)} pushed, {len(plan.pulls)} pulled "
            "— review with git diff, then commit"
        )
    if plan.conflicts:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
