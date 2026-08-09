import json
import sys
import zipfile
from collections import Counter
from pathlib import Path

ZERO_GUID = "00000000-0000-0000-0000-000000000000"


def read_json_lines(archive: zipfile.ZipFile, name: str) -> list[dict]:
    try:
        raw = archive.read(name).decode("utf-8-sig")
    except KeyError:
        return []
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def check_duplicate_ids(records: list[dict], source: str) -> list[str]:
    counts = Counter(record["Id"] for record in records)
    return [f"{source}: duplicate Id {record_id} ({n} times)" for record_id, n in counts.items() if n > 1]


def check_zero_guids(records: list[dict], source: str) -> list[str]:
    return [
        f"{source}: record {record.get('Id')} has zero guid in {field}"
        for record in records
        for field, value in record.items()
        if value == ZERO_GUID
    ]


def check_link_references(links: list[dict], thought_ids: set[str]) -> list[str]:
    return [
        f"links: link {link['Id']} references missing thought in {field}"
        for link in links
        for field in ("ThoughtIdA", "ThoughtIdB")
        if link.get(field) not in thought_ids
    ]


def check_attachment_sources(attachments: list[dict], thought_ids: set[str]) -> list[str]:
    return [
        f"attachments: attachment {a['Id']} references missing thought {a.get('SourceId')}"
        for a in attachments
        if a.get("SourceId") not in thought_ids
    ]


def check_attachment_files(archive: zipfile.ZipFile, attachments: list[dict]) -> list[str]:
    names = set(archive.namelist())
    problems = []
    for a in attachments:
        expected = f"{a.get('SourceId')}/Notes/{a.get('Location')}"
        if expected not in names:
            problems.append(f"attachments: attachment {a['Id']} file missing at {expected}")
    return problems


def check_attachment_states(meta: dict, attachments: list[dict]) -> list[str]:
    states = set(meta.get("AttachmentFileStates", {}))
    ids = {a["Id"] for a in attachments}
    problems = [f"meta: AttachmentFileStates lists unknown attachment {i}" for i in sorted(states - ids)]
    problems += [f"meta: attachment {i} missing from AttachmentFileStates" for i in sorted(ids - states)]
    return problems


def check_settings_references(settings: list[dict], thought_ids: set[str]) -> list[str]:
    home_setting_id = "0dc10ee1-5a89-548b-b6d2-b5eec064ca4a"
    return [
        f"settings: home thought {record.get('Value')} does not exist"
        for record in settings
        if record.get("Id") == home_setting_id and record.get("Value") not in thought_ids
    ]


def check_duplicate_files(archive: zipfile.ZipFile) -> list[str]:
    counts = Counter(archive.namelist())
    return [f"zip: duplicate entry {name} ({n} times)" for name, n in counts.items() if n > 1]


def validate(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        thoughts = read_json_lines(archive, "thoughts.json")
        links = read_json_lines(archive, "links.json")
        attachments = read_json_lines(archive, "attachments.json")
        settings = read_json_lines(archive, "settings.json")
        meta = json.loads(archive.read("meta.json").decode("utf-8-sig"))
        thought_ids = {t["Id"] for t in thoughts}

        problems = []
        for records, source in ((thoughts, "thoughts"), (links, "links"), (attachments, "attachments")):
            problems += check_duplicate_ids(records, source)
            problems += check_zero_guids(records, source)
        problems += check_link_references(links, thought_ids)
        problems += check_attachment_sources(attachments, thought_ids)
        problems += check_attachment_files(archive, attachments)
        problems += check_attachment_states(meta, attachments)
        problems += check_settings_references(settings, thought_ids)
        problems += check_duplicate_files(archive)
        return problems


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: brainsync check <archive.brz>")
    path = Path(sys.argv[1])
    problems = validate(path)
    if problems:
        print(f"{len(problems)} problem(s) found in {path}:")
        for problem in problems:
            print(f"  {problem}")
        sys.exit(1)
    print(f"{path}: OK")


if __name__ == "__main__":
    main()
