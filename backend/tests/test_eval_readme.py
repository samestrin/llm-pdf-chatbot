import json

import pytest

from evals import readme
from evals.readme import (
    BEGIN,
    END,
    NOT_PUBLISHED,
    check,
    newest_results,
    score_block,
    with_block,
)
from evals.score import Ratio, Scores

SCORES = {
    "answer_accuracy": {"score": 0.8, "hits": 24, "total": 30},
    "citation_precision": {"score": 0.75, "hits": 45, "total": 60},
    "refusal_accuracy": {"score": 0.925, "hits": 37, "total": 40},
    "cost_usd": 0.0123,
    "errors": 1,
}


def results(day="2026-11-06", run="full", judge=False, scores=SCORES) -> dict:
    return {
        "date": day,
        "run": run,
        "judge": judge,
        "model": "gpt-4o-mini",
        "embedding_model": "text-embedding-3-small",
        "pdfs": ["a.pdf", "b.pdf", "c.pdf", "d.pdf", "e.pdf"],
        "questions": 40,
        "scores": scores,
        "judge_cost_usd": None,
        "results": [],
    }


def write_results(results_dir, data) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / f"{data['date']}.json").write_text(json.dumps(data))


def write_readme(path, block) -> None:
    path.write_text(f"# Title\n\n## Evaluation\n\n{BEGIN}\n{block}\n{END}\n\nMore text.\n")


@pytest.fixture
def repo(tmp_path):
    """A README and a results folder, the README up to date with one full run."""
    results_dir = tmp_path / "results"
    write_results(results_dir, results())
    path = tmp_path / "README.md"
    write_readme(path, score_block(results()))
    return path, results_dir


# --- The committed README ---


def test_the_committed_readme_equals_the_newest_results_file():
    assert check(readme.README, readme.RESULTS) == []


# --- The newest results file ---


def test_the_newest_results_file_is_the_latest_date(tmp_path):
    for day in ("2026-11-06", "2027-01-02", "2026-12-31"):
        write_results(tmp_path, results(day))
    (tmp_path / "notes.json").write_text("{}")

    assert newest_results(tmp_path) == tmp_path / "2027-01-02.json"


def test_there_is_no_newest_results_file_without_one(tmp_path):
    assert newest_results(tmp_path) is None
    assert newest_results(tmp_path / "missing") is None


# --- The block ---


def test_the_block_is_the_runners_table_of_the_results_scores():
    block = score_block(results())

    assert "| Answer accuracy | 0.800 (24/30) |" in block
    assert "| Citation precision | 0.750 (45/60) |" in block
    assert "| Refusal accuracy | 0.925 (37/40) |" in block
    assert "| Cost | $0.0123 |" in block
    assert "| Errors | 1 |" in block


def test_the_block_says_which_run_it_is():
    block = score_block(results(day="2026-11-06", run="full", judge=True))

    assert "full run of 2026-11-06" in block
    assert "`backend/evals/results/2026-11-06.json`" in block
    assert "40 questions on 5 PDFs" in block
    assert "gpt-4o-mini" in block
    assert "judged by the same model" in block
    assert "judged by the same model" not in score_block(results(judge=False))
    assert "subset run" in score_block(results(run="subset"))


def test_an_unknown_cost_and_an_empty_ratio_read_as_in_the_runner():
    scores = dict(SCORES, cost_usd=None)
    scores["citation_precision"] = {"score": None, "hits": 0, "total": 0}

    block = score_block(results(scores=scores))

    assert "| Cost | unknown |" in block
    assert "| Citation precision | n/a |" in block


def test_without_results_the_block_says_the_table_is_not_published():
    assert score_block(None) == NOT_PUBLISHED


def test_scores_read_back_from_json_equal_the_scores_written():
    scores = Scores(Ratio(24, 30), Ratio(0, 0), Ratio(37, 40), None, 2)

    assert Scores.from_json(scores.as_json()) == scores


# --- The check ---


def test_the_check_passes_when_the_readme_equals_the_newest_results(repo):
    assert check(*repo) == []


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("0.800 (24/30)", "0.900 (24/30)"),
        ("0.925 (37/40)", "0.925 (38/40)"),
        ("$0.0123", "$0.0120"),
        ("| Errors | 1 |", "| Errors | 0 |"),
        ("40 questions", "41 questions"),
    ],
)
def test_the_check_fails_when_a_number_in_the_readme_is_edited(repo, before, after):
    path, results_dir = repo
    text = path.read_text()
    assert before in text
    path.write_text(text.replace(before, after))

    problems = check(path, results_dir)

    assert problems
    assert "2026-11-06.json" in "\n".join(problems)


def test_the_check_fails_when_a_newer_results_file_lands(repo):
    path, results_dir = repo
    newer = dict(SCORES, errors=0)
    write_results(results_dir, results(day="2026-11-07", scores=newer))

    assert check(path, results_dir)


def test_the_check_fails_when_a_table_is_shown_without_a_results_file(repo, tmp_path):
    path, _ = repo

    assert check(path, tmp_path / "empty")


def test_the_check_passes_on_the_not_published_line_without_a_results_file(tmp_path):
    path = tmp_path / "README.md"
    write_readme(path, NOT_PUBLISHED)

    assert check(path, tmp_path / "empty") == []


BAD_MARKERS = [
    "no markers\n",
    f"{BEGIN}\nonly the start\n",
    f"{END}\nonly the end\n",
    f"# Title\n\n{END}\nbetween\n{BEGIN}\nafter\n",
]


@pytest.mark.parametrize("text", BAD_MARKERS)
def test_the_check_fails_when_the_markers_are_missing_or_out_of_order(tmp_path, text):
    path = tmp_path / "README.md"
    path.write_text(text)

    problems = check(path, tmp_path)

    assert len(problems) == 1
    assert "markers" in problems[0]


# --- Writing the block ---


def test_writing_replaces_only_the_block(repo):
    path, results_dir = repo
    write_readme(path, "stale")

    updated = with_block(path.read_text(), score_block(results()))

    assert updated.startswith("# Title\n\n## Evaluation\n\n")
    assert updated.endswith("\nMore text.\n")
    path.write_text(updated)
    assert check(path, results_dir) == []


def test_the_command_checks_by_default_and_writes_when_asked(repo, capsys):
    path, results_dir = repo
    write_readme(path, "stale")
    args = ["--readme", str(path), "--results", str(results_dir)]

    assert readme.main(args) == 1
    assert "--write" in capsys.readouterr().err

    assert readme.main([*args, "--write"]) == 0
    assert readme.main(args) == 0


@pytest.mark.parametrize("text", BAD_MARKERS)
def test_writing_without_both_markers_in_order_is_an_error(text):
    with pytest.raises(ValueError, match="markers"):
        with_block(text, "block")


@pytest.mark.parametrize("text", BAD_MARKERS)
def test_the_command_does_not_write_a_readme_without_both_markers_in_order(tmp_path, capsys, text):
    path = tmp_path / "README.md"
    path.write_text(text)
    write_results(tmp_path / "results", results())

    code = readme.main(["--readme", str(path), "--results", str(tmp_path / "results"), "--write"])

    assert code == 1
    assert "markers" in capsys.readouterr().err
    assert path.read_text() == text
