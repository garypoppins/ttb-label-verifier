"""Unit tests for the behaviour changed after the evaluation on real label photos.

Each test names the real-photo failure it guards against. Text in, result out; the few tests
that need the OCR engine use the generated sample images.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from app import ocr
from app.checks import (
    MATCH,
    MISMATCH,
    OTHER_SIDE,
    REVIEW,
    CheckResult,
    check_alcohol,
    check_net_contents,
    check_text_field,
    combine_pictures,
    parse_volumes,
)
from app.main import _confirmed_by_extra_read
from app.ocr import Line, OcrResult, _covered
from app.warning import (
    NOT_ON_THIS_PICTURE,
    STATUTORY_WARNING,
    _block_lines,
    _collect,
    _line_edges,
    check_warning_text,
    compare_wording,
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def text_field(expected: str, lines: list[str], **options: bool) -> CheckResult:
    return check_text_field("brand_name", "Brand name", expected, lines, **options)


class TestNumbersOnAnUnreliableRead:
    def test_lost_decimal_point_in_alcohol_is_review(self) -> None:
        """Coors Light back: 4.2% under glare was read as 42% and reported as a mismatch."""
        result = check_alcohol("4.2% Alc./Vol.", ["S/container.PrServing.42%ALc/NOL"])
        assert result.status == REVIEW
        assert "decimal point" in result.reason

    def test_lost_decimal_point_works_both_ways(self) -> None:
        assert check_alcohol("13.5%", ["ALC. 135% BY VOL."]).status == REVIEW
        assert check_alcohol("45%", ["ALC. 4.5% BY VOL."]).status == REVIEW

    def test_different_digits_are_still_a_mismatch(self) -> None:
        assert check_alcohol("45%", ["ALC. 40% BY VOL."]).status == MISMATCH
        assert check_alcohol("40%", ["ALC. 4% BY VOL."]).status == MISMATCH
        assert check_alcohol("45%", ["Alc. 45.2%/Vol. (90.4 Proof)"]).status == MISMATCH

    def test_alcohol_difference_on_a_poor_read_is_review(self) -> None:
        result = check_alcohol("45%", ["ALC. 40% BY VOL."], readable=False)
        assert result.status == REVIEW
        assert "40%" in result.reason and "did not read cleanly" in result.reason
        assert result.found == "ALC. 40% BY VOL."

    def test_proof_difference_on_a_poor_read_is_review(self) -> None:
        assert check_alcohol("45% (90 Proof)", ["45% Alc./Vol. (80 Proof)"], readable=False).status == REVIEW

    def test_net_contents_difference_on_a_poor_read_is_review(self) -> None:
        result = check_net_contents("750 mL", ["1.75 L"], readable=False)
        assert result.status == REVIEW
        assert result.found == "1.75 L"
        assert "did not read cleanly" in result.reason

    def test_lost_decimal_point_in_net_contents_is_review(self) -> None:
        assert check_net_contents("1.75 L", ["175 L"]).status == REVIEW
        assert check_net_contents("1.5 L", ["15L"]).status == REVIEW

    def test_ten_times_smaller_bottle_is_still_a_mismatch(self) -> None:
        assert check_net_contents("500 mL", ["50 mL"]).status == MISMATCH

    def test_serving_size_is_not_net_contents(self) -> None:
        """Captain Morgan back: "Serving size: 1.5 fl. oz. (44 mL)" was compared with 750 mL."""
        serving = "Serving Facts: Serving size: 1.5 fl. oz. (44 mL); Servings per container: 17"
        assert check_net_contents("750 mL", [serving]).status == REVIEW
        assert check_net_contents("750 mL", ["wnygfctsSeringsize15floz44mL)Servingserontine"]).status == REVIEW
        assert check_net_contents("750 mL", [serving, "CA CRV 750 mL"]).status == MATCH

    def test_reserve_is_not_mistaken_for_serving(self) -> None:
        assert check_net_contents("750 mL", ["SPECIAL RESERVE 750 ML"]).status == MATCH


class TestNumbersAbsentFromOnePicture:
    def test_single_picture_asks_for_the_other_side(self) -> None:
        lines = ["GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL"]
        assert check_alcohol("45%", lines, other_side_possible=True).reason == OTHER_SIDE
        assert check_net_contents("750 mL", lines, other_side_possible=True).reason == OTHER_SIDE
        assert check_alcohol("45%", lines, other_side_possible=True).status == REVIEW

    def test_poor_read_still_asks_for_a_clearer_photo(self) -> None:
        assert "clearer photo" in check_alcohol("45%", ["xx"], readable=False, other_side_possible=True).reason


class TestNetContentsSpellings:
    @pytest.mark.parametrize(
        ("text", "millilitres"),
        [
            ("12FL-OZ", 354.882),
            ("12 FL.OZ.", 354.882),
            ("12 OZ.", 354.882),
            ("12 ounces", 354.882),
            ("700m!", 700.0),
            ("700m！", 700.0),
            ("750 m1", 750.0),
            ("750 mI", 750.0),
        ],
    )
    def test_parses(self, text: str, millilitres: float) -> None:
        assert parse_volumes(text)[0].millilitres == pytest.approx(millilitres)

    def test_can_with_ounces_and_millilitres(self) -> None:
        assert check_net_contents("355 mL", ["12OZ./355ML1l5.5%ALC./VOL"]).status == MATCH
        assert check_net_contents("12 fl oz", ["12 OZ."]).status == MATCH

    def test_the_word_liter_alone_means_one_litre(self) -> None:
        """Barton gin neck band says LITER with no number."""
        result = check_net_contents("1 L", ["BARTON", "GIN", "LITER"])
        assert result.status == MATCH
        assert result.found == "LITER"

    def test_the_word_liter_alone_against_another_size_is_review(self) -> None:
        assert check_net_contents("750 mL", ["BARTON", "GIN", "LITER"]).status == REVIEW

    def test_a_number_with_liter_still_wins(self) -> None:
        assert check_net_contents("1 L", ["1.75 LITER"]).status == MISMATCH


class TestTextMatchingOnRealOcrOutput:
    def test_name_joined_to_other_words_by_punctuation(self) -> None:
        """Bushmills and Komos back labels: the brand was only in "BUSHMILLS&ASSOCIATED" and "komos.com"."""
        assert text_field("Bushmills", ["BUSHMILLS&ASSOCIATED LOGOS ARE TRADE MARKS"]).status == MATCH
        assert text_field("Komos", ["komos.com", "CERTIFIED GLUTEN FREE"]).status == MATCH
        assert text_field("Komos", ["IMPORTED BY KOMOS,INC. NEW YORK"]).status == MATCH

    def test_apostrophes_are_kept_in_the_text_shown(self) -> None:
        assert text_field("Stone's Throw", ["STONE'S THROW"]).found == "STONE'S THROW"

    def test_name_inside_an_unbroken_run_of_letters_is_review(self) -> None:
        result = text_field("Bushmills", ["THEOLDBUSHMILLSDISTILLERY"])
        assert result.status == REVIEW
        assert "longer run" in result.reason

    def test_short_values_are_not_searched_inside_other_words(self) -> None:
        """Class "Beer" used to be shown with the closest text "BRTH"."""
        result = check_text_field("class_type", "Class/type", "Beer", ["www.tapintoyourbeerdotcom", "BRTH DEFECTS"])
        assert result.status == MISMATCH
        assert result.found == ""

    def test_class_printed_in_pieces_matches(self) -> None:
        """Komos front: TEQUILA above the brand, ANEJO CRISTALINO below it."""
        lines = ["TEQUILA", "KOMOS", "100%DEAGAVEAZUL", "ANEJO", "CRISTALINO"]
        result = check_text_field("class_type", "Class/type", "Tequila Anejo Cristalino", lines)
        assert result.status == MATCH
        assert "separate lines" in result.reason

    def test_words_far_apart_are_review(self) -> None:
        lines = ["BLANCO", *["FILLER LINE"] * 8, "SMALL BATCH TEQUILA"]
        result = check_text_field("class_type", "Class/type", "Blanco Tequila", lines)
        assert result.status == REVIEW

    def test_one_word_missing_is_not_enough(self) -> None:
        lines = ["TEQUILA", "KOMOS", "CRISTALINO"]
        assert check_text_field("class_type", "Class/type", "Tequila Anejo Cristalino", lines).status != MATCH

    def test_decorative_type_just_under_the_old_cut_off_is_review(self) -> None:
        """Jose Cuervo in blackletter was read as "Jost uervo" (84 percent alike) and rejected."""
        result = text_field("Jose Cuervo", ["Jost", "uervo", "ESPECIAL"])
        assert result.status == REVIEW
        assert result.found == "Jost uervo"

    def test_clearly_different_text_is_a_mismatch_with_no_closest_text(self) -> None:
        """No more "closest text" such as ACCORDING for Concord Grape."""
        result = check_text_field("class_type", "Class/type", "Concord Grape", ["(1) ACCORDING TO THE SURGEON"])
        assert result.status == MISMATCH
        assert result.found == ""

    def test_absent_on_one_picture_asks_for_the_other_side(self) -> None:
        result = text_field("Barton", ["GOVERNMENT WARNING: (1) ACCORDING TO THE"], other_side_possible=True)
        assert result.status == REVIEW
        assert result.reason == OTHER_SIDE

    def test_poor_read_message_wins_over_other_side(self) -> None:
        result = text_field("Barton", ["xx"], readable=False, other_side_possible=True)
        assert "clearer photo" in result.reason

    def test_near_miss_is_review_whatever_the_number_of_pictures(self) -> None:
        assert text_field("Van Winkel", ["VAN WINKLE"], other_side_possible=True).status == REVIEW
        assert text_field("Van Winkel", ["VAN WINKLE"]).status == REVIEW


def result(status: str, found: str = "", reason: str = "r") -> CheckResult:
    return CheckResult("brand_name", "Brand name", status, "X", found, reason)


class TestCombinePictures:
    def test_match_on_any_picture_wins(self) -> None:
        combined = combine_pictures([result(MISMATCH), result(MATCH, "OLD TOM")], ["front.jpg", "back.jpg"])
        assert combined.status == MATCH
        assert (combined.picture, combined.picture_name) == (2, "back.jpg")

    def test_contradiction_beats_not_found_and_review(self) -> None:
        combined = combine_pictures([result(REVIEW), result(MISMATCH, "40% ALC")], ["a", "b"])
        assert combined.status == MISMATCH
        assert combined.picture == 2

    def test_near_miss_beats_could_not_read(self) -> None:
        combined = combine_pictures([result(REVIEW), result(REVIEW, "OLD TOM DISTILLERV")], ["a", "b"])
        assert combined.found == "OLD TOM DISTILLERV"
        assert combined.picture == 2

    def test_not_found_needs_every_picture_to_read_well(self) -> None:
        assert combine_pictures([result(MISMATCH), result(REVIEW)], ["a", "b"]).status == REVIEW
        combined = combine_pictures([result(MISMATCH), result(MISMATCH)], ["a", "b"])
        assert combined.status == MISMATCH
        assert combined.reason == "Not found on any of the 2 pictures."
        assert combined.picture is None and combined.picture_name == ""

    def test_single_picture_is_passed_through(self) -> None:
        combined = combine_pictures([result(REVIEW, reason="Not on this picture.")], ["only.jpg"])
        assert combined.status == REVIEW
        assert combined.picture is None
        assert combine_pictures([result(MATCH, "OLD TOM")], ["only.jpg"]).picture == 1


LINES = [
    "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON",
    "GENERAL, WOMEN SHOULD NOT DRINK ALCOHOLIC BEVERAGES",
    "DURING PREGNANCY BECAUSE OF THE RISK OF BIRTH DEFECTS. (2)",
    "CONSUMPTION OF ALCOHOLIC BEVERAGES IMPAIRS YOUR ABILITY",
    "TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY CAUSE HEALTH",
    "PROBLEMS.",
]


class TestWarningTextThatWasNotRead:
    def test_clipped_start_of_a_line_is_review_not_rewording(self) -> None:
        """Manischewitz back: "CONSUMPTION" at a line start was read as "FION" and reported as changed wording."""
        lines = [*LINES[:3], "FION OF ALCOHOLIC BEVERAGES IMPAIRS YOUR ABILITY", *LINES[4:]]
        outcome = check_warning_text(lines, bold=True)
        assert outcome.status == REVIEW
        assert "was not read" in outcome.reason and '"Consumption"' in outcome.reason

    def test_missing_last_lines_are_review(self) -> None:
        """Stella Artois back: the last two lines were never detected and "beverages" was reported missing."""
        outcome = check_warning_text([*LINES[:4], "www.stellaartois.com", "PLEASE RECYCLE"][:4], bold=True)
        assert outcome.status == REVIEW
        assert "was not read" in outcome.reason

    def test_a_whole_line_missing_in_the_middle_is_review(self) -> None:
        outcome = check_warning_text([LINES[0], *LINES[2:]], bold=True)
        assert outcome.status == REVIEW
        assert "was not read" in outcome.reason

    def test_word_changed_inside_a_line_is_still_a_mismatch(self) -> None:
        lines = [line.replace("MAY CAUSE", "CAN CAUSE") for line in LINES]
        outcome = check_warning_text(lines, bold=True)
        assert outcome.status == MISMATCH
        assert '"may"' in outcome.reason

    def test_word_dropped_inside_a_line_is_still_a_mismatch(self) -> None:
        lines = [line.replace("SHOULD NOT DRINK", "SHOULD DRINK") for line in LINES]
        assert check_warning_text(lines, bold=True).status == MISMATCH

    def test_changed_last_words_are_still_a_mismatch(self) -> None:
        lines = [*LINES[:4], "TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY CAUSE HEALTH", "ISSUES."]
        assert check_warning_text(lines, bold=True).status == MISMATCH

    def test_strict_comparison_is_unchanged_without_line_positions(self) -> None:
        assert compare_wording(STATUTORY_WARNING.split("(2)")[0]).status == MISMATCH
        assert compare_wording(" ".join([*LINES[:3], "FION OF ALCOHOLIC", *LINES[4:]])).status == MISMATCH

    def test_collecting_stops_at_problems(self) -> None:
        """Long Drink can: the line after the warning ("12 OZ. / 355 ML") was taken as part of it."""
        pieces = _collect([*LINES[:5], "PROBLEMS. 12 OZ. / 355 ML", "CANNED BY THE LONG DRINK COMPANY"], 0, 0)
        assert pieces[-1] == "PROBLEMS."
        assert len(pieces) == 6
        lines = [*LINES[:4], "TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY CAUSE HEALTH PROBLEMS", "12OZ./355ML 5.5% ALC./VOL"]
        assert check_warning_text(lines, bold=True).status == MATCH

    def test_line_edges_count_comparable_characters(self) -> None:
        assert _line_edges(["AB (1) C", "D-E"]) == [4, 6]

    def test_no_warning_on_one_picture_asks_for_the_other_side(self) -> None:
        outcome = check_warning_text(["OLD TOM", "DISTILLERY", "750 mL"], other_side_possible=True)
        assert outcome.status == REVIEW
        assert outcome.reason == NOT_ON_THIS_PICTURE

    def test_no_warning_is_still_a_mismatch_when_the_set_is_complete(self) -> None:
        assert check_warning_text(["OLD TOM", "DISTILLERY", "750 mL"]).status == MISMATCH

    def test_wrong_capitals_are_a_mismatch_even_on_one_picture(self) -> None:
        lines = [LINES[0].replace("GOVERNMENT WARNING", "Government Warning"), *LINES[1:]]
        assert check_warning_text(lines, bold=True, other_side_possible=True).status == MISMATCH


def tilted(text: str, left: float, top: float, right: float, thickness: float, slack: float) -> Line:
    """A slightly tilted line: its upright rectangle is taller than the print by ``slack``."""
    return Line(text, 0.95, (left, top, right, top + thickness + slack), thickness)


class TestBlockOfTiltedLines:
    def test_next_line_is_not_mistaken_for_the_same_row(self) -> None:
        """Long Drink can: rectangles of neighbouring lines overlapped, so the second line was dropped."""
        lines = [
            tilted("GOVERNMENTWARNING:(1)ACCORDINGTOTHE SURGEON", 531, 539, 1133, 30, 29),
            tilted("GENERALWOMENSHOULDNOTDRINKALCOHOLICBEV", 534, 568, 1139, 30, 29),
            tilted("ERAGESDURINGPREGNANCYBECAUSEOFTHERISK OF", 534, 599, 1143, 30, 31),
            Line("PRODUCT OF THE USA", 0.9, (1165, 549, 1231, 798), 60),
        ]
        block = _block_lines(lines, 0)
        assert [item.text for item in block] == [item.text for item in lines[:3]]

    def test_height_falls_back_to_the_rectangle(self) -> None:
        assert Line("A", 0.9, (0, 10, 50, 40)).height == 30
        assert Line("A", 0.9, (0, 10, 50, 40), 18).height == 18


def read_of(texts: list[str], confidence: float = 0.9, unread: int = 0) -> OcrResult:
    lines = [Line(text, confidence, (0, 30 * index, 300, 30 * index + 25)) for index, text in enumerate(texts)]
    return OcrResult(lines, np.zeros((10, 10, 3), dtype=np.uint8), 0.0, unread, 1)


class TestReadable:
    def test_a_few_fragments_are_not_a_readable_label(self) -> None:
        """Chateau Ste. Michelle: four fragments at 0.82 confidence led to two "Not found" rejections."""
        fragments = read_of(["te", "hateati", "VINTAGE2015", "Established 1967"])
        assert fragments.clean
        assert not fragments.readable

    def test_enough_text_is_readable(self) -> None:
        assert read_of(["GOVERNMENT WARNING: (1) ACCORDING TO THE", "SURGEON GENERAL, WOMEN SHOULD NOT"]).readable

    def test_short_scraps_do_not_add_up(self) -> None:
        assert not read_of(["ab", "cd", "ef"] * 10).readable

    def test_low_confidence_or_many_unread_boxes_is_not_readable(self) -> None:
        long_lines = ["GOVERNMENT WARNING: (1) ACCORDING TO THE", "SURGEON GENERAL, WOMEN SHOULD NOT"]
        assert not read_of(long_lines, confidence=0.7).readable
        assert not read_of(long_lines, unread=3).readable


class TestExtraRead:
    def test_box_on_a_line_already_read_is_covered(self) -> None:
        lines = [Line("OLD TOM", 0.9, (100, 100, 400, 140), 40)]
        on_the_line = np.array([[120, 105], [380, 105], [380, 135], [120, 135]], dtype=np.float32)
        below = np.array([[120, 160], [380, 160], [380, 190], [120, 190]], dtype=np.float32)
        beside = np.array([[420, 105], [700, 105], [700, 135], [420, 135]], dtype=np.float32)
        assert _covered(on_the_line, lines, 1.0, 0.0)
        assert not _covered(below, lines, 1.0, 0.0)
        assert not _covered(beside, lines, 1.0, 0.0)

    def test_extra_read_keeps_every_line_already_read(self) -> None:
        engine = ocr.get_engine()
        first = engine.read((SAMPLES / "label_correct.png").read_bytes())
        again = engine.read_again(first)
        assert set(first.texts) <= set(again.texts)
        assert again.passes == first.passes + 1

    def test_sideways_text_is_recognised_from_the_boxes(self) -> None:
        def box(left: float, top: float, right: float, bottom: float, start: int = 0) -> np.ndarray:
            corners = [[left, top], [right, top], [right, bottom], [left, bottom]]
            return np.array(corners[start:] + corners[:start], dtype=np.float32)

        level = [box(10, 10 + 40 * row, 500, 40 + 40 * row) for row in range(4)]
        upright_lines = [box(10 + 40 * column, 10, 40 + 40 * column, 600, start) for column, start in enumerate((0, 1, 2, 3))]
        assert not ocr._runs_sideways(np.array(level))
        assert ocr._runs_sideways(np.array(upright_lines))
        assert ocr._runs_sideways(np.array([*upright_lines, level[0]]))
        assert not ocr._runs_sideways(np.array([box(0, 0, 30, 30)]))
        assert not ocr._runs_sideways(None)

    def test_picture_with_sideways_text_is_read_upright(self) -> None:
        engine = ocr.get_engine()
        result = engine.read((SAMPLES / "label_rotated.jpg").read_bytes())
        assert result.passes >= 2
        assert abs(math.sin(result.angle)) < 0.3  # level, either way up
        assert "GOVERNMENT WARNING" in result.text

    def test_extra_read_may_confirm_but_never_reject(self) -> None:
        not_found = CheckResult("brand_name", "Brand name", MISMATCH, "X", "", "Not found on the label.")
        confirmed = CheckResult("brand_name", "Brand name", MATCH, "X", "X", "ok")
        contradicted = CheckResult("alcohol_content", "Alcohol content", MISMATCH, "45%", "40%", "Label shows 40%.")
        unsure = CheckResult("brand_name", "Brand name", REVIEW, "X", "", "Could not read this on the label.")
        assert _confirmed_by_extra_read("brand_name", not_found, confirmed) is confirmed
        assert _confirmed_by_extra_read("brand_name", not_found, unsure) is unsure
        assert _confirmed_by_extra_read("alcohol_content", unsure, contradicted) is None
        assert _confirmed_by_extra_read("brand_name", unsure, not_found) is None

    def test_warning_found_only_by_the_extra_read_is_never_a_mismatch(self) -> None:
        absent = check_warning_text(["OLD TOM", "750 mL"])
        reworded = check_warning_text([line.replace("MAY CAUSE", "CAN CAUSE") for line in LINES], bold=True)
        assert (absent.status, reworded.status) == (MISMATCH, MISMATCH)
        softened = _confirmed_by_extra_read("government_warning", absent, reworded)
        assert softened is not None and softened.status == REVIEW
        assert "less certain read" in softened.reason

    def test_extra_read_cannot_overrule_capitals_or_bold(self) -> None:
        not_bold = check_warning_text(LINES, bold=False)
        clean = check_warning_text(LINES, bold=True)
        assert (not_bold.status, clean.status) == (REVIEW, MATCH)
        assert _confirmed_by_extra_read("government_warning", not_bold, clean) is None
