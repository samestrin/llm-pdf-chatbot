"""The lowest acceptable scores of the `--subset` eval run (`evals/floors.json`).

`below_floors` names every metric under its floor, one plain line each; a `Ratio`
with nothing to count has no score and counts as under. Raising a floor is an edit
to `floors.json` in the PR that earns it. Full runs are the owner's and have no floor.
"""

from typing import Any

from evals.score import Scores

METRICS = ("answer_accuracy", "citation_precision", "refusal_accuracy")


def below_floors(scores: Scores, floors: dict[str, Any]) -> list[str]:
    """One plain line per metric under its floor, in `METRICS` order, then errors.

    `floors` holds a lowest acceptable score per metric and `max_errors`, the highest
    acceptable error count. A score at its floor passes; a `Ratio` with no score
    (`value` is `None`) counts as under."""
    ratios = {
        "answer_accuracy": scores.accuracy,
        "citation_precision": scores.citation_precision,
        "refusal_accuracy": scores.refusal_accuracy,
    }
    lines = []
    for key in METRICS:
        floor = floors[key]
        value = ratios[key].value
        if value is None:
            lines.append(f"{key} has no score (floor {floor})")
        elif value < floor:
            lines.append(f"{key} {value:.3f} is below its floor {floor}")
    if scores.errors > floors["max_errors"]:
        lines.append(f"errors {scores.errors} is above max_errors {floors['max_errors']}")
    return lines
