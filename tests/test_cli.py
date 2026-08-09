import pytest

from brainsync.brain2notes import main
from tests.test_brainread import ESSAY, thought_record, write_test_brz


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["missing.brz", "--notes-dir", "{tmp}"], "brz file not found"),
        (["{brz}", "--notes-dir", "nowhere"], "notes dir not found"),
    ],
)
def test_main_fails_readably_on_missing_paths(tmp_path, monkeypatch, argv, message):
    brz = tmp_path / "ok.brz"
    write_test_brz(brz, [thought_record(ESSAY, "Essay")])
    resolved = [arg.format(tmp=tmp_path, brz=brz) for arg in argv]
    monkeypatch.setattr("sys.argv", ["brain2notes", *resolved])
    with pytest.raises(SystemExit, match=message):
        main()


def test_main_without_brz_uses_the_api_source(tmp_path, monkeypatch, mocker):
    from brainsync.brainread import KIND_TAG, BrainSnapshot, BrainThought

    source = mocker.Mock()
    tag = BrainThought(id="tag-1", name="#published", kind=KIND_TAG)
    source.snapshot.return_value = BrainSnapshot(thoughts={"tag-1": tag})
    make_source = mocker.patch("brainsync.brain2notes.make_api_source", return_value=source)
    monkeypatch.setattr(
        "sys.argv", ["brain2notes", "--notes-dir", str(tmp_path), "--tag", "published", "--dry-run"]
    )

    main()

    make_source.assert_called_once(), "main should build the API source when no brz path is given"
    source.snapshot.assert_called_once_with("published"), (
        "main should pull the snapshot for the requested tag"
    )


def test_main_with_brz_still_reads_the_archive(tmp_path, monkeypatch, mocker):
    brz = tmp_path / "ok.brz"
    write_test_brz(brz, [thought_record(ESSAY, "Essay")])
    make_source = mocker.patch("brainsync.brain2notes.make_api_source")
    monkeypatch.setattr("sys.argv", ["brain2notes", str(brz), "--notes-dir", str(tmp_path), "--dry-run"])

    main()

    make_source.assert_not_called(), "a brz argument should keep the brz transport"


def test_main_accepts_an_explicit_api_flag(tmp_path, monkeypatch, mocker):
    from brainsync.brainread import KIND_TAG, BrainSnapshot, BrainThought

    source = mocker.Mock()
    tag = BrainThought(id="tag-1", name="#published", kind=KIND_TAG)
    source.snapshot.return_value = BrainSnapshot(thoughts={"tag-1": tag})
    mocker.patch("brainsync.brain2notes.make_api_source", return_value=source)
    monkeypatch.setattr(
        "sys.argv", ["brain2notes", "--api", "--notes-dir", str(tmp_path), "--tag", "published", "--dry-run"]
    )

    main()

    source.snapshot.assert_called_once_with("published"), (
        "--api should select the API source explicitly"
    )


def test_main_rejects_api_flag_with_a_brz_path(tmp_path, monkeypatch):
    brz = tmp_path / "ok.brz"
    write_test_brz(brz, [thought_record(ESSAY, "Essay")])
    monkeypatch.setattr(
        "sys.argv", ["brain2notes", "--api", str(brz), "--notes-dir", str(tmp_path), "--dry-run"]
    )

    with pytest.raises(SystemExit, match="either"):
        main()


def test_main_turns_http_errors_into_clean_exits(tmp_path, monkeypatch, mocker):
    import requests

    source = mocker.Mock()
    source.snapshot.side_effect = requests.HTTPError("500 Server Error — body: 'boom'")
    mocker.patch("brainsync.brain2notes.make_api_source", return_value=source)
    monkeypatch.setattr("sys.argv", ["brain2notes", "--api", "--notes-dir", str(tmp_path), "--dry-run"])

    with pytest.raises(SystemExit, match="boom"):
        main()
