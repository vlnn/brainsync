import argparse
from pathlib import Path
from urllib.parse import urlsplit

import requests

from brainsync.brainzip import (
    RELATION_CHILD,
    RELATION_JUMP,
    Link,
    Thought,
    brain_url,
    make_link,
    make_thought,
    stable_id,
    write_brz,
)
from brainsync.convert import embed_images, rewrite_links
from brainsync.crawl import (
    BASE_URL,
    BytesFetcher,
    Note,
    cached,
    cached_bytes,
    crawl,
    http_fetch,
    http_fetch_bytes,
    site_of,
    throttled,
)

ROOT_NAME = "Andy Matuschak's Notes"


def brain_urls_for(notes: dict[str, Note]) -> dict[str, str]:
    return {url: brain_url(stable_id("thought", note.title), note.title) for url, note in notes.items()}


def _no_assets(url: str) -> bytes:
    raise requests.RequestException(f"asset fetching disabled: {url}")


def build_thoughts(
    notes: dict[str, Note],
    name: str = ROOT_NAME,
    base: str = BASE_URL,
    fetch_bytes: BytesFetcher = _no_assets,
) -> tuple[Thought, dict[str, Thought]]:
    root = make_thought("root", name)
    brain_urls = brain_urls_for(notes)
    by_title: dict[str, Thought] = {}
    for note in notes.values():
        if note.title in by_title:
            continue
        html, images = embed_images(rewrite_links(note.body_html, brain_urls, base), note.title, fetch_bytes, base)
        by_title[note.title] = make_thought(note.title, note.title, html, images)
    by_url = {url: by_title[note.title] for url, note in notes.items()}
    return root, by_url


def parent_url(url: str) -> str:
    return url.rsplit("/", 1)[0]


def hierarchy_parent(url: str, thought: Thought, by_url: dict[str, Thought], root: Thought) -> Thought:
    parent = by_url.get(parent_url(url))
    return parent if parent and parent.id != thought.id else root


def build_links(root: Thought, by_url: dict[str, Thought], notes: dict[str, Note]) -> list[Link]:
    first_url = {thought.id: url for url, thought in reversed(by_url.items())}
    child_links = [
        make_link(hierarchy_parent(first_url[thought.id], thought, by_url, root).id, thought.id, RELATION_CHILD)
        for thought in dict.fromkeys(by_url.values())
    ]
    jump_links = {
        make_link(by_url[url].id, by_url[target].id, RELATION_JUMP)
        for url, note in notes.items()
        for target in note.outgoing
        if target in by_url and by_url[target].id != by_url[url].id
    }
    return child_links + sorted(jump_links, key=lambda link: link.id)


def export(
    notes: dict[str, Note],
    output: Path,
    brain_name: str = ROOT_NAME,
    base: str = BASE_URL,
    fetch_bytes: BytesFetcher = _no_assets,
) -> None:
    root, by_url = build_thoughts(notes, brain_name, base, fetch_bytes)
    links = build_links(root, by_url, notes)
    write_brz(output, brain_name, [root, *dict.fromkeys(by_url.values())], links, home_thought_id=root.id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export notes.andymatuschak.org to a TheBrain .brz file")
    parser.add_argument("--start", default=f"{BASE_URL}/About_these_notes")
    parser.add_argument("--output", type=Path, default=Path("andy-notes.brz"))
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--cache-dir", type=Path, default=Path(".brainsync-cache"))
    parser.add_argument("--name", default=None, help="brain name (default: derived from the site)")
    args = parser.parse_args()

    base = site_of(args.start)
    name = args.name or (ROOT_NAME if base == BASE_URL else urlsplit(base).netloc)
    session = requests.Session()
    fetch = cached(throttled(lambda url: http_fetch(url, session), args.delay), args.cache_dir)
    fetch_assets = cached_bytes(throttled(lambda url: http_fetch_bytes(url, session), args.delay), args.cache_dir)
    notes = crawl(args.start, fetch, limit=args.limit, base=base)
    export(notes, args.output, name, base, fetch_assets)
    print(f"Exported {len(notes)} notes to {args.output}")


if __name__ == "__main__":
    main()
