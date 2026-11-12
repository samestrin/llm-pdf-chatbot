"""The API reference at /docs: Scalar from a vendored script, offline, in the app's accent."""

import base64
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.main import create_app

REPO = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
SOURCES = STATIC / "SOURCES.md"

# The spec's paths and methods before the reference page was added. The page and the script
# are not endpoints, so this must not move.
SPEC_PATHS = {
    "/collections/{collection}/documents": ["post"],
    "/documents/{document_id}/status": ["get"],
    "/collections": ["get"],
    "/documents/{document_id}/pdf": ["get"],
    "/documents/{document_id}/pages/{page}": ["get"],
    "/documents/{document_id}": ["delete"],
    "/collections/{collection}": ["delete"],
    "/collections/{collection}/chat": ["post"],
    "/health": ["get"],
}

# Hosts the page must never reach: a CDN, Scalar's font host, its request proxy or its own site.
OUTSIDE_HOSTS = [
    "cdn.",
    "unpkg.com",
    "jsdelivr",
    "fonts.googleapis.com",
    "fonts.scalar.com",
    "proxy.scalar.com",
    "scalar.com",
]


@pytest.fixture
def client():
    return TestClient(create_app(create_engine("sqlite://")))


class Tags(HTMLParser):
    """Every start tag with its attributes, so a test can read the page like a browser does."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.styles: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, {k: v or "" for k, v in attrs}))
        self._in_style = tag == "style"

    def handle_data(self, data):
        if self._in_style:
            self.styles.append(data)

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False


def parse(html: str) -> Tags:
    parser = Tags()
    parser.feed(html)
    return parser


def page(client) -> str:
    resp = client.get("/docs")
    assert resp.status_code == 200
    return resp.text


def configuration(html: str) -> dict:
    """The JSON in the `#api-reference` script tag, which is how Scalar standalone is told."""
    tags = parse(html).tags
    script = next(attrs for tag, attrs in tags if attrs.get("id") == "api-reference")
    return json.loads(script["data-configuration"])


def css_rules(css: str) -> list[tuple[str, str]]:
    return [(s.strip(), b.strip()) for s, b in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def accent_in(css: str, selector: str) -> str | None:
    for selectors, body in css_rules(css):
        if selector in [s.strip() for s in selectors.split(",")]:
            for declaration in body.split(";"):
                name, _, value = declaration.partition(":")
                if name.strip() == "--scalar-color-accent":
                    return value.strip()
    return None


def theme_accent() -> tuple[str, str]:
    """The light and dark `--color-accent` the frontend declares, read from the source of truth."""
    text = (REPO / "frontend" / "src" / "theme.css").read_text()
    match = re.search(
        r"--color-accent:\s*light-dark\((#[0-9a-fA-F]{6}),\s*(#[0-9a-fA-F]{6})\)", text
    )
    assert match, "frontend/src/theme.css no longer declares --color-accent as light-dark(...)"
    return match.group(1).lower(), match.group(2).lower()


def test_docs_is_html_that_names_the_spec_and_the_vendored_script(client):
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    html = resp.text
    assert "openapi.json" in html
    assert "docs/scalar.js" in html
    assert "<script" in html


def test_the_page_names_both_by_relative_url(client):
    html = page(client)
    tags = parse(html).tags
    script = next(attrs for tag, attrs in tags if tag == "script" and attrs.get("src"))
    reference = next(attrs for tag, attrs in tags if attrs.get("id") == "api-reference")
    assert script["src"] == "docs/scalar.js"
    assert reference["data-url"] == "openapi.json"
    # Relative, so the same page works behind nginx at /api/docs and on port 8000 at /docs.
    cases = [
        (
            "http://localhost:8080/api/docs",
            "http://localhost:8080/api/docs/scalar.js",
            "http://localhost:8080/api/openapi.json",
        ),
        (
            "http://localhost:8000/docs",
            "http://localhost:8000/docs/scalar.js",
            "http://localhost:8000/openapi.json",
        ),
    ]
    for base, script_url, spec_url in cases:
        assert urljoin(base, script["src"]) == script_url
        assert urljoin(base, reference["data-url"]) == spec_url


def test_the_page_fetches_nothing_from_outside(client):
    html = page(client)
    fetched = [
        value
        for tag, attrs in parse(html).tags
        for name, value in attrs.items()
        if name in {"src", "href", "data-url"}
    ]
    assert fetched, "the page fetches nothing at all"
    for value in fetched:
        assert urlparse(value).scheme in {"", "data"}, value
        assert not value.startswith("//"), value
    for host in OUTSIDE_HOSTS:
        assert host not in html.lower(), host


def test_the_defaults_that_fetch_from_outside_are_off(client):
    config = configuration(page(client))
    assert config["withDefaultFonts"] is False  # fonts.scalar.com
    assert config["proxyUrl"] is None  # proxy.scalar.com
    assert config["telemetry"] is False


def test_the_accent_is_the_frontends_in_both_themes(client):
    light, dark = theme_accent()
    css = "\n".join(parse(page(client)).styles)
    assert accent_in(css, ".light-mode") == light
    assert accent_in(css, ".dark-mode") == dark


def test_the_favicon_is_the_frontends_inlined_as_a_data_uri(client):
    tags = parse(page(client)).tags
    icon = next(attrs for tag, attrs in tags if tag == "link" and attrs.get("rel") == "icon")
    prefix = "data:image/svg+xml;base64,"
    assert icon["href"].startswith(prefix)
    assert (
        base64.b64decode(icon["href"][len(prefix) :])
        == (REPO / "frontend" / "public" / "favicon.svg").read_bytes()
    )


def test_the_vendored_script_is_served_as_javascript(client):
    resp = client.get("/docs/scalar.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert resp.content == (STATIC / "scalar.js").read_bytes()
    assert len(resp.content) > 100_000  # the standalone bundle, not a stub


def test_redoc_is_gone(client):
    assert client.get("/redoc").status_code == 404


def test_the_specs_paths_are_unchanged(client):
    spec = client.get("/openapi.json").json()
    assert {p: sorted(ops) for p, ops in spec["paths"].items()} == SPEC_PATHS


def test_the_reference_routes_are_not_in_the_schema(client):
    spec = client.get("/openapi.json").json()
    assert "/docs" not in spec["paths"]
    assert "/docs/scalar.js" not in spec["paths"]


SOURCE_FIELDS = ["file", "package", "version", "url", "licence", "retrieved", "sha256"]


def source_rows(path: Path) -> dict[str, dict[str, str]]:
    rows = {}
    for line in path.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.strip().startswith("|") or len(cells) != len(SOURCE_FIELDS):
            continue
        if cells[0] == "File" or set(cells[0]) <= set("-: "):
            continue
        rows[cells[0]] = dict(zip(SOURCE_FIELDS, cells, strict=True))
    return rows


def vendored_problems(static: Path, sources: Path) -> list[str]:
    """What is wrong with the vendored record: a missing file, a changed byte, an empty cell."""
    rows = source_rows(sources)
    problems = []
    if "scalar.js" not in rows:
        problems.append("scalar.js: not listed in SOURCES.md")
    for name, row in rows.items():
        path = static / name
        if not path.is_file():
            problems.append(f"{name}: listed in SOURCES.md but not found")
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            problems.append(f"{name}: sha256 differs from SOURCES.md")
        for field in SOURCE_FIELDS[1:-1]:
            if not row[field]:
                problems.append(f"{name}: {field} is empty")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["retrieved"]):
            problems.append(f"{name}: retrieval date is not YYYY-MM-DD")
    return problems


def write_sources(tmp_path: Path, **cells: str) -> tuple[Path, Path]:
    static = tmp_path / "static"
    static.mkdir()
    data = b"// a vendored bundle\n"
    (static / "scalar.js").write_bytes(data)
    row = {
        "file": "scalar.js",
        "package": "@scalar/api-reference",
        "version": "1.78.1",
        "url": "https://registry.npmjs.org/@scalar/api-reference/-/api-reference-1.78.1.tgz",
        "licence": "MIT",
        "retrieved": "2026-11-12",
        "sha256": hashlib.sha256(data).hexdigest(),
    } | cells
    sources = tmp_path / "SOURCES.md"
    sources.write_text(
        "# Vendored sources\n\n"
        "| File | Package | Version | URL | Licence | Retrieved | sha256 |\n"
        "|---|---|---|---|---|---|---|\n"
        "| " + " | ".join(row[f] for f in SOURCE_FIELDS) + " |\n"
    )
    return static, sources


def test_the_vendored_script_is_recorded_with_its_version_and_licence():
    assert vendored_problems(STATIC, SOURCES) == []
    row = source_rows(SOURCES)["scalar.js"]
    assert row["package"] == "@scalar/api-reference"
    assert re.fullmatch(r"\d+\.\d+\.\d+", row["version"]), "the version is pinned, not a range"
    assert row["licence"] == "MIT"


def test_a_valid_vendored_record_has_no_problems(tmp_path):
    assert vendored_problems(*write_sources(tmp_path)) == []


def test_a_changed_bundle_fails(tmp_path):
    static, sources = write_sources(tmp_path)
    (static / "scalar.js").write_bytes(b"// tampered\n")
    assert vendored_problems(static, sources) == ["scalar.js: sha256 differs from SOURCES.md"]


def test_a_listed_file_that_is_not_there_fails(tmp_path):
    static, sources = write_sources(tmp_path)
    (static / "scalar.js").unlink()
    assert vendored_problems(static, sources) == ["scalar.js: listed in SOURCES.md but not found"]


def test_an_empty_licence_or_version_fails(tmp_path):
    assert vendored_problems(*write_sources(tmp_path, licence=" ", version="")) == [
        "scalar.js: version is empty",
        "scalar.js: licence is empty",
    ]


def test_a_record_without_a_real_date_fails(tmp_path):
    assert vendored_problems(*write_sources(tmp_path, retrieved="today")) == [
        "scalar.js: retrieval date is not YYYY-MM-DD"
    ]


def test_a_bundle_missing_from_the_record_fails(tmp_path):
    static, sources = write_sources(tmp_path)
    sources.write_text("# Vendored sources\n\nno table\n")
    assert vendored_problems(static, sources) == ["scalar.js: not listed in SOURCES.md"]
