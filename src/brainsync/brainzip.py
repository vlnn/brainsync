import base64
import json
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
BOM = "\ufeff"
EXCHANGE_FORMAT_VERSION = 5
BRAIN_NAME_SETTING_IDS = ("eadd590c-a791-50e2-a8ed-6c96e93d15a8", "7501869f-6fc5-5c4b-b838-f3f7287c138c")
HOME_THOUGHT_SETTING_ID = "0dc10ee1-5a89-548b-b6d2-b5eec064ca4a"
THOUGHT_KIND_NORMAL = 1
LINK_KIND_NORMAL = 1
RELATION_CHILD = 1
RELATION_JUMP = 3
AC_TYPE_PUBLIC = 0
THOUGHT_ICON_INFO_NONE = "1::0:False:False:0:"
ATTACHMENT_TYPE_INTERNAL_FILE = 4
ATTACHMENT_TYPE_NOTES_IMAGE = 6
NOTE_TYPE_HTML = 2
SOURCE_TYPE_THOUGHT = 2


@dataclass(frozen=True)
class NotesImage:
    id: str
    extension: str
    data: bytes


@dataclass(frozen=True)
class Thought:
    id: str
    name: str
    notes_html: str = ""
    images: tuple[NotesImage, ...] = ()


@dataclass(frozen=True)
class Link:
    id: str
    id_a: str
    id_b: str
    relation: int


def short_guid(guid: str) -> str:
    return base64.urlsafe_b64encode(uuid.UUID(guid).bytes_le).decode().rstrip("=")


def long_guid(short: str) -> str:
    padded = short + "=" * (-len(short) % 4)
    return str(uuid.UUID(bytes_le=base64.urlsafe_b64decode(padded)))


def brain_url(thought_id: str, name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]", "", name)
    return f"brain://{short_guid(thought_id)}/{slug}"


def stable_id(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, json.dumps(parts)))


def make_thought(key: str, name: str, notes_html: str = "", images: tuple[NotesImage, ...] = ()) -> Thought:
    return Thought(id=stable_id("thought", key), name=name, notes_html=notes_html, images=images)


def make_link(id_a: str, id_b: str, relation: int) -> Link:
    return Link(id=stable_id("link", id_a, id_b, str(relation)), id_a=id_a, id_b=id_b, relation=relation)


def _timestamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


def _stamped(record: dict, brain_id: str, now: datetime, record_id: str) -> dict:
    return {
        **record,
        "CreationDateTime": _timestamp(now),
        "ModificationDateTime": _timestamp(now),
        "BrainId": brain_id,
        "Id": record_id,
    }


def thought_record(thought: Thought, brain_id: str, now: datetime) -> dict:
    record = {
        "Name": thought.name,
        "DisplayModificationDateTime": _timestamp(now),
        "ACType": AC_TYPE_PUBLIC,
        "Kind": THOUGHT_KIND_NORMAL,
        "TagIds": [],
        "ThoughtIconInfo": THOUGHT_ICON_INFO_NONE,
    }
    return _stamped(record, brain_id, now, thought.id)


def link_record(link: Link, brain_id: str, now: datetime) -> dict:
    record = {
        "ThoughtIdA": link.id_a,
        "ThoughtIdB": link.id_b,
        "Kind": LINK_KIND_NORMAL,
        "Relation": link.relation,
        "Direction": -1,
        "Meaning": 1,
        "Thickness": -1,
    }
    return _stamped(record, brain_id, now, link.id)


def notes_attachment_record(thought: Thought, data_length: int, brain_id: str, now: datetime) -> dict:
    record = {
        "SourceId": thought.id,
        "Name": "notes.html",
        "Position": 0.0,
        "Type": ATTACHMENT_TYPE_INTERNAL_FILE,
        "DataLength": data_length,
        "Location": "notes.html",
        "IsIcon": False,
        "SourceType": SOURCE_TYPE_THOUGHT,
        "NoteType": NOTE_TYPE_HTML,
        "IsWallpaper": False,
    }
    return _stamped(record, brain_id, now, stable_id("attachment", thought.id))


def notes_image_attachment_record(thought: Thought, image: NotesImage, brain_id: str, now: datetime) -> dict:
    record = {
        "SourceId": thought.id,
        "Name": f"{image.id}{image.extension}",
        "Position": 0.0,
        "Type": ATTACHMENT_TYPE_NOTES_IMAGE,
        "DataLength": len(image.data),
        "Location": f"{image.id}{image.extension}",
        "IsIcon": False,
        "SourceType": SOURCE_TYPE_THOUGHT,
        "NoteType": 0,
        "IsWallpaper": False,
    }
    return _stamped(record, brain_id, now, image.id)


def settings_records(brain_name: str, home_thought_id: str | None, brain_id: str, now: datetime) -> list[dict]:
    records = [_stamped({"Value": brain_name}, brain_id, now, setting_id) for setting_id in BRAIN_NAME_SETTING_IDS]
    if home_thought_id:
        records.append(_stamped({"Value": home_thought_id}, brain_id, now, HOME_THOUGHT_SETTING_ID))
    return records


def to_json_lines(records: list[dict]) -> str:
    return BOM + "\n".join(json.dumps(record, ensure_ascii=False) for record in records)


def meta_json(brain_id: str, attachment_ids: list[str]) -> str:
    states = {attachment_id: 1 for attachment_id in attachment_ids}
    return BOM + json.dumps(
        {"BrainId": brain_id, "ExchangeFormatVersion": EXCHANGE_FORMAT_VERSION, "AttachmentFileStates": states}
    )


def notes_html_document(notes_html: str) -> bytes:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{notes_html}'.encode()


def write_brz(
    path: Path,
    brain_name: str,
    thoughts: list[Thought],
    links: list[Link],
    now: datetime | None = None,
    home_thought_id: str | None = None,
) -> None:
    now = now or datetime.now(timezone.utc)
    brain_id = stable_id("brain", brain_name)
    with_notes = [(t, notes_html_document(t.notes_html)) for t in thoughts if t.notes_html]
    attachments = [notes_attachment_record(t, len(html), brain_id, now) for t, html in with_notes]
    attachments += [
        notes_image_attachment_record(t, image, brain_id, now) for t in thoughts for image in t.images
    ]

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("meta.json", meta_json(brain_id, [a["Id"] for a in attachments]))
        archive.writestr("thoughts.json", to_json_lines([thought_record(t, brain_id, now) for t in thoughts]))
        archive.writestr("links.json", to_json_lines([link_record(l, brain_id, now) for l in links]))
        archive.writestr("attachments.json", to_json_lines(attachments))
        archive.writestr("settings.json", to_json_lines(settings_records(brain_name, home_thought_id, brain_id, now)))
        for thought, html in with_notes:
            archive.writestr(f"{thought.id}/Notes/notes.html", html)
        for thought in thoughts:
            for image in thought.images:
                archive.writestr(f"{thought.id}/Notes/{image.id}{image.extension}", image.data)
