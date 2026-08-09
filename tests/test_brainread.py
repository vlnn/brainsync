import zipfile

import pytest

from brainsync.brainread import KIND_TAG, MEANING_NORMAL, MEANING_TAG, read_brz
from brainsync.brainzip import long_guid, short_guid, stable_id, to_json_lines

PUBLIC_TAG = stable_id("thought", "tag-public")
ESSAY = stable_id("thought", "essay")
DRAFT = stable_id("thought", "draft")


def thought_record(thought_id, name, kind=1, tag_ids=(), type_id=None, created="2026-07-01T10:00:00.000000"):
    record = {"Id": thought_id, "Name": name, "Kind": kind, "TagIds": list(tag_ids), "CreationDateTime": created}
    return record if type_id is None else {**record, "TypeId": type_id}


def link_record(id_a, id_b, meaning, relation=1):
    return {"Id": stable_id("link", id_a, id_b), "ThoughtIdA": id_a, "ThoughtIdB": id_b, "Relation": relation, "Meaning": meaning}


def note_attachment(thought_id, location, type=4, note_type=2):
    return {"SourceId": thought_id, "Location": location, "Type": type, "SourceType": 2, "NoteType": note_type}


def image_attachment(thought_id, location, type=6):
    return {"SourceId": thought_id, "Location": location, "Type": type, "SourceType": 2, "NoteType": 0}


def write_test_brz(path, thoughts, attachments=(), files=(), links=()):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("thoughts.json", to_json_lines(list(thoughts)))
        archive.writestr("links.json", to_json_lines(list(links)))
        archive.writestr("attachments.json", to_json_lines(list(attachments)))
        for name, data in files:
            archive.writestr(name, data)


@pytest.fixture
def sample_brz(tmp_path):
    path = tmp_path / "sample.brz"
    write_test_brz(
        path,
        thoughts=[
            thought_record(PUBLIC_TAG, "Public", kind=KIND_TAG),
            thought_record(ESSAY, "My Essay", type_id=DRAFT),
            thought_record(DRAFT, "Draft", tag_ids=[PUBLIC_TAG]),
        ],
        links=[
            link_record(PUBLIC_TAG, ESSAY, meaning=MEANING_TAG),
            link_record(ESSAY, DRAFT, meaning=MEANING_NORMAL),
        ],
        attachments=[
            note_attachment(ESSAY, "notes.html"),
            note_attachment(DRAFT, "Notes.md"),
            image_attachment(ESSAY, "diagram.png"),
        ],
        files=[
            (f"{ESSAY}/Notes/notes.html", '<?xml version="1.0" encoding="UTF-8"?>\n<p>hello</p>'),
            (f"{DRAFT}/Notes/Notes.md", "raw markdown"),
            (f"{ESSAY}/Notes/diagram.png", b"\x89PNG"),
        ],
    )
    return path


@pytest.mark.parametrize("guid", [stable_id("thought", "a"), stable_id("brain", "b"), "00000000-0000-0000-0000-000000000001"])
def test_long_guid_inverts_short_guid(guid):
    assert long_guid(short_guid(guid)) == guid, "long_guid should invert short_guid for any uuid"


def test_read_brz_reads_thoughts_with_kind_type_and_dates(sample_brz):
    snapshot = read_brz(sample_brz)
    essay = snapshot.thoughts[ESSAY]
    assert essay.name == "My Essay", "read_brz should keep the thought name"
    assert essay.type_id == DRAFT, "read_brz should keep the TypeId reference"
    assert essay.created == "2026-07-01T10:00:00.000000", "read_brz should keep the creation timestamp"
    assert snapshot.thoughts[PUBLIC_TAG].kind == KIND_TAG, "read_brz should keep the thought kind"


@pytest.mark.parametrize(
    ("thought_id", "reason"),
    [
        (ESSAY, "tagged via a meaning-5 link from the tag thought, like real TheBrain exports"),
        (DRAFT, "tagged via the TagIds field, like archives this project generates"),
    ],
)
def test_tag_ids_of_sees_both_tagging_mechanisms(sample_brz, thought_id, reason):
    snapshot = read_brz(sample_brz)
    assert snapshot.tag_ids_of(snapshot.thoughts[thought_id]) == (PUBLIC_TAG,), f"thought should count as {reason}"


def test_related_ids_of_follows_only_normal_meaning_links(sample_brz):
    snapshot = read_brz(sample_brz)
    assert snapshot.related_ids_of(snapshot.thoughts[ESSAY]) == (DRAFT,), (
        "related_ids_of should follow outgoing meaning-1 links"
    )
    assert snapshot.related_ids_of(snapshot.thoughts[PUBLIC_TAG]) == (), (
        "tag-membership links should not count as related"
    )


def test_type_name_of_resolves_type_id(sample_brz):
    snapshot = read_brz(sample_brz)
    assert snapshot.type_name_of(snapshot.thoughts[ESSAY]) == "Draft", "type_name_of should resolve TypeId to a name"


@pytest.mark.parametrize(
    ("thought_id", "expected_format", "expected_text"),
    [
        (ESSAY, "html", "<p>hello</p>"),
        (DRAFT, "md", "raw markdown"),
    ],
)
def test_read_brz_reads_notes_in_both_formats(sample_brz, thought_id, expected_format, expected_text):
    note = read_brz(sample_brz).notes[thought_id]
    assert note.format == expected_format, "note format should follow the attachment file extension"
    assert note.text == expected_text, "note text should be read with the xml prolog stripped"


def test_read_brz_skips_notes_whose_file_is_missing(tmp_path):
    path = tmp_path / "broken.brz"
    write_test_brz(path, [thought_record(ESSAY, "Essay")], [note_attachment(ESSAY, "notes.html")])
    assert read_brz(path).notes == {}, "attachments without a backing file should be skipped, not crash"


def test_read_brz_reads_note_images(sample_brz):
    images = read_brz(sample_brz).images[ESSAY]
    assert [(image.name, image.data) for image in images] == [("diagram.png", b"\x89PNG")], (
        "read_brz should collect note image attachments with their bytes"
    )


def test_tag_names_of_resolves_tag_ids_to_names(sample_brz):
    snapshot = read_brz(sample_brz)
    assert snapshot.tag_names_of(snapshot.thoughts[ESSAY]) == ("Public",), (
        "tag_names_of should map tag ids to the tag thoughts' names"
    )


@pytest.fixture
def real_layout_brz(tmp_path):
    path = tmp_path / "real.brz"
    write_test_brz(
        path,
        thoughts=[thought_record(ESSAY, "Kent Beck")],
        attachments=[
            note_attachment(ESSAY, "Notes.md", type=1, note_type=4),
            image_attachment(ESSAY, "pic.png", type=12),
            {"SourceId": ESSAY, "Location": "Icon.png", "Type": 5, "SourceType": 2, "NoteType": 0, "IsIcon": True},
        ],
        files=[
            (f"{ESSAY}/Notes.md", "\ufeff![](.data/md-images/pic.png)\nProgrammer"),
            (f"{ESSAY}/.data/md-images/pic.png", b"\x89PNG"),
            (f"{ESSAY}/.data/Icon.png", b"icon"),
        ],
    )
    return path


def test_read_brz_reads_real_thebrain_markdown_notes(real_layout_brz):
    note = read_brz(real_layout_brz).notes[ESSAY]
    assert note.format == "md", "Type-1/NoteType-4 attachments at <id>/Notes.md are markdown notes"
    assert note.text == "![](.data/md-images/pic.png)\nProgrammer", "note text should be read with the BOM stripped"


def test_read_brz_reads_real_thebrain_md_images_but_not_icons(real_layout_brz):
    snapshot = read_brz(real_layout_brz)
    assert [image.name for image in snapshot.images[ESSAY]] == ["pic.png"], (
        "Type-12 images under .data/md-images should be collected while Type-5 icons stay ignored"
    )
    assert snapshot.images[ESSAY][0].data == b"\x89PNG", "image bytes should come from the .data/md-images folder"
