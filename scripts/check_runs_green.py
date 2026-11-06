"""Fail unless every check run on a commit finished green.

Reads the commit's check runs as JSON lines on stdin, as printed by
`gh api --paginate repos/OWNER/REPO/commits/SHA/check-runs --jq '.check_runs[]'`.
The argument is the id of the workflow run doing the checking; its own jobs are
still running and are ignored.
"""

import json
import sys

GREEN = {"success", "skipped", "neutral"}


def failures(check_runs, own_run_id):
    own = f"/actions/runs/{own_run_id}/"
    others = [c for c in check_runs if own not in (c.get("details_url") or "")]
    if not others:
        return ["no check runs on this commit"]
    bad = []
    for c in others:
        if c["status"] != "completed":
            bad.append(f"{c['name']}: {c['status']}")
        elif c["conclusion"] not in GREEN:
            bad.append(f"{c['name']}: {c['conclusion']}")
    return bad


def main(argv, text):
    check_runs = [json.loads(line) for line in text.splitlines() if line.strip()]
    bad = failures(check_runs, argv[0])
    for line in bad:
        print(line, file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin.read()))
