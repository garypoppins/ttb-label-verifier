"""Optional checks: name and address of the bottler or producer, and country of origin.

Both are optional application fields. Like the other checks, each takes the
application value and the text lines read from one picture and returns a
CheckResult, so they can be tested without images.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.checks import (
    COULD_NOT_READ,
    MATCH,
    MISMATCH,
    OTHER_SIDE,
    REVIEW,
    CheckResult,
    _loose_lines,
    check_text_field,
    squash,
)

BOTTLER_FIELD, BOTTLER_LABEL = "bottler_name_address", "Bottler or producer"
COUNTRY_FIELD, COUNTRY_LABEL = "country_of_origin", "Country of origin"

# "Distilled and bottled by", "Produced & bottled by:", "Imported by" and so on.
# The application value does not need these words; if it has them they are ignored.
_ROLE_WORDS = (
    r"distilled|produced|bottled|brewed|canned|made|blended|imported|vinted|cellared|packed|"
    r"aged|finished|crafted|manufactured|prepared|distributed"
)
_ROLE_PREFIX = re.compile(rf"^\s*(?:(?:{_ROLE_WORDS})\b[\s,&]*(?:and\b)?\s*)+by\b[\s:]*", re.IGNORECASE)
# Words in an address that say nothing about where it is.
_ADDRESS_FILLER = {"inc", "llc", "ltd", "co", "company", "corp", "the", "of", "and", "usa", "us"}

US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california",
    "co": "colorado", "ct": "connecticut", "de": "delaware", "fl": "florida", "ga": "georgia",
    "hi": "hawaii", "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
    "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland", "ma": "massachusetts",
    "mi": "michigan", "mn": "minnesota", "ms": "mississippi", "mo": "missouri", "mt": "montana",
    "ne": "nebraska", "nv": "nevada", "nh": "newhampshire", "nj": "newjersey", "nm": "newmexico",
    "ny": "newyork", "nc": "northcarolina", "nd": "northdakota", "oh": "ohio", "ok": "oklahoma",
    "or": "oregon", "pa": "pennsylvania", "ri": "rhodeisland", "sc": "southcarolina",
    "sd": "southdakota", "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont",
    "va": "virginia", "wa": "washington", "wv": "westvirginia", "wi": "wisconsin", "wy": "wyoming",
    "dc": "districtofcolumbia",
}


def strip_role(value: str) -> str:
    """Remove a leading "Bottled by" style phrase."""
    return _ROLE_PREFIX.sub("", value).strip()


def _split_name_address(value: str) -> tuple[str, list[str]]:
    """Name is the text before the first comma; the rest are address parts ("Bardstown", "Kentucky")."""
    parts = [part.strip() for part in strip_role(value).split(",") if part.strip()]
    if not parts:
        return "", []
    return parts[0], parts[1:]


def _place_keys(part: str) -> list[str]:
    """Comparable forms of one address part. Two-letter US state codes also match the full name."""
    key = squash(part)
    if not key or key in _ADDRESS_FILLER:
        return []
    keys = [key]
    if key in US_STATES:
        keys.append(US_STATES[key])
    reverse = {name: code for code, name in US_STATES.items()}
    if key in reverse:
        keys.append(reverse[key])
    return keys


def _place_found(keys: list[str], label_words: set[str], label_text: str) -> bool:
    """A place counts as found as a whole word, or for longer names anywhere in the squashed text."""
    for key in keys:
        if key in label_words or (len(key) >= 5 and key in label_text):
            return True
    return False


def check_bottler(
    expected: str, lines: Sequence[str], readable: bool = True, other_side_possible: bool = False
) -> CheckResult:
    """Check the name and address of the bottler, producer or importer.

    The name is compared like the brand name. For the address, the city and
    state (or whatever address words were given) must be on the label. Name and
    address both found is a match; only part of it is review.
    """
    expected = " ".join(expected.split())
    name, address = _split_name_address(expected)
    if not name:
        return CheckResult(BOTTLER_FIELD, BOTTLER_LABEL, REVIEW, expected, "", "The application value is empty.")

    named = check_text_field(BOTTLER_FIELD, BOTTLER_LABEL, name, lines, readable)
    label_words = {squash(word) for line in _loose_lines(lines) for word in line.split()}
    label_text = squash(" ".join(lines))
    places = [part for part in address if _place_keys(part)]
    found_places = [part for part in places if _place_found(_place_keys(part), label_words, label_text)]
    missing_places = [part for part in places if part not in found_places]
    found = named.found.rstrip(" ,;:.") if named.status != MISMATCH else ""
    if found_places:
        found = ", ".join([found, *found_places]) if found else ", ".join(found_places)

    if named.status == MATCH and not missing_places:
        if found_places:
            reason = f"The name and the address ({', '.join(found_places)}) are on the label."
        else:
            reason = "The name is on the label."
        return CheckResult(BOTTLER_FIELD, BOTTLER_LABEL, MATCH, expected, found, reason)
    if named.status == MATCH:
        reason = f"The name is on the label, but not {', '.join(missing_places)}. Check the address by eye."
        return CheckResult(BOTTLER_FIELD, BOTTLER_LABEL, REVIEW, expected, found, reason)
    if named.status == REVIEW and named.found:
        reason = named.reason
        if missing_places:
            reason += f" Also not found: {', '.join(missing_places)}."
        return CheckResult(BOTTLER_FIELD, BOTTLER_LABEL, REVIEW, expected, found, reason)
    if found_places:
        reason = f"The address ({', '.join(found_places)}) is on the label, but not the name. Check by eye."
        return CheckResult(BOTTLER_FIELD, BOTTLER_LABEL, REVIEW, expected, found, reason)
    if not readable:
        return CheckResult(BOTTLER_FIELD, BOTTLER_LABEL, REVIEW, expected, "", COULD_NOT_READ)
    if other_side_possible:
        return CheckResult(BOTTLER_FIELD, BOTTLER_LABEL, REVIEW, expected, "", OTHER_SIDE)
    return CheckResult(BOTTLER_FIELD, BOTTLER_LABEL, MISMATCH, expected, "", "Not found on the label.")


# Country names and the other ways labels and applications write them. The first
# name in each entry is the one shown. Spellings are compared after squash().
_COUNTRIES = [
    ("United States", "unitedstatesofamerica", "unitedstates", "usa", "us", "america"),
    ("United Kingdom", "unitedkingdom", "greatbritain", "uk", "britain"),
    ("Scotland", "scotland", "ecosse", "escocia"),
    ("England", "england"),
    ("Wales", "wales"),
    ("Northern Ireland", "northernireland"),
    ("Ireland", "ireland", "eire", "irlande", "irlanda"),
    ("Mexico", "mexico", "mexique", "messico"),
    ("Canada", "canada"),
    ("France", "france", "francia", "frankreich"),
    ("Italy", "italy", "italia", "italie"),
    ("Spain", "spain", "espana", "espagne"),
    ("Portugal", "portugal"),
    ("Germany", "germany", "deutschland", "allemagne", "alemania"),
    ("Austria", "austria", "osterreich"),
    ("Belgium", "belgium", "belgique", "belgie"),
    ("Netherlands", "netherlands", "holland", "nederland"),
    ("Denmark", "denmark"),
    ("Sweden", "sweden", "sverige"),
    ("Norway", "norway"),
    ("Finland", "finland", "suomi"),
    ("Poland", "poland", "polska"),
    ("Czech Republic", "czechrepublic", "czechia"),
    ("Hungary", "hungary"),
    ("Greece", "greece"),
    ("Russia", "russia"),
    ("Ukraine", "ukraine"),
    ("Switzerland", "switzerland", "schweiz", "suisse"),
    ("Japan", "japan", "nippon"),
    ("China", "china"),
    ("Taiwan", "taiwan"),
    ("South Korea", "southkorea", "korea"),
    ("Thailand", "thailand"),
    ("India", "india"),
    ("Philippines", "philippines"),
    ("Australia", "australia"),
    ("New Zealand", "newzealand"),
    ("South Africa", "southafrica"),
    ("Argentina", "argentina"),
    ("Chile", "chile"),
    ("Peru", "peru"),
    ("Brazil", "brazil", "brasil"),
    ("Colombia", "colombia"),
    ("Venezuela", "venezuela"),
    ("Guatemala", "guatemala"),
    ("Nicaragua", "nicaragua"),
    ("Jamaica", "jamaica"),
    ("Barbados", "barbados"),
    ("Trinidad and Tobago", "trinidadandtobago", "trinidad"),
    ("Dominican Republic", "dominicanrepublic"),
    ("Puerto Rico", "puertorico"),
    ("Cuba", "cuba"),
    ("Haiti", "haiti"),
    ("Martinique", "martinique"),
    ("Guyana", "guyana"),
    ("Israel", "israel"),
    ("Lebanon", "lebanon"),
    ("Turkey", "turkey", "turkiye"),
    ("Georgia", "georgia"),
    ("Armenia", "armenia"),
    ("Moldova", "moldova"),
    ("Romania", "romania"),
    ("Bulgaria", "bulgaria"),
    ("Croatia", "croatia"),
    ("Slovenia", "slovenia"),
    ("Serbia", "serbia"),
    ("Lithuania", "lithuania"),
    ("Latvia", "latvia"),
    ("Estonia", "estonia"),
]
# The same country for the purpose of an origin statement: "Product of Scotland" on a
# label is consistent with "United Kingdom" on the application.
_PART_OF = {"Scotland": "United Kingdom", "England": "United Kingdom", "Wales": "United Kingdom",
            "Northern Ireland": "United Kingdom"}
_ALIASES = sorted(
    ((alias, entry[0]) for entry in _COUNTRIES for alias in entry[1:]), key=lambda item: -len(item[0])
)
# Origin phrases, in squashed form, followed by the country. A few letters may sit
# between "imported" and "from" ("Imported beer from Mexico").
_ORIGIN_PHRASE = re.compile(
    r"(productof|produceof|produitde|prodottoin|productode|productoof|madein|hechoen|fabriqueen|"
    r"elaboradoen|producedin|bottledin|distilledin|brewedin|vinifiedin|imported[a-z]{0,12}?from)(?:the)?"
)


def country_name(text: str) -> str | None:
    """The country a value names, in its shown form, or None if it is not in the list."""
    key = squash(text)
    for alias, name in _ALIASES:
        if key == alias:
            return name
    return None


def _same_country(stated: str, wanted: str) -> bool:
    return stated == wanted or _PART_OF.get(stated) == wanted or _PART_OF.get(wanted) == stated


def _origin_statements(lines: Sequence[str]) -> list[tuple[str, str]]:
    """Every "Product of X" style statement on the label, as (country, the line it is on)."""
    found = []
    for line in lines:
        key = squash(line)
        for match in _ORIGIN_PHRASE.finditer(key):
            rest = key[match.end() :]
            for alias, name in _ALIASES:
                # Two-letter aliases ("us", "uk") only count when nothing follows them.
                if rest.startswith(alias) and (len(alias) > 2 or len(rest) == len(alias)):
                    found.append((name, line))
                    break
    return found


def check_country(
    expected: str, lines: Sequence[str], readable: bool = True, other_side_possible: bool = False
) -> CheckResult:
    """Check the country of origin against statements such as "Product of Mexico"."""
    expected = " ".join(expected.split())
    wanted = country_name(expected) or expected
    statements = _origin_statements(lines)
    for country, line in statements:
        if _same_country(country, wanted):
            note = "" if country == wanted else f" ({country} is part of {wanted}.)"
            return CheckResult(COUNTRY_FIELD, COUNTRY_LABEL, MATCH, expected, line, f"Label says {country}.{note}")
    if statements:
        country, line = statements[0]
        shown = f"Label says {country}. The application says {expected}."
        if not readable:
            reason = f"{shown} The picture did not read cleanly, so this may be a reading error. Check by eye."
            return CheckResult(COUNTRY_FIELD, COUNTRY_LABEL, REVIEW, expected, line, reason)
        return CheckResult(COUNTRY_FIELD, COUNTRY_LABEL, MISMATCH, expected, line, shown)

    keys = [alias for alias, name in _ALIASES if name == wanted and len(alias) > 2] or [squash(wanted)]
    for line in lines:
        words = {squash(word) for word in _loose_lines([line])[0].split()}
        if any(key in words or (len(key) >= 5 and key in squash(line)) for key in keys):
            reason = f'"{wanted}" is on the label, but not in a statement such as "Product of {wanted}". Check by eye.'
            return CheckResult(COUNTRY_FIELD, COUNTRY_LABEL, REVIEW, expected, line, reason)
    if not readable:
        return CheckResult(COUNTRY_FIELD, COUNTRY_LABEL, REVIEW, expected, "", COULD_NOT_READ)
    if other_side_possible:
        return CheckResult(COUNTRY_FIELD, COUNTRY_LABEL, REVIEW, expected, "", OTHER_SIDE)
    reason = 'No country of origin statement (such as "Product of Mexico") found on the label. Check by eye.'
    return CheckResult(COUNTRY_FIELD, COUNTRY_LABEL, REVIEW, expected, "", reason)
