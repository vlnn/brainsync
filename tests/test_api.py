import json

import pytest
import requests

from brainsync.api import ApiClient, ApiConfig


def make_client(mocker):
    session = mocker.Mock()
    config = ApiConfig(base_url="http://127.0.0.1:4567", token="t0k3n", brain_id="brain-1")
    return ApiClient(config=config, session=session), session


def test_config_from_file(tmp_path):
    path = tmp_path / ".brainsync.json"
    path.write_text(json.dumps({"base_url": "http://127.0.0.1:9999", "token": "abc", "brain_id": "b"}))

    config = ApiConfig.from_file(path)

    assert config == ApiConfig(base_url="http://127.0.0.1:9999", token="abc", brain_id="b"), (
        "from_file should read all three fields from the json config"
    )


def test_config_from_env():
    env = {"BRAIN_API_URL": "http://127.0.0.1:4567", "BRAIN_API_TOKEN": "abc", "BRAIN_ID": "b"}

    assert ApiConfig.from_env(env) == ApiConfig(base_url="http://127.0.0.1:4567", token="abc", brain_id="b"), (
        "from_env should read all three fields from environment variables"
    )


@pytest.mark.parametrize(
    "missing", ["BRAIN_API_URL", "BRAIN_API_TOKEN", "BRAIN_ID"]
)
def test_config_from_env_incomplete(missing):
    env = {"BRAIN_API_URL": "u", "BRAIN_API_TOKEN": "t", "BRAIN_ID": "b"}
    del env[missing]

    assert ApiConfig.from_env(env) is None, (
        "from_env should return None when any variable is missing"
    )


@pytest.mark.parametrize(
    "call, expected_path",
    [
        (lambda c: c.tags(), "/thoughts/brain-1/tags"),
        (lambda c: c.thought("th-9"), "/thoughts/brain-1/th-9"),
        (lambda c: c.thought_graph("th-9"), "/thoughts/brain-1/th-9/graph"),
        (lambda c: c.types(), "/thoughts/brain-1/types"),
    ],
)
def test_get_endpoints(mocker, call, expected_path):
    client, session = make_client(mocker)
    session.get.return_value.json.return_value = {"ok": True}

    assert call(client) == {"ok": True}, "GET endpoints should return the parsed json body"

    args = session.get.call_args
    assert args.args[0] == f"http://127.0.0.1:4567/api{expected_path}", (
        "GET endpoints should hit their 1:1 path under the configured base url"
    )
    assert args.kwargs["headers"] == {"Authorization": "Bearer t0k3n"}, (
        "every request should carry the bearer token"
    )
    session.get.return_value.raise_for_status.assert_called_once()


def test_note_markdown(mocker):
    client, session = make_client(mocker)
    session.get.return_value.json.return_value = {"markdown": "# Note\n\nbody", "sourceId": "th-9"}

    assert client.note_markdown("th-9") == "# Note\n\nbody", (
        "note_markdown should return the raw markdown string from the note payload"
    )
    assert session.get.call_args.args[0] == "http://127.0.0.1:4567/api/notes/brain-1/th-9", (
        "note_markdown should hit the notes endpoint"
    )


def test_update_note_markdown(mocker):
    client, session = make_client(mocker)

    client.update_note_markdown("th-9", "new body")

    args = session.post.call_args
    assert args.args[0] == "http://127.0.0.1:4567/api/notes/brain-1/th-9/update", (
        "update_note_markdown should post to the note update endpoint"
    )
    assert args.kwargs["json"] == {"markdown": "new body"}, (
        "update_note_markdown should send the markdown in the json body"
    )
    session.post.return_value.raise_for_status.assert_called_once()


def test_load_config_prefers_env(tmp_path, mocker):
    from brainsync.api import load_config

    (tmp_path / ".brainsync.json").write_text(
        json.dumps({"base_url": "http://file", "token": "f", "brain_id": "f"})
    )
    env = {"BRAIN_API_URL": "http://env", "BRAIN_API_TOKEN": "e", "BRAIN_ID": "e"}

    assert load_config(tmp_path, env).base_url == "http://env", (
        "load_config should prefer complete env config over the file"
    )


def test_load_config_falls_back_to_file(tmp_path):
    from brainsync.api import load_config

    (tmp_path / ".brainsync.json").write_text(
        json.dumps({"base_url": "http://file", "token": "f", "brain_id": "f"})
    )

    assert load_config(tmp_path, {}).base_url == "http://file", (
        "load_config should read .brainsync.json when env is incomplete"
    )


def test_load_config_without_any_source(tmp_path):
    from brainsync.api import load_config

    with pytest.raises(SystemExit, match="brainsync.json"):
        load_config(tmp_path, {})


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("http://localhost:52341", "http://localhost:52341/api"),
        ("http://localhost:52341/", "http://localhost:52341/api"),
        ("http://localhost:52341/api", "http://localhost:52341/api"),
        ("http://localhost:52341/api/", "http://localhost:52341/api"),
    ],
)
def test_normalize_base_url(raw, expected):
    from brainsync.api import normalize_base_url

    assert normalize_base_url(raw) == expected, (
        "normalize_base_url should accept the bare origin or the /api/ url from the Local API widget"
    )


def test_requests_hit_the_api_prefix(mocker):
    client, session = make_client(mocker)
    session.get.return_value.json.return_value = []
    session.get.return_value.status_code = 200

    client.tags()

    assert session.get.call_args.args[0] == "http://127.0.0.1:4567/api/thoughts/brain-1/tags", (
        "every request should go under the /api prefix the local server routes"
    )


def test_401_is_fatal_config_error(mocker):
    client, session = make_client(mocker)
    session.get.return_value.status_code = 401

    with pytest.raises(SystemExit, match="API key.*thoughts/brain-1/tags"):
        client.tags()


def test_403_is_a_catchable_http_error(mocker):
    client, session = make_client(mocker)
    session.get.return_value.status_code = 403

    with pytest.raises(requests.HTTPError, match="not permitted.*thoughts/brain-1/tags") as excinfo:
        client.tags()

    assert excinfo.value.response.status_code == 403, (
        "403 should stay an HTTPError with the response attached so callers can skip restricted items"
    )


def test_non_json_body_dies_readably(mocker):
    client, session = make_client(mocker)
    session.get.return_value.status_code = 200
    session.get.return_value.json.side_effect = ValueError("Expecting value")
    session.get.return_value.text = "<html>Not Found</html>"

    with pytest.raises(SystemExit, match="non-JSON"):
        client.tags()


def test_brain_endpoint(mocker):
    client, session = make_client(mocker)
    session.get.return_value.status_code = 200
    session.get.return_value.json.return_value = {"id": "brain-1", "homeThoughtId": "th-home"}

    assert client.brain()["homeThoughtId"] == "th-home", (
        "brain should return the brain record with its home thought id"
    )
    assert session.get.call_args.args[0] == "http://127.0.0.1:4567/api/brains/brain-1", (
        "brain should hit the brain metadata endpoint"
    )


def test_http_errors_carry_the_response_body(mocker):
    client, session = make_client(mocker)
    session.get.return_value.status_code = 400
    session.get.return_value.text = '{"error": "maxThoughts is required"}'
    session.get.return_value.raise_for_status.side_effect = requests.HTTPError(
        "400 Client Error", response=session.get.return_value
    )

    with pytest.raises(requests.HTTPError, match="maxThoughts") as excinfo:
        client.tags()

    assert excinfo.value.response is session.get.return_value, (
        "the re-raised error should keep the response so 404 note handling still works"
    )


@pytest.mark.parametrize(
    "kwargs, expected_query",
    [
        ({"max_logs": 500}, "maxLogs=500"),
        ({"max_logs": 500, "end_time": "2026-08-01T00:00:00"}, "maxLogs=500&endTime=2026-08-01T00%3A00%3A00"),
        ({}, ""),
    ],
)
def test_modifications_query(mocker, kwargs, expected_query):
    client, session = make_client(mocker)
    session.get.return_value.status_code = 200
    session.get.return_value.json.return_value = []

    client.modifications(**kwargs)

    url = session.get.call_args.args[0]
    assert url == f"http://127.0.0.1:4567/api/brains/brain-1/modifications{'?' if expected_query else ''}{expected_query}", (
        "modifications should page with maxLogs and endTime query params"
    )


def test_fetch_bytes_url_token_is_the_auth(mocker):
    client, session = make_client(mocker)
    session.get.return_value.ok = True
    session.get.return_value.content = b"png-bytes"

    assert client.fetch_bytes("http://127.0.0.1:4567/notes-images/x/y/z.png") == b"png-bytes", (
        "fetch_bytes should return the raw body for tokenized image urls"
    )
    assert "headers" not in session.get.call_args.kwargs, (
        "the jwt in the url is the auth — no bearer header on the first attempt"
    )


def test_fetch_bytes_falls_back_to_bearer(mocker):
    client, session = make_client(mocker)
    denied = mocker.Mock(ok=False, status_code=403)
    granted = mocker.Mock(ok=True, content=b"png-bytes")
    session.get.side_effect = [denied, granted]

    assert client.fetch_bytes("http://u") == b"png-bytes", (
        "a rejected bare fetch should be retried with the bearer token"
    )
    assert session.get.call_args.kwargs["headers"] == {"Authorization": "Bearer t0k3n"}, (
        "the fallback attempt should carry the bearer token"
    )


def test_fetch_bytes_raises_with_body_when_both_fail(mocker):
    client, session = make_client(mocker)
    denied = mocker.Mock(ok=False, status_code=403, text="expired token")
    denied.raise_for_status.side_effect = requests.HTTPError("403", response=denied)
    session.get.side_effect = [denied, denied]

    with pytest.raises(requests.HTTPError):
        client.fetch_bytes("http://u")


def test_attachment_content(mocker):
    client, session = make_client(mocker)
    session.get.return_value.status_code = 200
    session.get.return_value.content = b"png-bytes"

    assert client.attachment_content("att-1") == b"png-bytes", (
        "attachment_content should return the raw file body"
    )
    args = session.get.call_args
    assert args.args[0] == "http://127.0.0.1:4567/api/attachments/brain-1/att-1/file-content", (
        "attachment_content should hit the documented file-content route"
    )
    assert args.kwargs["headers"] == {"Authorization": "Bearer t0k3n"}, (
        "attachment fetches are normal api requests and carry the bearer token"
    )
