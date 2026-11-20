"""The model a request may use: a preset from `presets.json` and the visitor's key or demo code.

The server takes a preset id, never a base URL, so a request cannot point it at another
address. A preset's base URL is in `presets.json`; the demo preset's is `DEMO_GATEWAY_URL`,
a gateway on the internal network that takes the demo code as its API key.

The key or code is held in memory for the request only. It is out of every repr, never
echoed in an error, and `LLMClient` takes it out of any error body an endpoint sends back.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Header, HTTPException, Request

from app.llm import (
    DEFAULT_TIMEOUT_SECONDS,
    EmbedCapError,
    LLMClient,
    LLMConfig,
    LLMHTTPError,
    NoEmbeddingModelError,
)

PRESETS_FILE = Path(__file__).parent / "presets.json"
DEMO = "demo"
# A key or code: printable ASCII with no space, as every provider's keys are.
SECRET = re.compile(r"[\x21-\x7e]{1,512}")


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    kind: Literal["key", "demo"]
    # `None` for the demo preset only: its base URL is `DEMO_GATEWAY_URL`.
    base_url: str | None
    chat_model: str
    embedding_model: str | None


@dataclass(frozen=True)
class Credentials:
    preset: Preset
    # The visitor's key, or the demo code for the demo preset.
    secret: str = field(repr=False)


def load_presets(path: Path = PRESETS_FILE) -> dict[str, Preset]:
    """The presets by id, in file order; a malformed file raises `ValueError`."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("presets") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items:
        raise ValueError(f"{path.name} holds no presets")
    presets: dict[str, Preset] = {}
    for item in items:
        preset = _preset(item)
        if preset.id in presets:
            raise ValueError(f"{path.name}: preset {preset.id!r} appears twice")
        presets[preset.id] = preset
    return presets


def _preset(item: Any) -> Preset:
    keys = {"id", "label", "kind", "base_url", "chat_model", "embedding_model"}
    if not isinstance(item, dict) or set(item) != keys:
        raise ValueError(f"a preset has exactly the fields {sorted(keys)}")
    preset = Preset(**item)
    if not all(isinstance(v, str) and v for v in (preset.id, preset.label, preset.chat_model)):
        raise ValueError("a preset's id, label and chat_model are non-empty text")
    if preset.embedding_model is not None and not (
        isinstance(preset.embedding_model, str) and preset.embedding_model
    ):
        raise ValueError(f"preset {preset.id!r}: embedding_model is text or null")
    is_demo = preset.id == DEMO
    if preset.kind != (DEMO if is_demo else "key"):
        raise ValueError(f"preset {preset.id!r}: kind must be {DEMO if is_demo else 'key'!r}")
    if is_demo and preset.base_url is not None:
        raise ValueError("the demo preset's base URL is DEMO_GATEWAY_URL, not the file's")
    if not is_demo and not (
        isinstance(preset.base_url, str) and preset.base_url.startswith("https://")
    ):
        raise ValueError(f"preset {preset.id!r}: base_url must be an https:// URL")
    return preset


PRESETS = load_presets()


def demo_gateway_url() -> str | None:
    return os.environ.get("DEMO_GATEWAY_URL", "").strip() or None


def public_presets(presets: dict[str, Preset]) -> list[dict[str, Any]]:
    """What the page may know of each preset: no base URL, gateway or key. The demo preset
    is listed only when `DEMO_GATEWAY_URL` is set."""
    return [
        {
            "id": p.id,
            "label": p.label,
            "kind": p.kind,
            "chat_model": p.chat_model,
            "embedding_model": p.embedding_model,
        }
        for p in presets.values()
        if p.id != DEMO or demo_gateway_url()
    ]


def request_credentials(
    request: Request,
    x_model_preset: Annotated[
        str | None,
        Header(description=f"A preset id from `GET /presets`; `{DEMO}` with `X-Demo-Code`."),
    ] = None,
    x_model_key: Annotated[
        str | None,
        Header(description="The visitor's own key for the preset; never stored or logged."),
    ] = None,
    x_demo_code: Annotated[
        str | None,
        Header(description="A demo code, sent to the demo gateway; never stored or logged."),
    ] = None,
) -> Credentials | None:
    """The request's credentials, or `None` when it sends none. A bad combination is a 400
    whose detail names headers only, never a value: a visitor may paste a key anywhere."""
    preset_id = (x_model_preset or "").strip() or None
    key = (x_model_key or "").strip() or None
    code = (x_demo_code or "").strip() or None
    presets: dict[str, Preset] = getattr(request.app.state, "presets", PRESETS)

    if key and code:
        raise _bad("send X-Model-Key or X-Demo-Code, not both")
    if code:
        if preset_id not in (None, DEMO):
            raise _bad("X-Demo-Code is accepted only with the demo preset")
        if not SECRET.fullmatch(code):
            raise _bad("X-Demo-Code is not a valid demo code")
        if not demo_gateway_url():
            raise HTTPException(status_code=503, detail="demo codes are not available here")
        return Credentials(presets[DEMO], code)
    if preset_id is None:
        if key:
            raise _bad("X-Model-Preset is required with X-Model-Key")
        return None
    if preset_id not in presets:
        raise _bad(f"unknown model preset; use one of: {', '.join(presets)}")
    if preset_id == DEMO:
        raise _bad("X-Demo-Code is required with the demo preset")
    if not key:
        raise _bad("X-Model-Key is required with X-Model-Preset")
    if not SECRET.fullmatch(key):
        raise _bad("X-Model-Key is not a valid key")
    return Credentials(presets[preset_id], key)


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def plain_model_error(exc: Exception, credentials: Credentials | None) -> str | None:
    """The shared plain wording for a key or code failure, or None when `exc` is not one.

    A rejected key (HTTP 401/403) names the preset's label; a provider rate limit (429)
    says to try again shortly. For a demo code the same statuses mean the code is used up
    or expired: the gateway turns a revoked or expired key down with 401/403 and an
    exhausted budget with 429. A public demo session past its embedding cap says so. Nothing here reads the body the endpoint sent: it can hold
    the key or account details, and stays in the server's log alone. Without credentials
    (the server's own endpoint) the caller's existing wording applies.
    """
    if isinstance(exc, NoEmbeddingModelError):
        return "this preset has no embedding model"
    if isinstance(exc, EmbedCapError):
        return str(exc)
    if credentials is None or not isinstance(exc, LLMHTTPError):
        return None
    preset = credentials.preset
    if preset.kind == DEMO:
        if exc.status_code in (401, 403, 429):
            return "This demo code is used up or expired"
        return None
    if exc.status_code in (401, 403):
        return f"The key was rejected by {preset.label}"
    if exc.status_code == 429:
        return f"{preset.label} is rate limiting this key. Try again shortly."
    return None


def _base_url(preset: Preset) -> str | None:
    return demo_gateway_url() if preset.id == DEMO else preset.base_url


def client_for(credentials: Credentials, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> LLMClient:
    """A model client for this request's preset and key; the caller closes it. Nothing in it
    comes from the `LLM_*` settings. Both chat roles use the preset's one chat model."""
    preset = credentials.preset
    base_url = _base_url(preset)
    if base_url is None:
        raise HTTPException(status_code=503, detail="demo codes are not available here")
    return LLMClient(
        LLMConfig(
            base_url=base_url,
            api_key=credentials.secret,
            fast_model=preset.chat_model,
            reasoning_model=preset.chat_model,
            embedding_model=preset.embedding_model,
            timeout=timeout,
        )
    )
