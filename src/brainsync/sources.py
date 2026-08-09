import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import requests

from brainsync.api import ApiClient
import re

from brainsync.brainread import (
    KIND_NORMAL,
    SOURCE_TYPE_THOUGHT,
    BrainImage,
    BrainLink,
    BrainNote,
    BrainSnapshot,
    BrainThought,
    read_brz,
)


@runtime_checkable
class BrainSource(Protocol):
    def snapshot(self, tag: str | None) -> BrainSnapshot: ...


@dataclass(frozen=True)
class BrzSource:
    path: Path

    def snapshot(self, tag: str | None) -> BrainSnapshot:
        return read_brz(self.path)


def _thought(record: dict, tag_ids: tuple[str, ...] = ()) -> BrainThought:
    return BrainThought(
        id=record["id"],
        name=record["name"],
        kind=record.get("kind", KIND_NORMAL),
        tag_ids=tag_ids,
        type_id=record.get("typeId") or "",
        created=record.get("creationDateTime") or "",
    )


def _link(record: dict) -> BrainLink:
    return BrainLink(
        id_a=record["thoughtIdA"],
        id_b=record["thoughtIdB"],
        relation=record["relation"],
        meaning=record["meaning"],
    )


LOG_PAGE = 500
NOTES_IMAGE_URL = re.compile(r"\(([^)\s]*/notes-images/[^)\s]*/([^/)\s]+))\)")


SKIPPABLE = (404, 403)


def _skippable(error: requests.HTTPError) -> bool:
    return error.response is not None and error.response.status_code in SKIPPABLE


def _fetch_graph(client: ApiClient, thought_id: str) -> dict | None:
    try:
        return client.thought_graph(thought_id)
    except requests.HTTPError as error:
        if _skippable(error):
            return None
        raise


def log_thought_ids(client: ApiClient, page_size: int = LOG_PAGE) -> set[str]:
    thought_ids: set[str] = set()
    end_time: str | None = None
    while True:
        page = client.modifications(max_logs=page_size, end_time=end_time)
        if not page:
            return thought_ids
        thought_ids.update(
            record["sourceId"] for record in page if record["sourceType"] == SOURCE_TYPE_THOUGHT
        )
        oldest = min(record["creationDateTime"] for record in page)
        if oldest == end_time:
            return thought_ids
        end_time = oldest


def _image_bytes(client: ApiClient, url: str, name: str) -> bytes:
    try:
        return client.attachment_content(name.rsplit(".", 1)[0])
    except requests.HTTPError:
        return client.fetch_bytes(url)


def _fetch_images(client: ApiClient, markdown: str) -> tuple[BrainImage, ...]:
    references = dict.fromkeys(NOTES_IMAGE_URL.findall(markdown))
    images = []
    for url, name in references:
        try:
            images.append(BrainImage(name=name, data=_image_bytes(client, url, name)))
        except requests.HTTPError as error:
            print(f"could not download note image {name}: {error}", file=sys.stderr)
    return tuple(images)


def _fetch_note(client: ApiClient, thought_id: str) -> str | None:
    try:
        return client.note_markdown(thought_id)
    except requests.HTTPError as error:
        if _skippable(error):
            return None
        raise


def _neighbors(graph: dict) -> list[dict]:
    adjacent = ("parents", "children", "siblings", "jumps", "tags")
    return [record for key in adjacent for record in graph.get(key) or []]


@dataclass(frozen=True)
class ApiSource:
    client: ApiClient

    def snapshot(self, tag: str | None) -> BrainSnapshot:
        thoughts: dict[str, BrainThought] = {}
        links: dict[BrainLink, None] = {}
        notes: dict[str, BrainNote] = {}
        images: dict[str, tuple[BrainImage, ...]] = {}
        seeds = [
            self.client.brain()["homeThoughtId"],
            *(record["id"] for record in self.client.tags()),
            *(record["id"] for record in self.client.types()),
            *sorted(log_thought_ids(self.client)),
        ]
        queue = deque(seeds)
        visited: set[str] = set()
        skipped: list[str] = []
        while queue:
            thought_id = queue.popleft()
            if thought_id in visited:
                continue
            visited.add(thought_id)
            graph = _fetch_graph(self.client, thought_id)
            if graph is None:
                skipped.append(thought_id)
                continue
            active = graph["activeThought"]
            tag_ids = tuple(record["id"] for record in graph.get("tags") or [])
            thoughts[active["id"]] = _thought(active, tag_ids=tag_ids)
            for neighbor in _neighbors(graph):
                thoughts.setdefault(neighbor["id"], _thought(neighbor))
                queue.append(neighbor["id"])
            links.update(dict.fromkeys(_link(record) for record in graph.get("links") or []))
            if _thought(active).kind == KIND_NORMAL and (markdown := _fetch_note(self.client, thought_id)) is not None:
                notes[thought_id] = BrainNote(format="md", text=markdown)
                if downloaded := _fetch_images(self.client, markdown):
                    images[thought_id] = downloaded
        if skipped:
            print(
                f"skipped {len(skipped)} deleted or restricted thought(s): {', '.join(skipped[:5])}"
                + (" …" if len(skipped) > 5 else ""),
                file=sys.stderr,
            )
        return BrainSnapshot(thoughts=thoughts, links=tuple(links), notes=notes, images=images)
