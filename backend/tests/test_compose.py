import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
VARS = [
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_FAST_MODEL",
    "LLM_REASONING_MODEL",
    "LLM_EMBEDDING_MODEL",
    "OCR_URL",
]

needs_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")


def compose_backend_env(extra_env=None):
    env = {k: v for k, v in os.environ.items() if k not in VARS}
    env.update(extra_env or {})
    out = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=REPO,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)["services"]["backend"]["environment"]


@needs_docker
def test_backend_receives_all_six_variables_empty_by_default():
    environment = compose_backend_env()
    for name in VARS:
        assert environment.get(name) == "", name


@needs_docker
def test_backend_receives_values_from_the_environment():
    values = {name: f"value-{name}" for name in VARS}
    environment = compose_backend_env(values)
    for name, value in values.items():
        assert environment[name] == value


def test_env_example_lists_all_six_variables():
    lines = (REPO / ".env.example").read_text().splitlines()
    names = {line.lstrip("# ").split("=", 1)[0] for line in lines if "=" in line}
    assert set(VARS) <= names


# Optional settings of the answer: empty unless set, so the backend's defaults apply.
ANSWER_VARS = [
    "REFUSAL_MIN_SIMILARITY",
    "REFUSAL_MIN_KEYWORD_COVERAGE",
    "CHAT_HISTORY_TURNS",
    "LLM_PRICE_FAST_PER_MTOK_IN",
    "LLM_PRICE_FAST_PER_MTOK_OUT",
]


@needs_docker
def test_backend_receives_the_answer_settings_from_the_environment():
    values = {name: f"value-{name}" for name in ANSWER_VARS}
    environment = compose_backend_env(values)
    for name, value in values.items():
        assert environment[name] == value


def test_env_example_lists_the_answer_settings():
    lines = (REPO / ".env.example").read_text().splitlines()
    names = {line.lstrip("# ").split("=", 1)[0] for line in lines if "=" in line}
    assert set(ANSWER_VARS) <= names


def test_compose_passes_the_answer_settings_to_the_backend():
    compose = (REPO / "docker-compose.yml").read_text()
    for name in ANSWER_VARS:
        assert f"{name}: ${{{name}:-}}" in compose, name


# Every variable the README documents in backticks reaches the backend through compose,
# except these, which compose itself uses (the db service and the published ports).
COMPOSE_LEVEL_VARS = {
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "BACKEND_PORT",
    "FRONTEND_PORT",
}


def readme_variables():
    readme = (REPO / "README.md").read_text()
    return set(re.findall(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`", readme)) - COMPOSE_LEVEL_VARS


def compose_backend_environment_names():
    compose = (REPO / "docker-compose.yml").read_text()
    backend = re.search(r"^  backend:\n(.*?)(?=^  \S)", compose, re.MULTILINE | re.DOTALL).group(1)
    environment = re.search(r"^    environment:\n((?:^      .*\n)+)", backend, re.MULTILINE).group(
        1
    )
    return set(re.findall(r"^      ([A-Z][A-Z0-9_]*):", environment, re.MULTILINE))


def test_readme_documents_variables():
    # Guards the test below against a README pattern that finds nothing.
    assert {"LLM_BASE_URL", "RERANK_URL", "LLM_TIMEOUT_SECONDS"} <= readme_variables()


def test_compose_passes_every_readme_documented_variable_to_the_backend():
    missing = readme_variables() - compose_backend_environment_names()
    assert not missing, sorted(missing)


# Optional settings of the model client and the reranker: empty unless set.
CLIENT_VARS = [
    "LLM_TIMEOUT_SECONDS",
    "EMBED_BATCH_SIZE",
    "RERANK_URL",
    "RERANK_MODEL",
    "RERANK_API_KEY",
    "RERANK_TIMEOUT_SECONDS",
]


def test_compose_passes_the_client_settings_empty_by_default():
    compose = (REPO / "docker-compose.yml").read_text()
    for name in CLIENT_VARS:
        assert f"{name}: ${{{name}:-}}" in compose, name


@needs_docker
def test_backend_receives_the_client_settings_from_the_environment():
    values = {name: f"value-{name}" for name in CLIENT_VARS}
    environment = compose_backend_env(values)
    for name, value in values.items():
        assert environment[name] == value


def test_env_example_lists_the_client_settings():
    lines = (REPO / ".env.example").read_text().splitlines()
    names = {line.lstrip("# ").split("=", 1)[0] for line in lines if "=" in line}
    assert set(CLIENT_VARS) <= names
