import pytest
import requests

from brainsync.brainread import KIND_NORMAL, KIND_TAG
from brainsync.sources import ApiSource, BrainSource


def thought_record(thought_id, name, kind=KIND_NORMAL, created=""):
    record = {"id": thought_id, "name": name, "kind": kind, "typeId": None}
    return {**record, "creationDateTime": created} if created else record


def link_record(id_a, id_b, meaning=1, relation=1):
    return {"thoughtIdA": id_a, "thoughtIdB": id_b, "relation": relation, "meaning": meaning}


def graph(active, neighbors=(), tags=(), links=()):
    return {
        "activeThought": active,
        "children": list(neighbors),
        "tags": list(tags),
        "links": list(links),
    }


HOME = thought_record("th-home", "Home")
ALPHA = thought_record("th-a", "Alpha", created="2026-08-05T09:30:00")
BETA = thought_record("th-b", "Beta")
TAG = thought_record("tag-1", "#public", kind=KIND_TAG)


@pytest.fixture
def client(mocker):
    client = mocker.Mock()
    client.brain.return_value = {"id": "brain-1", "homeThoughtId": "th-home"}
    client.thought_graph.side_effect = lambda thought_id: {
        "th-home": graph(HOME, neighbors=[ALPHA], links=[link_record("th-home", "th-a")]),
        "th-a": graph(ALPHA, neighbors=[BETA, HOME], tags=[TAG], links=[link_record("th-a", "th-b"), link_record("th-home", "th-a")]),
        "th-b": graph(BETA, neighbors=[ALPHA], tags=[TAG]),
        "tag-1": graph(TAG, neighbors=[ALPHA, BETA]),
    }[thought_id]
    client.note_markdown.side_effect = lambda thought_id: {
        "th-home": "Home body",
        "th-a": "Alpha body",
        "th-b": "Beta body",
    }[thought_id]
    client.tags.return_value = [TAG]
    client.types.return_value = []
    client.modifications.return_value = []
    return client


def test_api_source_is_a_brain_source(client):
    assert isinstance(ApiSource(client), BrainSource), (
        "ApiSource should conform to the BrainSource protocol"
    )


def test_snapshot_reaches_the_whole_brain_from_home(client):
    snapshot = ApiSource(client).snapshot(tag=None)

    assert set(snapshot.thoughts) == {"th-home", "th-a", "th-b", "tag-1"}, (
        "BFS from the home thought should reach every connected thought, tags included"
    )


def test_snapshot_survives_cycles(client):
    ApiSource(client).snapshot(tag=None)

    fetched = [call.args[0] for call in client.thought_graph.call_args_list]
    assert sorted(fetched) == sorted(set(fetched)), (
        "each thought's graph should be fetched exactly once despite cycles"
    )


def test_snapshot_tag_membership_from_graph_tags(client):
    snapshot = ApiSource(client).snapshot(tag=None)

    assert "tag-1" in snapshot.tag_ids_of(snapshot.thoughts["th-a"]), (
        "members should carry tag ids from their graph's tags list"
    )
    assert snapshot.tag_names_of(snapshot.thoughts["th-b"]) == ("#public",), (
        "tag names should resolve through the traversed tag thought"
    )


def test_snapshot_deduplicates_links(client):
    snapshot = ApiSource(client).snapshot(tag=None)

    pairs = [(link.id_a, link.id_b) for link in snapshot.links]
    assert len(pairs) == len(set(pairs)), (
        "a link appearing in both endpoints' graphs should be recorded once"
    )
    assert snapshot.related_ids_of(snapshot.thoughts["th-a"]) == ("th-b",), (
        "normal links should surface as related ids"
    )


def test_snapshot_fetches_notes_only_for_normal_thoughts(client):
    snapshot = ApiSource(client).snapshot(tag=None)

    assert snapshot.notes["th-a"].text == "Alpha body", (
        "notes should carry the raw markdown from the API"
    )
    assert snapshot.notes["th-a"].format == "md", (
        "api notes should be marked md so note_body never re-renders them"
    )
    fetched = {call.args[0] for call in client.note_markdown.call_args_list}
    assert fetched == {"th-home", "th-a", "th-b"}, (
        "notes should be fetched for normal thoughts only, never for tags"
    )


def test_snapshot_skips_missing_notes(client, mocker):
    response = mocker.Mock(status_code=404)
    client.note_markdown.side_effect = requests.HTTPError(response=response)

    snapshot = ApiSource(client).snapshot(tag=None)

    assert snapshot.notes == {}, "thoughts without a note should simply have no note entry"


def test_snapshot_propagates_other_http_errors(client, mocker):
    response = mocker.Mock(status_code=500)
    client.note_markdown.side_effect = requests.HTTPError(response=response)

    with pytest.raises(requests.HTTPError):
        ApiSource(client).snapshot(tag=None)


def test_snapshot_tolerates_sparse_graph_responses(client):
    client.thought_graph.side_effect = lambda thought_id: {
        "th-home": {"activeThought": HOME, "jumps": [ALPHA]},
        "th-a": {"activeThought": ALPHA, "tags": [TAG]},
        "tag-1": {"activeThought": TAG},
    }[thought_id]
    client.note_markdown.side_effect = lambda thought_id: "body"

    snapshot = ApiSource(client).snapshot(tag=None)

    assert "tag-1" in snapshot.tag_ids_of(snapshot.thoughts["th-a"]), (
        "snapshot should survive graph responses that omit any adjacency keys"
    )


def test_snapshot_ignores_the_tag_argument(client):
    whole = ApiSource(client).snapshot(tag=None)
    tagged = ApiSource(client).snapshot(tag="public")

    assert set(whole.thoughts) == set(tagged.thoughts), (
        "the snapshot is always whole-brain; tag filtering belongs to the merge, as with brz"
    )


def modification(source_id, created, source_type=2):
    return {"sourceId": source_id, "sourceType": source_type, "creationDateTime": created}


def test_log_thought_ids_pages_backwards(mocker):
    from brainsync.sources import log_thought_ids

    client = mocker.Mock()
    client.modifications.side_effect = [
        [modification("th-new", "2026-08-02T10:00:00"), modification("link-1", "2026-08-02T09:00:00", source_type=3)],
        [modification("th-old", "2026-08-01T10:00:00")],
        [],
    ]

    assert log_thought_ids(client) == {"th-new", "th-old"}, (
        "log_thought_ids should collect thought sourceIds only, across pages"
    )
    assert client.modifications.call_args_list[1].kwargs["end_time"] == "2026-08-02T09:00:00", (
        "each page should continue backwards from the oldest entry seen"
    )


def test_log_thought_ids_stops_when_time_stalls(mocker):
    from brainsync.sources import log_thought_ids

    stuck = [modification("th-x", "2026-08-01T00:00:00")]
    client = mocker.Mock()
    client.modifications.side_effect = [stuck, stuck, stuck]

    assert log_thought_ids(client) == {"th-x"}, (
        "paging should terminate when the oldest timestamp stops decreasing"
    )


def test_snapshot_reaches_disconnected_islands(client, mocker):
    island = thought_record("th-island", "Island")
    graphs = {
        "th-home": graph(HOME),
        "th-island": graph(island),
        "tag-1": graph(TAG),
    }
    client.thought_graph.side_effect = lambda thought_id: graphs[thought_id]
    client.tags.return_value = [TAG]
    client.types.return_value = []
    client.modifications.side_effect = [[modification("th-island", "2026-08-01T00:00:00")], []]
    client.note_markdown.side_effect = lambda thought_id: "body"

    snapshot = ApiSource(client).snapshot(tag=None)

    assert "th-island" in snapshot.thoughts, (
        "a subgraph without a path from home should still be found via the modification log"
    )


@pytest.mark.parametrize("status", [404, 403])
def test_snapshot_skips_unreachable_thoughts(client, mocker, capsys, status):
    graphs = {"th-home": graph(HOME), "tag-1": graph(TAG)}
    response = mocker.Mock(status_code=status)

    def fetch_graph(thought_id):
        if thought_id == "th-ghost":
            raise requests.HTTPError(response=response)
        return graphs[thought_id]

    client.thought_graph.side_effect = fetch_graph
    client.tags.return_value = [TAG]
    client.types.return_value = []
    client.modifications.side_effect = [[modification("th-ghost", "2026-08-01T00:00:00")], []]
    client.note_markdown.side_effect = lambda thought_id: "body"

    snapshot = ApiSource(client).snapshot(tag=None)

    assert "th-ghost" not in snapshot.thoughts, (
        "deleted or restricted thoughts should be skipped, not crash the pull"
    )
    assert "skipped 1" in capsys.readouterr().err, (
        "skipped thoughts should be reported so silent gaps can't hide"
    )


IMG_URL = "http://127.0.0.1:8001/notes-images/brain-1/eyJ.token.sig/86e0d31e.png"


def test_snapshot_downloads_images_via_the_attachment_route(client):
    client.note_markdown.side_effect = lambda thought_id: (
        f"body with ![](" + IMG_URL + ")" if thought_id == "th-a" else "plain"
    )
    client.attachment_content.return_value = b"png-bytes"

    snapshot = ApiSource(client).snapshot(tag=None)

    images = snapshot.images["th-a"]
    assert [(image.name, image.data) for image in images] == [("86e0d31e.png", b"png-bytes")], (
        "notes-images references should be fetched as attachments, keyed by basename"
    )
    client.attachment_content.assert_called_once_with("86e0d31e"), (
        "the basename stem is the attachment id"
    )
    client.fetch_bytes.assert_not_called(), (
        "the tokenized url should not be needed when the attachment route works"
    )


def test_snapshot_falls_back_to_the_tokenized_url(client, mocker):
    client.note_markdown.side_effect = lambda thought_id: (
        f"![]({IMG_URL})" if thought_id == "th-a" else "plain"
    )
    client.attachment_content.side_effect = requests.HTTPError(
        "404", response=mocker.Mock(status_code=404)
    )
    client.fetch_bytes.return_value = b"png-bytes"

    snapshot = ApiSource(client).snapshot(tag=None)

    assert snapshot.images["th-a"][0].data == b"png-bytes", (
        "when the attachment route misses, the tokenized url is the fallback"
    )
    client.fetch_bytes.assert_called_once_with(IMG_URL)


def test_snapshot_skips_unfetchable_images(client, mocker, capsys):
    client.note_markdown.side_effect = lambda thought_id: (
        f"![]({IMG_URL})" if thought_id == "th-a" else "plain"
    )
    client.attachment_content.side_effect = requests.HTTPError(
        "403", response=mocker.Mock(status_code=403)
    )
    client.fetch_bytes.side_effect = requests.HTTPError(
        "404 Client Error — body: 'gone'", response=mocker.Mock(status_code=404)
    )

    snapshot = ApiSource(client).snapshot(tag=None)

    assert "th-a" not in snapshot.images, (
        "an image that fails on both routes should be skipped, not crash the pull"
    )
    assert "404" in capsys.readouterr().err, (
        "skipped images should be reported with the underlying error"
    )


def test_image_regex_does_not_bridge_adjacent_links(client):
    wiki = "https://upload.wikimedia.org/File:Zone.svg"
    client.note_markdown.side_effect = lambda thought_id: (
        f"![]({IMG_URL})![]({wiki})" if thought_id == "th-a" else "plain"
    )
    client.attachment_content.return_value = b"png-bytes"

    snapshot = ApiSource(client).snapshot(tag=None)

    assert [image.name for image in snapshot.images["th-a"]] == ["86e0d31e.png"], (
        "adjacent image links must not be captured as one notes-images reference"
    )
    client.attachment_content.assert_called_once_with("86e0d31e")


def test_snapshot_deduplicates_repeated_images(client):
    client.note_markdown.side_effect = lambda thought_id: (
        f"![]({IMG_URL}) and again ![]({IMG_URL})" if thought_id == "th-a" else "plain"
    )
    client.attachment_content.return_value = b"png-bytes"

    snapshot = ApiSource(client).snapshot(tag=None)

    assert len(snapshot.images["th-a"]) == 1, (
        "the same image referenced twice should be downloaded and stored once"
    )


def test_snapshot_carries_thought_creation_dates(client):
    snapshot = ApiSource(client).snapshot(tag=None)
    assert snapshot.thoughts["th-a"].created == "2026-08-05T09:30:00", (
        "snapshot thoughts should carry creationDateTime so pulled notes get dates"
    )
    assert snapshot.thoughts["th-b"].created == "", (
        "records without creationDateTime should degrade to an empty created"
    )
