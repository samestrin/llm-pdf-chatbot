import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
README = (REPO / "README.md").read_text()


def corpus() -> str:
    parts = [(REPO / "docker-compose.yml").read_text(), (REPO / ".env.example").read_text()]
    for root in ("backend/app", "scripts"):
        for p in (REPO / root).rglob("*"):
            if p.is_file() and p.suffix in {".py", ".sh"}:
                parts.append(p.read_text())
    return "\n".join(parts)


def test_every_env_var_named_in_the_readme_exists():
    text = corpus()
    names = set(re.findall(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`", README))
    assert names
    missing = sorted(n for n in names if not re.search(rf"\b{n}\b", text))
    assert not missing, missing


def test_every_documented_command_exists():
    compose = (REPO / "docker-compose.yml").read_text()
    assert "services:" in compose
    scripts = set(re.findall(r"\./(scripts/[\w.-]+)", README))
    assert scripts
    for s in scripts:
        assert (REPO / s).is_file(), s
    pkg = (REPO / "frontend/package.json").read_text()
    for name in re.findall(r"npm run ([\w:-]+)", README):
        assert f'"{name}"' in pkg, name
    assert "docker compose up" in README
    assert (REPO / ".env.example").is_file() and "cp .env.example .env" in README


def test_every_image_link_resolves():
    links = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", README)
    assert len(links) >= 3
    for link in links:
        assert not link.startswith("http"), link
        assert (REPO / link).is_file(), link
