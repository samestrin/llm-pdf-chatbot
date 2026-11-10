import importlib.util
import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
VERSION = "1.0.0"
WORKFLOW = yaml.safe_load((REPO / ".github/workflows/release.yml").read_text())
JOBS = WORKFLOW["jobs"]
# PyYAML reads the bare key `on` as the boolean True.
TRIGGERS = WORKFLOW.get("on", WORKFLOW.get(True))

spec = importlib.util.spec_from_file_location(
    "check_runs_green", REPO / "scripts/check_runs_green.py"
)
check_runs_green = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_runs_green)


def steps_text(job):
    return "\n".join(s.get("run", "") for s in JOBS[job]["steps"])


def test_backend_version_is_1_0_0_in_pyproject_and_lock():
    pyproject = tomllib.loads((REPO / "backend/pyproject.toml").read_text())
    assert pyproject["project"]["version"] == VERSION
    lock = tomllib.loads((REPO / "backend/uv.lock").read_text())
    [own] = [p for p in lock["package"] if p["name"] == pyproject["project"]["name"]]
    assert own["version"] == VERSION


def test_frontend_version_is_1_0_0_in_package_and_lock():
    package = json.loads((REPO / "frontend/package.json").read_text())
    lock = json.loads((REPO / "frontend/package-lock.json").read_text())
    assert package["version"] == VERSION
    assert lock["version"] == VERSION
    assert lock["packages"][""]["version"] == VERSION


def test_changelog_has_a_dated_1_0_0_entry():
    changelog = (REPO / "CHANGELOG.md").read_text()
    assert re.search(r"^## \[1\.0\.0\] - \d{4}-\d{2}-\d{2}$", changelog, re.MULTILINE)


def test_triggers_are_v_tags_pull_requests_and_a_dispatch_that_does_not_publish():
    assert TRIGGERS["push"] == {"tags": ["v*"]}
    assert "branches" not in TRIGGERS["push"]
    assert "pull_request" in TRIGGERS
    publish = TRIGGERS["workflow_dispatch"]["inputs"]["publish"]
    assert publish["type"] == "boolean"
    assert publish["default"] is False


def test_packages_write_only_on_the_publish_job():
    assert WORKFLOW["permissions"] == {"contents": "read"}
    for name, job in JOBS.items():
        perms = job.get("permissions", {})
        if name == "publish":
            assert perms.get("packages") == "write"
            assert perms.get("contents") == "read"
        else:
            assert perms in ({}, {"contents": "read"}), name


def test_build_job_builds_both_images_and_pushes_nothing():
    text = steps_text("build")
    assert re.search(r"docker build\b.*\bbackend\b", text)
    assert re.search(r"docker build\b.*\bfrontend\b", text)
    assert "docker push" not in text
    assert "docker login" not in text


def test_publish_needs_the_build_and_runs_only_on_a_v_tag():
    publish = JOBS["publish"]
    assert publish["needs"] == "build"
    condition = publish["if"]
    assert "startsWith(github.ref, 'refs/tags/v')" in condition
    assert "pull_request" not in condition
    assert "inputs.publish" in condition
    for name, job in JOBS.items():
        if name != "publish":
            assert "docker push" not in "\n".join(s.get("run", "") for s in job["steps"])


def test_publish_checks_the_tagged_commit_is_green_before_pushing():
    steps = JOBS["publish"]["steps"]
    runs = [s.get("run", "") for s in steps]
    gate = next(i for i, r in enumerate(runs) if "check_runs_green.py" in r)
    first_push = next(i for i, r in enumerate(runs) if "docker push" in r)
    login = next(i for i, s in enumerate(steps) if "login" in s.get("uses", "") + s.get("run", ""))
    assert gate < login < first_push
    assert "github.sha" in runs[gate] or "GITHUB_SHA" in runs[gate]
    assert JOBS["publish"]["permissions"].get("checks") == "read"


def test_both_images_are_pushed_in_one_step_after_both_are_loaded():
    runs = [s.get("run", "") for s in JOBS["publish"]["steps"]]
    loaded = [i for i, r in enumerate(runs) if "docker load" in r]
    pushes = [i for i, r in enumerate(runs) if "docker push" in r]
    assert loaded and pushes
    assert max(loaded) < min(pushes)


def run(check_runs, own_run_id="99"):
    return check_runs_green.failures(check_runs, own_run_id)


def check(name, status="completed", conclusion="success", run_id="1"):
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "details_url": f"https://github.com/o/r/actions/runs/{run_id}/job/5",
    }


def test_all_green_checks_pass():
    assert run([check("backend"), check("frontend", conclusion="skipped")]) == []


def test_a_failed_check_fails():
    assert run([check("backend"), check("frontend", conclusion="failure")]) == ["frontend: failure"]


def test_a_check_still_running_fails():
    assert run([check("backend", status="in_progress", conclusion=None)]) == [
        "backend: in_progress"
    ]


def test_no_checks_at_all_fails():
    assert run([]) == ["no check runs on this commit"]


def test_this_runs_own_jobs_are_ignored():
    own = check("publish", status="in_progress", conclusion=None, run_id="99")
    assert run([check("backend"), own]) == []
    assert run([own]) == ["no check runs on this commit"]


@pytest.mark.parametrize("conclusion", ["cancelled", "timed_out", "action_required", "stale"])
def test_other_conclusions_fail(conclusion):
    assert run([check("ci", conclusion=conclusion)]) == [f"ci: {conclusion}"]


def test_main_reads_json_lines_and_exits_nonzero_on_failure(capsys):
    lines = "\n".join(json.dumps(c) for c in [check("a"), check("b", conclusion="failure")])
    assert check_runs_green.main(["99"], lines) == 1
    assert "b: failure" in capsys.readouterr().err
    assert check_runs_green.main(["99"], json.dumps(check("a"))) == 0


spec = importlib.util.spec_from_file_location(
    "changelog_section", REPO / "scripts/changelog_section.py"
)
changelog_section = importlib.util.module_from_spec(spec)
spec.loader.exec_module(changelog_section)

SAMPLE_CHANGELOG = """# Changelog

## [Unreleased]

### Added

- Next thing.

## [1.0.0] - 2026-11-06

Intro.

### Added

- First thing.

## [0.9.0] - 2026-01-01

- Old thing.

## [2.0.0] - 2026-12-01
"""


def write_changelog(tmp_path, text=SAMPLE_CHANGELOG):
    path = tmp_path / "CHANGELOG.md"
    path.write_text(text)
    return str(path)


def test_changelog_section_returns_the_version_without_the_next_heading(tmp_path, capsys):
    code = changelog_section.main([VERSION, write_changelog(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("Intro.")
    assert "- First thing." in out
    assert "## [" not in out
    assert "Old thing" not in out
    assert "Next thing" not in out


def test_changelog_section_exits_non_zero_for_a_missing_version(tmp_path, capsys):
    assert changelog_section.main(["9.9.9", write_changelog(tmp_path)]) == 1
    assert capsys.readouterr().out == ""


def test_changelog_section_exits_non_zero_for_an_empty_section(tmp_path, capsys):
    assert changelog_section.main(["2.0.0", write_changelog(tmp_path)]) == 1
    assert capsys.readouterr().out == ""


def test_changelog_section_of_the_real_changelog_is_not_empty(capsys):
    assert changelog_section.main([VERSION]) == 0
    assert capsys.readouterr().out.strip()


def test_changelog_section_prints_usage_and_exits_2_without_a_version_or_with_too_many(
    capsys,
):
    assert changelog_section.main([]) == 2
    assert changelog_section.main(["1", "2", "3"]) == 2
    assert capsys.readouterr().err.count("usage: changelog_section.py") == 2
