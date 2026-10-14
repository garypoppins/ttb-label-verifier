"""Field checks: compare what the application says with the text read from the label.

Every check takes plain text (the application value and the label lines in
reading order) and returns a CheckResult, so each can be tested without images.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field, replace

from rapidfuzz import fuzz

MATCH, REVIEW, MISMATCH = "match", "review", "mismatch"

CLOSE_SIMILARITY = 85.0
REVIEW_SIMILARITY = 70.0
SUBSTRING_MIN_LETTERS = 5
WORDS_WINDOW_LINES = 6
PERCENT_TOLERANCE = 0.05
VOLUME_TOLERANCE = 0.01

COULD_NOT_READ = "Could not read this on the label. Check by eye or try a clearer photo."
OTHER_SIDE = "Not on this picture. Add the other side of the label."


@dataclass(frozen=True)
class Part:
    """One piece of a check that has several pieces (used by the warning check)."""

    name: str
    status: str
    reason: str


@dataclass(frozen=True)
class CheckResult:
    """Outcome of checking one field."""

    field: str
    label: str
    status: str
    expected: str
    found: str
    reason: str
    parts: list[Part] = field(default_factory=list)
    # Which picture the value was found on (1 for the first), or None when it was not found.
    picture: int | None = None
    picture_name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def overall_status(results: Sequence[CheckResult]) -> str:
    """Worst status wins: any mismatch, else any review, else match."""
    statuses = {result.status for result in results}
    if MISMATCH in statuses:
        return MISMATCH
    return REVIEW if REVIEW in statuses else MATCH


def combine_pictures(results: Sequence[CheckResult], names: Sequence[str] = ()) -> CheckResult:
    """Merge the results for one item across the pictures of one product.

    The item counts as found if it is on any picture. Order of preference: a
    match; then a value read from a label that contradicts the application; then
    a near miss; then "could not tell"; and only when every picture read well
    and none shows the item, "not found".
    """

    def pick(index: int, **changes: str) -> CheckResult:
        result = results[index]
        located = bool(result.found) or result.status == MATCH
        name = names[index] if located and index < len(names) else ""
        return replace(result, picture=index + 1 if located else None, picture_name=name, **changes)

    if len(results) == 1:
        return pick(0)
    preferences = (
        lambda result: result.status == MATCH,
        lambda result: result.status == MISMATCH and bool(result.found),
        lambda result: result.status == REVIEW and bool(result.found),
        lambda result: result.status == REVIEW,
    )
    for wanted in preferences:
        for index, result in enumerate(results):
            if wanted(result):
                return pick(index)
    return pick(0, reason=f"Not found on any of the {len(results)} pictures.")


def squash(text: str) -> str:
    """Reduce text to lower-case letters and digits so case, accents, spacing and punctuation are ignored."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed.casefold() if char.isalnum())


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _phrases(lines: Sequence[str], low: int, high: int) -> Iterator[str]:
    """Yield runs of consecutive words, within one line or across up to three lines."""
    for start in range(len(lines)):
        for span in (1, 2, 3):
            words = " ".join(lines[start : start + span]).split()
            for first in range(len(words)):
                for last in range(first + 1, len(words) + 1):
                    phrase = " ".join(words[first:last])
                    size = len(squash(phrase))
                    if size > high:
                        break
                    if size >= low:
                        yield phrase


_TOKEN_BREAK = re.compile(r"[^\w'’]+|_")


def _loose_lines(lines: Sequence[str]) -> list[str]:
    """Lines with punctuation turned into spaces, so "KOMOS,INC." and "www.komos.com" split into words."""
    return [" ".join(_TOKEN_BREAK.split(line)) for line in lines]


def _best_phrase(target: str, lines: Sequence[str]) -> tuple[str, float]:
    """Closest run of words to the target. The first best match in reading order wins."""
    best_phrase, best_score = "", 0.0
    for phrase in _phrases(lines, low=round(len(target) * 0.6), high=round(len(target) * 1.5) + 1):
        score = fuzz.ratio(target, squash(phrase))
        if score > best_score:
            best_phrase, best_score = phrase, score
    return best_phrase, best_score


def _words_apart(expected: str, lines: Sequence[str]) -> tuple[bool, bool]:
    """Are all words of the value on the label as whole words? Returns (all present, all close together)."""
    wanted = [squash(word) for word in _TOKEN_BREAK.split(expected) if squash(word)]
    if len(wanted) < 2:
        return False, False
    where: dict[str, list[int]] = {word: [] for word in wanted}
    for index, line in enumerate(_loose_lines(lines)):
        tokens = {squash(token) for token in line.split()}
        for word in where:
            if word in tokens:
                where[word].append(index)
    if not all(where.values()):
        return False, False
    for first in sorted({index for found in where.values() for index in found}):
        if all(any(first <= index < first + WORDS_WINDOW_LINES for index in found) for found in where.values()):
            return True, True
    return True, False


def _inside_longer_text(target: str, lines: Sequence[str]) -> str:
    """Return the line that contains the value inside a longer unbroken run of letters, if any."""
    if len(target) < SUBSTRING_MIN_LETTERS:
        return ""
    for line in lines:
        if target in squash(line):
            return line
    return ""


def check_text_field(
    field_name: str,
    label: str,
    expected: str,
    lines: Sequence[str],
    readable: bool = True,
    other_side_possible: bool = False,
) -> CheckResult:
    """Check a name-like field (brand name, class/type) with tolerance for case and punctuation.

    ``other_side_possible`` is set when only one picture was sent: a value that is
    simply absent may be on the other side of the container, so it is not a mismatch.
    """
    expected = _collapse(expected)
    target = squash(expected)
    if not target:
        return CheckResult(field_name, label, REVIEW, expected, "", "The application value is empty.")

    # The first best match in reading order wins, so the brand as displayed near
    # the top of the label is reported rather than a later mention in small print.
    best_phrase, best_score = _best_phrase(target, lines)
    if best_score < 100:
        # Real labels often print the name inside punctuation ("KOMOS, INC.", "komos.com").
        loose_phrase, loose_score = _best_phrase(target, _loose_lines(lines))
        if loose_score > best_score:
            best_phrase, best_score = loose_phrase, loose_score

    if best_score == 100:
        reason = (
            "Label matches the application."
            if best_phrase == expected
            else "Same wording. Only capital letters, spacing or punctuation differ."
        )
        return CheckResult(field_name, label, MATCH, expected, best_phrase, reason)

    present, together = _words_apart(expected, lines)
    if together:
        reason = "All the words are on the label, printed on separate lines. Check the order by eye if it matters."
        return CheckResult(field_name, label, MATCH, expected, expected, reason)
    if best_score >= CLOSE_SIMILARITY:
        reason = "Close, but not the same. The label may differ or a letter may have been misread. Check by eye."
        return CheckResult(field_name, label, REVIEW, expected, best_phrase, reason)
    if present:
        reason = "All the words are on the label, but far apart. Check by eye."
        return CheckResult(field_name, label, REVIEW, expected, "", reason)
    inside = _inside_longer_text(target, lines)
    if inside:
        reason = "Found inside a longer run of letters. Check by eye that it is the same name."
        return CheckResult(field_name, label, REVIEW, expected, inside, reason)
    if best_score >= REVIEW_SIMILARITY:
        reason = "Something similar is on the label. A few letters differ or were misread. Check by eye."
        return CheckResult(field_name, label, REVIEW, expected, best_phrase, reason)
    if not readable:
        return CheckResult(field_name, label, REVIEW, expected, "", COULD_NOT_READ)
    if other_side_possible:
        return CheckResult(field_name, label, REVIEW, expected, "", OTHER_SIDE)
    return CheckResult(field_name, label, MISMATCH, expected, "", "Not found on the label.")


_ZERO_READ_AS_O = re.compile(r"(?<=\d)[oO](?![A-Za-z])")
_NUMBER = r"(\d{1,3}(?:[.,]\d{1,2})?)"
_PERCENT = re.compile(_NUMBER + r"\s*(?:%|percent)", re.IGNORECASE)
_PROOF = re.compile(_NUMBER + r"\s*°?\s*proof", re.IGNORECASE)
_BARE_NUMBER = re.compile(r"\s*" + _NUMBER + r"\s*")
_ALCOHOL_WORDS = re.compile(r"alc|vol|abv|proof", re.IGNORECASE)


def fix_digits(text: str) -> str:
    """Undo a common misread: a zero printed in an old-style font read as the letter o."""
    return _ZERO_READ_AS_O.sub("0", text)


def _number(text: str) -> float:
    return float(text.replace(",", "."))


def _plain(value: float) -> str:
    """Format a number without a trailing .0."""
    return f"{value:g}"


@dataclass(frozen=True)
class Alcohol:
    """Alcohol content as stated: percent by volume and, optionally, proof."""

    percent: float | None
    proof: float | None

    def __bool__(self) -> bool:
        return self.percent is not None or self.proof is not None


def parse_alcohol(text: str, allow_bare_number: bool = False) -> Alcohol:
    """Pull percent and proof out of text such as "45% Alc./Vol. (90 Proof)"."""
    percent = _PERCENT.search(text)
    proof = _PROOF.search(text)
    if percent is None and proof is None and allow_bare_number:
        percent = _BARE_NUMBER.fullmatch(text)
    return Alcohol(
        _number(percent.group(1)) if percent else None,
        _number(proof.group(1)) if proof else None,
    )


def check_alcohol(
    expected: str, lines: Sequence[str], readable: bool = True, other_side_possible: bool = False
) -> CheckResult:
    """Compare alcohol content by number, and check that label proof is twice the percent."""
    field_name, label = "alcohol_content", "Alcohol content"
    expected = _collapse(expected)
    want = parse_alcohol(expected, allow_bare_number=True)
    if not want:
        reason = 'Could not understand the application value. Write it like "45%" or "45% Alc./Vol. (90 Proof)".'
        return CheckResult(field_name, label, REVIEW, expected, "", reason)
    want_percent = want.percent if want.percent is not None else want.proof / 2

    fixed = [fix_digits(line) for line in lines]
    candidates: list[tuple[bool, Alcohol, str]] = []
    for index, line in enumerate(fixed):
        stated = parse_alcohol(line)
        if stated:
            nearby = " ".join(fixed[max(index - 1, 0) : index + 2])
            candidates.append((bool(_ALCOHOL_WORDS.search(nearby)), stated, line))
    if not candidates:
        reason = "No alcohol content found on the label. Check by eye."
        reason = COULD_NOT_READ if not readable else OTHER_SIDE if other_side_possible else reason
        return CheckResult(field_name, label, REVIEW, expected, "", reason)

    certain = [candidate for candidate in candidates if candidate[0]]
    pool = certain or candidates
    agreeing = [candidate for candidate in pool if _same_strength(candidate[1], want_percent)]
    _, stated, source = (agreeing or pool)[0]

    if not agreeing:
        if not certain:
            reason = "Found a percentage on the label but could not tell if it is the alcohol content. Check by eye."
            return CheckResult(field_name, label, REVIEW, expected, source, reason)
        shown = f"Label shows {_describe(stated)}. The application says {_plain(want_percent)}%."
        read_percent = stated.percent if stated.percent is not None else stated.proof / 2
        if _lost_decimal(read_percent, want_percent):
            reason = f"Read {_describe(stated)} on the label. A decimal point may have been missed. Check by eye."
            return CheckResult(field_name, label, REVIEW, expected, source, reason)
        if not readable:
            return CheckResult(field_name, label, REVIEW, expected, source, _unsure(shown))
        return CheckResult(field_name, label, MISMATCH, expected, source, shown)
    if want.proof is not None and stated.proof is not None and abs(stated.proof - want.proof) > PERCENT_TOLERANCE * 2:
        shown = f"Label shows {_plain(stated.proof)} proof. The application says {_plain(want.proof)} proof."
        if not readable:
            return CheckResult(field_name, label, REVIEW, expected, source, _unsure(shown))
        return CheckResult(field_name, label, MISMATCH, expected, source, shown)
    if stated.percent is not None and stated.proof is not None:
        if abs(stated.proof - 2 * stated.percent) > PERCENT_TOLERANCE * 2:
            reason = (
                f"The percent matches, but the label shows {_plain(stated.proof)} proof. "
                f"Proof should be twice the percent ({_plain(2 * stated.percent)})."
            )
            return CheckResult(field_name, label, REVIEW, expected, source, reason)
    return CheckResult(field_name, label, MATCH, expected, source, f"Label shows {_describe(stated)}.")


def _unsure(shown: str) -> str:
    """Wording for a difference seen on a picture that did not read cleanly."""
    return f"{shown} The picture did not read cleanly, so this may be a reading error. Check by eye."


def _lost_decimal(read: float, wanted: float) -> bool:
    """True when two numbers differ only by a decimal point, as when OCR drops it (42 for 4.2)."""
    left, right = _plain(read), _plain(wanted)
    if ("." in left) == ("." in right):
        return False
    return left.replace(".", "") == right.replace(".", "")


def _same_strength(stated: Alcohol, want_percent: float) -> bool:
    percent = stated.percent if stated.percent is not None else stated.proof / 2
    return abs(percent - want_percent) <= PERCENT_TOLERANCE


def _describe(stated: Alcohol) -> str:
    parts = []
    if stated.percent is not None:
        parts.append(f"{_plain(stated.percent)}%")
    if stated.proof is not None:
        parts.append(f"{_plain(stated.proof)} proof")
    return " and ".join(parts)


_VOLUME = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+(?:[.,]\d+)?)\s*"
    r"(m[li1!|！]|millilit(?:er|re)s?|cl|centilit(?:er|re)s?|l|lit(?:er|re)s?"
    r"|fl[.\-\s]*oz|fluid\s+ounces?|oz|ounces?)"
    r"(?![a-z])",
    re.IGNORECASE,
)
_ML_PER_UNIT = {"ml": 1.0, "cl": 10.0, "l": 1000.0, "fl oz": 29.5735}
_UNIT_PREFIXES = (
    ("ml", "ml"), ("m", "ml"), ("cl", "cl"), ("centil", "cl"), ("fl", "fl oz"), ("o", "fl oz"), ("l", "l"),
)
# A serving size ("Serving size: 1.5 fl. oz. (44 mL)") is not the net contents.
_SERVING = re.compile(r"ser\w?ing|per\s+container", re.IGNORECASE)
# The word alone, as on a neck band that says "LITER", means one litre.
_LITRE_WORD = re.compile(r"(?<![\w.,])(?:one\s+)?lit(?:er|re)(?![a-z])", re.IGNORECASE)


@dataclass(frozen=True)
class Volume:
    """A net contents statement converted to millilitres."""

    millilitres: float
    unit: str
    text: str

    @property
    def amount(self) -> float:
        """The number as printed, in its own unit."""
        return round(self.millilitres / _ML_PER_UNIT[self.unit], 4)


def parse_volumes(text: str) -> list[Volume]:
    """Find every amount with a volume unit, e.g. "750 mL", "75 cl", "1.75 L", "12 FL OZ"."""
    volumes = []
    for match in _VOLUME.finditer(text):
        digits, unit_text = match.group(1), re.sub(r"[^a-z]", "", match.group(2).lower()) or "m"
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", digits):
            amount = float(digits.replace(",", ""))
        else:
            amount = _number(digits)
        unit = next(name for prefix, name in _UNIT_PREFIXES if unit_text.startswith(prefix))
        volumes.append(Volume(amount * _ML_PER_UNIT[unit], unit, _collapse(match.group(0))))
    return volumes


def check_net_contents(
    expected: str, lines: Sequence[str], readable: bool = True, other_side_possible: bool = False
) -> CheckResult:
    """Compare net contents by amount after converting units."""
    field_name, label = "net_contents", "Net contents"
    expected = _collapse(expected)
    wanted = parse_volumes(expected)
    if not wanted:
        reason = 'Could not understand the application value. Write the amount with its unit, like "750 mL".'
        return CheckResult(field_name, label, REVIEW, expected, "", reason)
    want = wanted[0]

    fixed = [fix_digits(line) for line in lines]
    stated = [volume for line in fixed if not _SERVING.search(line) for volume in parse_volumes(line)]
    for volume in stated:
        if abs(volume.millilitres - want.millilitres) <= VOLUME_TOLERANCE * want.millilitres:
            reason = (
                "Label matches the application."
                if volume.unit == want.unit
                else f"Same amount in different units ({volume.text} is {want.text})."
            )
            return CheckResult(field_name, label, MATCH, expected, volume.text, reason)
    if not stated:
        word = next((match.group(0) for line in fixed if (match := _LITRE_WORD.search(line))), "")
        if word and abs(want.millilitres - 1000.0) <= VOLUME_TOLERANCE * 1000.0:
            reason = f'Label says "{word}" with no number, which means one litre.'
            return CheckResult(field_name, label, MATCH, expected, word, reason)
        if word:
            reason = f'Label says "{word}" with no number. The application says {want.text}. Check by eye.'
            return CheckResult(field_name, label, REVIEW, expected, word, reason)
        reason = "No net contents found on the label. Check by eye."
        reason = COULD_NOT_READ if not readable else OTHER_SIDE if other_side_possible else reason
        return CheckResult(field_name, label, REVIEW, expected, "", reason)

    shown = f"Label shows {stated[0].text}. The application says {want.text}."
    if any(_lost_decimal(volume.amount, want.millilitres / _ML_PER_UNIT[volume.unit]) for volume in stated):
        reason = f"Read {stated[0].text} on the label. A decimal point may have been missed. Check by eye."
        return CheckResult(field_name, label, REVIEW, expected, stated[0].text, reason)
    if not readable:
        return CheckResult(field_name, label, REVIEW, expected, stated[0].text, _unsure(shown))
    return CheckResult(field_name, label, MISMATCH, expected, stated[0].text, shown)
