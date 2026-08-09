import pytest

from brainsync.cli import COMMANDS, main


@pytest.mark.parametrize("command", ["sync", "pull", "crawl", "check"])
def test_dispatch_calls_the_command_with_trimmed_argv(command, monkeypatch, mocker):
    target = mocker.Mock()
    monkeypatch.setitem(COMMANDS, command, target)
    monkeypatch.setattr("sys.argv", ["brainsync", command, "--some-flag", "value"])

    main()

    target.assert_called_once(), f"'brainsync {command}' should dispatch to its command"
    import sys

    assert sys.argv == [f"brainsync {command}", "--some-flag", "value"], (
        "the subcommand name should be consumed before the command parses its own flags"
    )


def test_unknown_command_exits_nonzero_and_lists_commands(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["brainsync", "shovel"])

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code != 0, "an unknown command should exit nonzero"
    err = capsys.readouterr().err
    for command in COMMANDS:
        assert command in err, "the error should list the available commands"


def test_no_arguments_prints_usage_and_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["brainsync"])

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code != 0, "bare 'brainsync' should exit nonzero"
    assert "sync" in capsys.readouterr().err, "bare 'brainsync' should print usage"


def test_help_lists_all_commands(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["brainsync", "--help"])

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0, "--help should exit zero"
    out = capsys.readouterr().out
    for command in ("sync", "pull", "crawl", "check"):
        assert command in out, "--help should list every command"


def test_sync_main_no_longer_wants_an_inner_subcommand(repo_argv_smoke=None):
    import brainsync.sync as sync
    import inspect

    source = inspect.getsource(sync.main)
    assert 'choices=["sync"]' not in source, (
        "the inner 'sync' positional should be gone — 'brainsync sync' supplies the verb"
    )


def test_check_without_a_path_fails_loudly(monkeypatch):
    from brainsync.check import main as check_main

    monkeypatch.setattr("sys.argv", ["brainsync check"])

    with pytest.raises(SystemExit, match="brz"):
        check_main()
