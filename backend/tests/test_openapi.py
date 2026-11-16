import importlib.util
import json
import tomllib
from pathlib import Path

import pytest
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


def served_spec() -> dict:
    """The spec as the frontend serves it at /api/openapi.json (nginx strips /api and says so)."""
    client = TestClient(create_app(create_engine("sqlite://")))
    resp = client.get("/openapi.json", headers={"X-Forwarded-Prefix": "/api"})
    assert resp.json()["servers"] == [{"url": "/api"}]
    return resp.json()


def resolve(spec: dict, schema: dict) -> dict:
    if "$ref" in schema:
        return spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
    return schema


def test_the_chat_stream_declares_its_events_as_a_oneof_on_type():
    spec = served_spec()
    response = spec["paths"]["/collections/{collection}/chat"]["post"]["responses"]["200"]
    stream = response["content"]["application/x-ndjson"]
    # OpenAPI 3.1 has no item schema for a stream: the schema is one line's, and says so.
    assert spec["openapi"].startswith("3.1")
    event = resolve(spec, stream["schema"])
    assert "one line" in event["description"]
    assert event["discriminator"]["propertyName"] == "type"
    kinds = {
        kind: resolve(spec, {"$ref": ref})
        for kind, ref in event["discriminator"]["mapping"].items()
    }
    assert sorted(kinds) == ["claim", "done", "error", "refusal", "step"]
    assert sorted(r["$ref"] for r in event["oneOf"]) == sorted(
        event["discriminator"]["mapping"].values()
    )
    fields = {
        "step": ["type", "question", "pages"],
        "claim": ["type", "text", "document", "page", "chunk_id", "quote"],
        "refusal": ["type", "passages"],
        "error": ["type", "message"],
        "done": ["type", "mode", "model", "cost"],
    }
    for kind, schema in kinds.items():
        assert list(schema["properties"]) == fields[kind], kind
        assert sorted(schema["required"]) == sorted(fields[kind]), kind
        assert schema["properties"]["type"]["const"] == kind
        assert schema["additionalProperties"] is False


def test_the_pdf_download_is_a_binary_pdf():
    op = served_spec()["paths"]["/documents/{document_id}/pdf"]["get"]
    content = op["responses"]["200"]["content"]
    assert content == {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}
    assert "404" in op["responses"]


@pytest.mark.parametrize(
    "path, errors",
    [
        ("/documents/{document_id}", {"404": "document not found"}),
        (
            "/collections/{collection}",
            {"404": "collection not found", "409": "collection is not empty"},
        ),
    ],
)
def test_a_delete_is_204_with_no_body_and_its_errors_have_a_detail_body(path, errors):
    spec = served_spec()
    responses = spec["paths"][path]["delete"]["responses"]
    assert "content" not in responses["204"]
    assert [s for s in responses if s.startswith("2")] == ["204"]
    for status, detail in errors.items():
        assert detail in responses[status]["description"]
        body = resolve(spec, responses[status]["content"]["application/json"]["schema"])
        assert body["properties"]["detail"]["type"] == "string"
        assert body["required"] == ["detail"]


def test_each_of_the_four_operations_has_a_response_schema():
    spec = served_spec()
    chat = spec["paths"]["/collections/{collection}/chat"]["post"]["responses"]["200"]
    pdf = spec["paths"]["/documents/{document_id}/pdf"]["get"]["responses"]["200"]
    assert chat["content"]["application/x-ndjson"]["schema"]
    assert pdf["content"]["application/pdf"]["schema"]
    for path in ["/documents/{document_id}", "/collections/{collection}"]:
        responses = spec["paths"][path]["delete"]["responses"]
        assert responses["204"]["description"] == "Deleted; no body"
        assert responses["404"]["content"]["application/json"]["schema"]
