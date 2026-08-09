from pathlib import Path

from brainsync.brainread import BrainSnapshot
from brainsync.sources import BrainSource, BrzSource


def test_brz_source_is_a_brain_source():
    assert isinstance(BrzSource(Path("x.brz")), BrainSource), (
        "BrzSource should conform to the BrainSource protocol"
    )


def test_brz_source_snapshot_reads_its_archive(mocker):
    snapshot = BrainSnapshot(thoughts={})
    read = mocker.patch("brainsync.sources.read_brz", return_value=snapshot)

    result = BrzSource(Path("garden.brz")).snapshot(tag="published")

    assert result is snapshot, "snapshot should return exactly what read_brz produced"
    read.assert_called_once_with(Path("garden.brz")), (
        "snapshot should read the archive it was constructed with"
    )


def test_brz_source_ignores_the_tag(mocker):
    mocker.patch("brainsync.sources.read_brz", return_value=BrainSnapshot(thoughts={}))

    BrzSource(Path("garden.brz")).snapshot(tag=None)
    BrzSource(Path("garden.brz")).snapshot(tag="anything")
