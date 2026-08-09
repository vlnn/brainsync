import pytest

from brainsync.crawl import crawl, is_internal, normalize_url, parse_note

PAGE = """
<html><head><title>Evergreen notes</title>
<meta property="og:title" content="Evergreen notes"></head><body>
<header><h1>Andy\u02bcs working notes</h1>
<a href="/About_these_notes">About these notes</a></header>
<div class="note">
<h1>Evergreen notes</h1>
<p>See <a href="/zHTevHGZQPu8QHpRhUmtsuK">morning writing</a>
and <a href="https://notes.andymatuschak.org/z5E5QawiXCMbtNtupvxeoEX">evergreen</a>
and <a href="https://andymatuschak.org">homepage</a>
and <a href="mailto:andy@example.org">mail</a>
and <a href="#anchor">anchor</a>.</p>
</div></body></html>
"""


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("/zAbc", True),
        ("https://notes.andymatuschak.org/zAbc", True),
        ("https://andymatuschak.org/prompts", False),
        ("https://twitter.com/andy_matuschak", False),
    ],
)
def test_is_internal(href, expected):
    assert is_internal(href) is expected, f"is_internal({href!r}) should be {expected}"


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("/zAbc", "https://notes.andymatuschak.org/zAbc"),
        ("zAbc", "https://notes.andymatuschak.org/zAbc"),
        ("https://notes.andymatuschak.org/zAbc#frag", "https://notes.andymatuschak.org/zAbc"),
        ("https://notes.andymatuschak.org/zAbc/", "https://notes.andymatuschak.org/zAbc"),
    ],
)
def test_normalize_url(href, expected):
    assert normalize_url(href) == expected, f"normalize_url({href!r}) should strip fragments and trailing slashes"


def test_parse_note_takes_title_from_title_tag_not_site_banner():
    note = parse_note("https://notes.andymatuschak.org/z5E5Q", PAGE)
    assert note.title == "Evergreen notes", (
        "parse_note should take the title from <title>, not the site banner h1"
    )


def test_parse_note_scopes_body_to_note_container():
    note = parse_note("https://notes.andymatuschak.org/z5E5Q", PAGE)
    assert "morning writing" in note.body_html, "body should contain the note content"
    assert "working notes" not in note.body_html, "body should exclude the site header"


def test_parse_note_extracts_links_from_note_body_only():
    note = parse_note("https://notes.andymatuschak.org/z5E5Q", PAGE)
    assert note.outgoing == (
        "https://notes.andymatuschak.org/zHTevHGZQPu8QHpRhUmtsuK",
        "https://notes.andymatuschak.org/z5E5QawiXCMbtNtupvxeoEX",
    ), "outgoing should keep only internal links from the note body, excluding header nav, self, mailto and anchors"


def test_parse_note_falls_back_to_h1_then_untitled():
    html = "<html><body><h1>Only Heading</h1><p>hi</p></body></html>"
    note = parse_note("https://notes.andymatuschak.org/zX", html)
    assert note.title == "Only Heading", "parse_note should fall back to h1 when <title> is missing"


def _linked_page(title, hrefs):
    anchors = "".join(f'<a href="{href}">x</a>' for href in hrefs)
    return f"<html><body><main><h1>{title}</h1>{anchors}</main></body></html>"


def test_crawl_walks_graph_breadth_first(mocker):
    pages = {
        "https://notes.andymatuschak.org/a": _linked_page("A", ["/b", "/c"]),
        "https://notes.andymatuschak.org/b": _linked_page("B", ["/a"]),
        "https://notes.andymatuschak.org/c": _linked_page("C", []),
    }
    fetch = mocker.Mock(side_effect=pages.__getitem__)

    notes = crawl("https://notes.andymatuschak.org/a", fetch)

    assert set(notes) == set(pages), "crawl should visit every reachable note exactly once"
    assert fetch.call_count == 3, "crawl should fetch each unique url exactly once"


def test_crawl_respects_limit(mocker):
    pages = {
        "https://notes.andymatuschak.org/a": _linked_page("A", ["/b"]),
        "https://notes.andymatuschak.org/b": _linked_page("B", ["/c"]),
        "https://notes.andymatuschak.org/c": _linked_page("C", []),
    }
    fetch = mocker.Mock(side_effect=pages.__getitem__)

    notes = crawl("https://notes.andymatuschak.org/a", fetch, limit=2)

    assert len(notes) == 2, "crawl should stop once the note limit is reached"


def test_throttled_sleeps_between_calls_but_not_before_first(mocker):
    from brainsync.crawl import throttled

    sleep = mocker.Mock()
    fetch = throttled(mocker.Mock(return_value="page"), delay_seconds=0.5, sleep=sleep)

    fetch("https://notes.andymatuschak.org/a")
    sleep.assert_not_called(), "throttled should not sleep before the first request"
    fetch("https://notes.andymatuschak.org/b")
    sleep.assert_called_once_with(0.5), "throttled should sleep the configured delay between requests"


def test_cached_hits_skip_throttling(tmp_path, mocker):
    from brainsync.crawl import cached, throttled

    sleep = mocker.Mock()
    fetch = cached(throttled(mocker.Mock(return_value="page"), delay_seconds=0.5, sleep=sleep), tmp_path)

    for _ in range(3):
        fetch("https://notes.andymatuschak.org/a")

    sleep.assert_not_called(), "cache hits should never trigger the inter-request delay"


def test_cached_fetch_hits_network_once_per_url(tmp_path, mocker):
    from brainsync.crawl import cached

    fetch = mocker.Mock(return_value="<html>page</html>")
    cached_fetch = cached(fetch, tmp_path)

    first = cached_fetch("https://notes.andymatuschak.org/zAbc")
    second = cached_fetch("https://notes.andymatuschak.org/zAbc")

    assert first == second == "<html>page</html>", "cached fetch should return the same page content"
    assert fetch.call_count == 1, "cached fetch should hit the network only once per url"


def test_cached_fetch_survives_restarts(tmp_path, mocker):
    from brainsync.crawl import cached

    cached(mocker.Mock(return_value="cached page"), tmp_path)("https://notes.andymatuschak.org/zAbc")
    fresh_fetch = mocker.Mock(return_value="new page")

    result = cached(fresh_fetch, tmp_path)("https://notes.andymatuschak.org/zAbc")

    assert result == "cached page", "a new run should reuse pages cached by a previous run"
    fresh_fetch.assert_not_called(), "a new run should not re-download previously cached urls"


def test_rewrite_links_targets_brain_thoughts():
    from brainsync.convert import rewrite_links

    html = (
        '<p><a href="/zKnown">known</a>'
        ' <a href="/zUnknown">unknown</a>'
        ' <a href="https://example.org/x">external</a></p>'
    )
    brain_urls = {"https://notes.andymatuschak.org/zKnown": "brain://AAA/Known"}

    result = rewrite_links(html, brain_urls)

    assert 'href="brain://AAA/Known"' in result, "links to crawled notes should become brain:// thought links"
    assert 'class="thought-link"' in result, "brain links should carry the thought-link class like native notes"
    assert 'href="https://notes.andymatuschak.org/zUnknown"' in result, (
        "internal links outside the crawl should fall back to absolute web urls"
    )
    assert 'href="https://example.org/x"' in result, "external links should stay untouched"


def test_crawl_stays_on_the_start_sites_host(mocker):
    from brainsync.crawl import site_of

    pages = {
        "https://garden.example.com/a": _linked_page("A", ["/b", "https://notes.andymatuschak.org/z1"]),
        "https://garden.example.com/b": _linked_page("B", []),
    }
    fetch = mocker.Mock(side_effect=pages.__getitem__)

    notes = crawl("https://garden.example.com/a", fetch, base=site_of("https://garden.example.com/a"))

    assert set(notes) == set(pages), "crawl should follow only links on the start url's host"


def test_site_of_extracts_scheme_and_host():
    from brainsync.crawl import site_of

    assert site_of("https://garden.example.com/notes/a?x=1") == "https://garden.example.com", (
        "site_of should reduce any url to scheme and host"
    )


VITEPRESS_PAGE = """
<html><head><title>Habits | Everything I Know</title></head><body>
<aside><a href="/focusing">Focusing</a><a href="/art">Art</a></aside>
<main><h1>Habits \u200b</h1><p>Content with <a href="/focusing/rules">rules</a>.</p></main>
</body></html>
"""


def test_extract_title_prefers_main_h1_over_suffixed_title_tag():
    note = parse_note("https://wiki.example.com/focusing/habits", VITEPRESS_PAGE)
    assert note.title == "Habits", (
        "title should come from the h1 inside main, cleaned of anchors, not the suffixed title tag"
    )


def test_extract_title_strips_site_suffix_from_title_tag():
    html = "<html><head><title>Habits | Everything I Know</title></head><body><p>hi</p></body></html>"
    note = parse_note("https://wiki.example.com/x", html)
    assert note.title == "Habits", "title tag fallback should strip the ' | site name' suffix"


def test_sidebar_links_stay_out_of_note_body(mocker):
    base = "https://wiki.example.com"
    note = parse_note(f"{base}/focusing/habits", VITEPRESS_PAGE, base)
    assert note.outgoing == (f"{base}/focusing/rules",), (
        "outgoing links should come from the main content, not the sidebar"
    )


MKDOCS_PAGE = """
<html><head><title>Introduction - Things Learned</title></head><body>
<div class="md-sidebar"><nav class="md-nav">
<a href="Markdown%20Syntax/">Markdown Syntax</a>
<a href="Coda.md">Coda</a>
<a href="Aleph/">Aleph</a>
</nav></div>
<main><article class="md-content__inner"><h1>Things-Learned</h1>
<p>No links here.</p></article></main>
</body></html>
"""


def test_parse_note_falls_back_to_navigation_when_body_has_no_links():
    note = parse_note(
        "https://sunflowerno0b.github.io/ATW/", MKDOCS_PAGE, "https://sunflowerno0b.github.io"
    )
    assert note.outgoing == (
        "https://sunflowerno0b.github.io/ATW/Markdown%20Syntax",
        "https://sunflowerno0b.github.io/ATW/Aleph",
    ), "a body with no links should discover the sidebar navigation, skipping raw source files"


def test_parse_note_keeps_body_links_over_navigation_fallback(mocker):
    html = (
        "<html><body><main><h1>Page</h1>"
        '<p><a href="/note">note</a></p></main>'
        '<nav><a href="/other">other</a></nav></body></html>'
    )
    note = parse_note("https://garden.example.com/page", html, "https://garden.example.com")
    assert note.outgoing == ("https://garden.example.com/note",), (
        "a body with links should not fall back to whole-page navigation links"
    )


def test_same_site_asset_matches_registrable_domain():
    from brainsync.convert import same_site_asset

    base = "https://wiki-old.nikiv.dev"
    cases = [
        ("https://wiki-old.nikiv.dev/img/a.png", True),
        ("https://images.nikiv.dev/a.png", True),
        ("/relative/a.png", True),
        ("https://i.imgur.com/a.png", False),
    ]
    for src, expected in cases:
        assert same_site_asset(src, base) is expected, f"same_site_asset({src!r}) should be {expected}"


def test_embed_images_localizes_same_site_images(mocker):
    from brainsync.convert import embed_images

    fetch = mocker.Mock(return_value=b"png-bytes")
    html = '<p><img src="https://images.nikiv.dev/a.png"> <img src="https://i.imgur.com/b.png"></p>'

    result, images = embed_images(html, "Habits", fetch, "https://wiki-old.nikiv.dev")

    assert len(images) == 1, "only same-domain images should be embedded"
    assert images[0].data == b"png-bytes", "embedded image should carry the fetched bytes"
    assert f'src="<!--BrainNotesBase-->/{images[0].id}.png"' in result, (
        "embedded image src should use the literal BrainNotesBase token, unescaped"
    )
    assert 'src="https://i.imgur.com/b.png"' in result, "external images should stay remote"


def test_embed_images_keeps_remote_src_on_fetch_failure(mocker):
    import requests
    from brainsync.convert import embed_images

    fetch = mocker.Mock(side_effect=requests.RequestException)
    html = '<p><img src="/img/a.png"></p>'

    result, images = embed_images(html, "Habits", fetch, "https://wiki-old.nikiv.dev")

    assert images == (), "failed downloads should embed nothing"
    assert 'src="https://wiki-old.nikiv.dev/img/a.png"' in result, (
        "failed downloads should fall back to the absolute remote url"
    )


def test_embed_images_dedupes_repeated_images(mocker):
    from brainsync.convert import embed_images

    fetch = mocker.Mock(return_value=b"png-bytes")
    html = '<p><img src="/img/a.png"><img src="/img/a.png"></p>'

    result, images = embed_images(html, "Habits", fetch, "https://wiki.example.com")

    assert len(images) == 1, "the same image repeated in one note should produce a single attachment"
    assert fetch.call_count == 1, "the same image url should be fetched once"
    assert result.count(f"<!--BrainNotesBase-->/{images[0].id}.png") == 2, (
        "both img tags should reference the single embedded copy"
    )
