"""Check that a quoted claim's words are in the chunk it cites.

The model copies its quote from the passage text, but not always character for
character: a ligature such as "ﬁ" comes back as "fi", a word hyphenated across a line
comes back whole, a non-breaking space as a space. So both the quote and the chunk are
normalised the same way before the quote is looked for as a substring:

- unicode: compatibility-decomposed (NFKD: ligatures split, non-breaking spaces become
  spaces, accents become combining marks either way), typographic quotes and hyphens
  folded to ASCII, and invisible format characters (soft hyphen, zero-width space)
  dropped;
- case: case-folded;
- hyphenation: a hyphen between two letters, with or without a line break after it,
  is dropped, so "re-\\ntreated", "retreated" and "forty-metre" / "fortymetre" match;
- whitespace: each run becomes one space, and the ends are trimmed.

A quote shorter than `MIN_QUOTE_CHARS` once normalised is never found: a word or two
is a substring of nearly any chunk.

Every normalised character remembers the character of the original text it came
from, so a match maps back to an exact span of the chunk, and from there of the page.
"""

import unicodedata

# The shortest quote, normalised, that is checked at all. A word or two is a substring
# of nearly any chunk, so it would show a claim as cited without showing where from.
MIN_QUOTE_CHARS = 15

_FOLD = str.maketrans(
    {
        "\u2010": "-",  # hyphen
        "\u2011": "-",  # non-breaking hyphen
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
    }
)


def normalise(text: str) -> str:
    return "".join(c for c, _ in _normalised(text))


def find_quote(quote: str, text: str) -> tuple[int, int] | None:
    """The span `[start, end)` of `text` where `quote` is, compared normalised.

    None when the quote, normalised, is shorter than `MIN_QUOTE_CHARS` or is not a
    substring of the text.
    """
    needle = normalise(quote)
    if len(needle) < MIN_QUOTE_CHARS:
        return None
    hay = _normalised(text)
    at = "".join(c for c, _ in hay).find(needle)
    if at < 0:
        return None
    return hay[at][1], hay[at + len(needle) - 1][1] + 1


def _normalised(text: str) -> list[tuple[str, int]]:
    """The normalised text, one (character, index in `text` it came from) per character."""
    folded = [
        (c, i)
        for i, char in enumerate(text)
        for c in unicodedata.normalize("NFKD", char).casefold().translate(_FOLD)
        if unicodedata.category(c) != "Cf"
    ]
    out: list[tuple[str, int]] = []
    i = 0
    while i < len(folded):
        c, origin = folded[i]
        if c == "-" and out and _is_letter(out[-1][0]):
            after = i + 1
            while after < len(folded) and folded[after][0].isspace():
                after += 1
            if after < len(folded) and _is_letter(folded[after][0]):
                i = after
                continue
        if c.isspace():
            while i + 1 < len(folded) and folded[i + 1][0].isspace():
                i += 1
            if out:
                out.append((" ", origin))
        else:
            out.append((c, origin))
        i += 1
    if out and out[-1][0] == " ":
        out.pop()
    return out


def _is_letter(c: str) -> bool:
    # A combining mark belongs to the letter before it, as in a decomposed "é".
    return unicodedata.category(c)[0] in "LM"
