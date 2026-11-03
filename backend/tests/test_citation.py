import pytest

from app.citation import find_quote, normalise

GLACIER = "The glacier retreated forty metres last year."


def found(quote: str, text: str) -> str | None:
    span = find_quote(quote, text)
    return None if span is None else text[span[0] : span[1]]


# --- The task's cases ---


def test_a_planted_fake_quote_is_not_found():
    assert find_quote("retreated fifty metres", GLACIER) is None
    assert find_quote("The glacier advanced", GLACIER) is None


def test_a_quote_across_a_ligature_is_found():
    text = "The \ufb01eld station was cut in half."

    assert found("field station", text) == "\ufb01eld station"


def test_a_quote_across_a_line_break_hyphen_is_found():
    text = "The glacier re-\ntreated forty metres last year."

    assert found("glacier retreated forty", text) == "glacier re-\ntreated forty"


def test_a_quote_across_a_non_breaking_space_is_found():
    text = "The glacier retreated forty\u00a0metres last year."

    assert found("forty metres", text) == "forty\u00a0metres"
    assert found("forty\u00a0metres", GLACIER) == "forty metres"


# --- Each normalisation, on both sides ---


@pytest.mark.parametrize(
    ("quote", "text", "expected"),
    [
        ("THE GLACIER", GLACIER, "The glacier"),
        ("retreated  \n forty", GLACIER, "retreated forty"),
        ("retreated forty", "retreated\n\t forty", "retreated\n\t forty"),
        ("  forty metres  ", GLACIER, "forty metres"),
        ("retreated", "re\u00adtreated", "re\u00adtreated"),  # soft hyphen
        ("retreated", "re\u200btreated", "re\u200btreated"),  # zero-width space
        ("forty-metre", "a forty-\nmetre drop", "forty-\nmetre"),
        ("fortymetre", "a forty-metre drop", "forty-metre"),
        ("forty\u2011metre", "a forty-metre drop", "forty-metre"),  # non-breaking hyphen
        ("re-\ntreated", "retreated", "retreated"),
        ("'the station'", "\u2018the station\u2019", "\u2018the station\u2019"),
        ('"the station"', "\u201cthe station\u201d", "\u201cthe station\u201d"),
        ("caf\u00e9", "the cafe\u0301 shut", "cafe\u0301"),
        ("cafe\u0301", "the caf\u00e9 shut", "caf\u00e9"),
        ("STRASSE", "die Stra\u00dfe", "Stra\u00dfe"),
        ("\ufb01eld", "the field", "field"),
    ],
)
def test_normalised_forms_match(quote, text, expected):
    assert found(quote, text) == expected


def test_a_dash_between_digits_is_kept():
    assert normalise("1990\u20132000") != normalise("19902000")
    assert normalise("1990-2000") != normalise("19902000")


def test_a_hyphen_before_a_space_and_a_digit_is_kept():
    assert normalise("minus -\n4") == "minus - 4"


def test_normalise_folds_case_whitespace_and_compatibility_forms():
    assert normalise("  The\u00a0\ufb01eld\n\nRE-\nTREATED  ") == "the field retreated"


# --- What is not a match ---


@pytest.mark.parametrize("quote", ["", "   ", "\u00ad", "\n\u200b"])
def test_an_empty_quote_is_not_found(quote):
    assert find_quote(quote, GLACIER) is None


def test_a_quote_longer_than_the_text_is_not_found():
    assert find_quote(GLACIER + " And more.", GLACIER) is None


def test_a_quote_that_differs_by_one_letter_is_not_found():
    assert find_quote("retreated forty meters", GLACIER) is None


def test_the_span_ends_after_the_whole_ligature_the_quote_ends_in():
    text = "an of\ufb01ce"

    assert found("an of", text) == "an of"
    assert found("an off", text) == "an of\ufb01"


def test_the_first_occurrence_is_returned():
    text = "Forty metres. Then forty metres more."

    assert find_quote("forty metres", text) == (0, 12)
