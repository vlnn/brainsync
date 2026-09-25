import argparse
import datetime
import os
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests
from markdownify import markdownify

from brainsync.brainread import KIND_NORMAL, KIND_TAG, BrainSnapshot, BrainThought
from brainsync.api import ApiClient, load_config
from brainsync.sources import ApiSource, BrainSource, BrzSource
from brainsync.brainzip import brain_url, long_guid
from brainsync.crawl import http_fetch
from brainsync.garden import GardenNote, load_garden, parse_frontmatter, pull_site, slugify

BRAIN_LINK = re.compile(r"\[([^\]]*)\]\((brain://[^)\s]+)\)")
MD_NOTE_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)/:\s]+)\.md\)")
FENCE_LINE = re.compile(r"^```", re.MULTILINE)
TITLE_LIMIT = 80
EDGE_QUOTES = "\"'\u201c\u201d\u2018\u2019"


@dataclass(frozen=True)
class MergePlan:
    writes: dict[str, str] = field(default_factory=dict)
    images: dict[str, bytes] = field(default_factory=dict)
    skipped: tuple[str, ...] = ()
    orphans: tuple[str, ...] = ()
    adopted: dict[str, str] = field(default_factory=dict)


def tag_thought(snapshot: BrainSnapshot, tag: str) -> BrainThought | None:
    matches = (t for t in snapshot.thoughts.values() if t.kind == KIND_TAG and t.name.lstrip("#").lower() == tag.lower())
    return next(matches, None)


def select_thoughts(snapshot: BrainSnapshot, tag: str | None) -> list[BrainThought]:
    normal = [t for t in snapshot.thoughts.values() if t.kind == KIND_NORMAL]
    if tag is None:
        return sorted(normal, key=lambda t: (t.name, t.id))
    marker = tag_thought(snapshot, tag)
    if marker is None:
        names = ", ".join(sorted(t.name for t in snapshot.thoughts.values() if t.kind == KIND_TAG)) or "(none)"
        raise SystemExit(f"tag {tag!r} not found in the brain — available tags: {names}")
    return sorted((t for t in normal if marker.id in snapshot.tag_ids_of(t)), key=lambda t: (t.name, t.id))


FALLBACK_SLUG = re.compile(r"untitled(-[0-9a-f]{8})?")


def kept_slug(owned_slug: str | None) -> str | None:
    return None if owned_slug is None or FALLBACK_SLUG.fullmatch(owned_slug) else owned_slug


def assign_slugs(
    thoughts: list[BrainThought], garden: dict[str, GardenNote], preassigned: dict[str, str] | None = None
) -> dict[str, str]:
    preassigned = preassigned or {}
    owned = {note.brain_id: note.slug for note in garden.values() if note.brain_id}
    foreign = {slug for slug, note in garden.items() if note.brain_id not in {t.id for t in thoughts}}
    slugs: dict[str, str] = {}
    for thought in thoughts:
        if thought.id in preassigned:
            slugs[thought.id] = preassigned[thought.id]
            continue
        slug = kept_slug(owned.get(thought.id)) or slugify(thought.name)
        if slug in slugs.values() or (slug in foreign and slug != owned.get(thought.id)):
            slug = f"{slug}-{thought.id[:8]}"
        slugs[thought.id] = slug
    return slugs


def thought_id_of(brain_url: str, known_ids: set[str]) -> str | None:
    for segment in brain_url.removeprefix("brain://").split("/"):
        try:
            guid = long_guid(segment)
        except ValueError:
            continue
        if guid in known_ids:
            return guid
    return None


def resolve_brain_link(text: str, url: str, snapshot: BrainSnapshot, slugs: dict[str, str], by_title: dict[str, str]) -> str:
    target = thought_id_of(url, set(snapshot.thoughts))
    if target in slugs:
        return f"[{text}]({slugs[target]}.md)"
    name = snapshot.thoughts[target].name if target else ""
    if name in by_title:
        return f"[{text}]({by_title[name]})"
    return text or name


def rewrite_brain_links(body: str, snapshot: BrainSnapshot, slugs: dict[str, str], by_title: dict[str, str]) -> str:
    return BRAIN_LINK.sub(lambda m: resolve_brain_link(m.group(1), m.group(2), snapshot, slugs, by_title), body)


def short_title(name: str, limit: int = TITLE_LIMIT) -> str:
    return name if len(name) <= limit else name[:limit].rsplit(" ", 1)[0] + "\u2026"


def frontmatter_title(name: str) -> str:
    return short_title(name.strip(EDGE_QUOTES).strip())


def name_is_content(name: str) -> bool:
    return len(name) > TITLE_LIMIT


def drop_leading_title(body: str, title: str) -> str:
    return re.sub(rf"\A\s*#{{1,6}}\s*{re.escape(title)}\s*\n", "", body)


def note_body(snapshot: BrainSnapshot, thought: BrainThought) -> str:
    note = snapshot.notes.get(thought.id)
    if note is None:
        return thought.name if name_is_content(thought.name) else ""
    markdown = markdownify(note.text, heading_style="ATX") if note.format in ("html", "htm") else note.text
    return drop_leading_title(markdown.strip(), thought.name)


def fence_left_open(body: str) -> bool:
    return len(FENCE_LINE.findall(body)) % 2 == 1


def close_open_fence(body: str) -> str:
    return body + "\n```" if fence_left_open(body) else body


def related_section(snapshot: BrainSnapshot, thought: BrainThought, slugs: dict[str, str], by_title: dict[str, str]) -> str:
    targets = (snapshot.thoughts[i] for i in snapshot.related_ids_of(thought) if i in snapshot.thoughts)
    lines = [
        f"- [{short_title(target.name)}]({filename})"
        for target in targets
        if (filename := f"{slugs[target.id]}.md" if target.id in slugs else by_title.get(target.name))
    ]
    return "## Related\n\n" + "\n".join(lines) + "\n" if lines else ""


def rewrite_image_paths(body: str, slug: str, image_names: tuple[str, ...]) -> str:
    for name in image_names:
        body = re.sub(rf"\(\S*{re.escape(name)}\)", f"(../static/brain/{slug}/{name})", body)
    return body


FRONTMATTER_BLOCK = re.compile(r"\A---\n.*?\n---\n\n?", re.DOTALL)
RELATED_SECTION = re.compile(r"(?:\A|\n)## Related\n\n(?:- \[[^\]]*\]\([^)]*\)\n?)+\n*\Z")


def strip_frontmatter(text: str) -> str:
    return FRONTMATTER_BLOCK.sub("", text, count=1)


def related_of(text: str) -> str:
    match = RELATED_SECTION.search(text)
    return match.group(0).strip("\n") + "\n" if match else ""


def strip_related(text: str) -> str:
    return RELATED_SECTION.sub("\n", text).rstrip("\n") + "\n" if related_of(text) else text


def md_links_to_brain(body: str, ids_by_slug: dict[str, str]) -> str:
    def retarget(match: re.Match) -> str:
        text, slug = match.groups()
        if slug not in ids_by_slug:
            return match.group(0)
        return f"[{text}]({brain_url(ids_by_slug[slug], slug)})"

    return MD_NOTE_LINK.sub(retarget, body)


def push_body(garden_text: str, title: str, ids_by_slug: dict[str, str]) -> str:
    body = drop_leading_title(strip_related(strip_frontmatter(garden_text)), title).strip("\n")
    return f"# {title}\n\n{md_links_to_brain(body, ids_by_slug)}\n"


def adoption_targets(thoughts: list[BrainThought], garden: dict[str, GardenNote]) -> dict[str, GardenNote]:
    candidates = {note.title: note for note in garden.values() if not note.brain_id and note.format == "md"}
    targets: dict[str, GardenNote] = {}
    claimed: set[str] = set()
    for thought in thoughts:
        note = candidates.get(thought.name)
        if note and note.slug not in claimed:
            targets[thought.id] = note
            claimed.add(note.slug)
    return targets


def adopt_text(rendered: str, existing: str, title: str) -> str:
    merged = merge_frontmatter(rendered, existing)
    fences = FRONTMATTER_FENCES.match(merged)
    body = drop_leading_title(strip_frontmatter(existing), title).strip("\n")
    return merged[: fences.end()] + "\n" + body + "\n"


FRONTMATTER_FENCES = re.compile(r"\A---\n(.*?\n)---\n", re.DOTALL)
OWNED_FIELDS = ("title", "tags", "brain-id")


def _frontmatter_fields(match: re.Match) -> dict[str, str]:
    lines = (line for line in match.group(1).splitlines() if ":" in line)
    return {line.split(":", 1)[0].strip(): line for line in lines}


def merge_frontmatter(rendered: str, existing: str, owned: tuple[str, ...] = OWNED_FIELDS) -> str:
    rendered_match = FRONTMATTER_FENCES.match(rendered)
    existing_match = FRONTMATTER_FENCES.match(existing)
    if not rendered_match or not existing_match:
        return rendered
    rendered_fields = _frontmatter_fields(rendered_match)
    existing_fields = _frontmatter_fields(existing_match)
    merged = [
        rendered_fields[key] if key in owned else line
        for key, line in existing_fields.items()
        if key in rendered_fields or key not in owned
    ]
    merged += [line for key, line in rendered_fields.items() if key not in existing_fields]
    return "---\n" + "\n".join(merged) + "\n---\n" + rendered[rendered_match.end():]


LEADING_HEADING = re.compile(r"\A\s*#\s+[^\n]*\n?")
DATE_LINE = re.compile(r"^date:.*$", re.MULTILINE)


def is_stub(text: str) -> bool:
    body = strip_related(strip_frontmatter(text))
    return bool(text) and not LEADING_HEADING.sub("", body).strip()


def fleshed_out(existing: str, rendered: str) -> bool:
    return is_stub(existing) and not is_stub(rendered)


def publish_day(changed: str, previous: str, today: str) -> str:
    day = changed[:10]
    return day if day and day >= previous else today


def with_date(text: str, day: str) -> str:
    fences = FRONTMATTER_FENCES.match(text)
    if not fences:
        return text
    head = fences.group(1)
    head = DATE_LINE.sub(f"date: {day}", head) if DATE_LINE.search(head) else f"{head}date: {day}\n"
    return f"---\n{head}---\n{text[fences.end():]}"


def redated(rendered: str, existing: str, changed: str, today: str) -> str:
    day = publish_day(changed, parse_frontmatter(existing).get("date", ""), today)
    return merge_frontmatter(with_date(rendered, day), existing, owned=(*OWNED_FIELDS, "date"))


def merged_note(rendered: str, existing: str, changed: str, today: str) -> str:
    if fleshed_out(existing, rendered):
        return redated(rendered, existing, changed, today)
    return merge_frontmatter(rendered, existing)


def today_iso() -> str:
    return datetime.date.today().isoformat()


def frontmatter(thought: BrainThought, tags: tuple[str, ...]) -> str:
    lines = [f"title: {frontmatter_title(thought.name)}"]
    if thought.created:
        lines.append(f"date: {thought.created[:10]}")
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    lines.append(f"brain-id: {thought.id}")
    return "---\n" + "\n".join(lines) + "\n---\n"


def published_tags(snapshot: BrainSnapshot, thought: BrainThought, exclude: str | None) -> tuple[str, ...]:
    names = snapshot.tag_names_of(thought) + ((snapshot.type_name_of(thought),) if thought.type_id else ())
    slugged = (slugify(name.lstrip("#")) for name in names if name)
    return tuple(dict.fromkeys(tag for tag in slugged if exclude is None or tag != slugify(exclude)))


def render_note(snapshot: BrainSnapshot, thought: BrainThought, slugs: dict[str, str], by_title: dict[str, str], tag: str | None) -> str:
    body = rewrite_brain_links(note_body(snapshot, thought), snapshot, slugs, by_title)
    body = rewrite_image_paths(body, slugs[thought.id], tuple(i.name for i in snapshot.images.get(thought.id, ())))
    body = close_open_fence(body)
    related = related_section(snapshot, thought, slugs, by_title)
    sections = [part for part in (body, related) if part]
    return frontmatter(thought, published_tags(snapshot, thought, tag)) + "\n" + "\n\n".join(sections) + "\n"


def _garden_by_title(garden: dict[str, GardenNote]) -> dict[str, str]:
    return {note.title: note.filename for note in reversed(garden.values())}


def plan_merge(
    snapshot: BrainSnapshot,
    garden: dict[str, GardenNote],
    tag: str | None,
    adopt: bool = False,
    today: str | None = None,
) -> MergePlan:
    today = today or today_iso()
    thoughts = select_thoughts(snapshot, tag)
    handwritten = {note.title: note for note in garden.values() if not note.brain_id}
    adoptions = adoption_targets(thoughts, garden) if adopt else {}
    exported = [t for t in thoughts if t.name not in handwritten or t.id in adoptions]
    slugs = assign_slugs(exported, garden, preassigned={tid: note.slug for tid, note in adoptions.items()})
    by_title = {**_garden_by_title(garden), **{t.name: f"{slugs[t.id]}.md" for t in exported}}
    texts = {note.filename: note.text for note in garden.values()}
    writes = {
        f"{slugs[t.id]}.md": merged_note(
            render_note(snapshot, t, slugs, by_title, tag),
            texts.get(f"{slugs[t.id]}.md", ""),
            snapshot.changed.get(t.id, ""),
            today,
        )
        for t in exported
    }
    images = {
        f"{slugs[t.id]}/{image.name}": image.data for t in exported for image in snapshot.images.get(t.id, ())
    }
    exported_ids = {t.id for t in exported}
    orphans = tuple(
        note.filename for note in garden.values() if note.brain_id and note.brain_id not in exported_ids
    )
    return MergePlan(
        writes=writes,
        images=images,
        skipped=tuple(t.name for t in thoughts if t.name in handwritten and t.id not in adoptions),
        orphans=orphans,
        adopted={f"{note.slug}.md": tid for tid, note in adoptions.items()},
    )


def write_images(images: dict[str, bytes], notes_dir: Path) -> None:
    for relative, data in images.items():
        target = notes_dir.parent / "static" / "brain" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def apply_plan(plan: MergePlan, notes_dir: Path, prune: bool) -> None:
    for filename, text in plan.writes.items():
        (notes_dir / filename).write_text(text)
    write_images(plan.images, notes_dir)
    if prune:
        for filename in plan.orphans:
            (notes_dir / filename).unlink()


def report(plan: MergePlan, prune: bool) -> str:
    lines = [f"write {name}" for name in sorted(plan.writes)]
    lines += [f"skip  {name} (hand-written note with the same title exists)" for name in plan.skipped]
    verb = "prune" if prune else "orphan"
    lines += [f"{verb} {name}" for name in plan.orphans]
    return "\n".join(lines)


def make_api_source(notes_dir: Path) -> ApiSource:
    config = load_config(notes_dir.parent, os.environ)
    return ApiSource(ApiClient(config=config, session=requests.Session()))


def choose_source(brz: Path | None, notes_dir: Path, api: bool = False) -> BrainSource:
    if api and brz is not None:
        raise SystemExit("pass either a brz path or --api, not both")
    if brz is None:
        return make_api_source(notes_dir)
    if not brz.is_file():
        raise SystemExit(f"brz file not found: {brz}")
    return BrzSource(brz)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge thoughts from a TheBrain .brz or the Local API into a stacked-notes garden"
    )
    parser.add_argument("brz", type=Path, nargs="?", default=None, help="brz archive; omit to pull via the Local API")
    parser.add_argument("--api", action="store_true", help="pull via the Local API (same as omitting the brz path)")
    parser.add_argument("--notes-dir", type=Path, required=True, help="notes/ directory of the garden checkout")
    parser.add_argument("--tag", default=None, help="only export thoughts carrying this TheBrain tag")
    parser.add_argument("--pull", default=None, metavar="URL", help="first download missing notes from the live site")
    parser.add_argument("--prune", action="store_true", help="delete previously exported notes no longer selected")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.notes_dir.is_dir():
        raise SystemExit(f"notes dir not found: {args.notes_dir}")
    source = choose_source(args.brz, args.notes_dir, args.api)

    if args.pull:
        session = requests.Session()
        pulled = pull_site(args.pull, args.notes_dir, lambda url: http_fetch(url, session))
        print(f"Pulled {len(pulled)} missing notes from {args.pull}")

    try:
        snapshot = source.snapshot(args.tag)
    except requests.HTTPError as error:
        raise SystemExit(str(error))
    plan = plan_merge(snapshot, load_garden(args.notes_dir), args.tag)
    print(report(plan, args.prune) or "nothing to do")
    if args.dry_run:
        return
    apply_plan(plan, args.notes_dir, args.prune)
    print(f"Merged {len(plan.writes)} notes into {args.notes_dir} — review with git diff, then commit/push")


if __name__ == "__main__":
    main()
