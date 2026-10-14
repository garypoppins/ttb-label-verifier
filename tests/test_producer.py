"""Unit tests for the optional bottler/producer and country of origin checks. Text in, result out."""

from __future__ import annotations

import pytest

from app.checks import MATCH, MISMATCH, OTHER_SIDE, REVIEW, CheckResult, combine_pictures
from app.producer import check_bottler, check_country, country_name, strip_role

BOTTLER_LINE = ["OLD TOM", "DISTILLERY", "Distilled and Bottled by Old Tom Distillery, Bardstown, Kentucky"]


class TestBottler:
    @pytest.mark.parametrize(
        "value",
        [
            "Old Tom Distillery, Bardstown, Kentucky",
            "Distilled and bottled by Old Tom Distillery, Bardstown, Kentucky",
            "Bottled by: OLD TOM DISTILLERY, BARDSTOWN, KENTUCKY",
            "Old Tom Distillery, Bardstown, KY",
        ],
    )
    def test_name_and_address_match(self, value: str) -> None:
        result = check_bottler(value, BOTTLER_LINE)
        assert result.status == MATCH, result.reason
        assert "bardstown" in result.found.lower()

    def test_name_only_matches(self) -> None:
        assert check_bottler("Old Tom Distillery", BOTTLER_LINE).status == MATCH

    def test_wrong_city_is_review(self) -> None:
        result = check_bottler("Old Tom Distillery, Louisville, Kentucky", BOTTLER_LINE)
        assert result.status == REVIEW
        assert "Louisville" in result.reason

    def test_address_without_the_name_is_review(self) -> None:
        result = check_bottler("Silver Creek Spirits, Bardstown, Kentucky", BOTTLER_LINE)
        assert result.status == REVIEW
        assert "not the name" in result.reason

    def test_nothing_found_on_a_good_read_is_a_mismatch(self) -> None:
        result = check_bottler("Silver Creek Spirits, Austin, Texas", BOTTLER_LINE)
        assert result.status == MISMATCH
        assert result.found == ""

    def test_nothing_found_on_one_picture_asks_for_the_other_side(self) -> None:
        result = check_bottler("Silver Creek Spirits, Austin, Texas", BOTTLER_LINE, other_side_possible=True)
        assert (result.status, result.reason) == (REVIEW, OTHER_SIDE)

    def test_poor_read_is_review(self) -> None:
        assert "clearer photo" in check_bottler("Silver Creek, Austin, Texas", ["xx"], readable=False).reason

    def test_importer_with_state_code_on_the_label(self) -> None:
        lines = ["IMPORTED BY CROWN IMPORTS, CHICAGO, IL"]
        assert check_bottler("Crown Imports, Chicago, Illinois", lines).status == MATCH

    def test_ocr_running_words_together(self) -> None:
        lines = ["CANNED BY THE LONG DRINK COMPANY.KANNAPOLIS,NC"]
        assert check_bottler("The Long Drink Company, Kannapolis, North Carolina", lines).status == MATCH

    def test_role_words_are_removed(self) -> None:
        assert strip_role("Produced & bottled by: St. Julian Wine Co.") == "St. Julian Wine Co."
        assert strip_role("Imported by Crown Imports") == "Crown Imports"
        assert strip_role("Old Tom Distillery") == "Old Tom Distillery"

    def test_empty_value(self) -> None:
        assert check_bottler("Bottled by", BOTTLER_LINE).status == REVIEW


class TestCountryNames:
    @pytest.mark.parametrize(
        ("value", "name"),
        [("USA", "United States"), ("U.S.A.", "United States"), ("United States of America", "United States"),
         ("UK", "United Kingdom"), ("United Kingdom", "United Kingdom"), ("México", "Mexico"), ("Italia", "Italy")],
    )
    def test_variants(self, value: str, name: str) -> None:
        assert country_name(value) == name

    def test_unknown(self) -> None:
        assert country_name("Atlantis") is None


class TestCountry:
    @pytest.mark.parametrize(
        "line",
        ["PRODUCT OF MEXICO", "Made in Mexico", "Imported from Mexico", "Produce of Mexico", "HECHO EN MÉXICO",
         "IMPORTED BEER FROM MEXICO", "PRODUCTOFMEXICO8"],
    )
    def test_statements_match(self, line: str) -> None:
        result = check_country("Mexico", ["CASA SOLANA", line])
        assert result.status == MATCH, result.reason
        assert result.found == line

    def test_united_states_variants(self) -> None:
        assert check_country("USA", ["PRODUCT OF THE USA"]).status == MATCH
        assert check_country("United States", ["Made in U.S.A."]).status == MATCH
        assert check_country("U.S.", ["PRODUCT OF UNITED STATES"]).status == MATCH

    def test_part_of_the_united_kingdom(self) -> None:
        result = check_country("UK", ["PRODUCT OF SCOTLAND"])
        assert result.status == MATCH
        assert "part of" in result.reason

    def test_different_country_stated_is_a_mismatch(self) -> None:
        result = check_country("Italy", ["PRODUCT OF MEXICO"])
        assert result.status == MISMATCH
        assert "Mexico" in result.reason and "Italy" in result.reason

    def test_different_country_on_a_poor_read_is_review(self) -> None:
        assert check_country("Italy", ["PRODUCT OF MEXICO"], readable=False).status == REVIEW

    def test_country_named_without_a_statement_is_review(self) -> None:
        result = check_country("Belgium", ["BREWED BY THE STELLA ARTOIS BREWERY, LEUVEN, BELGIUM"])
        assert result.status == REVIEW
        assert "not in a statement" in result.reason

    def test_other_country_named_without_a_statement_is_not_a_mismatch(self) -> None:
        assert check_country("Italy", ["CERVECERIA MODELO, MEXICO"]).status == REVIEW

    def test_not_found(self) -> None:
        assert check_country("France", ["OLD TOM"]).status == REVIEW
        assert check_country("France", ["OLD TOM"], other_side_possible=True).reason == OTHER_SIDE
        assert "clearer photo" in check_country("France", ["xx"], readable=False).reason

    def test_us_inside_a_word_is_not_a_country(self) -> None:
        assert check_country("Mexico", ["MADE IN USE BY"]).status == REVIEW

    def test_country_not_in_the_list_still_works(self) -> None:
        assert check_country("Atlantis", ["PRODUCT OF ATLANTIS"]).status == REVIEW


class TestCombine:
    def test_found_on_the_back_picture(self) -> None:
        front = check_country("Mexico", ["CASA SOLANA"])
        back = check_country("Mexico", ["PRODUCT OF MEXICO"])
        combined = combine_pictures([front, back], ["front.jpg", "back.jpg"])
        assert combined.status == MATCH and combined.picture == 2

    def test_bottler_on_either_picture(self) -> None:
        front = check_bottler("Old Tom Distillery, Bardstown, Kentucky", ["OLD TOM", "BOURBON"])
        back = check_bottler("Old Tom Distillery, Bardstown, Kentucky", BOTTLER_LINE)
        combined = combine_pictures([front, back], ["front.jpg", "back.jpg"])
        assert isinstance(combined, CheckResult)
        assert combined.status == MATCH and combined.picture == 2
