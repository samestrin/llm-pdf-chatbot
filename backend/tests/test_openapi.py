import tomllib
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.main import create_app

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_openapi_version_is_the_package_version():
    version = tomllib.loads(PYPROJECT.read_text())["project"]["version"]
    client = TestClient(create_app(create_engine("sqlite://")))
    schema = client.get("/openapi.json").json()
    assert schema["info"]["version"] == version
