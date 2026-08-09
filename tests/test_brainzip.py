import json
import zipfile
from datetime import datetime, timezone

import pytest

from brainsync.brainzip import (
    RELATION_CHILD,
    RELATION_JUMP,
    make_link,
    make_thought,
    stable_id,
    to_json_lines,
    write_brz,
)
from brainsync.crawl import Note
from brainsync.main import build_links, build_thoughts, export

NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("parts_a", "parts_b", "should_match"),
    [
        (("thought", "url1"), ("thought", "url1"), True),
        (("thought", "url1"), ("thought", "url2"), False),
        (("thought", "a|b"), ("thought", "a", "b"), False),
    ],
)
def test_stable_id_determinism(parts_a, parts_b, should_match):
    matches = stable_id(*parts_a) == stable_id(*parts_b)
    assert matches is should_match, f"stable_id{parts_a} vs stable_id{parts_b} equality should be {should_match}"


def test_to_json_lines_emits_one_record_per_line():
    lines = to_json_lines([{"a": 1}, {"b": 2}]).lstrip("\ufeff").splitlines()
    assert [json.loads(line) for line in lines] == [{"a": 1}, {"b": 2}], (
        "to_json_lines should produce parseable JSON, one record per line"
    )


def _read_json_lines(archive, name):
    return [json.loads(line) for line in archive.read(name).decode("utf-8-sig").splitlines() if line]


def test_write_brz_layout(tmp_path):
    with_notes = make_thought("u1", "First", "<h2>First</h2><p>body</p>")
    without_notes = make_thought("u2", "Second")
    link = make_link(with_notes.id, without_notes.id, RELATION_JUMP)
    target = tmp_path / "out.brz"

    write_brz(target, "Test Brain", [with_notes, without_notes], [link], now=NOW)

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        assert {"meta.json", "thoughts.json", "links.json", "attachments.json", "settings.json"} <= names, (
            "write_brz should include meta, thoughts, links, attachments and settings files"
        )
        assert f"{with_notes.id}/Notes/notes.html" in names, (
            "write_brz should store html notes in the thought's Notes subfolder"
        )
        assert not any(name.startswith(without_notes.id) for name in names), (
            "write_brz should create no folder for thoughts without notes"
        )
        assert archive.read("thoughts.json").startswith(b"\xef\xbb\xbf"), (
            "json files should start with a UTF-8 BOM like real TheBrain exports"
        )

        thoughts = _read_json_lines(archive, "thoughts.json")
        assert [t["Name"] for t in thoughts] == ["First", "Second"], (
            "thoughts.json should contain every thought with its name"
        )

        links = _read_json_lines(archive, "links.json")
        assert links[0]["Relation"] == RELATION_JUMP, "links.json should preserve the link relation"


def test_write_brz_registers_notes_as_attachments(tmp_path):
    thought = make_thought("u1", "First", "<h2>First</h2>")
    target = tmp_path / "out.brz"

    write_brz(target, "Test Brain", [thought], [], now=NOW)

    with zipfile.ZipFile(target) as archive:
        attachment = _read_json_lines(archive, "attachments.json")[0]
        assert attachment["SourceId"] == thought.id, "notes attachment should point at its thought via SourceId"
        assert (attachment["Type"], attachment["NoteType"], attachment["Location"]) == (4, 2, "notes.html"), (
            "notes attachment should be Type 4 / NoteType 2 located at notes.html, matching real exports"
        )
        html = archive.read(f"{thought.id}/Notes/notes.html")
        assert attachment["DataLength"] == len(html), "DataLength should equal the notes.html byte size"

        meta = json.loads(archive.read("meta.json").decode("utf-8-sig"))
        assert meta["AttachmentFileStates"] == {attachment["Id"]: 1}, (
            "meta.json should list every notes attachment id in AttachmentFileStates"
        )


@pytest.mark.parametrize(
    ("setting_id", "expected"),
    [
        ("eadd590c-a791-50e2-a8ed-6c96e93d15a8", "Andy Notes"),
        ("7501869f-6fc5-5c4b-b838-f3f7287c138c", "Andy Notes"),
    ],
)
def test_write_brz_stores_brain_name_under_both_known_setting_ids(tmp_path, setting_id, expected):
    target = tmp_path / "out.brz"

    write_brz(target, "Andy Notes", [make_thought("u1", "First")], [], now=NOW)

    with zipfile.ZipFile(target) as archive:
        settings = {record["Id"]: record["Value"] for record in _read_json_lines(archive, "settings.json")}
        assert settings.get(setting_id) == expected, (
            f"settings.json should carry the brain name under setting id {setting_id}"
        )


def test_write_brz_marks_home_thought(tmp_path):
    from brainsync.brainzip import HOME_THOUGHT_SETTING_ID

    root = make_thought("root", "Root")
    target = tmp_path / "out.brz"

    write_brz(target, "Andy Notes", [root], [], now=NOW, home_thought_id=root.id)

    with zipfile.ZipFile(target) as archive:
        settings = {record["Id"]: record["Value"] for record in _read_json_lines(archive, "settings.json")}
        assert settings.get(HOME_THOUGHT_SETTING_ID) == root.id, (
            "settings.json should point the home thought setting at the root thought"
        )


def _note(url, title, outgoing=()):
    return Note(url=url, title=title, body_html=f"<main><h1>{title}</h1></main>", outgoing=tuple(outgoing))


def test_build_links_creates_children_and_jumps():
    notes = {
        "https://notes.andymatuschak.org/a": _note("https://notes.andymatuschak.org/a", "A", ["https://notes.andymatuschak.org/b"]),
        "https://notes.andymatuschak.org/b": _note("https://notes.andymatuschak.org/b", "B"),
    }
    root, by_url = build_thoughts(notes)

    links = build_links(root, by_url, notes)

    child_count = sum(1 for link in links if link.relation == RELATION_CHILD)
    jump_count = sum(1 for link in links if link.relation == RELATION_JUMP)
    assert child_count == 2, "build_links should attach every note as a child of the root"
    assert jump_count == 1, "build_links should create one jump link per note-to-note reference"


def test_build_links_ignores_targets_outside_crawl():
    notes = {
        "https://notes.andymatuschak.org/a": _note(
            "https://notes.andymatuschak.org/a", "A", ["https://notes.andymatuschak.org/missing"]
        ),
    }
    root, by_url = build_thoughts(notes)

    links = build_links(root, by_url, notes)

    assert all(link.relation == RELATION_CHILD for link in links), (
        "build_links should drop jump links pointing outside the crawled set"
    )


def test_export_is_deterministic(tmp_path):
    notes = {
        "https://notes.andymatuschak.org/a": _note("https://notes.andymatuschak.org/a", "A"),
    }
    first, second = tmp_path / "one.brz", tmp_path / "two.brz"

    export(notes, first)
    export(notes, second)

    def thought_ids(path):
        with zipfile.ZipFile(path) as archive:
            return [json.loads(line)["Id"] for line in archive.read("thoughts.json").decode("utf-8-sig").splitlines()]

    assert thought_ids(first) == thought_ids(second), (
        "export should assign the same thought ids across runs so re-imports merge cleanly"
    )


def test_thought_record_carries_default_icon_info():
    from brainsync.brainzip import THOUGHT_ICON_INFO_NONE, thought_record

    record = thought_record(make_thought("u1", "First"), "brain-id", NOW)
    assert record["ThoughtIconInfo"] == THOUGHT_ICON_INFO_NONE == "1::0:False:False:0:", (
        "thought records should carry the exact no-icon ThoughtIconInfo native to the user's TheBrain version"
    )
    assert "DisplayModificationDateTime" in record, (
        "thought records should carry DisplayModificationDateTime like native exports"
    )


def test_short_guid_matches_real_thebrain_encoding():
    from brainsync.brainzip import short_guid

    assert short_guid("a6249712-850b-4795-8bfe-25c3cbfd7000") == "EpckpguFlUeL_iXDy_1wAA", (
        "short_guid should reproduce the exact brain:// id observed in a real TheBrain archive"
    )


def test_brain_url_combines_short_guid_and_name_slug():
    from brainsync.brainzip import brain_url

    url = brain_url("a6249712-850b-4795-8bfe-25c3cbfd7000", "Reince Priebus")
    assert url == "brain://EpckpguFlUeL_iXDy_1wAA/ReincePriebus", (
        "brain_url should join the short guid with an alphanumeric slug of the thought name"
    )


def _aliased_notes():
    slug = "https://notes.andymatuschak.org/Evergreen_notes"
    zid = "https://notes.andymatuschak.org/z5E5Q"
    referrer = "https://notes.andymatuschak.org/zTaxonomy"
    return {
        slug: _note(slug, "Evergreen notes"),
        zid: _note(zid, "Evergreen notes"),
        referrer: Note(
            url=referrer,
            title="Taxonomy",
            body_html=f'<div><h1>Taxonomy</h1><a href="{zid}">evergreen</a></div>',
            outgoing=(zid,),
        ),
    }


def test_aliased_urls_merge_into_one_thought():
    root, by_url = build_thoughts(_aliased_notes())

    unique = dict.fromkeys(by_url.values())
    assert len(unique) == 2, "urls sharing a title should merge into a single thought"
    slug_thought = by_url["https://notes.andymatuschak.org/Evergreen_notes"]
    zid_thought = by_url["https://notes.andymatuschak.org/z5E5Q"]
    assert slug_thought.id == zid_thought.id, "slug and z-id aliases should resolve to the same thought id"


def test_links_to_any_alias_become_the_same_brain_link():
    _, by_url = build_thoughts(_aliased_notes())

    taxonomy = by_url["https://notes.andymatuschak.org/zTaxonomy"]
    evergreen = by_url["https://notes.andymatuschak.org/z5E5Q"]
    from brainsync.brainzip import brain_url

    assert brain_url(evergreen.id, "Evergreen notes") in taxonomy.notes_html, (
        "a reference to any alias url should rewrite to the merged thought's brain link"
    )


def test_export_writes_each_merged_thought_once(tmp_path):
    target = tmp_path / "out.brz"

    export(_aliased_notes(), target)

    with zipfile.ZipFile(target) as archive:
        thoughts = _read_json_lines(archive, "thoughts.json")
        names = [t["Name"] for t in thoughts]
        assert names.count("Evergreen notes") == 1, "merged thoughts should appear in thoughts.json exactly once"
        links = _read_json_lines(archive, "links.json")
        child_targets = [l["ThoughtIdB"] for l in links if l["Relation"] == RELATION_CHILD]
        assert len(child_targets) == len(set(child_targets)), "merged thoughts should get exactly one child link"


def test_nested_urls_build_parent_child_hierarchy():
    base = "https://wiki.example.com"
    notes = {
        f"{base}/focusing": _note(f"{base}/focusing", "Focusing"),
        f"{base}/focusing/habits": _note(f"{base}/focusing/habits", "Habits"),
        f"{base}/art": _note(f"{base}/art", "Art"),
    }
    root, by_url = build_thoughts(notes)

    links = build_links(root, by_url, notes)

    child_of = {l["ThoughtIdB"]: l["ThoughtIdA"] for l in ({"ThoughtIdA": l.id_a, "ThoughtIdB": l.id_b} for l in links if l.relation == RELATION_CHILD)}
    assert child_of[by_url[f"{base}/focusing/habits"].id] == by_url[f"{base}/focusing"].id, (
        "a nested url should become a child of its parent path's thought"
    )
    assert child_of[by_url[f"{base}/focusing"].id] == root.id, "top-level pages should stay children of the root"
    assert child_of[by_url[f"{base}/art"].id] == root.id, "pages without a crawled parent should attach to the root"


def test_write_brz_stores_notes_images_as_type6_attachments(tmp_path):
    from brainsync.brainzip import NotesImage

    image = NotesImage(id=stable_id("image", "k"), extension=".png", data=b"png-bytes")
    thought = make_thought("u1", "First", "<p>x</p>", images=(image,))
    target = tmp_path / "out.brz"

    write_brz(target, "Test Brain", [thought], [], now=NOW)

    with zipfile.ZipFile(target) as archive:
        assert archive.read(f"{thought.id}/Notes/{image.id}.png") == b"png-bytes", (
            "notes images should be stored next to notes.html in the Notes folder"
        )
        records = {r["Id"]: r for r in _read_json_lines(archive, "attachments.json")}
        assert records[image.id]["Type"] == 6, "notes images should be Type 6 attachments"
        assert records[image.id]["Location"] == f"{image.id}.png", (
            "notes image attachment Location should be the guid filename"
        )
        meta = json.loads(archive.read("meta.json").decode("utf-8-sig"))
        assert image.id in meta["AttachmentFileStates"], (
            "notes image attachment ids should be listed in AttachmentFileStates"
        )


def test_validate_passes_on_generated_export(tmp_path):
    from brainsync.check import validate

    target = tmp_path / "out.brz"
    export(_aliased_notes(), target)

    assert validate(target) == [], "a freshly generated export should pass every integrity check"


def test_validate_reports_dangling_link(tmp_path):
    from brainsync.check import validate
    from brainsync.brainzip import RELATION_JUMP

    thought = make_thought("u1", "First")
    ghost = make_thought("ghost", "Ghost")
    target = tmp_path / "out.brz"
    write_brz(target, "Test", [thought], [make_link(thought.id, ghost.id, RELATION_JUMP)], now=NOW)

    problems = validate(target)
    assert any("missing thought" in p for p in problems), (
        "validate should report links referencing thoughts absent from thoughts.json"
    )
