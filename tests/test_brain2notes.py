import pytest

from brainsync.brain2notes import (
    apply_plan,
    close_open_fence,
    frontmatter_title,
    rewrite_image_paths,
    assign_slugs,
    drop_leading_title,
    is_stub,
    note_body,
    merge_frontmatter,
    plan_merge,
    rewrite_brain_links,
    select_thoughts,
    short_title,
)
from brainsync.brainread import KIND_TAG, KIND_TYPE, MEANING_NORMAL, MEANING_TAG, BrainLink, BrainNote, BrainSnapshot, BrainThought
from brainsync.brainzip import brain_url, stable_id
from brainsync.garden import GardenNote

PUBLIC = stable_id("thought", "tag-public")
QUOTES = stable_id("thought", "type-quotes")
ESSAY = stable_id("thought", "essay")
IDEAS = stable_id("thought", "ideas")
SECRET = stable_id("thought", "secret")


def snapshot(notes=None, links=(), images=None, changed=None):
    return BrainSnapshot(
        thoughts={
            PUBLIC: BrainThought(PUBLIC, "Public", kind=KIND_TAG),
            QUOTES: BrainThought(QUOTES, "Quotes", kind=KIND_TYPE),
            ESSAY: BrainThought(ESSAY, "My Essay", type_id=QUOTES, created="2026-07-01T10:00:00"),
            IDEAS: BrainThought(IDEAS, "Ideas", tag_ids=(PUBLIC,)),
            SECRET: BrainThought(SECRET, "Secret Plan"),
        },
        links=(BrainLink(PUBLIC, ESSAY, 1, MEANING_TAG), *links),
        notes=notes or {},
        images=images or {},
        changed=changed or {},
    )


def test_select_thoughts_by_tag_sees_link_tagging_and_tagids_alike():
    names = [t.name for t in select_thoughts(snapshot(), "public")]
    assert names == ["Ideas", "My Essay"], (
        "selection should match the tag case-insensitively via meaning-5 links and TagIds both"
    )


def test_select_thoughts_without_tag_takes_all_normal_thoughts():
    names = [t.name for t in select_thoughts(snapshot(), None)]
    assert names == ["Ideas", "My Essay", "Secret Plan"], "no tag should mean every normal thought, tags excluded"


def test_select_thoughts_fails_loudly_on_unknown_tag():
    with pytest.raises(SystemExit, match="nosuch"):
        select_thoughts(snapshot(), "nosuch")


@pytest.mark.parametrize(
    ("target_id", "text", "expected"),
    [
        (IDEAS, "some ideas", "see [some ideas](ideas.md) end"),
        (SECRET, "the plan", "see the plan end"),
        (SECRET, "", "see Secret Plan end"),
    ],
)
def test_rewrite_brain_links_exported_vs_private(target_id, text, expected):
    thought = snapshot().thoughts[target_id]
    body = f"see [{text}]({brain_url(thought.id, thought.name)}) end"
    slugs = {ESSAY: "my-essay", IDEAS: "ideas"}
    result = rewrite_brain_links(body, snapshot(), slugs, {})
    assert result == expected, "links to exported thoughts should become note links, private ones unwrap to text"


def test_rewrite_brain_links_targets_existing_garden_note_by_title():
    body = f"see [plan]({brain_url(SECRET, 'SecretPlan')})"
    result = rewrite_brain_links(body, snapshot(), {}, {"Secret Plan": "secret-plan.org"})
    assert result == "see [plan](secret-plan.org)", (
        "a link to an unexported thought whose name matches a garden note should point at that note"
    )


def test_rewrite_brain_links_leaves_external_links_alone():
    body = "see [site](https://example.org) end"
    assert rewrite_brain_links(body, snapshot(), {}, {}) == body, "non-brain links should pass through untouched"


def test_note_body_converts_html_and_drops_duplicate_title_heading():
    notes = {ESSAY: BrainNote("html", "<h2>My Essay</h2><p>hello <b>world</b></p>")}
    body = note_body(snapshot(notes), snapshot().thoughts[ESSAY])
    assert body == "hello **world**", "html notes should become markdown without the duplicated title heading"


def test_note_body_passes_markdown_through():
    notes = {ESSAY: BrainNote("md", "plain *md* text")}
    assert note_body(snapshot(notes), snapshot().thoughts[ESSAY]) == "plain *md* text", (
        "markdown notes should pass through unconverted"
    )


@pytest.mark.parametrize(
    ("body", "title", "expected"),
    [
        ("# My Essay\nrest", "My Essay", "rest"),
        ("## My Essay\nrest", "My Essay", "rest"),
        ("# Other\nrest", "My Essay", "# Other\nrest"),
    ],
)
def test_drop_leading_title(body, title, expected):
    assert drop_leading_title(body, title) == expected, "only a leading heading equal to the title should be dropped"


def garden_note(slug, title, brain_id="", fmt="md", text=""):
    return GardenNote(slug=slug, format=fmt, title=title, brain_id=brain_id, text=text)


def test_assign_slugs_keeps_owned_slug_and_suffixes_collisions():
    thoughts = [BrainThought(ESSAY, "My Essay"), BrainThought(IDEAS, "About")]
    garden = {
        "renamed-essay": garden_note("renamed-essay", "My Essay", brain_id=ESSAY),
        "about": garden_note("about", "About the site"),
    }
    slugs = assign_slugs(thoughts, garden)
    assert slugs[ESSAY] == "renamed-essay", "a previously exported thought should keep its garden slug"
    assert slugs[IDEAS] == f"about-{IDEAS[:8]}", "a slug taken by a hand-written note should get a guid suffix"


@pytest.mark.parametrize(
    ("name", "owned_slug", "expected"),
    [
        ("Теплиця", f"untitled-{ESSAY[:8]}", "teplytsia"),
        ("Теплиця", "untitled", "teplytsia"),
        ("Untitled", "untitled", "untitled"),
        ("My Essay", "untitled-draft", "untitled-draft"),
    ],
    ids=[
        "suffixed fallback slug is replaced by a real one",
        "bare fallback slug is replaced by a real one",
        "a thought really named Untitled keeps its slug",
        "a chosen slug that merely starts with untitled is kept",
    ],
)
def test_assign_slugs_heals_fallback_slugs(name, owned_slug, expected):
    garden = {owned_slug: garden_note(owned_slug, name, brain_id=ESSAY)}
    slugs = assign_slugs([BrainThought(ESSAY, name)], garden)
    assert slugs[ESSAY] == expected, (
        "assign_slugs should drop an owned untitled fallback slug once the name slugifies properly"
    )


def test_assign_slugs_healing_never_takes_a_handwritten_slug():
    garden = {
        "untitled": garden_note("untitled", "Теплиця", brain_id=ESSAY),
        "teplytsia": garden_note("teplytsia", "Моя теплиця"),
    }
    slugs = assign_slugs([BrainThought(ESSAY, "Теплиця")], garden)
    assert slugs[ESSAY] == f"teplytsia-{ESSAY[:8]}", (
        "a healed slug colliding with a hand-written note should get a guid suffix, not overwrite it"
    )


def test_plan_merge_writes_frontmatter_with_date_tags_and_ownership():
    notes = {ESSAY: BrainNote("md", "body")}
    plan = plan_merge(snapshot(notes), {}, "public")
    text = plan.writes["my-essay.md"]
    assert text.startswith("---\n"), "exported notes should start with yaml frontmatter"
    assert "title: My Essay\n" in text, "frontmatter values should be raw text for the site's plain parser"
    assert "date: 2026-07-01" in text, "frontmatter should carry the creation date"
    assert f"brain-id: {ESSAY}" in text, "frontmatter should mark the note as brain-owned"
    assert "tags: [quotes]\n" in text, (
        "the thought's type should become an unquoted garden tag, with the selection tag excluded"
    )


def test_plan_merge_skips_handwritten_notes_and_links_to_them():
    notes = {IDEAS: BrainNote("md", f"see [essay]({brain_url(ESSAY, 'MyEssay')})")}
    garden = {"my-essay": garden_note("my-essay", "My Essay")}
    plan = plan_merge(snapshot(notes), garden, "public")
    assert plan.skipped == ("My Essay",), "a hand-written note with the same title should never be overwritten"
    assert "my-essay.md" not in plan.writes, "skipped thoughts should produce no file"
    assert "[essay](my-essay.md)" in plan.writes["ideas.md"], (
        "links to a skipped thought should target the existing hand-written note"
    )


def test_plan_merge_reports_orphans_and_prunes_only_on_request(tmp_path):
    garden = {"old": garden_note("old", "Old", brain_id="gone-guid")}
    (tmp_path / "old.md").write_text("---\ntitle: Old\nbrain-id: gone-guid\n---\n")
    plan = plan_merge(snapshot(), garden, "public")
    assert plan.orphans == ("old.md",), "owned notes whose thought lost the tag should be reported as orphans"

    apply_plan(plan, tmp_path, prune=False)
    assert (tmp_path / "old.md").exists(), "orphans should survive without --prune"
    apply_plan(plan, tmp_path, prune=True)
    assert not (tmp_path / "old.md").exists(), "orphans should be deleted with --prune"


def test_apply_plan_writes_notes_and_images_into_the_garden(tmp_path):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    images = {ESSAY: (type("I", (), {"name": "pic.png", "data": b"png"})(),)}
    notes = {ESSAY: BrainNote("md", "![d](pic.png)")}
    plan = plan_merge(snapshot(notes, images=images), {}, "public")

    apply_plan(plan, notes_dir, prune=False)

    assert "(../static/brain/my-essay/pic.png)" in (notes_dir / "my-essay.md").read_text(), (
        "image references should point into static/brain/<slug>/"
    )
    assert (tmp_path / "static" / "brain" / "my-essay" / "pic.png").read_bytes() == b"png", (
        "note images should be written next to the notes dir under static/brain"
    )


def test_render_appends_related_section_from_plex_links():
    linked = snapshot(links=(BrainLink(ESSAY, IDEAS, 1, MEANING_NORMAL), BrainLink(ESSAY, SECRET, 3, MEANING_NORMAL)))
    plan = plan_merge(linked, {}, "public")
    assert "## Related\n\n- [Ideas](ideas.md)" in plan.writes["my-essay.md"], (
        "plex links between exported thoughts should become a Related section"
    )
    assert "Secret Plan" not in plan.writes["my-essay.md"], (
        "plex links to unexported thoughts should not appear in Related"
    )


def test_related_section_can_target_handwritten_garden_notes():
    linked = snapshot(links=(BrainLink(IDEAS, SECRET, 3, MEANING_NORMAL), BrainLink(PUBLIC, SECRET, 1, MEANING_TAG)))
    garden = {"secret-plan": garden_note("secret-plan", "Secret Plan", fmt="org")}
    plan = plan_merge(linked, garden, "public")
    assert "- [Secret Plan](secret-plan.org)" in plan.writes["ideas.md"], (
        "Related should link to the hand-written note when the thought is skipped for it"
    )


QUOTE_NAME = '"A long quotation that plainly is the content of the thought rather than a label, kept in the name field."'


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Kent Beck", "Kent Beck"),
        (QUOTE_NAME, '"A long quotation that plainly is the content of the thought rather than a\u2026'),
    ],
)
def test_short_title_truncates_only_long_names_at_word_boundary(name, expected):
    assert short_title(name) == expected, "short_title should leave labels alone and ellipsize content-length names"


def quote_snapshot(notes=None):
    quote = BrainThought(SECRET, QUOTE_NAME, created="2026-07-01T10:00:00")
    base = snapshot(notes)
    return BrainSnapshot(
        thoughts={**base.thoughts, SECRET: quote},
        links=(*base.links, BrainLink(PUBLIC, SECRET, 1, MEANING_TAG), BrainLink(ESSAY, SECRET, 3, MEANING_NORMAL)),
        notes=base.notes,
    )


@pytest.mark.parametrize(
    ("thought_id", "notes", "expected_body"),
    [
        (SECRET, None, QUOTE_NAME),
        (IDEAS, None, ""),
        (SECRET, {SECRET: BrainNote("md", "real note")}, "real note"),
    ],
)
def test_note_body_promotes_content_length_names_of_noteless_thoughts(thought_id, notes, expected_body):
    body = note_body(quote_snapshot(notes), quote_snapshot(notes).thoughts[thought_id])
    assert body == expected_body, (
        "a noteless thought's name should become the body only when it is content-length, "
        "and a real note should always win over the name"
    )


def test_plan_merge_gives_name_as_content_thoughts_a_short_title_and_full_body():
    plan = plan_merge(quote_snapshot(), {}, "public")
    filename = next(name for name in plan.writes if name.startswith("a-long-quotation"))
    text = plan.writes[filename]
    assert text.splitlines()[1] == "title: A long quotation that plainly is the content of the thought rather than a\u2026", (
        "frontmatter title should be the ellipsized short form without surrounding quote marks"
    )
    assert QUOTE_NAME in text.split("---")[2], "the full name should be stored as the note body"
    assert f"]({filename})" in plan.writes["my-essay.md"], "Related links should target the shortened slug"
    assert "rather than a\u2026](" in plan.writes["my-essay.md"], (
        "Related link text should use the short title, not the full quotation"
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("![d](pic.png)", "![d](../static/brain/my-essay/pic.png)"),
        ("![d](<!--BrainNotesBase-->/pic.png)", "![d](../static/brain/my-essay/pic.png)"),
        ("![d](other.png)", "![d](other.png)"),
        ("![d](https://elsewhere.example/pic2.png)", "![d](https://elsewhere.example/pic2.png)"),
    ],
)
def test_rewrite_image_paths_handles_plain_and_token_references(body, expected):
    assert rewrite_image_paths(body, "my-essay", ("pic.png",)) == expected, (
        "embedded image references should be rewritten into static/brain/<slug>/ whether plain "
        "or behind TheBrain's BrainNotesBase token, and unknown images should stay untouched"
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Kent Beck", "Kent Beck"),
        ('"Short quote."', "Short quote."),
        ("\u201cCurly quote.\u201d", "Curly quote."),
        (QUOTE_NAME, "A long quotation that plainly is the content of the thought rather than a\u2026"),
    ],
)
def test_frontmatter_title_strips_edge_quotes_before_ellipsizing(name, expected):
    assert frontmatter_title(name) == expected, (
        "titles should lose quotation marks at the edges and only then be ellipsized"
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("```test\nQ: q?\nA: a", "```test\nQ: q?\nA: a\n```"),
        ("```test\nQ: q?\nA: a\n```", "```test\nQ: q?\nA: a\n```"),
        ("prose without fences", "prose without fences"),
        ("", ""),
        (
            "```python\nprint(1)\n```\n\n```test\nQ: q?\nA: a",
            "```python\nprint(1)\n```\n\n```test\nQ: q?\nA: a\n```",
        ),
    ],
)
def test_close_open_fence_balances_trailing_code_block(body, expected):
    assert close_open_fence(body) == expected, (
        "a fence left open at end-of-note should be closed, balanced bodies should pass through untouched"
    )


def test_render_closes_open_fence_before_related_section():
    text = "Known as BST.\n\n```test\nQ: What does BST depend on?\nA: Height."
    linked = snapshot(
        notes={ESSAY: BrainNote(format="md", text=text)},
        links=(BrainLink(ESSAY, IDEAS, 1, MEANING_NORMAL),),
    )
    plan = plan_merge(linked, {}, "public")
    note = plan.writes["my-essay.md"]
    closer = note.index("```", note.index("```test") + len("```test"))
    assert closer < note.index("## Related"), (
        "an open ```test fence should be closed before the Related section, "
        "so appended links never land inside the code block"
    )


def test_select_thoughts_lists_available_tags_on_miss():
    from brainsync.brainread import KIND_TAG, BrainSnapshot, BrainThought
    from brainsync.brain2notes import select_thoughts

    snapshot = BrainSnapshot(
        thoughts={
            "t1": BrainThought(id="t1", name="#public", kind=KIND_TAG),
            "t2": BrainThought(id="t2", name="#book", kind=KIND_TAG),
        }
    )

    with pytest.raises(SystemExit, match="ghost.*#book, #public"):
        select_thoughts(snapshot, "ghost")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("---\ntitle: T\nbrain-id: x\n---\n\nBody\n", "Body\n"),
        ("---\ntitle: T\n---\nBody without gap\n", "Body without gap\n"),
        ("No frontmatter\n", "No frontmatter\n"),
    ],
)
def test_strip_frontmatter(text, expected):
    from brainsync.brain2notes import strip_frontmatter

    assert strip_frontmatter(text) == expected, (
        "strip_frontmatter should remove the leading yaml block and separator blank line"
    )


@pytest.mark.parametrize(
    "text, expected_body, expected_related",
    [
        (
            "Body\n\n## Related\n\n- [A](a.md)\n- [B](b.md)\n",
            "Body\n",
            "## Related\n\n- [A](a.md)\n- [B](b.md)\n",
        ),
        (
            "Body\n\n## Related\n\n- [A](a.md)\n\n",
            "Body\n",
            "## Related\n\n- [A](a.md)\n",
        ),
        ("Body only\n", "Body only\n", ""),
        (
            "Intro\n\n## Related reading\n\nprose\n",
            "Intro\n\n## Related reading\n\nprose\n",
            "",
        ),
    ],
)
def test_strip_related_and_related_of(text, expected_body, expected_related):
    from brainsync.brain2notes import related_of, strip_related

    assert strip_related(text) == expected_body, (
        "strip_related should drop exactly the trailing generated Related section"
    )
    assert related_of(text) == expected_related, (
        "related_of should return exactly the trailing generated Related section"
    )


@pytest.mark.parametrize(
    "body, expected",
    [
        ("See [Alpha](alpha.md).", "See [Alpha](brain://{a_short}/alpha)."),
        ("Unknown [X](ghost.md) stays.", "Unknown [X](ghost.md) stays."),
        ("Image ![p](alpha.md) stays.", "Image ![p](alpha.md) stays."),
        ("External [x](https://e.com/a.md) stays.", "External [x](https://e.com/a.md) stays."),
    ],
)
def test_md_links_to_brain(body, expected):
    from brainsync.brain2notes import md_links_to_brain
    from brainsync.brainzip import short_guid

    ids_by_slug = {"alpha": "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000"}
    result = md_links_to_brain(body, ids_by_slug)

    assert result == expected.format(a_short=short_guid(ids_by_slug["alpha"])), (
        "md_links_to_brain should retarget known .md links to brain urls and touch nothing else"
    )


def test_push_body_is_the_pull_inverse():
    from brainsync.brain2notes import md_links_to_brain, push_body
    from brainsync.brainzip import short_guid

    garden_text = (
        "---\ntitle: Alpha\nbrain-id: id-a\n---\n\n"
        "Edited paragraph with [Beta](beta.md).\n\n"
        "## Related\n\n- [Beta](beta.md)\n"
    )
    ids_by_slug = {"beta": "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000"}

    pushed = push_body(garden_text, "Alpha", ids_by_slug)

    assert pushed == (
        "# Alpha\n\n"
        f"Edited paragraph with [Beta](brain://{short_guid(ids_by_slug['beta'])}/beta).\n"
    ), "push_body should strip frontmatter and Related, re-add the title, and retarget links"


def test_round_trip_pull_edit_push_pull():
    from brainsync.brain2notes import push_body, render_note

    body = f"# My Essay\n\nFirst paragraph.\n\nSecond paragraph with [Ideas]({brain_url(IDEAS, 'Ideas')})."
    snap = snapshot(
        notes={ESSAY: BrainNote("md", body), IDEAS: BrainNote("md", "# Ideas\n\nIdeas body.")},
        links=(BrainLink(ESSAY, IDEAS, 1, MEANING_NORMAL),),
    )
    thoughts = select_thoughts(snap, None)
    slugs = assign_slugs(thoughts, {})
    by_title = {t.name: f"{slugs[t.id]}.md" for t in thoughts}
    ids_by_slug = {slug: thought_id for thought_id, slug in slugs.items()}

    pulled = render_note(snap, snap.thoughts[ESSAY], slugs, by_title, None)
    edited = pulled.replace("First paragraph.", "First paragraph, edited.")

    pushed = push_body(edited, "My Essay", ids_by_slug)
    updated = snapshot(
        notes={ESSAY: BrainNote("md", pushed), IDEAS: BrainNote("md", "# Ideas\n\nIdeas body.")},
        links=(BrainLink(ESSAY, IDEAS, 1, MEANING_NORMAL),),
    )
    pulled_again = render_note(updated, updated.thoughts[ESSAY], slugs, by_title, None)

    assert pulled_again == edited, (
        "pull -> edit -> push -> pull should be byte-identical except for the edit"
    )


@pytest.mark.parametrize(
    ("rendered", "existing", "expected"),
    [
        (
            "---\ntitle: New\ndate: 2026-08-08\ntags: [b]\nbrain-id: x\n---\n\nbody\n",
            "---\ntitle: Old\ndate: 2026-08-05\ntags: [a]\nbrain-id: x\n---\n\nold\n",
            "---\ntitle: New\ndate: 2026-08-05\ntags: [b]\nbrain-id: x\n---\n\nbody\n",
        ),
        (
            "---\ntitle: T\ndate: 2026-08-08\nbrain-id: x\n---\n\nbody\n",
            "---\ntitle: T\ndescription: keeper\nbrain-id: x\n---\n\nold\n",
            "---\ntitle: T\ndescription: keeper\nbrain-id: x\ndate: 2026-08-08\n---\n\nbody\n",
        ),
        (
            "---\ntitle: T\nbrain-id: x\n---\n\nbody\n",
            "---\ntitle: T\ntags: [a, b]\nbrain-id: x\n---\n\nold\n",
            "---\ntitle: T\nbrain-id: x\n---\n\nbody\n",
        ),
        (
            "---\ntitle: T\ndate: 2026-08-08\nbrain-id: x\n---\n\nbody\n",
            "",
            "---\ntitle: T\ndate: 2026-08-08\nbrain-id: x\n---\n\nbody\n",
        ),
        (
            "---\ntitle: T\nbrain-id: x\n---\n\nbody\n",
            "no frontmatter here\n",
            "---\ntitle: T\nbrain-id: x\n---\n\nbody\n",
        ),
    ],
    ids=[
        "existing date should win over the rendered creation date while owned fields follow the render",
        "unknown existing fields should be preserved in place and new rendered fields appended",
        "tags dropped on the brain side should disappear from the merge",
        "a new note without an existing file should render unchanged",
        "an existing file without frontmatter should not derail the merge",
    ],
)
def test_merge_frontmatter(rendered, existing, expected):
    assert merge_frontmatter(rendered, existing) == expected, (
        "merge_frontmatter should overlay brain-owned fields and preserve the rest of the garden's frontmatter"
    )


def test_merge_frontmatter_is_idempotent():
    rendered = "---\ntitle: T\ndate: 2026-08-08\ntags: [b]\nbrain-id: x\n---\n\nbody\n"
    existing = "---\ntitle: T\ndate: 2026-08-05\ndescription: keeper\nbrain-id: x\n---\n\nold\n"
    once = merge_frontmatter(rendered, existing)
    assert merge_frontmatter(rendered, once) == once, (
        "merging a render against its own previous merge should be a fixed point, or sync would churn forever"
    )


def test_plan_merge_preserves_garden_frontmatter_on_pull():
    notes = {ESSAY: BrainNote("md", "body")}
    existing = f"---\ntitle: Stale\ndate: 2026-08-05\ndescription: keeper\ntags: [stale]\nbrain-id: {ESSAY}\n---\n\nold\n"
    garden = {"my-essay": garden_note("my-essay", "My Essay", brain_id=ESSAY, text=existing)}
    plan = plan_merge(snapshot(notes), garden, "public")
    text = plan.writes["my-essay.md"]
    assert "date: 2026-08-05\n" in text, "an existing garden date should survive the pull"
    assert "description: keeper\n" in text, "fields the sync does not own should survive the pull"
    assert "title: My Essay\n" in text, "the title should still follow the brain"
    assert "tags: [quotes]\n" in text, "tags should still follow the brain"


def test_plan_merge_adopts_title_matching_md_notes_when_asked():
    notes = {IDEAS: BrainNote("md", f"see [essay]({brain_url(ESSAY, 'MyEssay')})")}
    garden = {"my-essay": garden_note("my-essay", "My Essay", text="# My Essay\n\nsite body\n")}
    plan = plan_merge(snapshot(notes), garden, "public", adopt=True)
    assert plan.adopted == {"my-essay.md": ESSAY}, (
        "adopt should link a title-matching brain-id-less md note to its thought"
    )
    assert plan.skipped == (), "an adopted note should no longer be reported as skipped"
    assert "my-essay.md" in plan.writes, "the adopted thought should render under the existing file's slug"
    assert "[essay](my-essay.md)" in plan.writes["ideas.md"], (
        "links to an adopted thought should target the adopted file"
    )


def test_plan_merge_adopt_leaves_org_matches_skipped():
    garden = {"my-essay": garden_note("my-essay", "My Essay", fmt="org", text="#+TITLE: My Essay\n")}
    plan = plan_merge(snapshot(), garden, "public", adopt=True)
    assert plan.adopted == {}, "org notes cannot carry yaml frontmatter, so adopt should not claim them"
    assert plan.skipped == ("My Essay",), "an unadoptable title match should still be reported as skipped"


def test_plan_merge_without_adopt_is_unchanged():
    garden = {"my-essay": garden_note("my-essay", "My Essay", text="site body\n")}
    plan = plan_merge(snapshot(), garden, "public")
    assert plan.adopted == {}, "adoption should be strictly opt-in"
    assert plan.skipped == ("My Essay",), "the default skip protection should stay in place"


@pytest.mark.parametrize(
    "existing, expected",
    [
        (
            "# My Essay\n\nsite body\n",
            "---\ntitle: My Essay\nbrain-id: X\n---\n\nsite body\n",
        ),
        (
            "---\ntitle: My Essay\ndraft: true\n---\n\nsite body\n",
            "---\ntitle: My Essay\ndraft: true\nbrain-id: X\n---\n\nsite body\n",
        ),
        (
            "site body\n",
            "---\ntitle: My Essay\nbrain-id: X\n---\n\nsite body\n",
        ),
    ],
    ids=[
        "a leading title heading should move into the stamped frontmatter",
        "extra frontmatter fields should survive the brain-id stamp",
        "a bare body should gain the rendered frontmatter unchanged",
    ],
)
def test_adopt_text(existing, expected):
    from brainsync.brain2notes import adopt_text

    rendered = "---\ntitle: My Essay\nbrain-id: X\n---\n\nbrain body\n"
    assert adopt_text(rendered, existing, "My Essay") == expected, (
        "adopt_text should keep the garden body and take ownership frontmatter from the render"
    )


def test_push_body_drops_leading_title_heading():
    from brainsync.brain2notes import push_body

    garden_text = "---\ntitle: My Essay\nbrain-id: X\n---\n\n# My Essay\n\nbody\n"
    assert push_body(garden_text, "My Essay", {}) == "# My Essay\n\nbody\n", (
        "push_body should not double the title heading for adopted site notes"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("---\ntitle: T\n---\n", True),
        ("---\ntitle: T\n---\n\n", True),
        ("---\ntitle: T\n---\n\n# T\n", True),
        ("---\ntitle: T\n---\n\n## Related\n\n- [A](a.md)\n", True),
        ("---\ntitle: T\n---\n\nbody\n", False),
        ("---\ntitle: T\n---\n\nbody\n\n## Related\n\n- [A](a.md)\n", False),
        ("", False),
    ],
    ids=[
        "frontmatter only",
        "frontmatter and blank lines",
        "title heading only",
        "related section only",
        "body",
        "body with related",
        "missing file",
    ],
)
def test_is_stub(text, expected):
    assert is_stub(text) is expected, (
        "is_stub should be true only for an existing note with nothing but frontmatter, title and Related"
    )


def existing_essay(date, body=""):
    return f"---\ntitle: My Essay\ndate: {date}\ntags: [quotes]\nbrain-id: {ESSAY}\n---\n\n{body}"


@pytest.mark.parametrize(
    ("existing", "note", "changed", "expected_date"),
    [
        (existing_essay("2026-09-16"), "body", {ESSAY: "2026-09-24T18:02:11"}, "2026-09-24"),
        (existing_essay("2026-09-16", "## Related\n\n- [Ideas](ideas.md)\n"), "body", {ESSAY: "2026-09-24T18:02:11"}, "2026-09-24"),
        (existing_essay("2026-09-16"), "body", {}, "2026-09-25"),
        (existing_essay("2026-09-16"), "body", {ESSAY: "2026-09-10T08:00:00"}, "2026-09-25"),
        (existing_essay("2026-09-16"), None, {ESSAY: "2026-09-24T18:02:11"}, "2026-09-16"),
        (existing_essay("2026-09-16", "old body\n"), "new body", {ESSAY: "2026-09-24T18:02:11"}, "2026-09-16"),
        (None, "body", {ESSAY: "2026-09-24T18:02:11"}, "2026-07-01"),
    ],
    ids=[
        "stub fleshed out in the brain takes the brain's last change date",
        "a Related-only note counts as a stub",
        "without a logged change the fleshing-out sync day is used",
        "a logged change older than the stub date falls back to the sync day",
        "a stub that is still empty keeps its date",
        "an already written note keeps its date on edits",
        "a note exported for the first time keeps the creation date",
    ],
)
def test_plan_merge_redates_stubs_when_they_get_content(existing, note, changed, expected_date):
    notes = {ESSAY: BrainNote("md", note)} if note is not None else {}
    garden = (
        {"my-essay": garden_note("my-essay", "My Essay", brain_id=ESSAY, text=existing)} if existing else {}
    )
    plan = plan_merge(snapshot(notes, changed=changed), garden, "public", today="2026-09-25")
    assert f"date: {expected_date}\n" in plan.writes["my-essay.md"], (
        "plan_merge should move the date to the moment a stub gets content, and leave it alone otherwise"
    )


def test_plan_merge_redating_is_a_fixed_point():
    notes = {ESSAY: BrainNote("md", "body")}
    brain = snapshot(notes, changed={ESSAY: "2026-09-24T18:02:11"})
    stub = {"my-essay": garden_note("my-essay", "My Essay", brain_id=ESSAY, text=existing_essay("2026-09-16"))}
    first = plan_merge(brain, stub, "public", today="2026-09-25").writes["my-essay.md"]
    fleshed = {"my-essay": garden_note("my-essay", "My Essay", brain_id=ESSAY, text=first)}
    second = plan_merge(brain, fleshed, "public", today="2026-09-30").writes["my-essay.md"]
    assert second == first, "a note redated once should not be redated again by later syncs"
