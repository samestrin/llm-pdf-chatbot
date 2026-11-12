import importlib.util
import tomllib
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.main import create_app

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "docs" / "openapi.json"
EXPORT_COMMAND = "cd backend && uv run python ../scripts/export_openapi.py"

spec = importlib.util.spec_from_file_location("export_openapi", REPO / "scripts/export_openapi.py")
export_openapi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_openapi)


def test_openapi_version_is_the_package_version():
    version = tomllib.loads(PYPROJECT.read_text())["project"]["version"]
    client = TestClient(create_app(create_engine("sqlite://")))
    schema = client.get("/openapi.json").json()
    assert schema["info"]["version"] == version


def test_committed_openapi_spec_matches_the_app():
    """docs/openapi.json is exactly what the export script writes from the app."""
    assert SPEC.read_text(encoding="utf-8") == export_openapi.openapi_json(), (
        f"docs/openapi.json is stale; regenerate it with: {EXPORT_COMMAND}"
    )
