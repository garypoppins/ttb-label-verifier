"""Unit tests for the government warning check, using text only."""

from __future__ import annotations

import numpy as np

from app.checks import MATCH, MISMATCH, REVIEW
from app.ocr import Line, OcrResult
from app.warning import (
    STATUTORY_WARNING,
    _block_lines,
    _has_split_lines,
    _stroke_width,
    check_capitals,
    check_warning_text,
    compare_wording,
)

# The wording as published in 27 CFR 16.21, typed out again here so that an
# accidental edit to the constant in the app is caught.
CFR_TEXT = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink alcoholic "
    "beverages during pregnancy because of the risk of birth defects. (2) Consumption of alcoholic "
    "beverages impairs your ability to drive a car or operate machinery, and may cause health problems."
)

WARNING_LINES = [
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women",
    "should not drink alcoholic beverages during pregnancy because of the risk",
    "of birth defects. (2) Consumption of alcoholic beverages impairs your",
    "ability to drive a car or operate machinery, and may cause health",
    "problems.",
]
LABEL_LINES = ["OLD TOM", "DISTILLERY", "45% Alc./Vol. (90 Proof)", "750 mL", *WARNING_LINES]


def replaced(old: str, new: str) -> list[str]:
    lines = [line.replace(old, new) for line in WARNING_LINES]
    assert lines != WARNING_LINES
    return lines


def test_statutory_text_is_exact() -> None:
    assert STATUTORY_WARNING == CFR_TEXT


class TestWording:
    def test_exact_text_matches(self) -> None:
        assert compare_wording(STATUTORY_WARNING).status == MATCH

    def test_punctuation_spacing_and_case_noise_is_tolerated(self) -> None:
        noisy = "GOVERNMENT WARNING (1)According to the SurgeonGeneral women should not drink alcoholic"
        noisy += STATUTORY_WARNING.split("alcoholic", 1)[1].upper().replace(",", "")
        assert compare_wording(noisy).status == MATCH

    def test_lookalike_characters_are_tolerated(self) -> None:
        assert compare_wording(STATUTORY_WARNING.replace("(1)", "(l)").replace("ability", "abi1ity")).status == MATCH

    def test_changed_word_is_a_mismatch_and_is_named(self) -> None:
        part = compare_wording(STATUTORY_WARNING.replace("may cause", "can cause"))
        assert part.status == MISMATCH
        assert '"may"' in part.reason and '"can"' in part.reason

    def test_missing_word_is_a_mismatch(self) -> None:
        part = compare_wording(STATUTORY_WARNING.replace("should not drink", "should drink"))
        assert part.status == MISMATCH
        assert '"not"' in part.reason and "missing" in part.reason

    def test_added_word_is_a_mismatch(self) -> None:
        assert compare_wording(STATUTORY_WARNING.replace("may cause", "may sometimes cause")).status == MISMATCH

    def test_missing_one_letter_word_is_a_mismatch(self) -> None:
        assert compare_wording(STATUTORY_WARNING.replace("drive a car", "drive car")).status == MISMATCH

    def test_one_wrong_letter_in_a_short_word_needs_review(self) -> None:
        assert compare_wording(STATUTORY_WARNING.replace("risk of birth", "risk or birth")).status == REVIEW

    def test_misread_list_number_needs_review(self) -> None:
        assert compare_wording(STATUTORY_WARNING.replace("(1)", "()")).status == REVIEW
        assert compare_wording(STATUTORY_WARNING.replace("(2)", "(?)")).status == REVIEW

    def test_short_word_swap_is_a_mismatch(self) -> None:
        assert compare_wording(STATUTORY_WARNING.replace("drive a car or", "drive a car and")).status == MISMATCH

    def test_missing_second_sentence_is_a_mismatch(self) -> None:
        assert compare_wording(STATUTORY_WARNING.split("(2)")[0]).status == MISMATCH

    def test_single_letter_slip_needs_review_never_passes(self) -> None:
        part = compare_wording(STATUTORY_WARNING.replace("pregnancy", "pregnaney"))
        assert part.status == REVIEW
        assert '"pregnancy"' in part.reason

    def test_extra_text_after_the_end_needs_review(self) -> None:
        assert compare_wording(STATUTORY_WARNING + " Enjoy responsibly").status == REVIEW


class TestCapitals:
    def test_capitals_pass(self) -> None:
        assert check_capitals(STATUTORY_WARNING).status == MATCH

    def test_title_case_is_a_mismatch(self) -> None:
        part = check_capitals(STATUTORY_WARNING.replace("GOVERNMENT WARNING", "Government Warning"))
        assert part.status == MISMATCH
        assert "capital letters" in part.reason

    def test_single_misread_letter_needs_review(self) -> None:
        assert check_capitals(STATUTORY_WARNING.replace("WARNING", "WARNiNG")).status == REVIEW


class TestWarningText:
    def test_correct_warning_with_bold_confirmed(self) -> None:
        result = check_warning_text(LABEL_LINES, bold=True)
        assert result.status == MATCH
        assert [part.status for part in result.parts] == [MATCH, MATCH, MATCH]
        assert result.found.startswith("GOVERNMENT WARNING:")
        assert result.found.endswith("problems.")

    def test_bold_not_confirmed_needs_review(self) -> None:
        result = check_warning_text(LABEL_LINES, bold=None)
        assert result.status == REVIEW
        assert "bold" in result.reason

    def test_heading_that_does_not_look_bold_needs_review(self) -> None:
        result = check_warning_text(LABEL_LINES, bold=False)
        assert result.status == REVIEW
        assert "does not look bold" in result.reason

    def test_title_case_heading_is_a_mismatch(self) -> None:
        result = check_warning_text(replaced("GOVERNMENT WARNING:", "Government Warning:"), bold=True)
        assert result.status == MISMATCH
        assert "capital letters" in result.reason

    def test_reworded_warning_is_a_mismatch(self) -> None:
        result = check_warning_text(replaced("may cause health", "can cause health"), bold=True)
        assert result.status == MISMATCH
        assert '"may"' in result.reason

    def test_missing_warning_on_a_good_read_is_a_mismatch(self) -> None:
        result = check_warning_text(LABEL_LINES[:4])
        assert result.status == MISMATCH
        assert result.found == ""

    def test_missing_warning_on_a_poor_read_needs_review(self) -> None:
        result = check_warning_text(["0LD T0M"], readable=False)
        assert result.status == REVIEW
        assert "clearer photo" in result.reason

    def test_differences_on_a_poor_read_need_review_not_rejection(self) -> None:
        result = check_warning_text(replaced("may cause health", "can cause health"), readable=False, bold=True)
        assert result.status == REVIEW
        assert "did not read cleanly" in result.reason and '"may"' in result.reason

    def test_body_without_heading_is_a_mismatch(self) -> None:
        result = check_warning_text(replaced("GOVERNMENT WARNING: ", ""), bold=True)
        assert result.status == MISMATCH
        assert "heading" in result.reason

    def test_heading_split_over_two_lines(self) -> None:
        lines = ["GOVERNMENT", "WARNING: (1) According to the Surgeon General, women", *WARNING_LINES[1:]]
        assert check_warning_text(lines, bold=True).status == MATCH

    def test_text_before_and_after_the_warning_is_ignored(self) -> None:
        lines = [*LABEL_LINES, "Product of USA", "750 mL"]
        assert check_warning_text(lines, bold=True).status == MATCH

    def test_short_lines_are_not_mistaken_for_the_heading(self) -> None:
        assert check_warning_text(["I", "GOVERNOR'S RESERVE", "1"]).status == MISMATCH


def line(text: str, left: float, top: float, right: float, bottom: float) -> Line:
    return Line(text, 0.95, (left, top, right, bottom))


class TestBlockAndStroke:
    def test_block_keeps_the_paragraph_and_drops_other_columns_and_distant_text(self) -> None:
        lines = [
            line("GOVERNMENT WARNING: (1) According to", 50, 100, 500, 120),
            line("INGREDIENTS: WATER, BARLEY", 600, 100, 900, 120),
            line("the Surgeon General, women should not", 50, 125, 500, 145),
            line("HOPS, YEAST", 600, 125, 900, 145),
            line("drink alcoholic beverages", 50, 150, 400, 170),
            line("BOTTLED IN OREGON", 50, 300, 400, 320),
        ]
        block = _block_lines(lines, 0)
        assert [item.text for item in block] == [lines[0].text, lines[2].text, lines[4].text]

    def test_line_cut_into_overlapping_boxes_is_kept_and_flagged(self) -> None:
        lines = [
            line("GOVERNMENT WARNING: (1) According to the", 40, 100, 580, 130),
            line("ne Surgeon General, women", 560, 100, 880, 130),
            line("should not drink alcoholic beverages", 40, 132, 880, 162),
        ]
        block = _block_lines(lines, 0)
        assert len(block) == 3
        assert _has_split_lines(block)
        assert not _has_split_lines([lines[0], lines[2]])

    def test_thicker_strokes_measure_wider(self) -> None:
        def bars(thickness: int) -> np.ndarray:
            image = np.full((40, 200, 3), 255, dtype=np.uint8)
            for x in range(10, 190, 20):
                image[5:35, x : x + thickness] = 0
            return image

        thin, thick = _stroke_width(bars(2)), _stroke_width(bars(4))
        assert thin is not None and thick is not None
        assert 1.6 < thick / thin < 2.4

    def test_blank_crop_has_no_stroke_width(self) -> None:
        assert _stroke_width(np.full((20, 100, 3), 255, dtype=np.uint8)) is None

    def test_empty_read_is_not_readable(self) -> None:
        result = OcrResult([], np.zeros((10, 10, 3), dtype=np.uint8), 0.0, 0, 1)
        assert not result.readable
        assert result.text == ""
