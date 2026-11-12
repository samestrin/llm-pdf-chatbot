import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
LICENCES = ROOT / "docs" / "fixture-licences.md"

# Internal names that must not reach a public repo. They are built from pieces so this
# file does not contain them itself; the scan still skips this file and the workflow that
# runs gitleaks.
#
# The private root is an absolute path, so a lookbehind keeps a relative path segment from
# matching it: the vendored Scalar bundle names an icon "../icons/Workspace.svg".
BANNED = re.compile(
    "gaunt" + "let|" + r"[\w-]+\.lan\b|" + "paper" + "clip|" + r"(?<![\w.-])/work" + "space"
    "|llm-pdf-chatbot-planning",
    re.IGNORECASE,
)
SKIPPED = {"backend/tests/test_public_ready.py", ".github/workflows/public-ready.yml"}

# Private host names that must not appear anywhere in the tree, this file excluded.
PRIVATE_HOSTS = re.compile(
    "|".join(
        [
            re.escape("estr" + ".in"),
            "em" + "bassy",
            r"\." + "lan" + r"\b",
            "orches" + "trator",
        ]
    ),
    re.IGNORECASE,
)
LOCKFILES = {"package-lock.json", "uv.lock", "yarn.lock", "pnpm-lock.yaml", "poetry.lock"}


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, text=True
    ).stdout
    return [p for p in out.split("\0") if p and (ROOT / p).is_file()]


def banned_names(
    files: list[str], pattern: re.Pattern[str] = BANNED, skipped: set[str] = SKIPPED
) -> list[str]:
    hits = []
    for rel in files:
        if rel in skipped:
            continue
        try:
            text = (ROOT / rel).read_text()
        except UnicodeDecodeError:
            continue  # binary
        for n, line in enumerate(text.splitlines(), 1):
            for match in pattern.finditer(line):
                hits.append(f"{rel}:{n}: {match.group(0)}")
    return hits


def fixture_files(files: list[str]) -> list[str]:
    return [p for p in files if "fixtures" in PurePosixPath(p).parent.parts]


def test_the_banned_pattern_catches_each_name():
    names = [
        "Gaunt" + "let",
        "runner" + ".lan",
        "orches" + "trator" + ".lan:3100",
        "Paper" + "Clip",
        "/workspace/x",
        # The private root on its own, and the shapes a leak of it takes in a command or a path.
        "/work" + "space",
        "cd /work" + "space",
        "path=/work" + "space/llm-pdf-chatbot",
        "(/work" + "space)",
    ]
    for text in names:
        assert BANNED.search(text), text
    assert BANNED.search("samestrin/llm-pdf-chatbot-planning")


def test_the_banned_pattern_leaves_a_vendored_icon_path_alone():
    # A real line from the vendored Scalar bundle (app/static/scalar.js): an icon module
    # name in a relative path, not the private root. The bundle is scanned like every
    # other tracked file, so the pattern has to tell the two apart.
    assert not BANNED.search('"../icons/Work' + 'space.svg":K1e')
    assert not BANNED.search("icons/Work" + "space.svg")


def test_the_banned_pattern_leaves_ordinary_words_alone():
    for text in ["plan", "the plan", "language", "x.lang", "workspaces", "llm-pdf-chatbot"]:
        assert not BANNED.search(text), text


def test_the_tree_has_no_banned_internal_names():
    assert banned_names(tracked_files()) == []


def private_host_files(files: list[str]) -> list[str]:
    return [
        p
        for p in files
        if PurePosixPath(p).name not in LOCKFILES
        and "node_modules" not in PurePosixPath(p).parts
        and ".git" not in PurePosixPath(p).parts
    ]


def test_the_private_host_pattern_catches_each_name():
    names = [
        "pdfchat." + "estr" + ".in",
        "ESTR" + ".IN",
        "Em" + "bassy",
        "docker-compose." + "em" + "bassy" + ".yml",
        "ai" + ".lan",
        "http://runner" + ".LAN:8000",
        "Orches" + "trator",
    ]
    for text in names:
        assert PRIVATE_HOSTS.search(text), text


def test_the_private_host_pattern_leaves_ordinary_words_alone():
    for text in ["x.lang", "plan", "estrin", "the proxy", "chat.example.com", "lan"]:
        assert not PRIVATE_HOSTS.search(text), text


def test_the_private_host_scan_skips_lockfiles_and_vendored_code():
    files = ["a/package-lock.json", "uv.lock", "x/node_modules/y.js", "docs/a.md"]
    assert private_host_files(files) == ["docs/a.md"]


def test_the_tree_has_no_private_host_names():
    files = private_host_files(tracked_files())
    hits = banned_names(files, PRIVATE_HOSTS, {"backend/tests/test_public_ready.py"})
    assert hits == []


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
