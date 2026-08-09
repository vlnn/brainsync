import pytest

from brainsync.sync import NotePair, SyncPlan, classify, plan_sync


def pair(base, garden, brain, filename="note.md"):
    return NotePair(filename=filename, base=base, garden=garden, brain=brain)


@pytest.mark.parametrize(
    "base, garden, brain, expected",
    [
        ("v1", "v1", "v1", "noop"),
        ("v1", "v2", "v1", "push"),
        ("v1", "v1", "v2", "pull"),
        ("v1", "v2", "v3", "conflict"),
        ("v1", "v2", "v2", "noop"),
        (None, None, "v1", "pull"),
        ("v1", None, "v1", "deleted"),
        ("v1", None, "v2", "deleted"),
        ("v1", "v1", None, "orphan"),
        ("v1", "v2", None, "orphan"),
        (None, "v1", "v1", "noop"),
        (None, "v1", "v2", "conflict"),
    ],
    ids=[
        "all equal should be a noop",
        "dirty garden with unchanged brain should plan a push",
        "clean garden with changed brain should plan a pull",
        "both sides changed differently should be a conflict",
        "both sides converged on the same text should be a noop",
        "note new on the brain side should plan a pull",
        "file deleted locally with brain unchanged should be reported, not pushed",
        "file deleted locally with brain changed should be reported, not pulled",
        "thought gone from the brain should be an orphan",
        "thought gone with local edits should still be an orphan",
        "untracked file matching the brain should be a noop",
        "untracked file differing from the brain should be a conflict",
    ],
)
def test_classify(base, garden, brain, expected):
    assert classify(pair(base, garden, brain)) == expected, (
        f"classify({base!r}, {garden!r}, {brain!r}) should be {expected!r}"
    )


def test_plan_sync_buckets_by_action():
    pairs = [
        pair("v1", "v2", "v1", filename="pushed.md"),
        pair("v1", "v1", "v2", filename="pulled.md"),
        pair("v1", "v2", "v3", filename="conflicted.md"),
        pair("v1", "v1", "v1", filename="untouched.md"),
        pair("v1", None, "v1", filename="deleted.md"),
        pair("v1", "v1", None, filename="orphaned.md"),
    ]

    plan = plan_sync(pairs)

    assert plan == SyncPlan(
        pushes=("pushed.md",),
        pulls=("pulled.md",),
        conflicts=("conflicted.md",),
        deleted=("deleted.md",),
        orphans=("orphaned.md",),
    ), "plan_sync should bucket every pair by its classification, dropping noops"


def test_plan_sync_first_run_is_all_pulls():
    pairs = [
        pair(None, None, "alpha body", filename="alpha.md"),
        pair(None, None, "beta body", filename="beta.md"),
    ]

    plan = plan_sync(pairs)

    assert plan.pulls == ("alpha.md", "beta.md"), (
        "with no base and no local files, the first sync should pull everything"
    )
    assert not (plan.pushes or plan.conflicts), (
        "a first run should never push or conflict"
    )


def test_plan_sync_is_deterministic():
    pairs = [
        pair("v1", "v1", "v2", filename="b.md"),
        pair("v1", "v1", "v2", filename="a.md"),
    ]

    assert plan_sync(pairs).pulls == ("a.md", "b.md"), (
        "buckets should be sorted so plans are reproducible"
    )


@pytest.fixture
def repo(tmp_path):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "alpha.md").write_text("---\ntitle: Alpha\nbrain-id: id-a\n---\n\nbase alpha\n")
    git("init", "-q")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-qm", "base")
    return tmp_path


def test_head_content_reads_the_committed_version(repo):
    from brainsync.sync import head_content

    (repo / "notes" / "alpha.md").write_text("working tree version")

    assert head_content(repo, "notes/alpha.md") == (
        "---\ntitle: Alpha\nbrain-id: id-a\n---\n\nbase alpha\n"
    ), "head_content should return the HEAD version, not the working tree"


def test_head_content_none_for_untracked(repo):
    from brainsync.sync import head_content

    assert head_content(repo, "notes/new.md") is None, (
        "head_content should be None for files absent from HEAD"
    )


def test_build_pairs_covers_writes_and_orphans(repo):
    from brainsync.sync import build_pairs

    (repo / "notes" / "alpha.md").write_text("locally edited")
    writes = {"alpha.md": "fresh render", "new.md": "brand new render"}
    orphans = ("gone.md",)
    (repo / "notes" / "gone.md").write_text("still on disk")

    pairs = {pair.filename: pair for pair in build_pairs(writes, orphans, repo / "notes")}

    assert pairs["alpha.md"].garden == "locally edited", (
        "build_pairs should read the working tree for the garden side"
    )
    assert pairs["alpha.md"].base == "---\ntitle: Alpha\nbrain-id: id-a\n---\n\nbase alpha\n", (
        "build_pairs should read HEAD for the base"
    )
    assert pairs["alpha.md"].brain == "fresh render", (
        "build_pairs should take the brain side from the merge's rendered writes"
    )
    assert pairs["new.md"] == NotePair("new.md", None, None, "brand new render"), (
        "a note new on the brain side should have no base and no garden content"
    )
    assert pairs["gone.md"].brain is None, (
        "an orphaned file should have no brain side"
    )


def test_report_plan_prints_all_buckets(capsys):
    from brainsync.sync import report_plan

    plan = SyncPlan(
        pushes=("p.md",), pulls=("l.md",), conflicts=("c.md",),
        deleted=("d.md",), orphans=("o.md",),
    )

    report_plan(plan)
    out = capsys.readouterr().out

    for line in ("push  p.md", "pull  l.md", "conflict c.md", "deleted-locally d.md", "orphan o.md"):
        assert line in out, f"report_plan should print {line!r}"


def test_report_plan_when_nothing_to_do(capsys):
    from brainsync.sync import report_plan

    report_plan(SyncPlan())

    assert "in sync" in capsys.readouterr().out, (
        "an empty plan should say the garden is in sync"
    )


def test_execute_plan_pushes_then_pulls(repo, mocker):
    from brainsync.sync import execute_plan
    from brainsync.brain2notes import MergePlan

    client = mocker.Mock()
    (repo / "notes" / "alpha.md").write_text(
        "---\ntitle: Alpha\nbrain-id: id-a\n---\n\nedited body\n"
    )
    plan = SyncPlan(pushes=("alpha.md",), pulls=("new.md",))
    merge = MergePlan(writes={"new.md": "fresh render", "alpha.md": "stale render"})
    names = {"id-a": "Alpha"}
    ids_by_slug = {"alpha": "id-a", "new": "id-n"}

    execute_plan(plan, merge, repo / "notes", client, names, ids_by_slug)

    client.update_note_markdown.assert_called_once_with("id-a", "# Alpha\n\nedited body\n"), (
        "execute_plan should push the stripped, retargeted garden body"
    )
    assert (repo / "notes" / "new.md").read_text() == "fresh render", (
        "execute_plan should write pulled notes from the merge render"
    )
    assert (repo / "notes" / "alpha.md").read_text().endswith("edited body\n"), (
        "a pushed note's local file must not be overwritten by its stale render"
    )


def test_execute_plan_flags_edited_related(repo, mocker, capsys):
    from brainsync.sync import execute_plan
    from brainsync.brain2notes import MergePlan

    client = mocker.Mock()
    (repo / "notes" / "alpha.md").write_text(
        "---\ntitle: Alpha\nbrain-id: id-a\n---\n\nbody\n\n## Related\n\n- [Hand added](hand.md)\n"
    )
    plan = SyncPlan(pushes=("alpha.md",))
    merge = MergePlan(writes={"alpha.md": "---\nbrain-id: id-a\n---\n\nbody\n\n## Related\n\n- [Real](real.md)\n"})

    execute_plan(plan, merge, repo / "notes", client, {"id-a": "Alpha"}, {"alpha": "id-a"})

    assert "Related" in capsys.readouterr().err, (
        "edits under ## Related should be flagged as ignored, since Related is derived from the plex"
    )
    pushed = client.update_note_markdown.call_args.args[1]
    assert "Hand added" not in pushed, (
        "the edited Related section must not be pushed to the brain"
    )


def sync_argv(repo, *extra):
    return ["brainsync sync", "--notes-dir", str(repo / "notes"), *extra]


@pytest.fixture
def synced_world(repo, mocker):
    from brainsync.brainread import BrainSnapshot
    from brainsync.brain2notes import MergePlan

    source = mocker.Mock()
    source.snapshot.return_value = BrainSnapshot(thoughts={})
    client = mocker.Mock()
    mocker.patch("brainsync.sync.make_api_source", return_value=(client, source))
    merge = MergePlan(writes={"alpha.md": (repo / "notes" / "alpha.md").read_text()})
    plan_merge = mocker.patch("brainsync.sync.plan_merge", return_value=merge)
    return client, plan_merge, merge


def test_sync_main_dry_run_touches_nothing(repo, monkeypatch, synced_world, capsys):
    from brainsync.sync import main

    client, _, _ = synced_world
    (repo / "notes" / "alpha.md").write_text("---\ntitle: A\nbrain-id: id-a\n---\n\nedited\n")
    monkeypatch.setattr("sys.argv", sync_argv(repo, "--dry-run"))

    main()

    assert "push  alpha.md" in capsys.readouterr().out, (
        "dry-run should print the plan"
    )
    client.update_note_markdown.assert_not_called(), (
        "dry-run must not touch the brain"
    )


def test_sync_main_exits_nonzero_on_conflict(repo, monkeypatch, synced_world, mocker):
    from brainsync.sync import main
    from brainsync.brain2notes import MergePlan

    client, plan_merge, _ = synced_world
    plan_merge.return_value = MergePlan(writes={"alpha.md": "brain changed render"})
    (repo / "notes" / "alpha.md").write_text("---\ntitle: A\nbrain-id: id-a\n---\n\nlocally changed\n")
    monkeypatch.setattr("sys.argv", sync_argv(repo))

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code != 0, "conflicts should make the sync exit nonzero"
    client.update_note_markdown.assert_not_called(), (
        "conflicted notes must never be pushed"
    )


def test_sync_main_requires_a_git_repo(tmp_path, monkeypatch, mocker):
    from brainsync.sync import main

    (tmp_path / "notes").mkdir()
    mocker.patch("brainsync.sync.make_api_source")
    monkeypatch.setattr("sys.argv", sync_argv(tmp_path))

    with pytest.raises(SystemExit, match="git"):
        main()


def test_execute_plan_writes_images_for_pulled_notes(repo, mocker):
    from brainsync.sync import execute_plan
    from brainsync.brain2notes import MergePlan

    plan = SyncPlan(pulls=("pic.md",))
    merge = MergePlan(
        writes={"pic.md": "render with image"},
        images={"pic/shot.png": b"png-bytes", "other/ignored.png": b"nope"},
    )

    execute_plan(plan, merge, repo / "notes", mocker.Mock(), {}, {})

    written = repo / "static" / "brain" / "pic" / "shot.png"
    assert written.read_bytes() == b"png-bytes", (
        "pulling a note should also write its images under static/brain/<slug>/"
    )
    assert not (repo / "static" / "brain" / "other").exists(), (
        "images of notes that were not pulled should stay untouched"
    )


def test_execute_plan_adopts_by_pushing_garden_body_and_stamping_brain_id(repo, mocker):
    from brainsync.sync import execute_plan
    from brainsync.brain2notes import MergePlan

    client = mocker.Mock()
    (repo / "notes" / "about.md").write_text("# About these notes\n\nsite body\n")
    rendered = "---\ntitle: About these notes\nbrain-id: id-x\n---\n\nbrain body\n"
    plan = SyncPlan(adoptions=("about.md",))
    merge = MergePlan(writes={"about.md": rendered}, adopted={"about.md": "id-x"})

    execute_plan(plan, merge, repo / "notes", client, {"id-x": "About these notes"}, {"about": "id-x"})

    client.update_note_markdown.assert_called_once_with("id-x", "# About these notes\n\nsite body\n"), (
        "adoption should push the garden body, not the brain render"
    )
    stamped = (repo / "notes" / "about.md").read_text()
    assert "brain-id: id-x" in stamped, "adoption should stamp the file with its thought id"
    assert stamped.endswith("site body\n"), "adoption must never overwrite the garden body"


def test_report_plan_lists_adoptions(capsys):
    from brainsync.sync import report_plan

    report_plan(SyncPlan(adoptions=("about.md",)))

    assert "adopt about.md" in capsys.readouterr().out, (
        "planned adoptions should be visible before anything is touched"
    )


def test_sync_main_adopt_all_flag_reaches_plan_merge(repo, monkeypatch, synced_world):
    from brainsync.sync import main

    _, plan_merge, _ = synced_world
    monkeypatch.setattr("sys.argv", sync_argv(repo, "--adopt-all", "--dry-run"))

    main()

    assert plan_merge.call_args.kwargs.get("adopt") is True, (
        "--adopt-all should turn on adoption in the merge plan"
    )


def test_sync_main_reports_remaining_skips(repo, monkeypatch, synced_world, mocker, capsys):
    from brainsync.sync import main
    from brainsync.brain2notes import MergePlan

    _, plan_merge, _ = synced_world
    plan_merge.return_value = MergePlan(skipped=("My Org Note",))
    monkeypatch.setattr("sys.argv", sync_argv(repo, "--adopt-all", "--dry-run"))

    main()

    assert "My Org Note" in capsys.readouterr().err, (
        "titles that stay skipped under --adopt-all should be reported, not silent"
    )
