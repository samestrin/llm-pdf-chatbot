"""Print the CHANGELOG.md section for a version, for use as release notes.

Usage: changelog_section.py VERSION [CHANGELOG]
Prints the text under `## [VERSION] - date` up to the next `## [` heading, without
either heading. Exits 1 when the section is missing or empty.
"""

import re
import sys
from pathlib import Path

DEFAULT_CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"


def section(text, version):
    heading = re.compile(rf"^## \[{re.escape(version)}\](?:\s.*)?$")
    body = None
    for line in text.splitlines():
        if body is None:
            if heading.match(line):
                body = []
        elif line.startswith("## ["):
            break
        else:
            body.append(line)
    return "\n".join(body or []).strip()


def main(argv):
    if not argv or len(argv) > 2:
        print("usage: changelog_section.py VERSION [CHANGELOG]", file=sys.stderr)
        return 2
    path = Path(argv[1]) if len(argv) == 2 else DEFAULT_CHANGELOG
    text = section(path.read_text(), argv[0])
    if not text:
        print(f"no changelog section for {argv[0]}", file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
