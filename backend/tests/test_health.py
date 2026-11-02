from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.main import create_app


def test_health_ok_when_database_answers():
    client = TestClient(create_app(create_engine("sqlite://")))
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_503_when_database_down(tmp_path):
    # A path inside a missing directory cannot be opened by SQLite.
    engine = create_engine(f"sqlite:///{tmp_path}/missing/db.sqlite")
    client = TestClient(create_app(engine))
    assert client.get("/health").status_code == 503
