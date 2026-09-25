import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from brainsync.check import read_json_lines

KIND_NORMAL = 1
KIND_TYPE = 2
KIND_TAG = 4
MEANING_NORMAL = 1
MEANING_TYPE = 2
MEANING_TAG = 5
NOTE_IMAGE_TYPES = (6, 12)
SOURCE_TYPE_THOUGHT = 2
XML_PROLOG = re.compile(r"^\s*<\?xml[^>]*\?>\s*")


@dataclass(frozen=True)
class BrainNote:
    format: str
    text: str


@dataclass(frozen=True)
class BrainImage:
    name: str
    data: bytes


@dataclass(frozen=True)
class BrainThought:
    id: str
    name: str
    kind: int = KIND_NORMAL
    tag_ids: tuple[str, ...] = ()
    type_id: str = ""
    created: str = ""


@dataclass(frozen=True)
class BrainLink:
    id_a: str
    id_b: str
    relation: int
    meaning: int


@dataclass(frozen=True)
class BrainSnapshot:
    thoughts: dict[str, BrainThought]
    links: tuple[BrainLink, ...] = ()
    notes: dict[str, BrainNote] = field(default_factory=dict)
    images: dict[str, tuple[BrainImage, ...]] = field(default_factory=dict)
    changed: dict[str, str] = field(default_factory=dict)

    def _linked_tag_ids(self, thought: BrainThought) -> tuple[str, ...]:
        return tuple(
            link.id_a
            for link in self.links
            if link.id_b == thought.id
            and link.meaning == MEANING_TAG
            and self.thoughts.get(link.id_a, thought).kind == KIND_TAG
        )

    def tag_ids_of(self, thought: BrainThought) -> tuple[str, ...]:
        return tuple(dict.fromkeys(thought.tag_ids + self._linked_tag_ids(thought)))

    def tag_names_of(self, thought: BrainThought) -> tuple[str, ...]:
        return tuple(self.thoughts[tag_id].name for tag_id in self.tag_ids_of(thought) if tag_id in self.thoughts)

    def type_name_of(self, thought: BrainThought) -> str:
        return self.thoughts[thought.type_id].name if thought.type_id in self.thoughts else ""

    def related_ids_of(self, thought: BrainThought) -> tuple[str, ...]:
        outgoing = (link.id_b for link in self.links if link.id_a == thought.id and link.meaning == MEANING_NORMAL)
        return tuple(dict.fromkeys(target for target in outgoing if target != thought.id))


def _thought(record: dict) -> BrainThought:
    return BrainThought(
        id=record["Id"],
        name=record.get("Name", "Untitled"),
        kind=record.get("Kind", KIND_NORMAL),
        tag_ids=tuple(record.get("TagIds") or ()),
        type_id=record.get("TypeId", ""),
        created=record.get("CreationDateTime", ""),
    )


def _link(record: dict) -> BrainLink:
    return BrainLink(
        id_a=record["ThoughtIdA"],
        id_b=record["ThoughtIdB"],
        relation=record.get("Relation", 0),
        meaning=record.get("Meaning", MEANING_NORMAL),
    )


def _note_format(location: str) -> str:
    return PurePosixPath(location).suffix.lstrip(".").lower() or "html"


def _member_path(archive: zipfile.ZipFile, source_id: str, location: str) -> str | None:
    filename = PurePosixPath(location).name
    candidates = (
        f"{source_id}/Notes/{location}",
        f"{source_id}/{location}",
        f"{source_id}/.data/md-images/{filename}",
    )
    names = archive.namelist()
    known = next((path for path in candidates if path in names), None)
    scanned = (name for name in names if name.startswith(f"{source_id}/") and PurePosixPath(name).name == filename)
    return known or next(scanned, None)


def _read_note(archive: zipfile.ZipFile, record: dict) -> BrainNote | None:
    path = _member_path(archive, record["SourceId"], record["Location"])
    if path is None:
        return None
    text = XML_PROLOG.sub("", archive.read(path).decode("utf-8-sig"))
    return BrainNote(format=_note_format(record["Location"]), text=text)


def _read_image(archive: zipfile.ZipFile, record: dict) -> BrainImage | None:
    path = _member_path(archive, record["SourceId"], record["Location"])
    if path is None:
        return None
    return BrainImage(name=PurePosixPath(record["Location"]).name, data=archive.read(path))


def _is_note_attachment(record: dict) -> bool:
    return record.get("SourceType") == SOURCE_TYPE_THOUGHT and record.get("NoteType", 0) != 0


def _is_image_attachment(record: dict) -> bool:
    return (
        record.get("SourceType") == SOURCE_TYPE_THOUGHT
        and record.get("Type") in NOTE_IMAGE_TYPES
        and not record.get("IsIcon", False)
    )


def read_brz(path: Path) -> BrainSnapshot:
    with zipfile.ZipFile(path) as archive:
        thoughts = {record["Id"]: _thought(record) for record in read_json_lines(archive, "thoughts.json")}
        links = tuple(_link(record) for record in read_json_lines(archive, "links.json"))
        attachments = read_json_lines(archive, "attachments.json")
        notes = {
            record["SourceId"]: note
            for record in attachments
            if _is_note_attachment(record) and (note := _read_note(archive, record))
        }
        images: dict[str, list[BrainImage]] = {}
        for record in filter(_is_image_attachment, attachments):
            if image := _read_image(archive, record):
                images.setdefault(record["SourceId"], []).append(image)
    return BrainSnapshot(
        thoughts=thoughts,
        links=links,
        notes=notes,
        images={source: tuple(items) for source, items in images.items()},
    )
