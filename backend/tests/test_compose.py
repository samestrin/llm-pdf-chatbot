import json
import os
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
