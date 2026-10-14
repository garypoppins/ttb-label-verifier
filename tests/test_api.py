"""End-to-end tests: sample label images through the HTTP API, with a time limit per label."""

from __future__ import annotations

import csv
import io
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import MAX_UPLOAD_BYTES, app

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
TIME_LIMIT_SECONDS = 5.0
FIELDS = ("brand_name", "class_type", "alcohol_content", "net_contents", "government_warning")

ALL_MATCH = dict.fromkeys(FIELDS, "match")
EXPECTED = {
    "label_correct.png": ALL_MATCH,
    "label_wrong_abv.png": {**ALL_MATCH, "alcohol_content": "mismatch"},
    "label_titlecase_warning.png": {**ALL_MATCH, "government_warning": "mismatch"},
    "label_reworded_warning.png": {**ALL_MATCH, "government_warning": "mismatch"},
    # Changed when several pictures per application were added: one picture with no warning is
    # no longer a mismatch, because the warning is usually on the back label. It needs review and
    # asks for the other side. Two pictures with no warning are still a mismatch (tested below).
    "label_missing_warning.png": {**ALL_MATCH, "government_warning": "review"},
    "label_warning_not_bold.png": {**ALL_MATCH, "government_warning": "review"},
    "label_brand_case.png": ALL_MATCH,
    "label_rotated.jpg": ALL_MATCH,
    "label_tilted.jpg": ALL_MATCH,
    "label_wine.png": ALL_MATCH,
    "label_beer.png": ALL_MATCH,
}


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """Start the app once. Entering the context loads and warms the OCR models."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def applications() -> dict[str, dict[str, str]]:
    """Application values for each sample, read from the sample batch list."""
    with (SAMPLES / "batch.csv").open(newline="", encoding="utf-8") as handle:
        return {row["filename"]: row for row in csv.DictReader(handle)}


def post(client: TestClient, filename: str, form: dict[str, str]) -> dict:
    data = {name: value for name, value in form.items() if name != "filename"}
    with (SAMPLES / filename).open("rb") as handle:
        response = client.post("/api/verify", files={"image": (filename, handle)}, data=data)
    assert response.status_code == 200, response.text
    return response.json()


def statuses(body: dict) -> dict[str, str]:
    return {check["field"]: check["status"] for check in body["fields"]}


def test_batch_list_covers_every_sample_image(applications: dict[str, dict[str, str]]) -> None:
    images = {path.name for path in SAMPLES.glob("label_*")}
    listed = {name for row in applications for name in row.split(";")}
    assert images == listed
    assert set(EXPECTED) | {"label_low_contrast.jpg", "label_front.png", "label_back.png"} == images


@pytest.mark.parametrize("filename", EXPECTED)
def test_sample_label(client: TestClient, applications: dict[str, dict[str, str]], filename: str) -> None:
    started = time.perf_counter()
    body = post(client, filename, applications[filename])
    elapsed = time.perf_counter() - started

    assert statuses(body) == EXPECTED[filename]
    worst = "mismatch" if "mismatch" in EXPECTED[filename].values() else "match"
    worst = "review" if worst == "match" and "review" in EXPECTED[filename].values() else worst
    assert body["overall"] == worst
    assert elapsed < TIME_LIMIT_SECONDS
    assert body["elapsed_ms"] < TIME_LIMIT_SECONDS * 1000


def test_low_contrast_label_is_read_without_false_rejection(
    client: TestClient, applications: dict[str, dict[str, str]]
) -> None:
    """A dim photo may need a human look, but nothing on it should be reported as wrong."""
    started = time.perf_counter()
    body = post(client, "label_low_contrast.jpg", applications["label_low_contrast.jpg"])
    assert time.perf_counter() - started < TIME_LIMIT_SECONDS
    result = statuses(body)
    assert "mismatch" not in result.values()
    assert result["alcohol_content"] == "match"
    assert result["net_contents"] == "match"


def post_image(client: TestClient, image: Image.Image, form: dict[str, str]) -> dict:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    data = {name: value for name, value in form.items() if name != "filename"}
    response = client.post("/api/verify", files={"image": ("generated.png", buffer.getvalue())}, data=data)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("turn", [Image.Transpose.ROTATE_90, Image.Transpose.ROTATE_180])
def test_label_turned_on_its_side_or_upside_down(
    client: TestClient, applications: dict[str, dict[str, str]], turn: Image.Transpose
) -> None:
    with Image.open(SAMPLES / "label_correct.png") as image:
        body = post_image(client, image.transpose(turn), applications["label_correct.png"])
    assert statuses(body) == ALL_MATCH


@pytest.mark.parametrize("upside_down", [False, True])
def test_picture_of_only_the_warning(
    client: TestClient, applications: dict[str, dict[str, str]], upside_down: bool
) -> None:
    """A back-label style strip: the warning passes and the missing front-label items are not rejected."""
    with Image.open(SAMPLES / "label_correct.png") as image:
        strip = image.crop((40, 1080, 960, 1280))
    body = post_image(client, strip.rotate(180) if upside_down else strip, applications["label_correct.png"])
    result = statuses(body)
    assert result["government_warning"] == "match"
    assert result["alcohol_content"] == "review"
    assert result["net_contents"] == "review"


def test_brand_case_difference_is_explained(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    body = post(client, "label_brand_case.png", applications["label_brand_case.png"])
    brand = body["fields"][0]
    assert brand["expected"] == "Stone's Throw"
    assert brand["found"] == "STONE'S THROW"
    assert "capital letters" in brand["reason"]


def test_wrong_abv_reason_names_both_values(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    body = post(client, "label_wrong_abv.png", applications["label_wrong_abv.png"])
    alcohol = next(check for check in body["fields"] if check["field"] == "alcohol_content")
    assert "40%" in alcohol["reason"] and "45%" in alcohol["reason"]


def test_reworded_warning_names_the_changed_word(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    body = post(client, "label_reworded_warning.png", applications["label_reworded_warning.png"])
    assert '"may"' in body["fields"][-1]["reason"]


def test_response_includes_raw_text_and_timing(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    body = post(client, "label_correct.png", applications["label_correct.png"])
    assert "GOVERNMENT WARNING" in body["ocr"]["text"]
    assert body["ocr"]["readable"] is True
    assert body["elapsed_ms"] > 0
    assert body["filename"] == "label_correct.png"


def test_wine_without_alcohol_content_skips_that_check(client: TestClient) -> None:
    form = {"brand_name": "Chateau Meridian", "class_type": "Napa Valley Cabernet Sauvignon",
            "net_contents": "750 mL", "beverage_type": "wine"}
    body = post(client, "label_wine.png", form)
    assert "alcohol_content" not in statuses(body)
    assert body["skipped"] == ["Alcohol content"]
    assert body["overall"] == "match"


def test_spirits_requires_alcohol_content(client: TestClient) -> None:
    form = {"brand_name": "OLD TOM DISTILLERY", "class_type": "Bourbon", "net_contents": "750 mL"}
    with (SAMPLES / "label_correct.png").open("rb") as handle:
        response = client.post("/api/verify", files={"image": ("label.png", handle)}, data=form)
    assert response.status_code == 422
    assert response.json()["detail"] == "Please fill in: Alcohol content."


def test_unknown_beverage_type_is_rejected(client: TestClient) -> None:
    form = {"brand_name": "A", "class_type": "B", "net_contents": "750 mL", "beverage_type": "cider"}
    with (SAMPLES / "label_correct.png").open("rb") as handle:
        response = client.post("/api/verify", files={"image": ("label.png", handle)}, data=form)
    assert response.status_code == 422


def test_blank_image_gives_review_not_rejection(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 200, 200)).save(buffer, "PNG")
    data = {name: value for name, value in applications["label_correct.png"].items() if name != "filename"}
    response = client.post("/api/verify", files={"image": ("blank.png", buffer.getvalue())}, data=data)
    body = response.json()
    assert response.status_code == 200
    assert body["overall"] == "review"
    assert set(statuses(body).values()) == {"review"}
    assert body["ocr"]["passes"] == 2


def test_file_that_is_not_an_image(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    data = {name: value for name, value in applications["label_correct.png"].items() if name != "filename"}
    response = client.post("/api/verify", files={"image": ("notes.txt", b"this is not a picture")}, data=data)
    assert response.status_code == 400
    assert "not a picture" in response.json()["detail"]


def test_file_over_the_size_limit(client: TestClient) -> None:
    too_big = b"\0" * (MAX_UPLOAD_BYTES + 200_000)
    response = client.post("/api/verify", files={"image": ("big.png", too_big)}, data={"brand_name": "A"})
    assert response.status_code == 413
    assert "15 MB" in response.json()["detail"]


def test_missing_image_gives_a_plain_message(client: TestClient) -> None:
    response = client.post("/api/verify", data={"brand_name": "A"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["cache-control"] == "no-store"


def test_batch_template_download(client: TestClient) -> None:
    response = client.get("/api/batch-template.csv")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    header = next(csv.reader(io.StringIO(response.text)))
    assert header == ["filename", "brand_name", "class_type", "alcohol_content", "net_contents", "beverage_type"]


def test_front_end_is_served_without_outside_resources(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert "Check one label" in page.text and "Check many labels" in page.text
    for path in ("/", "/static/style.css", "/static/app.js"):
        text = client.get(path).text
        assert "http://" not in text and "https://" not in text


# ---------- Several pictures for one application ----------

PAIR = "label_front.png;label_back.png"


def post_many(client: TestClient, filenames: list[str], form: dict[str, str]):
    data = {name: value for name, value in form.items() if name != "filename"}
    files = [("image", (name, (SAMPLES / name).read_bytes())) for name in filenames]
    return client.post("/api/verify", files=files, data=data)


def by_field(body: dict) -> dict[str, dict]:
    return {check["field"]: check for check in body["fields"]}


def test_front_and_back_together_match(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    started = time.perf_counter()
    response = post_many(client, PAIR.split(";"), applications[PAIR])
    elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.text
    body = response.json()
    assert statuses(body) == ALL_MATCH
    assert body["overall"] == "match"
    checks = by_field(body)
    assert checks["brand_name"]["picture"] == 1 and checks["brand_name"]["picture_name"] == "label_front.png"
    assert checks["net_contents"]["picture"] == 1
    assert checks["government_warning"]["picture"] == 2
    assert checks["government_warning"]["picture_name"] == "label_back.png"
    assert [picture["filename"] for picture in body["pictures"]] == PAIR.split(";")
    assert "GOVERNMENT WARNING" in body["pictures"][1]["text"]
    assert "GOVERNMENT WARNING" not in body["pictures"][0]["text"]
    assert elapsed < TIME_LIMIT_SECONDS
    assert body["elapsed_ms"] < TIME_LIMIT_SECONDS * 1000


def test_order_of_the_pictures_does_not_matter(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    body = post_many(client, PAIR.split(";")[::-1], applications[PAIR]).json()
    assert body["overall"] == "match"
    assert by_field(body)["government_warning"]["picture"] == 1


def test_front_alone_asks_for_the_other_side(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    body = post(client, "label_front.png", applications[PAIR])
    warning = by_field(body)["government_warning"]
    assert warning["status"] == "review"
    assert "other side" in warning["reason"]
    assert warning["picture"] is None
    assert body["overall"] == "review"


def test_back_alone_asks_for_the_other_side(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    body = post(client, "label_back.png", applications[PAIR])
    checks = by_field(body)
    assert checks["government_warning"]["status"] == "match"
    # The back label names the bottler, "Old Tom Distillery", so the brand is on this picture.
    assert checks["brand_name"]["status"] == "match"
    for name in ("class_type", "alcohol_content", "net_contents"):
        assert checks[name]["status"] == "review"
        assert "other side" in checks[name]["reason"]
    assert "mismatch" not in statuses(body).values()


def test_two_pictures_with_no_warning_is_a_mismatch(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    """With more than one picture the set is taken as complete, so a warning on none of them is a finding."""
    response = post_many(client, ["label_front.png", "label_missing_warning.png"], applications[PAIR])
    warning = by_field(response.json())["government_warning"]
    assert warning["status"] == "mismatch"
    assert "any of the 2 pictures" in warning["reason"]
    assert warning["picture"] is None


def test_wrong_value_is_still_caught_with_two_pictures(
    client: TestClient, applications: dict[str, dict[str, str]]
) -> None:
    response = post_many(client, ["label_wrong_abv.png", "label_back.png"], applications[PAIR])
    body = response.json()
    alcohol = by_field(body)["alcohol_content"]
    assert alcohol["status"] == "mismatch"
    assert alcohol["picture"] == 1
    assert body["overall"] == "mismatch"


def test_wrong_brand_with_two_pictures_is_a_mismatch(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    form = {**applications[PAIR], "brand_name": "Silver Creek"}
    brand = by_field(post_many(client, PAIR.split(";"), form).json())["brand_name"]
    assert brand["status"] == "mismatch"


def test_wrong_brand_with_one_picture_never_passes(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    """One picture cannot prove a name is absent from the product, so this is review, never a match."""
    form = {**applications["label_correct.png"], "brand_name": "Silver Creek"}
    brand = by_field(post(client, "label_correct.png", form))["brand_name"]
    assert brand["status"] == "review"
    assert brand["found"] == ""


def test_reworded_warning_on_the_back_is_still_a_mismatch(
    client: TestClient, applications: dict[str, dict[str, str]]
) -> None:
    response = post_many(client, ["label_front.png", "label_reworded_warning.png"], applications[PAIR])
    warning = by_field(response.json())["government_warning"]
    assert warning["status"] == "mismatch"
    assert warning["picture"] == 2


def test_single_picture_response_keeps_its_old_shape(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    body = post(client, "label_correct.png", applications["label_correct.png"])
    assert body["filename"] == "label_correct.png"
    assert len(body["pictures"]) == 1 and body["pictures"][0]["number"] == 1
    assert body["ocr"]["text"] == body["pictures"][0]["text"]
    assert all(check["picture"] == 1 for check in body["fields"])
    assert body["ocr"]["extra_reads"] == 0


def test_too_many_pictures(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    response = post_many(client, ["label_back.png"] * 7, applications[PAIR])
    assert response.status_code == 422
    assert "Too many pictures" in response.json()["detail"]


def test_one_bad_file_among_several_is_named(client: TestClient, applications: dict[str, dict[str, str]]) -> None:
    data = {name: value for name, value in applications[PAIR].items() if name != "filename"}
    files = [
        ("image", ("label_front.png", (SAMPLES / "label_front.png").read_bytes())),
        ("image", ("notes.txt", b"this is not a picture")),
    ]
    response = client.post("/api/verify", files=files, data=data)
    assert response.status_code == 400
    assert '"notes.txt"' in response.json()["detail"]


def test_batch_template_shows_two_pictures_for_one_label(client: TestClient) -> None:
    rows = list(csv.reader(io.StringIO(client.get("/api/batch-template.csv").text)))
    assert any(";" in row[0] for row in rows[1:])


def test_page_lets_the_user_add_and_take_pictures(client: TestClient) -> None:
    page = client.get("/").text
    script = client.get("/static/app.js").text
    for needed in ('id="camera-open"', 'id="camera-panel"', 'id="single-capture"', 'id="single-pictures"'):
        assert needed in page
    assert "multiple" in page.split('id="single-image"')[1].split(">")[0]
    for needed in ("openCamera", "snapPicture", "useCapturedFile", "addPictures", "removePicture"):
        assert needed in script
