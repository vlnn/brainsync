import json

import pytest

from brainsync.garden import load_garden, parse_note, pull_site, slugify


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("My Essay", "my-essay"),
        ("Ideas: AI & tools!", "ideas-ai-tools"),
        ("Café Zettelkasten", "cafe-zettelkasten"),
        ("---", "untitled"),
        ("word " * 30, ("word-" * 12)[:-1]),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected, f"slugify({name!r}) should produce {expected!r}"


@pytest.mark.parametrize(
    ("filename", "text", "expected_title", "expected_brain_id"),
    [
        ("post.md", '---\ntitle: "A Post"\nbrain-id: guid-1\n---\nbody', "A Post", "guid-1"),
        ("plain.md", "# Heading Title\nbody", "Heading Title", ""),
        ("bare.md", "just prose", "bare", ""),
        ("note.org", "#+title: Org Note\n#+date: 2024-01-01\n\nbody", "Org Note", ""),
    ],
)
def test_parse_note_extracts_title_and_ownership(tmp_path, filename, text, expected_title, expected_brain_id):
    path = tmp_path / filename
    path.write_text(text)
    note = parse_note(path)
    assert note.title == expected_title, "parse_note should find the title in frontmatter, org keyword, or heading"
    assert note.brain_id == expected_brain_id, "parse_note should read brain-id only when present"


def test_load_garden_indexes_notes_by_slug_and_ignores_other_files(tmp_path):
    (tmp_path / "one.md").write_text("# One")
    (tmp_path / "two.org").write_text("#+title: Two")
    (tmp_path / "index.json").write_text("{}")
    garden = load_garden(tmp_path)
    assert set(garden) == {"one", "two"}, "load_garden should index only .md/.org files by slug"
    assert garden["two"].filename == "two.org", "GardenNote.filename should keep the original extension"


def fake_site(pages):
    def fetch(url):
        if url not in pages:
            raise IOError(f"404 {url}")
        return pages[url]

    return fetch


def test_pull_site_downloads_only_missing_notes(tmp_path):
    (tmp_path / "have.md").write_text("# Have")
    fetch = fake_site(
        {
            "https://vlnn.dev/index.json": json.dumps({"have": {}, "need-md": {}, "need-org": {}}),
            "https://vlnn.dev/notes/need-md.md": "# Need",
            "https://vlnn.dev/notes/need-org.org": "#+title: Need Org",
        }
    )

    pulled = pull_site("https://vlnn.dev/", tmp_path, fetch)

    assert pulled == ["need-md", "need-org"], "pull_site should download exactly the notes missing locally"
    assert (tmp_path / "need-org.org").read_text() == "#+title: Need Org", (
        "pull_site should fall back to .org when .md is not on the site"
    )
    assert (tmp_path / "have.md").read_text() == "# Have", "pull_site should never overwrite local files"


def test_parse_note_carries_the_raw_text(tmp_path):
    path = tmp_path / "n.md"
    path.write_text("---\ntitle: N\ndate: 2026-08-05\n---\n\nbody\n")
    assert parse_note(path).text == path.read_text(), (
        "parse_note should keep the raw text so pulls can merge frontmatter against it"
    )
