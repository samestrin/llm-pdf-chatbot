"""The interactive API reference and the short guide to behavior outside its spec."""

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
from starlette.routing import Match

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


def test_api_guide_links_the_reference_and_spec_without_an_endpoint_table():
    guide = (REPO / "docs" / "api.md").read_text()
    first_paragraph = guide.split("\n\n")[1]
    assert "[`/api/docs`](/api/docs)" in first_paragraph
    assert "localhost:8000/docs" not in first_paragraph
    assert "`/api/openapi.json`" in guide
    assert "[`docs/openapi.json`](openapi.json)" in guide
    assert "## Behaviour the spec does not carry" in guide
    assert "## Endpoints" not in guide
    assert not re.search(r"^\|\s*(?:GET|POST|DELETE|PUT|PATCH)\s", guide, re.MULTILINE)


def test_api_guide_keeps_the_behavior_the_spec_does_not_carry():
    guide = (REPO / "docs" / "api.md").read_text()
    required = [
        "`chunks_total`",
        "`chunks_embedded`",
        "`ready` with failed pages",
        "This PDF has no text layer (a scan or blank pages); OCR is off (set OCR_URL to read scans)",
        "no page could be read",
        "client's file name never touches the file system",
        "oldest first",
        "client keeps",
        "answer may be empty",
        "`CHAT_HISTORY_TURNS`",
        "default 6",
        "follow-up",
        "retrieved passages only",
        "whitespace-only question",
        "before any model call",
    ]
    missing = [text for text in required if text not in guide]
    assert not missing, missing


def test_api_guide_has_one_valid_ndjson_example_for_every_chat_event_type():
    guide = (REPO / "docs" / "api.md").read_text()
    frontend = (REPO / "frontend" / "src" / "api.ts").read_text()
    union = re.search(r"export type ChatEvent = ([^\n]+)", frontend).group(1)
    interfaces = re.findall(r"[A-Za-z]+Event", union)
    event_types = {
        re.search(rf"export interface {name}\b.*?\btype: '([^']+)'", frontend, re.DOTALL).group(1)
        for name in interfaces
    }
    block = re.search(r"```ndjson\n(.*?)\n```", guide, re.DOTALL).group(1)
    events = [json.loads(line) for line in block.splitlines()]
    assert [event["type"] for event in events] == ["step", "claim", "refusal", "error", "done"]
    assert {event["type"] for event in events} == event_types
    required_fields = {
        "step": {"type", "question", "pages"},
        "claim": {"type", "text", "document", "page", "chunk_id", "quote"},
        "refusal": {"type", "passages"},
        "error": {"type", "message"},
        "done": {"type", "mode", "model", "cost"},
    }
    for event in events:
        assert set(event) == required_fields[event["type"]]


def test_readme_points_to_the_short_api_guide():
    readme = (REPO / "README.md").read_text()
    assert "Every endpoint, its fields and the event shapes are in" not in readme
    assert "interactive reference" in readme
    assert "behaviour the spec does not carry" in readme
    assert readme.count("[docs/api.md](docs/api.md)") == 2


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
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)  # a comment is not part of a selector
    return [(s.strip(), b.strip()) for s, b in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def accent_in(css: str, selector: str) -> str | None:
    return declaration_in(css, selector, "--scalar-color-accent")


def declaration_in(css: str, selector: str, prop: str) -> str | None:
    for selectors, body in css_rules(css):
        if selector in [s.strip() for s in selectors.split(",")]:
            for declaration in body.split(";"):
                name, _, value = declaration.partition(":")
                if name.strip() == prop:
                    return value.strip()
    return None


def theme_token(token: str) -> tuple[str, str]:
    """The light and dark value of a frontend colour token, read from the source of truth."""
    text = (REPO / "frontend" / "src" / "theme.css").read_text()
    match = re.search(rf"{token}:\s*light-dark\((#[0-9a-fA-F]{{6}}),\s*(#[0-9a-fA-F]{{6}})\)", text)
    assert match, f"frontend/src/theme.css no longer declares {token} as light-dark(...)"
    return match.group(1).lower(), match.group(2).lower()


def theme_accent() -> tuple[str, str]:
    """The light and dark `--color-accent` the frontend declares, read from the source of truth."""
    return theme_token("--color-accent")


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


# Scalar does not draw buttons, links or the active sidebar item in --scalar-color-accent: each
# has its own theme variable, which Scalar's default theme sets to black, white or grey. Every
# one of them is the frontend's token, so the page shows the chatbot blue.
SCALAR_THEME = {
    "--scalar-color-accent": "--color-accent",
    "--scalar-background-accent": "--color-accent-subtle",
    "--scalar-link-color": "--color-accent",
    "--scalar-link-color-hover": "--color-accent-hover",
    "--scalar-link-color-visited": "--color-accent",
    "--scalar-button-1": "--color-accent",
    "--scalar-button-1-hover": "--color-accent-hover",
    "--scalar-button-1-color": "--color-on-accent",
    "--scalar-sidebar-color-active": "--color-accent",
    "--scalar-sidebar-item-active-background": "--color-accent-subtle",
    "--scalar-sidebar-indent-border-active": "--color-accent",
}


def test_links_buttons_and_the_active_sidebar_item_wear_the_frontends_accent(client):
    css = "\n".join(parse(page(client)).styles)
    for variable, token in SCALAR_THEME.items():
        light, dark = theme_token(token)
        assert declaration_in(css, ".light-mode", variable) == light, variable
        assert declaration_in(css, ".dark-mode", variable) == dark, variable


def test_wide_content_scrolls_inside_its_block_not_the_page(client):
    # Checked in Chromium at a 375 px viewport: the page was 409 px wide, pushed out by the
    # request body's schema name (one 60-character word); the code samples scroll in their
    # own box. Both rules keep the overflow inside the block that has it.
    css = "\n".join(parse(page(client)).styles)
    assert declaration_in(css, ".scalar-app pre", "overflow-x") == "auto"
    assert declaration_in(css, ".scalar-app pre", "max-width") == "100%"
    assert declaration_in(css, ".scalar-app .request-body-title", "flex-wrap") == "wrap"
    assert declaration_in(css, ".scalar-app .request-body-title", "overflow-wrap") == "anywhere"
    # Hiding the page's overflow would cut content off instead of fitting it.
    for selector in ("html", "body", ".scalar-app"):
        assert declaration_in(css, selector, "overflow-x") is None, selector
        assert declaration_in(css, selector, "overflow") is None, selector


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


# Scalar's "Test Request" and code samples call the spec's server URL plus the path; with no
# server entry they call the page's origin plus the path. Behind the frontend the API is under
# /api, so the spec served there must name /api, and the spec served directly must not.


def nginx_api_location() -> dict[str, str]:
    """The /api location of frontend/nginx.conf: its prefix, proxy_pass and the prefix header."""
    conf = (REPO / "frontend" / "nginx.conf").read_text()
    match = re.search(r"location\s+(/api/)\s*\{(.*?)\n\s*\}", conf, re.DOTALL)
    assert match, "frontend/nginx.conf has no `location /api/` block"
    body = match.group(2)
    proxy_pass = re.search(r"^\s*proxy_pass\s+(\S+);", body, re.MULTILINE)
    header = re.search(r"^\s*proxy_set_header\s+X-Forwarded-Prefix\s+(\S+);", body, re.MULTILINE)
    assert proxy_pass, "the /api location has no proxy_pass"
    assert header, "the /api location does not send X-Forwarded-Prefix"
    return {
        "location": match.group(1),
        "proxy_pass": proxy_pass.group(1),
        "prefix": header.group(1),
    }


def through_nginx(browser_path: str, location: dict[str, str]) -> str:
    """The path the backend sees for a browser request, as nginx forwards it.

    A proxy_pass with a URI part replaces the matched location prefix with that URI.
    """
    assert browser_path.startswith(location["location"]), f"{browser_path} misses nginx's /api/"
    uri = urlparse(location["proxy_pass"]).path
    assert uri, "proxy_pass has no URI part, so nginx would not strip /api"
    return uri + browser_path[len(location["location"]) :]


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def operations(spec: dict) -> list[tuple[str, str]]:
    return [(method.upper(), path) for path, ops in spec["paths"].items() for method in ops]


def routed(client, method: str, path: str) -> bool:
    """Whether the app has an API route for this method and concrete path.

    The page, the script and the spec are left out (included routers carry no flag).
    """
    scope = {"type": "http", "method": method, "path": path, "root_path": ""}
    return any(
        route.matches(scope)[0] is Match.FULL
        for route in client.app.routes
        if getattr(route, "include_in_schema", True)
    )


def test_the_spec_served_through_nginx_names_the_api_mount_point(client):
    location = nginx_api_location()
    spec = client.get("/openapi.json", headers={"X-Forwarded-Prefix": location["prefix"]}).json()
    assert spec["servers"] == [{"url": "/api"}]
    assert operations(spec)
    for method, path in operations(spec):
        browser_path = spec["servers"][0]["url"] + concrete(path)
        backend_path = through_nginx(browser_path, location)
        assert backend_path == concrete(path)
        assert routed(client, method, backend_path), f"{method} {browser_path}"
    health = through_nginx(spec["servers"][0]["url"] + "/health", location)
    assert client.get(health).json() == {"status": "ok"}


def test_the_spec_served_directly_names_no_server_so_scalar_uses_the_origin(client):
    # `uv run uvicorn app.main:app` or the published port 8000: /docs calls /health there.
    spec = client.get("/openapi.json").json()
    assert "servers" not in spec
    for method, path in operations(spec):
        assert routed(client, method, concrete(path)), f"{method} {path}"
    assert client.get("/health").json() == {"status": "ok"}


def test_a_request_through_nginx_does_not_leak_its_server_into_later_ones(client):
    client.get("/openapi.json", headers={"X-Forwarded-Prefix": "/api"})
    assert "servers" not in client.get("/openapi.json").json()


@pytest.mark.parametrize(
    "prefix",
    ["", "/", "api", "https://elsewhere.example", "//elsewhere.example", "/api/", "/a b", "/a?x"],
)
def test_a_prefix_that_is_not_a_plain_path_names_no_server(client, prefix):
    spec = client.get("/openapi.json", headers={"X-Forwarded-Prefix": prefix}).json()
    assert "servers" not in spec


def test_the_specs_paths_are_the_same_through_nginx(client):
    spec = client.get("/openapi.json", headers={"X-Forwarded-Prefix": "/api"}).json()
    assert {p: sorted(ops) for p, ops in spec["paths"].items()} == SPEC_PATHS


def test_the_vite_dev_proxy_sends_the_same_prefix():
    # `npm run dev` serves /api/docs through Vite's proxy, which strips /api like nginx does.
    config = (REPO / "frontend" / "vite.config.ts").read_text()
    assert re.search(r"""['"]X-Forwarded-Prefix['"]\s*:\s*['"]/api['"]""", config)


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
