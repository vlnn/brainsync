# brainsync

Tools for moving notes between a server-rendered digital garden and TheBrain, in both
directions:

- `brainsync crawl` — crawls a garden site and exports a TheBrain BrainZip (`.brz`) file:
  one root thought, every note as its child, jump links for every note-to-note
  reference, note bodies converted to markdown.
- `brainsync pull` — merges thoughts from a `.brz` export (or the Local API) into a
  garden checkout.
- `brainsync sync` — two-way sync between the garden and TheBrain via the Local API.
- `brainsync check` — validates a `.brz` archive.

## Install

```sh
uv tool install --editable .   # from this checkout; drop --editable for a frozen install
brainsync sync --help
```

All commands live under the single `brainsync` entry point and work from any
directory — paths are resolved from `--notes-dir`, not the cwd.

## Crawling a site into a brain

```sh
uv sync
uv run brainsync crawl --limit 20 --output sample.brz   # quick sample first
uv run brainsync crawl --output notes.brz               # full crawl, several minutes
```

| flag | default | meaning |
|---|---|---|
| `--start` | `https://notes.andymatuschak.org/About_these_notes` | seed note for the crawl |
| `--output` | `andy-notes.brz` | output file |
| `--delay` | `0.5` | seconds between network requests (be polite) |
| `--limit` | none | stop after N notes |
| `--cache-dir` | `.brainsync-cache` | downloaded pages are cached here |
| `--name` | derived from site | brain name |

Point `--start` at any server-rendered digital garden and the crawler stays on that
site's host, e.g. `uv run brainsync crawl --start https://garden.example.com/wiki/ --output wiki.brz`.
Works with static-HTML gardens (Quartz sites, Jekyll/Next.js SSG gardens). Does not
work with client-rendered sites (Obsidian Publish, Roam, Notion). Assumes note
titles are unique — same-titled pages merge into one thought. Nested url paths
(`/focusing/habits` under `/focusing`) become parent-child links; flat sites get
all notes as children of the root. Images hosted on the same registrable domain
(including subdomains like `images.example.com`) are downloaded and embedded in
the brain; third-party images stay as remote links.

Pages are cached on disk, so re-runs and interrupted crawls only download what's
missing — cache hits skip the delay entirely. Delete the cache dir to force a
fresh crawl.

Thought and link IDs are `uuid5` of the note URL, so re-running produces identical IDs
and re-importing merges instead of duplicating.

## The other direction: brz → garden

`brainsync pull` merges thoughts from a `.brz` export into a stacked-notes garden
checkout (`notes/*.md|org` + `static/` layout):

```sh
uv run brainsync pull my-brain.brz --notes-dir ~/src/my-garden/notes --tag public --dry-run
uv run brainsync pull my-brain.brz --notes-dir ~/src/my-garden/notes --tag public
```

Intended flow: `git pull` in the garden checkout, run `brainsync pull`, review with
`git diff`, commit and push. The diff *is* the publish review.

| flag | meaning |
|---|---|
| `--api` | pull via the Local API instead of a brz file (same as omitting the brz path) |
| `--tag NAME` | export only thoughts carrying this TheBrain tag (default: every normal thought) |
| `--pull URL` | first download notes listed in the site's `index.json` that are missing locally (never overwrites) |
| `--prune` | delete previously exported notes whose thought lost the tag (default: report as orphans) |
| `--dry-run` | print the plan, touch nothing |

Merge semantics:

- Exported notes are `.md` with yaml frontmatter (`title`, `date` from the thought's
  creation time, `tags`, `brain-id`). `brain-id` is the ownership marker: owned files
  are overwritten on re-export, hand-written notes are never touched.
- Tags come from TheBrain tag thoughts (attached via `Meaning=5` links in real
  exports, or the `TagIds` field in archives this project generates) plus the
  thought's Type, all slugified, minus the selection tag.
- Plex links (`Meaning=1`, child and jump) to other exported or existing notes become
  a trailing `## Related` section, so brains whose structure lives in the plex rather
  than in note text still produce a connected garden.
- A thought whose name matches a hand-written note's title is skipped, and other
  notes' links to it resolve to that existing note instead (see `--adopt-all` below
  for claiming such notes on purpose).
- `brain://` links: target exported → `slug.md`; target unexported but matching an
  existing note title → that note; otherwise unwrapped to plain text, so private
  structure never leaks as dead links.
- Note images land in `static/brain/<slug>/` with their references rewritten. Both
  archive layouts are understood: real TheBrain exports (`<id>/Notes.md` with
  `Type 1/NoteType 4`, images under `<id>/.data/md-images/` with `Type 12`) and
  archives this project generates (`<id>/Notes/notes.html`, `Type 4/6`).
- HTML notes are converted to markdown; markdown notes pass through.
- Name-as-content thoughts (no note, name longer than 80 chars — e.g. a quotation
  kept in the thought name) store the full name as the note body; the frontmatter
  title and Related link texts use an ellipsized short form.

## Two-way sync

`brainsync sync` keeps the garden and the brain in step via the Local API. Git
history is the sync base — the command only runs inside the garden repo, and each
file is classified by three-way comparison of HEAD, the working tree, and the
brain's rendered note:

```sh
uv run brainsync sync --notes-dir ~/src/my-garden/notes --tag public --dry-run
uv run brainsync sync --notes-dir ~/src/my-garden/notes --tag public
```

| action | meaning |
|---|---|
| `push` | garden edited, brain unchanged → garden body is pushed to the thought's note |
| `pull` | brain changed, garden clean → file is rewritten from the brain |
| `conflict` | both sides changed → reported, exit nonzero, nothing touched |
| `deleted-locally` | file removed but the thought still exists → reported only |
| `orphan` | thought gone from the brain → reported only |
| `adopt` | (with `--adopt-all`) title match linked, garden body wins |

Edits under `## Related` are ignored on push with a warning — that section is
derived from the plex. Commit after each sync; the next run diffs against that
commit.

### `--adopt-all`

`brainsync sync --adopt-all` links garden notes to same-named brain thoughts: any
`.md` note without a `brain-id` whose title matches a normal thought's name is
adopted — the garden body is pushed to the thought's note and `brain-id` is stamped
into the file's frontmatter. Garden wins on first contact; afterwards the note takes
part in ordinary three-way sync. `.org` matches can't carry yaml frontmatter and stay
skipped (reported on stderr).

### Local API configuration

Both API-backed commands read the connection settings from environment variables
(`BRAIN_API_URL`, `BRAIN_API_TOKEN`, `BRAIN_ID`) or from a `.brainsync.json` file in
the garden repo root:

```json
{"base_url": "http://127.0.0.1:6543", "token": "…", "brain_id": "…"}
```

The token comes from TheBrain's Settings > User > Local API.

## Tests

```sh
uv run pytest
```

## BrainZip format

The output matches the layout of real TheBrain exports (verified against an actual
expanded `.brz` archive): BOM-prefixed JSON-lines `thoughts.json` / `links.json`,
`meta.json` with `ExchangeFormatVersion` and `AttachmentFileStates`, per-thought
notes at `<thought-id>/Notes/notes.html` registered in `attachments.json`
(Type 4 / NoteType 2 — unregistered files are ignored by the importer), and the
brain name as a `settings.json` record. All format constants live in
`src/brainsync/brainzip.py`.

Validate any archive with `uv run brainsync check my-brain.brz` — it checks for
duplicate ids, dangling link and attachment references, missing attachment files,
and `AttachmentFileStates` consistency.

