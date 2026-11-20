"""The eval floors: `evals.floors.below_floors` and the `--subset` gate in `evals.run.main`.

The committed `floors.json` holds the measured subset scores rounded down to two
decimals (0.66, 0.83, 0.74 from 18/27, 20/24, 26/35) and `max_errors: 0`, so a subset
run at today's scores passes and any drop names its metric and exits 1.
"""

import json
from types import SimpleNamespace

import pytest

from evals import run as eval_run
from evals.floors import below_floors
from evals.questions import load_questions
from evals.run import main
from evals.score import Ratio, Scores
from evals.sources import EVALS

AT_FLOORS = {
    "answer_accuracy": 0.66,
    "citation_precision": 0.83,
    "refusal_accuracy": 0.74,
    "max_errors": 0,
}
RATIO_KEYS = ("answer_accuracy", "citation_precision", "refusal_accuracy")


def scores(
    accuracy: Ratio = Ratio(66, 100),
    citation: Ratio = Ratio(83, 100),
    refusal: Ratio = Ratio(74, 100),
    errors: int = 0,
) -> Scores:
    return Scores(accuracy, citation, refusal, cost=None, errors=errors)


FIELD = {
    "answer_accuracy": "accuracy",
    "citation_precision": "citation",
    "refusal_accuracy": "refusal",
}


def under(key: str, ratio: Ratio | None = None) -> Scores:
    """Scores at the floors except one metric just under its floor."""
    just_under = {
        "answer_accuracy": Ratio(6599, 10000),
        "citation_precision": Ratio(8299, 10000),
        "refusal_accuracy": Ratio(7399, 10000),
    }
    return scores(**{FIELD[key]: ratio if ratio is not None else just_under[key]})


# --- below_floors, both ways ---


def test_scores_at_the_floors_give_no_lines():
    assert below_floors(scores(), AT_FLOORS) == []


def test_scores_above_the_floors_give_no_lines():
    assert below_floors(scores(Ratio(1, 1), Ratio(1, 1), Ratio(1, 1)), AT_FLOORS) == []


@pytest.mark.parametrize("key", RATIO_KEYS)
def test_a_score_just_under_its_floor_returns_one_line_naming_the_metric(key):
    lines = below_floors(under(key), AT_FLOORS)

    assert len(lines) == 1
    assert key in lines[0]
    assert str(AT_FLOORS[key]) in lines[0]


@pytest.mark.parametrize("key", RATIO_KEYS)
def test_a_ratio_with_no_score_counts_as_under(key):
    lines = below_floors(under(key, Ratio(0, 0)), AT_FLOORS)

    assert len(lines) == 1
    assert key in lines[0]


def test_errors_above_max_errors_return_a_line_naming_them():
    lines = below_floors(scores(errors=1), AT_FLOORS)

    assert len(lines) == 1
    assert "max_errors" in lines[0]
    assert "1" in lines[0]


def test_errors_at_max_errors_give_no_line():
    assert below_floors(scores(errors=0), AT_FLOORS) == []


def test_every_metric_under_its_floor_gets_its_own_plain_line():
    low = scores(Ratio(0, 1), Ratio(0, 1), Ratio(0, 1), errors=2)

    lines = below_floors(low, AT_FLOORS)

    assert len(lines) == 4
    for key, line in zip([*RATIO_KEYS, "max_errors"], lines, strict=True):
        assert key in line
        assert "\n" not in line


def test_the_committed_floors_file_holds_the_four_settings():
    floors = json.loads((EVALS / "floors.json").read_text())

    assert set(AT_FLOORS) <= set(floors)
    for key in RATIO_KEYS:
        assert 0.0 <= floors[key] <= 1.0
    assert isinstance(floors["max_errors"], int)
    assert floors["max_errors"] >= 0


# --- The gate in main ---


def test_subset_run_just_under_a_floor_names_the_metric_and_exits_1(
    postgres_url, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    monkeypatch.delenv("OCR_URL", raising=False)
    # The measured accuracy is 18/27 = 0.6667: a floor of 0.67 is just above it.
    floors = AT_FLOORS | {"answer_accuracy": 0.67}
    floors_path = tmp_path / "floors.json"
    floors_path.write_text(json.dumps(floors))
    monkeypatch.setattr(eval_run, "FLOORS", floors_path)
    out = tmp_path / "out"

    assert main(["--subset", "--out", str(out)]) == 1

    captured = capsys.readouterr()
    # The table and the results file are written before the exit code turns to 1.
    assert "| Measure | Score |" in captured.out
    saved = json.loads(next(out.iterdir()).read_text())
    assert saved["run"] == "subset"
    assert saved["scores"]["errors"] == 0
    err_lines = captured.err.splitlines()
    assert len(err_lines) == 1
    assert "answer_accuracy" in err_lines[0]
    assert "0.67" in err_lines[0]


def test_subset_run_at_the_committed_floors_exits_0_with_no_floor_lines(
    postgres_url, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    monkeypatch.delenv("OCR_URL", raising=False)

    assert main(["--subset", "--out", str(tmp_path / "out")]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "| Measure | Score |" in captured.out


def test_a_full_run_never_reads_the_floors_file(postgres_url, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    monkeypatch.delenv("OCR_URL", raising=False)
    # Reading this path would raise; a full run must not touch it. The fake model
    # stands in for the real one, and one text-PDF question keeps OCR out of it.
    monkeypatch.setattr(eval_run, "FLOORS", tmp_path / "absent-floors.json")
    monkeypatch.setattr(eval_run, "LLMClient", lambda config: eval_run.FakeModel())
    monkeypatch.setattr(
        eval_run, "LLMConfig", SimpleNamespace(from_env=lambda: eval_run.FakeModel.config)
    )
    monkeypatch.setattr(eval_run, "load_questions", lambda: load_questions()[:1])
    out = tmp_path / "out"

    assert main(["--out", str(out)]) == 0

    captured = capsys.readouterr()
    assert "floor" not in captured.err
    assert json.loads(next(out.iterdir()).read_text())["run"] == "full"
