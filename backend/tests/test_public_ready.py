import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
LICENCES = ROOT / "docs" / "fixture-licences.md"

# Internal names that must not reach a public repo. This file and the workflow that
# runs gitleaks name them on purpose, so the scan skips both.
BANNED = re.compile(
    r"gauntlet|[\w-]+\.lan\b|paperclip|/workspace|llm-pdf-chatbot-planning", re.IGNORECASE
)
SKIPPED = {"backend/tests/test_public_ready.py", ".github/workflows/public-ready.yml"}


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, text=True
    ).stdout
    return [p for p in out.split("\0") if p and (ROOT / p).is_file()]


def banned_names(files: list[str]) -> list[str]:
    hits = []
    for rel in files:
        if rel in SKIPPED:
            continue
        try:
            text = (ROOT / rel).read_text()
        except UnicodeDecodeError:
            continue  # binary
        for n, line in enumerate(text.splitlines(), 1):
            for match in BANNED.finditer(line):
                hits.append(f"{rel}:{n}: {match.group(0)}")
    return hits


def fixture_files(files: list[str]) -> list[str]:
    return [p for p in files if "fixtures" in PurePosixPath(p).parent.parts]


def test_the_banned_pattern_catches_each_name():
    for text in ["Gauntlet", "ci-runner", "build-host:3100", "PaperClip", "/workspace/x"]:
        assert BANNED.search(text), text
    assert BANNED.search("samestrin/llm-pdf-chatbot-planning")


def test_the_banned_pattern_leaves_ordinary_words_alone():
    for text in ["plan", "the plan", "language", "x.lang", "workspaces", "llm-pdf-chatbot"]:
        assert not BANNED.search(text), text


def test_the_tree_has_no_banned_internal_names():
    assert banned_names(tracked_files()) == []


def test_every_fixture_file_is_listed_in_the_licences_doc():
    listed = LICENCES.read_text()
    fixtures = fixture_files(tracked_files())
    assert fixtures, "no fixtures/ directory found"
    assert [p for p in fixtures if f"`{p}`" not in listed] == []


def test_fixture_files_are_found_at_any_depth_but_not_by_name_alone():
    files = ["a/fixtures/x.pdf", "fixtures/y.txt", "a/fixtures/b/z.json", "a/fixtures.ts"]
    assert fixture_files(files) == files[:3]


def test_the_planning_directory_stays_ignored():
    path = ".planning/epics/active/plan.md"
    result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False)
    assert result.returncode == 0
