import pytest

from app.citation import MIN_QUOTE_CHARS, find_quote, normalise

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

    assert found("field station was", text) == "\ufb01eld station was"


def test_a_quote_across_a_line_break_hyphen_is_found():
    text = "The glacier re-\ntreated forty metres last year."

    assert found("glacier retreated forty", text) == "glacier re-\ntreated forty"


def test_a_quote_across_a_non_breaking_space_is_found():
    text = "The glacier retreated forty\u00a0metres last year."

    assert found("forty metres last", text) == "forty\u00a0metres last"
    assert found("forty\u00a0metres last", GLACIER) == "forty metres last"


# --- Each normalisation, on both sides ---


@pytest.mark.parametrize(
    ("quote", "text", "expected"),
    [
        ("THE GLACIER RETREATED", GLACIER, "The glacier retreated"),
        ("retreated  \n forty", GLACIER, "retreated forty"),
        ("retreated forty", "retreated\n\t forty", "retreated\n\t forty"),
        ("  forty metres last  ", GLACIER, "forty metres last"),
        ("glacier retreated", "glacier re\u00adtreated", "glacier re\u00adtreated"),  # soft hyphen
        (
            "glacier retreated",
            "glacier re\u200btreated",
            "glacier re\u200btreated",
        ),  # zero-width space
        ("a forty-metre drop", "a forty-\nmetre drop", "a forty-\nmetre drop"),
        ("a fortymetre drop", "a forty-metre drop", "a forty-metre drop"),
        (
            "a forty\u2011metre drop",
            "a forty-metre drop",
            "a forty-metre drop",
        ),  # non-breaking hyphen
        ("glacier re-\ntreated", "the glacier retreated", "glacier retreated"),
        ("'the station' shut", "\u2018the station\u2019 shut", "\u2018the station\u2019 shut"),
        ('"the station" shut', "\u201cthe station\u201d shut", "\u201cthe station\u201d shut"),
        ("met at the caf\u00e9", "they met at the cafe\u0301 by noon", "met at the cafe\u0301"),
        ("met at the cafe\u0301", "they met at the caf\u00e9 by noon", "met at the caf\u00e9"),
        ("WOHNT IN DER STRASSE", "er wohnt in der Stra\u00dfe", "wohnt in der Stra\u00dfe"),
        ("the \ufb01eld station", "at the field station", "the field station"),
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
    text = "she rented an of\ufb01ce"

    assert found("she rented an of", text) == "she rented an of"
    assert found("she rented an off", text) == "she rented an of\ufb01"


def test_the_first_occurrence_is_returned():
    text = "Forty metres down. Then forty metres down more."

    assert find_quote("forty metres down", text) == (0, 17)


# --- A quote too short to show what it cites ---


@pytest.mark.parametrize("quote", ["the", "glacier", "forty metres", "retreated fort"])
def test_a_quote_shorter_than_the_minimum_is_not_found(quote):
    assert quote.lower() in GLACIER.lower()
    assert len(normalise(quote)) < MIN_QUOTE_CHARS

    assert find_quote(quote, GLACIER) is None


def test_the_minimum_is_counted_on_the_normalised_quote():
    # 19 characters as given, 12 once the spaces and the soft hyphen are normalised.
    quote = "  forty   met­res  "

    assert find_quote(quote, GLACIER) is None


def test_a_quote_of_exactly_the_minimum_is_found():
    quote = "retreated forty"

    assert len(normalise(quote)) == MIN_QUOTE_CHARS == 15
    assert found(quote, GLACIER) == "retreated forty"
