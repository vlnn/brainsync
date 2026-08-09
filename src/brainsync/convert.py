from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from brainsync.crawl import BASE_URL, BytesFetcher, is_internal, normalize_url

NOTES_BASE_TOKEN = "BRAIN-NOTES-BASE-TOKEN"


def registrable_domain(host: str) -> str:
    return ".".join(host.split(".")[-2:])


def same_site_asset(src: str, base: str) -> bool:
    host = urlsplit(urljoin(base + "/", src)).netloc
    return registrable_domain(host) == registrable_domain(urlsplit(base).netloc)


def _image_extension(src: str) -> str:
    return PurePosixPath(urlsplit(src).path).suffix or ".png"


def embed_images(body_html: str, thought_key: str, fetch_bytes: BytesFetcher, base: str = BASE_URL):
    from brainsync.brainzip import NotesImage, stable_id

    soup = BeautifulSoup(body_html, "html.parser")
    images: dict[str, "NotesImage"] = {}
    for img in soup.find_all("img", src=True):
        absolute = urljoin(base + "/", img["src"])
        if not same_site_asset(img["src"], base):
            continue
        image_id = stable_id("image", thought_key, absolute)
        if image_id not in images:
            try:
                data = fetch_bytes(absolute)
            except requests.RequestException:
                img["src"] = absolute
                continue
            images[image_id] = NotesImage(id=image_id, extension=_image_extension(absolute), data=data)
        img["src"] = f"{NOTES_BASE_TOKEN}/{image_id}{images[image_id].extension}"
    return str(soup).replace(NOTES_BASE_TOKEN, "<!--BrainNotesBase-->"), tuple(images.values())


def rewrite_links(body_html: str, brain_urls: dict[str, str], base: str = BASE_URL) -> str:
    soup = BeautifulSoup(body_html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if href.startswith(("mailto:", "#")) or not is_internal(href, base):
            continue
        target = normalize_url(href, base)
        if target in brain_urls:
            anchor["href"] = brain_urls[target]
            anchor["class"] = "thought-link"
        else:
            anchor["href"] = target
    return str(soup)
