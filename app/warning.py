"""Government health warning check.

The required wording is fixed by 27 CFR 16.21. The check has three parts:
the wording (word for word), the heading in capital letters, and the heading
in bold type (27 CFR 16.22). Bold is estimated from the image and is the least
certain part, so it can only ever produce "match" or "review".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher

import cv2
import numpy as np
from rapidfuzz import fuzz

from app.checks import COULD_NOT_READ, MATCH, MISMATCH, REVIEW, CheckResult, Part, overall_status
from app.ocr import Line, OcrResult, Rect, crop_rect

STATUTORY_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink "
    "alcoholic beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)
HEADING = "GOVERNMENT WARNING:"

FIELD, LABEL = "government_warning", "Government warning"

HEADING_SIMILARITY = 85.0
BODY_OPENING = "accordingtothesurgeongeneral"
SHORT_READ_ALLOWANCE = 5
MAJOR_CHANGE_SHARE = 1 / 3
MAJOR_CHANGE_LETTERS = 3
TITLE_CASE_LETTERS = 4
# Text missing right where one read line ends and the next begins is treated as
# "not read" (the reader skipped a line or clipped its edge), not as rewording.
UNREAD_GAP_LETTERS = 3
LINE_EDGE_REACH = 2
NOT_ON_THIS_PICTURE = "No government warning on this picture. Add the other side of the label."

BOLD_RATIO = 1.25
NOT_BOLD_RATIO = 1.10
BOLD_WIDTH_FACTOR = 1.4
SAME_SIZE_LIMIT = 1.25
STROKE_UPSCALE = 4
MIN_INK_PIXELS = 200

# Letters and digits that OCR commonly swaps. Both texts go through the same
# mapping, so a swap is ignored while any other change still shows up.
_LOOKALIKES = str.maketrans({"l": "1", "i": "1", "o": "0"})


def _key(text: str) -> str:
    """Lower-case letters and digits only, with look-alike characters merged."""
    return "".join(char for char in text.lower() if char.isalnum()).translate(_LOOKALIKES)


@dataclass(frozen=True)
class _Stream:
    """Words of a text plus one long string of their comparable characters."""

    words: list[str]
    chars: str
    owner: list[int]

    @classmethod
    def of(cls, text: str) -> _Stream:
        words = [word for word in text.split() if _key(word)]
        owner = [index for index, word in enumerate(words) for _ in _key(word)]
        return cls(words, "".join(_key(word) for word in words), owner)

    def word_at(self, position: int) -> str:
        """Word that owns a character position, without surrounding punctuation."""
        if not self.words:
            return ""
        word = self.words[self.owner[min(position, len(self.owner) - 1)]]
        return word.strip(".,;:!?\"'")


_REQUIRED = _Stream.of(STATUTORY_WARNING)
_HEADING_KEY = _key(HEADING)
_HEADING_LETTERS = sum(char.isalpha() for char in HEADING)


def _find_start(lines: Sequence[str]) -> tuple[int, int, bool] | None:
    """Locate the warning: (line index, character offset in that line, heading present).

    Looks for the heading first, then for the opening words of the body in case
    the heading is absent. Each is tried on single lines, then on pairs of lines
    because the heading is sometimes split in two.
    """
    for needle, has_heading in ((_HEADING_KEY, True), (_key(BODY_OPENING), False)):
        for span in (1, 2):
            for index in range(len(lines)):
                joined = " ".join(lines[index : index + span])
                if len(_key(joined)) < len(needle):
                    continue
                alignment = fuzz.partial_ratio_alignment(needle, _key(joined))
                if alignment.score >= HEADING_SIMILARITY:
                    offset = min(_raw_offset(joined, alignment.dest_start), len(lines[index]))
                    return index, offset, has_heading
    return None


def _raw_offset(text: str, key_offset: int) -> int:
    """Convert an offset in the comparable characters back to an offset in the raw text."""
    seen = 0
    for position, char in enumerate(text):
        if char.isalnum():
            if seen == key_offset:
                return position
            seen += 1
    return len(text)


_ENDING_KEY = _key("problems")


def _collect(lines: Sequence[str], start: int, offset: int) -> list[str]:
    """Pieces of the warning, one per read line, from its start to "problems." or until enough text is gathered."""
    needed = len(_REQUIRED.chars) - SHORT_READ_ALLOWANCE
    pieces: list[str] = []
    gathered = 0
    for piece in [lines[start][offset:], *lines[start + 1 :]]:
        if gathered >= needed:
            break
        end = _key(piece).find(_ENDING_KEY)
        if end >= 0 and gathered + end >= needed // 2:
            cut = _raw_offset(piece, end + len(_ENDING_KEY) - 1) + 1
            pieces.append(piece[:cut] + ("." if piece[cut : cut + 1] == "." else ""))
            break
        pieces.append(piece)
        gathered += len(_key(piece))
    return [piece.strip() for piece in pieces if piece.strip()]


def _line_edges(pieces: Sequence[str]) -> list[int]:
    """Positions in the comparable characters where one read line ends and the next begins."""
    edges, position = [], 0
    for piece in pieces:
        position += len(_Stream.of(piece).chars)
        edges.append(position)
    return edges


def compare_wording(found_text: str, line_edges: Sequence[int] | None = None) -> Part:
    """Compare label text with the required wording and name the first word that differs.

    ``line_edges`` says where the read lines begin and end. When it is given,
    required text that is missing exactly at such an edge, or after the last
    line, counts as "not read" (needs review) instead of as changed wording.
    """
    found = _Stream.of(found_text)
    changed = [0] * len(_REQUIRED.words)
    dropped = [0] * len(_REQUIRED.words)
    seen_instead: dict[int, str] = {}
    unread: list[int] = []
    trailing = ""
    edges = None if line_edges is None else [*line_edges, len(found.chars)]
    matcher = SequenceMatcher(None, _REQUIRED.chars, found.chars, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if i1 == len(_REQUIRED.chars):
            trailing = found.word_at(j1)
            continue
        touched = [_REQUIRED.owner[position] for position in range(i1, i2)] or [_REQUIRED.owner[i1]]
        missing = (i2 - i1) - (j2 - j1)
        if edges is not None and missing >= UNREAD_GAP_LETTERS:
            if any(abs(j1 - edge) <= LINE_EDGE_REACH or abs(j2 - edge) <= LINE_EDGE_REACH for edge in edges):
                unread.append(touched[0])
                continue
        for word in touched[: i2 - i1]:
            changed[word] += 1
            dropped[word] += tag == "delete"
        changed[touched[-1]] += max((j2 - j1) - (i2 - i1), 0)
        for word in touched:
            seen_instead.setdefault(word, found.word_at(j1) if j2 > j1 else "")

    keys = [_key(word) for word in _REQUIRED.words]
    major = [index for index, key in enumerate(keys) if _is_major(key, changed[index], dropped[index])]
    minor = [index for index, count in enumerate(changed) if count]
    if major:
        return Part("wording", MISMATCH, _difference(major[0], seen_instead, certain=True))
    if unread:
        word = _REQUIRED.word_at(_REQUIRED.owner.index(unread[0]))
        reason = (
            f'Part of the warning was not read, starting near the word "{word}". '
            "Check by eye or try a clearer photo."
        )
        return Part("wording", REVIEW, reason)
    if minor:
        return Part("wording", REVIEW, _difference(minor[0], seen_instead, certain=False))
    if trailing:
        reason = f'The wording matches, but extra text follows it ("{trailing}"). Check by eye.'
        return Part("wording", REVIEW, reason)
    return Part("wording", MATCH, "The wording matches the required text word for word.")


def _is_major(key: str, changed: int, dropped: int) -> bool:
    """Decide whether a word was really changed, as opposed to possibly misread.

    A single wrong character is treated as a possible misread (review). Several
    wrong characters, or a whole word gone, is a real difference (mismatch). The
    list numbers "(1)" and "(2)" are one character each and are often misread,
    so on their own they only ever lead to review.
    """
    if key.isdigit():
        return False
    if changed >= MAJOR_CHANGE_LETTERS or dropped == len(key):
        return True
    return changed >= 2 and changed / len(key) >= MAJOR_CHANGE_SHARE


def _difference(word_index: int, seen_instead: dict[int, str], certain: bool) -> str:
    required = _REQUIRED.word_at(_REQUIRED.owner.index(word_index))
    seen = seen_instead.get(word_index, "")
    if certain and seen.lower() == required.lower():
        return f'The wording is different near the word "{required}". There is extra or repeated text.'
    if certain and seen:
        return f'The wording is different. Required word: "{required}". The label reads: "{seen}".'
    if certain:
        return f'The wording is different. The required word "{required}" is missing.'
    read_as = f' (read as "{seen}")' if seen else ""
    return f'The word "{required}" did not read cleanly{read_as}. This may be a reading error. Check by eye.'


def check_capitals(found_text: str) -> Part:
    """The heading must be printed in capital letters."""
    letters = [char for char in found_text if char.isalpha()][:_HEADING_LETTERS]
    lower = sum(char.islower() for char in letters)
    shown = " ".join(found_text.split()[:2])[: len(HEADING) + 4]
    if lower == 0:
        return Part("capitals", MATCH, "The heading is in capital letters.")
    if lower >= TITLE_CASE_LETTERS:
        reason = f'The heading must be in capital letters: "{HEADING}". The label reads "{shown}".'
        return Part("capitals", MISMATCH, reason)
    reason = f'Could not confirm the heading is all capital letters (read as "{shown}"). Check by eye.'
    return Part("capitals", REVIEW, reason)


def _bold_part(bold: bool | None) -> Part:
    if bold is True:
        return Part("bold", MATCH, "The heading appears to be in bold type.")
    if bold is False:
        return Part("bold", REVIEW, "The heading does not look bold. Check by eye.")
    return Part("bold", REVIEW, "Could not confirm the heading is in bold type. Check by eye.")


def check_warning_text(
    lines: Sequence[str],
    readable: bool = True,
    bold: bool | None = None,
    other_side_possible: bool = False,
) -> CheckResult:
    """Check the warning using label text only. ``bold`` is the result of the image estimate, if any.

    ``other_side_possible`` is set when only one picture was sent: a warning that
    is simply absent may be on the other side of the container.
    """
    start = _find_start(lines)
    if start is None:
        if not readable:
            return CheckResult(FIELD, LABEL, REVIEW, STATUTORY_WARNING, "", COULD_NOT_READ)
        if other_side_possible:
            return CheckResult(FIELD, LABEL, REVIEW, STATUTORY_WARNING, "", NOT_ON_THIS_PICTURE)
        reason = "No government warning found in this picture. If it is on another label, check that picture."
        return CheckResult(FIELD, LABEL, MISMATCH, STATUTORY_WARNING, "", reason)

    index, offset, has_heading = start
    pieces = _collect(lines, index, offset)
    found = " ".join(pieces)
    if not has_heading:
        reason = f'The warning text is there, but the heading "{HEADING}" is missing or could not be read.'
        status = MISMATCH if readable else REVIEW
        return CheckResult(FIELD, LABEL, status, STATUTORY_WARNING, found, reason)

    parts = [compare_wording(found, _line_edges(pieces)), check_capitals(found), _bold_part(bold)]
    if not readable:
        # On a poor read a difference may be a reading error, so it is never reported as wrong.
        parts = [replace(part, status=REVIEW) if part.status == MISMATCH else part for part in parts]
    status = overall_status(parts)
    if status == MATCH:
        reason = "The wording matches word for word. The heading is in capital letters and appears bold."
    else:
        reason = next(part.reason for part in parts if part.status == status)
        if not readable:
            reason = "The warning did not read cleanly. " + reason + " Check by eye or try a clearer photo."
    return CheckResult(FIELD, LABEL, status, STATUTORY_WARNING, found, reason, parts)


def check_warning(ocr: OcrResult, other_side_possible: bool = False) -> CheckResult:
    """Check the warning on a label that has been read, including the bold estimate."""
    start = _find_start(ocr.texts)
    if start is None or not start[2]:
        return check_warning_text(ocr.texts, ocr.readable, other_side_possible=other_side_possible)
    block = _block_lines(ocr.lines, start[0])
    bold = estimate_bold(ocr, block)
    readable = ocr.readable and not _has_split_lines(block)
    return check_warning_text([line.text for line in block], readable, bold)


def warning_found(result: CheckResult) -> bool:
    """True when some warning text was located on the picture, whatever its status."""
    return bool(result.found)


def _block_lines(lines: Sequence[Line], start: int) -> list[Line]:
    """Lines of the warning paragraph: beside or below the heading, in its column, without a large gap."""
    block = [lines[start]]
    height = lines[start].height
    for line in lines[start + 1 :]:
        last = block[-1]
        if abs(_middle(line) - _middle(last)) < 0.5 * height:
            joins = -height <= line.rect[0] - last.rect[2] < 2 * height
        else:
            left = min(item.rect[0] for item in block)
            right = max(item.rect[2] for item in block)
            in_column = line.rect[0] < right and line.rect[2] > left
            joins = in_column and line.rect[1] - last.rect[3] < 1.5 * height
        if joins:
            block.append(line)
    return block


def _has_split_lines(block: Sequence[Line]) -> bool:
    """True when the detector cut one printed line into overlapping boxes, which garbles the seam."""
    return any(
        abs(_middle(line) - _middle(last)) < 0.5 * last.height and line.rect[0] < last.rect[2]
        for last, line in zip(block, block[1:])
    )


def _middle(line: Line) -> float:
    return (line.rect[1] + line.rect[3]) / 2


def estimate_bold(ocr: OcrResult, block: Sequence[Line]) -> bool | None:
    """Estimate whether the heading is bold. None means it could not be decided."""
    ratio = heading_stroke_ratio(ocr, block)
    if ratio is None:
        return None
    if ratio >= BOLD_RATIO:
        return True
    return False if ratio <= NOT_BOLD_RATIO else None


def heading_stroke_ratio(ocr: OcrResult, block: Sequence[Line]) -> float | None:
    """Stroke thickness of the heading divided by that of the body text.

    Returns None when there is nothing reliable to compare, for example when the
    heading is printed larger than the body or the text is too small.
    """
    first = block[0]
    left, top, right, bottom = first.rect
    letters = len(_key(first.text))
    heading_rect: Rect = first.rect
    body_rects: list[Rect] = [line.rect for line in block[1:] if _middle(line) > bottom][:2]
    if letters > len(_HEADING_KEY) + 2:
        heading_width = len(_HEADING_KEY) * BOLD_WIDTH_FACTOR
        share = heading_width / (heading_width + letters - len(_HEADING_KEY))
        width = right - left
        heading_rect = (left, top, left + 0.85 * share * width, bottom)
        body_rects.insert(0, (left + min(1.15 * share, 0.95) * width, top, right, bottom))
    elif body_rects and first.height > SAME_SIZE_LIMIT * (body_rects[0][3] - body_rects[0][1]):
        return None

    heading = _stroke_width(crop_rect(ocr, heading_rect))
    body = [width for width in (_stroke_width(crop_rect(ocr, rect)) for rect in body_rects) if width]
    if not heading or not body:
        return None
    return heading / float(np.median(body))


def _stroke_width(crop: np.ndarray) -> float | None:
    """Average thickness of the pen strokes in a text crop, in enlarged pixels."""
    grey = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    grey = cv2.resize(grey, None, fx=STROKE_UPSCALE, fy=STROKE_UPSCALE, interpolation=cv2.INTER_CUBIC)
    _, binary = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink = binary if np.count_nonzero(binary) * 2 < binary.size else cv2.bitwise_not(binary)
    area = np.count_nonzero(ink)
    if area < MIN_INK_PIXELS:
        return None
    inside = cv2.erode(ink, cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3)))
    outline = area - np.count_nonzero(inside)
    return 2.0 * area / outline if outline else None
