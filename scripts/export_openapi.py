"""Write docs/openapi.json from the backend app's OpenAPI schema.

Usage: cd backend && uv run python ../scripts/export_openapi.py

`openapi_json()` is the one serializer: this writer and the backend test
(tests/test_openapi.py) both use it, so the committed file and the check can
never disagree on format. Building the schema does not enter the app's
lifespan, so no database is touched and no worker starts.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "docs" / "openapi.json"


def openapi_json() -> str:
    """The exact text of docs/openapi.json, serialized from the app's schema."""
    # Imported here, not at the top: `app` lives in backend/, which is on
    # sys.path under pytest (pythonpath = ["."]) and via __main__ below when
    # this file is run as a script.
    from sqlalchemy import create_engine

    from app.main import create_app

    # An explicit in-memory engine: the schema never queries it, and the
    # export must not depend on DATABASE_URL.
    app = create_app(create_engine("sqlite://"))
    return json.dumps(app.openapi(), indent=2) + "\n"


def main() -> None:
    SPEC.write_text(openapi_json(), encoding="utf-8")
    print(f"wrote {SPEC}")


if __name__ == "__main__":
    sys.path.insert(0, str(REPO / "backend"))
    main()
