import sys

from brainsync import brain2notes, check, main as site, sync

COMMANDS = {
    "sync": sync.main,
    "pull": brain2notes.main,
    "crawl": site.main,
    "check": check.main,
}

USAGE = """usage: brainsync <command> [options]

commands:
  sync    two-way sync between the garden and TheBrain via the Local API
  pull    one-way merge of brain thoughts (brz or API) into the garden
  crawl   crawl a garden site into a TheBrain .brz file
  check   validate a .brz archive

run 'brainsync <command> --help' for command options
"""


def main() -> None:
    if len(sys.argv) < 2:
        print(USAGE, file=sys.stderr)
        raise SystemExit(2)
    command = sys.argv[1]
    if command in ("-h", "--help"):
        print(USAGE)
        raise SystemExit(0)
    if command not in COMMANDS:
        print(f"unknown command: {command}\n\n{USAGE}", file=sys.stderr)
        raise SystemExit(2)
    sys.argv = [f"brainsync {command}", *sys.argv[2:]]
    COMMANDS[command]()
