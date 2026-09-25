import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from brainsync.crawl import Fetcher

NOTE_SUFFIXES = (".md", ".org")
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
ORG_KEYWORD = re.compile(r"^#\+(\w+):\s*(.*)$", re.MULTILINE)
MD_HEADING = re.compile(r"^#\s+(.*)$", re.MULTILINE)


@dataclass(frozen=True)
class GardenNote:
    slug: str
    format: str
    title: str
    brain_id: str = ""
    text: str = ""

    @property
    def filename(self) -> str:
        return f"{self.slug}.{self.format}"


UKRAINIAN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie",
    "ж": "zh", "з": "z", "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "",
    "ю": "iu", "я": "ia",
})
WORD_INITIAL = {"є": "ye", "ї": "yi", "й": "y", "ю": "yu", "я": "ya"}
WORD_INITIAL_LETTER = re.compile(r"\b[єїйюя]")
INNER_APOSTROPHE = re.compile(r"(?<=[а-яієїґ])['’ʼ](?=[а-яієїґ])")


def transliterate(text: str) -> str:
    text = unicodedata.normalize("NFC", text.lower())
    text = INNER_APOSTROPHE.sub("", text).replace("зг", "zgh")
    text = WORD_INITIAL_LETTER.sub(lambda match: WORD_INITIAL[match.group(0)], text)
    return text.translate(UKRAINIAN)


def slugify(name: str, limit: int = 60) -> str:
    ascii_name = unicodedata.normalize("NFKD", transliterate(name)).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "untitled"
    return slug if len(slug) <= limit else slug[:limit].rsplit("-", 1)[0]


def parse_frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER.match(text)
    if not match:
        return {}
    pairs = (line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    return {key.strip().lower(): value.strip().strip("\"'") for key, value in pairs}


def _md_title(text: str) -> str:
    heading = MD_HEADING.search(text)
    return heading.group(1).strip() if heading else ""


def _org_keywords(text: str) -> dict[str, str]:
    return {key.lower(): value.strip() for key, value in ORG_KEYWORD.findall(text)}


def parse_note(path: Path) -> GardenNote:
    text = path.read_text()
    meta = parse_frontmatter(text) if path.suffix == ".md" else _org_keywords(text)
    title = meta.get("title") or (_md_title(text) if path.suffix == ".md" else "") or path.stem
    return GardenNote(slug=path.stem, format=path.suffix.lstrip("."), title=title, brain_id=meta.get("brain-id", ""), text=text)


def load_garden(notes_dir: Path) -> dict[str, GardenNote]:
    paths = sorted(path for path in notes_dir.iterdir() if path.suffix in NOTE_SUFFIXES)
    return {path.stem: parse_note(path) for path in paths}


def _fetch_note(base_url: str, slug: str, fetch: Fetcher) -> tuple[str, str] | None:
    for suffix in NOTE_SUFFIXES:
        try:
            return suffix, fetch(f"{base_url}/notes/{slug}{suffix}")
        except Exception:
            continue
    return None


def pull_site(base_url: str, notes_dir: Path, fetch: Fetcher) -> list[str]:
    base_url = base_url.rstrip("/")
    index = json.loads(fetch(f"{base_url}/index.json"))
    existing = {path.stem for path in notes_dir.iterdir() if path.suffix in NOTE_SUFFIXES}
    pulled = []
    for slug in sorted(set(index) - existing):
        if found := _fetch_note(base_url, slug, fetch):
            suffix, text = found
            (notes_dir / f"{slug}{suffix}").write_text(text)
            pulled.append(slug)
    return pulled
