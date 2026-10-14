"""Web API and static page hosting for the label verifier."""

from __future__ import annotations

import os
import time
from dataclasses import replace
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.formparsers import MultiPartParser

from app import ocr
from app.checks import (
    MATCH,
    MISMATCH,
    REVIEW,
    CheckResult,
    check_alcohol,
    check_net_contents,
    check_text_field,
    combine_pictures,
    overall_status,
)
from app.warning import check_warning

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
FORM_OVERHEAD_BYTES = 64 * 1024
# One application can have several pictures: front, back, neck, each side of a can.
MAX_PICTURES = 6
MAX_REQUEST_BYTES = MAX_PICTURES * MAX_UPLOAD_BYTES + FORM_OVERHEAD_BYTES
# Items still unconfirmed after the normal read get one more, looser read of each
# picture (see OcrEngine.read_again). No new extra read starts after this many
# seconds per picture, so a slow machine degrades to "needs review" instead of a long wait.
EXTRA_READ = os.environ.get("OCR_EXTRA_READ", "1") != "0"
EXTRA_READ_BUDGET_SECONDS = float(os.environ.get("OCR_EXTRA_READ_BUDGET", "3.0"))

# Starlette moves uploads larger than 1 MB to a temporary file. Raising the
# limit above our size cap keeps every accepted image in memory only.
MultiPartParser.spool_max_size = MAX_UPLOAD_BYTES + FORM_OVERHEAD_BYTES

# Which application fields must be filled in for each beverage type. Assumption:
# alcohol content is optional for wine and beer, because some wines and most
# malt beverages are not required to state it. The warning is always checked.
REQUIRED_FIELDS = {
    "spirits": ("brand_name", "class_type", "alcohol_content", "net_contents"),
    "wine": ("brand_name", "class_type", "net_contents"),
    "beer": ("brand_name", "class_type", "net_contents"),
}
FIELD_LABELS = {
    "brand_name": "Brand name",
    "class_type": "Class/type",
    "alcohol_content": "Alcohol content",
    "net_contents": "Net contents",
}
BATCH_TEMPLATE = (
    "filename,brand_name,class_type,alcohol_content,net_contents,beverage_type\r\n"
    'label_0001.jpg,OLD TOM DISTILLERY,Kentucky Straight Bourbon Whiskey,"45% Alc./Vol. (90 Proof)",750 mL,spirits\r\n'
    "label_0002_front.jpg;label_0002_back.jpg,CHATEAU MERIDIAN,Napa Valley Cabernet Sauvignon,"
    "13.5% Alc. by Vol.,750 mL,wine\r\n"
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Load and warm the OCR models before the first request."""
    ocr.get_engine().warm_up()
    yield


app = FastAPI(title="TTB Label Verifier", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def limit_and_label(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Refuse oversized uploads before reading them, and stop API responses being cached."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
        detail = "These pictures are too big. The limit is 15 MB for each picture."
        return JSONResponse({"detail": detail}, status_code=413)
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def plain_validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
    """Replace FastAPI's technical validation output with one plain sentence."""
    return JSONResponse({"detail": "Please choose a label picture and fill in the form."}, status_code=422)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    """Liveness check. The models are loaded before the server accepts requests."""
    return {"status": "ok", "ocr_threads": ocr.THREADS, "max_image_side": ocr.MAX_SIDE}


@app.get("/api/batch-template.csv")
def batch_template() -> Response:
    """Blank list for "Check many labels", with one example row."""
    headers = {"Content-Disposition": 'attachment; filename="label-list-template.csv"'}
    return Response(BATCH_TEMPLATE, media_type="text/csv", headers=headers)


def _field_checks(values: dict[str, str], single: bool) -> dict[str, Callable[[ocr.OcrResult], CheckResult]]:
    """One function per item to check, each taking the text read from one picture."""
    checks: dict[str, Callable[[ocr.OcrResult], CheckResult]] = {}
    for name in ("brand_name", "class_type"):
        checks[name] = lambda read, name=name: check_text_field(
            name, FIELD_LABELS[name], values[name], read.texts, read.readable, other_side_possible=single
        )
    if values["alcohol_content"]:
        checks["alcohol_content"] = lambda read: check_alcohol(
            values["alcohol_content"], read.texts, read.readable, other_side_possible=single
        )
    checks["net_contents"] = lambda read: check_net_contents(
        values["net_contents"], read.texts, read.readable, other_side_possible=single
    )
    checks["government_warning"] = lambda read: check_warning(read, other_side_possible=single)
    return checks


def _confirmed_by_extra_read(name: str, first: CheckResult, again: CheckResult) -> CheckResult | None:
    """Decide whether the looser second read may replace the result of the normal read.

    The looser read finds more text and more noise. It may confirm an item, or
    locate a warning the normal read did not see at all. It may also show that
    the picture has text that could not be read, which turns a "not found" into
    "needs review". It never turns an item into a mismatch, and it cannot
    overrule a capitals or bold finding.
    """
    if again.status == MATCH:
        if name == "government_warning" and any(part.status != MATCH for part in first.parts[1:]):
            return None
        return again
    if name == "government_warning" and not first.found and again.parts:
        if again.status == MISMATCH:
            reason = again.reason + " This came from a second, less certain read. Check by eye."
            return CheckResult(again.field, again.label, REVIEW, again.expected, again.found, reason, again.parts)
        return again
    if first.status == MISMATCH and not first.found and again.status == REVIEW:
        return again
    return None


def _unsettled(result: CheckResult) -> bool:
    """True for an item that was not confirmed and was not contradicted by text read from the label."""
    return result.status == REVIEW or (result.status == MISMATCH and not result.found)


@app.post("/api/verify")
def verify(
    image: list[UploadFile] = File(...),
    brand_name: str = Form(""),
    class_type: str = Form(""),
    alcohol_content: str = Form(""),
    net_contents: str = Form(""),
    beverage_type: str = Form("spirits"),
) -> dict:
    """Read one or more pictures of the same product and compare them with the application values.

    Send the ``image`` field once for a single picture, or several times for the
    front, back and other sides. An item counts as found if it is on any picture.
    """
    if len(image) > MAX_PICTURES:
        raise HTTPException(422, f"Too many pictures. The limit is {MAX_PICTURES} for one label.")

    several = len(image) > 1
    uploads: list[tuple[str, bytes]] = []
    for upload in image:
        name = upload.filename or ""
        which = f'"{name}"' if several and name else "This picture"
        data = upload.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"{which} is too big. The limit is 15 MB.")
        if not data:
            raise HTTPException(400, f"{which} is empty." if several else "The picture file is empty.")
        uploads.append((name, data))

    beverage_type = beverage_type.strip().lower() or "spirits"
    if beverage_type not in REQUIRED_FIELDS:
        raise HTTPException(422, "Beverage type must be spirits, wine or beer.")
    values = {
        "brand_name": brand_name.strip(),
        "class_type": class_type.strip(),
        "alcohol_content": alcohol_content.strip(),
        "net_contents": net_contents.strip(),
    }
    missing = [FIELD_LABELS[name] for name in REQUIRED_FIELDS[beverage_type] if not values[name]]
    if missing:
        raise HTTPException(422, "Please fill in: " + ", ".join(missing) + ".")
    started = time.perf_counter()
    engine = ocr.get_engine()
    reads: list[ocr.OcrResult] = []
    timings: list[int] = []
    for name, data in uploads:
        picture_started = time.perf_counter()
        try:
            reads.append(engine.read(data))
        except ocr.ImageError as error:
            which = f'"{name}"' if several and name else "This file"
            raise HTTPException(400, f"{which} is not a picture we can open. Use a JPG or PNG photo.") from error
        timings.append(round((time.perf_counter() - picture_started) * 1000))

    names = [name for name, _ in uploads]
    checks = _field_checks(values, single=not several)
    per_picture = {name: [check(read) for read in reads] for name, check in checks.items()}

    # One more, looser read of each picture for whatever is still unconfirmed.
    extra_reads = 0
    ran_out_of_time = False
    if EXTRA_READ:
        for index, read in enumerate(reads):
            open_items = [
                name
                for name, results in per_picture.items()
                if not any(result.status == MATCH for result in results) and _unsettled(results[index])
            ]
            if not open_items:
                continue
            if time.perf_counter() - started > EXTRA_READ_BUDGET_SECONDS * len(reads):
                ran_out_of_time = True
                break
            extra_started = time.perf_counter()
            again = engine.read_again(read)
            extra_reads += 1
            for name in open_items:
                better = _confirmed_by_extra_read(name, per_picture[name][index], checks[name](again))
                if better is not None:
                    per_picture[name][index] = better
            timings[index] += round((time.perf_counter() - extra_started) * 1000)

    results = [combine_pictures(per_picture[name], names) for name in checks]
    if ran_out_of_time:
        # "Not found" is only a mismatch when every picture also had its second look.
        # If time ran out before that, say so instead of rejecting.
        reason = "Not found on these pictures, but there was no time for a second look. Check by eye."
        results = [
            replace(result, status=REVIEW, reason=reason) if result.status == MISMATCH and not result.found else result
            for result in results
        ]
    letters = sum(len(line.text) for read in reads for line in read.lines)
    weighted = sum(line.confidence * len(line.text) for read in reads for line in read.lines)

    return {
        "filename": names[0],
        "beverage_type": beverage_type,
        "overall": overall_status(results),
        "fields": [result.to_dict() for result in results],
        "skipped": [FIELD_LABELS[name] for name in values if not values[name]],
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "pictures": [
            {
                "number": index + 1,
                "filename": names[index],
                "elapsed_ms": timings[index],
                "text": read.text,
                "lines": len(read.lines),
                "confidence": round(read.confidence, 3),
                "readable": read.readable,
                "passes": read.passes,
            }
            for index, read in enumerate(reads)
        ],
        "ocr": {
            "text": "\n".join(read.text for read in reads if read.text),
            "lines": sum(len(read.lines) for read in reads),
            "confidence": round(weighted / letters, 3) if letters else 0.0,
            "readable": all(read.readable for read in reads),
            "passes": max(read.passes for read in reads),
            "extra_reads": extra_reads,
        },
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
