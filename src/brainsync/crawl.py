import time
import uuid
from collections import deque
from pathlib import Path
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://notes.andymatuschak.org"


def site_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
USER_AGENT = "brainsync/0.1 (personal archive tool)"

Fetcher = Callable[[str], str]
BytesFetcher = Callable[[str], bytes]


@dataclass(frozen=True)
class Note:
    url: str
    title: str
    body_html: str
    outgoing: tuple[str, ...]


def normalize_url(href: str, base: str = BASE_URL) -> str:
    absolute = urljoin(base + "/", href)
    parts = urlsplit(absolute)
    return f"{parts.scheme}://{parts.netloc}{parts.path}".rstrip("/")


def is_internal(href: str, base: str = BASE_URL) -> bool:
    absolute = urljoin(base + "/", href)
    return urlsplit(absolute).netloc == urlsplit(base).netloc


def _clean_heading(text: str) -> str:
    return text.replace("\u200b", "").strip()


def _without_site_suffix(title: str) -> str:
    return title.split(" | ")[0].strip()


def extract_title(soup: BeautifulSoup) -> str:
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content", "").strip():
        return og_title["content"].strip()
    container = soup.find("main") or soup.find("article")
    heading = container.find("h1") if container else None
    if heading and _clean_heading(heading.get_text()):
        return _clean_heading(heading.get_text())
    if soup.title and soup.title.get_text(strip=True):
        return _without_site_suffix(soup.title.get_text(strip=True))
    heading = soup.find("h1")
    if heading and _clean_heading(heading.get_text()):
        return _clean_heading(heading.get_text())
    return "Untitled"


def extract_body(soup: BeautifulSoup, title: str):
    for heading in soup.find_all(["h1", "h2"]):
        if _clean_heading(heading.get_text()) == title and heading.parent and heading.parent.name != "body":
            return heading.parent
    return soup.find("main") or soup.find("article") or soup.body or soup


def extract_internal_links(scope, page_url: str, base: str = BASE_URL) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for anchor in scope.find_all("a", href=True):
        href = anchor["href"]
        if href.startswith(("mailto:", "#")) or not is_internal(href, base):
            continue
        target = normalize_url(href, base)
        if target != normalize_url(page_url, base) and target != base:
            seen[target] = None
    return tuple(seen)


def parse_note(url: str, html: str, base: str = BASE_URL) -> Note:
    soup = BeautifulSoup(html, "html.parser")
    title = extract_title(soup)
    body = extract_body(soup, title)
    return Note(
        url=normalize_url(url, base),
        title=title,
        body_html=str(body),
        outgoing=extract_internal_links(body, url, base),
    )


def throttled(fetch: Fetcher, delay_seconds: float, sleep: Callable[[float], None] = time.sleep) -> Fetcher:
    fetched_before = False

    def fetch_with_delay(url: str) -> str:
        nonlocal fetched_before
        if fetched_before:
            sleep(delay_seconds)
        fetched_before = True
        return fetch(url)

    return fetch_with_delay


def cached(fetch: Fetcher, cache_dir: Path) -> Fetcher:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_with_cache(url: str) -> str:
        page = cache_dir / f"{uuid.uuid5(uuid.NAMESPACE_URL, normalize_url(url))}.html"
        if page.exists():
            return page.read_text()
        html = fetch(url)
        page.write_text(html)
        return html

    return fetch_with_cache


def cached_bytes(fetch: BytesFetcher, cache_dir: Path) -> BytesFetcher:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_with_cache(url: str) -> bytes:
        item = cache_dir / f"{uuid.uuid5(uuid.NAMESPACE_URL, url)}.bin"
        if item.exists():
            return item.read_bytes()
        data = fetch(url)
        item.write_bytes(data)
        return data

    return fetch_with_cache


def http_fetch(url: str, session: requests.Session | None = None) -> str:
    return http_fetch_bytes(url, session).decode("utf-8", errors="replace")


def http_fetch_bytes(url: str, session: requests.Session | None = None) -> bytes:
    client = session or requests.Session()
    response = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    return response.content


def crawl(start_url: str, fetch: Fetcher, limit: int | None = None, base: str = BASE_URL) -> dict[str, Note]:
    queue = deque([normalize_url(start_url, base)])
    notes: dict[str, Note] = {}
    while queue and (limit is None or len(notes) < limit):
        url = queue.popleft()
        if url in notes:
            continue
        try:
            note = parse_note(url, fetch(url), base)
        except requests.RequestException:
            continue
        notes[url] = note
        queue.extend(target for target in note.outgoing if target not in notes)
    return notes
