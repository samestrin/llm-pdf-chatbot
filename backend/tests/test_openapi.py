import importlib.util
import json
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


# The media type each operation's success response is sent as. The chat streams one JSON
# event per line (app/answer.py); every other body is one JSON document or a PDF.
SUCCESS_MEDIA_TYPES = {
    ("post", "/collections/{collection}/documents"): ["application/json"],
    ("get", "/documents/{document_id}/status"): ["application/json"],
    ("get", "/collections"): ["application/json"],
    ("get", "/documents/{document_id}/pdf"): ["application/pdf"],
    ("get", "/documents/{document_id}/pages/{page}"): ["application/json"],
    ("delete", "/documents/{document_id}"): [],
    ("delete", "/collections/{collection}"): [],
    ("post", "/collections/{collection}/chat"): ["application/x-ndjson"],
    ("get", "/health"): ["application/json"],
}


def success_media_types(schema: dict) -> dict[tuple[str, str], list[str]]:
    return {
        (method, path): sorted(response.get("content", {}))
        for path, ops in schema["paths"].items()
        for method, op in ops.items()
        for status, response in op["responses"].items()
        if status.startswith("2")
    }


def test_each_success_response_is_declared_as_the_media_type_it_is_sent_as():
    client = TestClient(create_app(create_engine("sqlite://")))
    assert success_media_types(client.get("/openapi.json").json()) == SUCCESS_MEDIA_TYPES


def test_the_committed_spec_declares_the_chat_stream_as_ndjson_only():
    chat = json.loads(SPEC.read_text(encoding="utf-8"))["paths"]["/collections/{collection}/chat"]
    content = chat["post"]["responses"]["200"]["content"]
    assert list(content) == ["application/x-ndjson"]
    assert "application/json" not in content
