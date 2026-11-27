"""The repo LICENSE served at same-origin /LICENSE (owner decision 2026-11-27, EST-9380).

The footer's `MIT License` links to `/LICENSE`, so the backend serves the repo's own
LICENSE file as plain text, with no credentials and no redirect. The image must ship
the file too: the backend build's context must contain the repo LICENSE and the
Dockerfile must copy it beside app/ (scripts/compose-smoke.sh asks the built stack).
"""

import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.main import create_app

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def client():
    return TestClient(create_app(create_engine("sqlite://")))


def test_license_is_served_as_plain_text_with_the_mit_text(client):
    response = client.get("/LICENSE")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "MIT License" in response.text
    assert response.text == (REPO / "LICENSE").read_text(encoding="utf-8")


def test_license_needs_no_credentials_and_does_not_redirect(client):
    # No preset, key or demo-code headers: the licence is public.
    response = client.get("/LICENSE", follow_redirects=False)
    assert response.status_code == 200


def test_license_is_a_file_route_not_an_endpoint(client):
    # Like /docs: out of the schema, so docs/openapi.json stays as it is.
    spec = client.get("/openapi.json").json()
    assert "/LICENSE" not in spec["paths"]


def backend_build(compose_file: str) -> tuple[Path, Path]:
    """The backend service's build context and Dockerfile, resolved from the repo root."""
    services = yaml.safe_load((REPO / compose_file).read_text(encoding="utf-8"))["services"]
    build = services["backend"]["build"]
    if isinstance(build, str):
        context, dockerfile = build, "Dockerfile"
    else:
        context, dockerfile = build["context"], build.get("dockerfile", "Dockerfile")
    context = (REPO / context).resolve()
    return context, (context / dockerfile).resolve()


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.proxy.yml"])
def test_the_backend_image_ships_the_repo_license(compose_file):
    context, dockerfile = backend_build(compose_file)
    # COPY's source is relative to the build context: the repo LICENSE must be inside it.
    assert (context / "LICENSE").is_file(), f"{compose_file}: LICENSE is outside the build context"
    assert (context / "LICENSE").read_bytes() == (REPO / "LICENSE").read_bytes()
    # And the Dockerfile must copy it beside app/, where the route reads it in the image.
    text = dockerfile.read_text(encoding="utf-8")
    assert re.search(r"^COPY\s+LICENSE\s+\./LICENSE\s*$", text, re.MULTILINE), (
        f"{dockerfile}: no `COPY LICENSE ./LICENSE`"
    )
