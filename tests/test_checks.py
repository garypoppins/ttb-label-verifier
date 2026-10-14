"""Unit tests for the field checks. Plain text in, result out; no images involved."""

from __future__ import annotations

import pytest

from app.checks import (
    MATCH,
    MISMATCH,
    REVIEW,
    CheckResult,
    check_alcohol,
    check_net_contents,
    check_text_field,
    fix_digits,
    overall_status,
    parse_alcohol,
    parse_volumes,
    squash,
)

LABEL = [
    "EST. 1892",
    "OLD TOM",
    "DISTILLERY",
    "Kentucky Straight Bourbon Whiskey",
    "45% Alc./Vol. (90 Proof)",
    "750 mL",
    "Distilled and Bottled by Old Tom Distillery, Bardstown, Kentucky",
]


def brand(expected: str, lines: list[str], readable: bool = True) -> CheckResult:
    return check_text_field("brand_name", "Brand name", expected, lines, readable)


class TestSquash:
    def test_ignores_case_punctuation_and_spacing(self) -> None:
        assert squash("STONE'S  THROW") == squash("Stone’s Throw") == "stonesthrow"

    def test_ignores_accents(self) -> None:
        assert squash("Château") == "chateau"


class TestTextField:
    def test_exact_match(self) -> None:
        result = brand("STONE'S THROW", ["SMALL BATCH", "STONE'S THROW"])
        assert result.status == MATCH
        assert result.reason == "Label matches the application."

    def test_case_difference_matches_with_a_note(self) -> None:
        result = brand("Stone's Throw", ["SMALL BATCH", "STONE'S THROW", "Straight Rye Whiskey"])
        assert result.status == MATCH
        assert result.found == "STONE'S THROW"
        assert "capital letters" in result.reason

    def test_brand_split_across_two_lines(self) -> None:
        result = brand("OLD TOM DISTILLERY", LABEL)
        assert result.status == MATCH
        assert result.found == "OLD TOM DISTILLERY"

    def test_brand_inside_a_longer_line(self) -> None:
        result = brand("Old Tom", ["Proudly made by Old Tom in Kentucky"])
        assert result.status == MATCH

    def test_words_run_together_by_ocr_still_match(self) -> None:
        assert brand("OLD TOM DISTILLERY", ["OLDTOM", "DISTILLERY"]).status == MATCH

    def test_one_letter_off_needs_review(self) -> None:
        result = brand("OLD TOM DISTILLERY", ["OLD TOM DISTILLERV"])
        assert result.status == REVIEW
        assert result.found == "OLD TOM DISTILLERV"

    def test_different_brand_is_a_mismatch(self) -> None:
        result = brand("Silver Creek", LABEL)
        assert result.status == MISMATCH

    def test_does_not_match_part_of_a_longer_word(self) -> None:
        assert brand("Old Tom", ["OLD TOMMYKNOCKER RESERVE"]).status != MATCH

    def test_poor_read_gives_review_not_mismatch(self) -> None:
        result = brand("OLD TOM DISTILLERY", ["0L0 T", "xx"], readable=False)
        assert result.status == REVIEW
        assert "clearer photo" in result.reason

    def test_empty_label_with_good_read_is_a_mismatch(self) -> None:
        assert brand("OLD TOM DISTILLERY", []).status == MISMATCH

    def test_class_type(self) -> None:
        result = check_text_field("class_type", "Class/type", "Kentucky Straight Bourbon Whiskey", LABEL)
        assert result.status == MATCH
        assert result.field == "class_type"


class TestParseAlcohol:
    @pytest.mark.parametrize(
        ("text", "percent", "proof"),
        [
            ("45% Alc./Vol. (90 Proof)", 45.0, 90.0),
            ("45%Alc./Vol.(90Proof)", 45.0, 90.0),
            ("ALC. 13.5% BY VOL.", 13.5, None),
            ("5,2 % vol", 5.2, None),
            ("40 percent alcohol by volume", 40.0, None),
            ("80 PROOF", None, 80.0),
            ("Kentucky Straight Bourbon", None, None),
        ],
    )
    def test_parses(self, text: str, percent: float | None, proof: float | None) -> None:
        stated = parse_alcohol(text)
        assert (stated.percent, stated.proof) == (percent, proof)

    def test_bare_number_only_when_allowed(self) -> None:
        assert parse_alcohol("45").percent is None
        assert parse_alcohol("45", allow_bare_number=True).percent == 45.0

    def test_fix_digits_repairs_zero_read_as_letter_o(self) -> None:
        assert fix_digits("4o% Alc./Vol. (8o Proof)") == "40% Alc./Vol. (80 Proof)"
        assert fix_digits("12oz of cool") == "12oz of cool"


class TestAlcohol:
    def test_match(self) -> None:
        result = check_alcohol("45% Alc./Vol. (90 Proof)", LABEL)
        assert result.status == MATCH
        assert result.found == "45% Alc./Vol. (90 Proof)"

    def test_formatting_differences_do_not_matter(self) -> None:
        assert check_alcohol("45%", ["ALC. 45.0% BY VOL."]).status == MATCH
        assert check_alcohol("45", LABEL).status == MATCH

    def test_wrong_percent_is_a_mismatch(self) -> None:
        result = check_alcohol("45% Alc./Vol. (90 Proof)", ["40% Alc./Vol. (80 Proof)"])
        assert result.status == MISMATCH
        assert "40%" in result.reason and "45%" in result.reason

    def test_zero_read_as_letter_o_is_tolerated(self) -> None:
        assert check_alcohol("45% Alc./Vol. (90 Proof)", ["45% Alc./Vol. (9o Proof)"]).status == MATCH
        assert check_alcohol("45%", ["4o% Alc./Vol. (8o Proof)"]).status == MISMATCH

    def test_label_proof_not_twice_percent_is_flagged(self) -> None:
        result = check_alcohol("45%", ["45% Alc./Vol. (80 Proof)"])
        assert result.status == REVIEW
        assert "twice" in result.reason

    def test_proof_differs_from_application(self) -> None:
        result = check_alcohol("45% Alc./Vol. (90 Proof)", ["45% Alc./Vol. (80 Proof)"])
        assert result.status == MISMATCH
        assert "proof" in result.reason

    def test_application_gives_proof_only(self) -> None:
        assert check_alcohol("90 proof", LABEL).status == MATCH

    def test_label_gives_proof_only(self) -> None:
        assert check_alcohol("45%", ["90 PROOF"]).status == MATCH

    def test_other_percentages_are_not_mistaken_for_alcohol(self) -> None:
        lines = ["100% BLUE AGAVE", "TEQUILA", "40% ALC./VOL."]
        assert check_alcohol("40%", lines).status == MATCH
        result = check_alcohol("40%", ["100% BLUE AGAVE", "TEQUILA"])
        assert result.status == REVIEW

    def test_not_found_gives_review(self) -> None:
        result = check_alcohol("45%", ["OLD TOM", "750 mL"])
        assert result.status == REVIEW
        assert result.found == ""

    def test_not_found_on_poor_read_asks_for_clearer_photo(self) -> None:
        assert "clearer photo" in check_alcohol("45%", ["OLD"], readable=False).reason

    def test_application_value_not_understood(self) -> None:
        assert check_alcohol("strong", LABEL).status == REVIEW


class TestParseVolumes:
    @pytest.mark.parametrize(
        ("text", "millilitres"),
        [
            ("750 mL", 750.0),
            ("750ml", 750.0),
            ("750 ML", 750.0),
            ("75 cl", 750.0),
            ("1.75 L", 1750.0),
            ("1,75 L", 1750.0),
            ("1 Liter", 1000.0),
            ("1,000 mL", 1000.0),
            ("12 FL OZ", 354.882),
            ("12 fl. oz.", 354.882),
            ("12 fluid ounces", 354.882),
        ],
    )
    def test_parses(self, text: str, millilitres: float) -> None:
        assert parse_volumes(text)[0].millilitres == pytest.approx(millilitres)

    @pytest.mark.parametrize("text", ["EST. 1892 Louisville", "45% Alc./Vol. (90 Proof)", "Lot 12 Line 4"])
    def test_ignores_other_numbers(self, text: str) -> None:
        assert parse_volumes(text) == []


class TestNetContents:
    def test_match(self) -> None:
        result = check_net_contents("750 mL", LABEL)
        assert result.status == MATCH
        assert result.found == "750 mL"

    def test_same_amount_in_other_units_matches_with_a_note(self) -> None:
        result = check_net_contents("750 mL", ["75 cl"])
        assert result.status == MATCH
        assert "different units" in result.reason
        assert check_net_contents("355 mL", ["12 FL OZ"]).status == MATCH

    def test_different_amount_is_a_mismatch(self) -> None:
        result = check_net_contents("750 mL", ["1.75 L"])
        assert result.status == MISMATCH
        assert result.found == "1.75 L"

    def test_any_matching_statement_on_the_label_counts(self) -> None:
        assert check_net_contents("750 mL", ["25.4 FL OZ", "750 ML"]).status == MATCH

    def test_zero_read_as_letter_o_is_tolerated(self) -> None:
        assert check_net_contents("750 mL", ["75o mL"]).status == MATCH

    def test_not_found_gives_review(self) -> None:
        assert check_net_contents("750 mL", ["OLD TOM"]).status == REVIEW
        assert "clearer photo" in check_net_contents("750 mL", [], readable=False).reason

    def test_application_value_without_unit(self) -> None:
        result = check_net_contents("750", LABEL)
        assert result.status == REVIEW
        assert "unit" in result.reason


class TestOverallStatus:
    def make(self, status: str) -> CheckResult:
        return CheckResult("f", "F", status, "", "", "")

    def test_worst_status_wins(self) -> None:
        assert overall_status([self.make(MATCH), self.make(MATCH)]) == MATCH
        assert overall_status([self.make(MATCH), self.make(REVIEW)]) == REVIEW
        assert overall_status([self.make(REVIEW), self.make(MISMATCH)]) == MISMATCH
