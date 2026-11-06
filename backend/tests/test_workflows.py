from pathlib import Path

GITHUB = Path(__file__).resolve().parents[2] / ".github"
RUNS_ON = "runs-on: ${{ fromJSON(vars.CI_RUNS_ON || '[\"ubuntu-latest\"]') }}"


def test_no_runner_names_in_github_dir():
    for path in GITHUB.rglob("*"):
        if path.is_file():
            text = path.read_text().lower()
            assert "gauntlet" not in text, path
            assert "self-hosted" not in text, path


def test_every_job_takes_its_runner_from_the_variable():
    for path in (GITHUB / "workflows").glob("*.yml"):
        text = path.read_text()
        assert text.count("runs-on:") == text.count(RUNS_ON), path


def test_ci_has_an_actionlint_step():
    assert "actionlint" in (GITHUB / "workflows" / "ci.yml").read_text()
