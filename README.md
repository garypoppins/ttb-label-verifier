# TTB Label Verifier

A prototype web app that checks an alcohol beverage label image against the
values on its label application. It reads the label with OCR that runs on the
server itself, then compares brand name, class/type, alcohol content, net
contents and the government health warning, and, when they are filled in, the
bottler or producer and the country of origin. Each item comes back as Match,
Needs review or Does not match, with a one-line reason.

It was built for the Treasury (TTB) take-home exercise. Design choices,
assumptions and limits are in [APPROACH.md](APPROACH.md).

- No outside AI or cloud services are called. The OCR models are included in
  the installed packages.
- Uploaded images are processed in memory and are not saved.
- One application can have several pictures (front, back, neck). An item counts
  as found if it is on any of them, and the result says which picture.
- One sample label takes about half a second to one and a half seconds on a
  desktop CPU limited to two threads. Phone photos of real bottles took about
  two seconds per picture (measured; see APPROACH.md for the numbers).
- I tested it on 34 photos of real labels as well as on the generated samples.
  The results, including where it still falls short, are in
  [Tested on real photos](#tested-on-real-photos).

## Quick start

Requires Python 3.12 (3.11 to 3.13 should also work; use a released version,
not a pre-release build).

### Windows (PowerShell)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

### macOS and Linux

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn app.main:app --port 8000
```

Then open http://127.0.0.1:8000 in a browser. The first start takes a few
seconds while the OCR models load.

On Linux, OpenCV needs two system libraries that minimal installs may lack:
`sudo apt-get install libgl1 libglib2.0-0`.

## Using it

**Check one label.** Add the label pictures, type the application values, press
"Check this label". Results show the application value next to what was read
from the label, with the reason for each result and the time taken.

- **More than one picture.** Most bottles carry the brand on the front and the
  government warning on the back. Add both (up to 6 pictures for one label).
  Each added picture shows as a small preview with a "Remove" button. With
  several pictures, each result says which picture the item was found on.
- **Choose a picture** opens the file chooser. You can pick several files at
  once, press it again to add more, or drag pictures into the box.
- **Take a picture** uses the camera. On a phone or tablet it opens the
  device's own camera app. On a computer with a webcam it shows a live view in
  the page with an "Add this picture" button. Press it again for the next side.
  A browser only allows the camera on `https://` pages or on `localhost`.
- **One picture only.** If an item is not on the picture, the result is Needs
  review with "Not on this picture. Add the other side of the label." It is not
  a mismatch, because one picture cannot show that something is missing from
  the whole container. With two or more pictures the set is taken as complete:
  a warning or a brand that is on none of them is a mismatch, provided every
  picture read well.

**Optional items.** Under step 2 there is an "Optional" group with two more
fields. Leave them empty and they are not checked and do not appear in the
results.

- **Bottler or producer, with city and state**, for example
  `Old Tom Distillery, Bardstown, Kentucky`. You do not need to type "Bottled
  by", "Distilled and bottled by" or "Imported by". The name is compared like
  the brand name; the address counts as found when its parts (city, state) are
  on the label, and a two-letter state such as `KY` also matches "Kentucky".
  Name and address found is a match; only one of them is Needs review.
- **Country of origin**, for example `Mexico`. The label must state it, as in
  "Product of Mexico", "Made in Mexico", "Imported from Mexico", "Produce of
  Mexico" or "Hecho en México". Common variants count as the same country (USA,
  U.S.A., United States; UK, United Kingdom; Scotland is part of the United
  Kingdom). A label that clearly states a different country is a mismatch. The
  country named without such a statement, or not found, is Needs review.

**Check many labels.** Choose all the pictures, then choose one CSV file with a
row per label. The page shows progress, a results table (problems first) with
details on request, and a "Download results (CSV)" button. A blank CSV can be
downloaded from the page. Columns:

```
filename,brand_name,class_type,alcohol_content,net_contents,beverage_type,bottler_name_address,country_of_origin
```

`beverage_type` is optional (spirits, wine or beer; default spirits).
`bottler_name_address` and `country_of_origin` are optional too: the columns can
be left out of the file, or left empty on any row. If one
label has several pictures, put them all in the `filename` column with a
semicolon between them:

```
front_0002.jpg;back_0002.jpg,CHATEAU MERIDIAN,Napa Valley Cabernet Sauvignon,13.5% Alc. by Vol.,750 mL,wine
```

To try it with the bundled samples, choose every image in `samples/` and then
`samples/batch.csv`. One row of that list is a front and back pair, and the
last row is an imported tequila with a bottler and a country of origin.
Expected results are listed in [samples/README.md](samples/README.md).

## Running the tests

```bash
.venv/bin/python -m pytest          # Windows: .venv\Scripts\python -m pytest
```

The suite has unit tests for every check (text in, result out) and end-to-end
tests that send the sample images through the HTTP API, one picture at a time
and as a front and back pair. Each end-to-end label test asserts the expected
status per field and that the response took under 5 seconds.
`tests/test_real_photo_fixes.py` holds one test for each failure found on real
photos; each test names the photo it came from. The real photos themselves are
third-party images and are not in the repository.

## Docker

```bash
docker build -t ttb-label-verifier .
docker run --rm -p 8000:8000 ttb-label-verifier
```

The image is CPU only. The build runs one OCR read so that a broken install
fails at build time. Note: the Dockerfile was written but not built on the
development machine, which has no Docker installed.

## Configuration

| Environment variable | Default | Meaning |
|---|---|---|
| `OCR_THREADS` | `2` | CPU threads ONNX Runtime may use. Keep at or below the number of cores. |
| `OCR_MAX_SIDE` | `1600` | Larger images are scaled down so the long side is this many pixels. Lower is faster, higher reads smaller print. |
| `OCR_MEMORY_ARENA` | `1` | `1` is faster and used about 700 MB of memory in testing. `0` used about 150 MB and was up to twice as slow on labels with line lengths not seen before. Use `0` on a small or shared server. |
| `OCR_EXTRA_READ` | `1` | When an item is still unconfirmed, the picture gets a second look for text the first read skipped. `0` turns this off: faster on photos, fewer matches. |
| `OCR_EXTRA_READ_BUDGET` | `3.0` | Seconds per picture after which no new second look starts. When time runs out, "not found" becomes Needs review instead of a mismatch. |

## Deployment notes

- Run one worker process. Label reads are done one at a time inside the
  process so that a small server stays responsive. More workers each load
  their own copy of the models.
- Memory: about 700 MB with the default settings, about 150 MB with
  `OCR_MEMORY_ARENA=0` (measured on Windows after 24 labels). If the service
  runs under a memory cap below about 900 MB, set `OCR_MEMORY_ARENA=0`.
- Put a reverse proxy in front for HTTPS and set its upload limit to 91 MB or
  more (the app accepts up to 6 pictures of up to 15 MB each in one request).
  Example for nginx: `client_max_body_size 91m;` and
  `proxy_pass http://127.0.0.1:8000;`. HTTPS is also what lets the browser use
  the camera.
- Example systemd command:
  `ExecStart=/srv/labelcheck/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`
  with `Environment=OCR_THREADS=2` and, on a shared host, `MemoryMax=` and
  `CPUQuota=` limits.
- The app needs no outbound network access at run time, no database and no
  writable disk.
- There is no sign-in. Add authentication at the proxy before exposing it
  beyond a demonstration.

## API reference

### `GET /api/health`

Returns `{"status": "ok", "ocr_threads": 2, "max_image_side": 1600}` once the
models are loaded.

### `GET /api/batch-template.csv`

Downloads a blank CSV with the batch columns and two example rows, one with a
single picture and one with a front and back pair.

### `POST /api/verify`

Multipart form.

| Field | Required | Notes |
|---|---|---|
| `image` | yes | JPG, PNG or another common image type, up to 15 MB. Send the field once for one picture, or repeat it (up to 6 times) for several pictures of the same product. |
| `brand_name` | yes | |
| `class_type` | yes | |
| `alcohol_content` | spirits only | For example `45% Alc./Vol. (90 Proof)` or `45%`. If left empty for wine or beer, the item is skipped. |
| `net_contents` | yes | Amount with unit, for example `750 mL` |
| `beverage_type` | no | `spirits` (default), `wine` or `beer` |
| `bottler_name_address` | no | For example `Old Tom Distillery, Bardstown, Kentucky`. Checked only when filled in. |
| `country_of_origin` | no | For example `Mexico`. Checked only when filled in. |

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/verify \
  -F "image=@samples/label_correct.png" \
  -F "brand_name=OLD TOM DISTILLERY" \
  -F "class_type=Kentucky Straight Bourbon Whiskey" \
  -F "alcohol_content=45% Alc./Vol. (90 Proof)" \
  -F "net_contents=750 mL" \
  -F "beverage_type=spirits"
```

Front and back together:

```bash
curl -X POST http://127.0.0.1:8000/api/verify \
  -F "image=@samples/label_front.png" \
  -F "image=@samples/label_back.png" \
  -F "brand_name=OLD TOM DISTILLERY" \
  -F "class_type=Kentucky Straight Bourbon Whiskey" \
  -F "alcohol_content=45% Alc./Vol. (90 Proof)" \
  -F "net_contents=750 mL"
```

Response (shortened):

```json
{
  "filename": "label_correct.png",
  "beverage_type": "spirits",
  "overall": "match",
  "fields": [
    {
      "field": "brand_name",
      "label": "Brand name",
      "status": "match",
      "expected": "OLD TOM DISTILLERY",
      "found": "OLD TOM DISTILLERY",
      "reason": "Label matches the application.",
      "parts": [],
      "picture": 1,
      "picture_name": "label_correct.png"
    },
    {
      "field": "government_warning",
      "label": "Government warning",
      "status": "match",
      "expected": "GOVERNMENT WARNING: (1) According to ...",
      "found": "GOVERNMENT WARNING: (1) According to ...",
      "reason": "The wording matches word for word. The heading is in capital letters and appears bold.",
      "parts": [
        {"name": "wording", "status": "match", "reason": "..."},
        {"name": "capitals", "status": "match", "reason": "..."},
        {"name": "bold", "status": "match", "reason": "..."}
      ]
    }
  ],
  "skipped": [],
  "elapsed_ms": 912,
  "pictures": [
    {"number": 1, "filename": "label_correct.png", "elapsed_ms": 910, "text": "EST. 1892\nOLD TOM\n...",
     "lines": 12, "confidence": 0.906, "readable": true, "passes": 1}
  ],
  "ocr": {"text": "EST. 1892\nOLD TOM\n...", "lines": 12, "confidence": 0.906, "readable": true, "passes": 1, "extra_reads": 0}
}
```

`status` and `overall` are one of `match`, `review` or `mismatch`. `overall` is
the worst of the field statuses. The optional `bottler_name_address` and
`country_of_origin` items appear in `fields` only when a value was sent; when
empty they are left out entirely (not listed in `skipped`, which is for a
required item left empty, such as alcohol content for wine). Each field has `picture` (1 for the first
picture sent) and `picture_name`, saying where the value was found, or `null`
and an empty name when it was not found on any picture. `pictures` has one
entry per picture with the text read from it. `ocr` is the same information
for all pictures together and is kept so that a single-picture client written
against the earlier version still works. `elapsed_ms` is the server time to
read and check all the pictures, not counting the upload. Pictures are read one
after another, so two pictures take about twice as long as one. Errors return a
JSON body `{"detail": "plain sentence"}` with status 400 (not an image), 413
(a picture over 15 MB) or 422 (a required value is missing, or more than 6
pictures). When several pictures are sent, the message names the file at fault.

## Tested on real photos

The bundled samples are drawn by a script. To see how the app does on real
labels I collected 34 photos of real bottles and cans and wrote down by eye
what each one prints, before running anything.

**Sources.** 31 photos from Open Food Facts (phone photos of US products
uploaded by its contributors) and 3 from Wikimedia Commons. I wanted to use the
TTB Public COLA Registry, because it has the real label artwork with the real
application data, but its pages answered my script with a browser challenge and
I did not try to get around that. So there is no agency data in this test, and
no flat label artwork. These are consumer photos: curved bottles, glare, text
running up the side, script lettering. They are harder than the artwork an
applicant submits. The photos are other people's images and are not in this
repository.

**What I measured.** 15 spirits, 10 wine, 9 beer. 10 photos show the complete
warning. Each photo was checked alone with the correct application values, and
13 more runs used one deliberately wrong value. For 9 products I had both sides
and also checked front and back together.

The first run, with the app as first written, was poor: 18 of 34 correct labels
came back "Does not match". I then fixed what that run exposed and ran it
again.

One picture at a time, correct values (34 photos):

| | Before | After |
|---|---|---|
| Overall: match / needs review / does not match | 0 / 16 / 18 | 0 / 34 / 0 |
| Items clearly legible in the photo (76): match / review / mismatch | 39 / 28 / 9 | 49 / 27 / 0 |
| Brand name, legible (27) | 14 / 8 / 5 | 18 / 9 / 0 |
| Class or type, legible (17) | 8 / 6 / 3 | 11 / 6 / 0 |
| Alcohol content, legible (17) | 10 / 7 / 0 | 11 / 6 / 0 |
| Net contents, legible (15) | 7 / 7 / 1 | 9 / 6 / 0 |
| Complete, correct warning (10): match / review / mismatch | 0 / 6 / 4 | 3 / 7 / 0 |
| Items not on the photo marked as a match | 0 | 0 |
| Server time per photo, median and slowest | 1.2 s, 2.8 s | 2.1 s, 4.7 s |

One deliberately wrong value (13 runs, one picture each):

| | Before | After |
|---|---|---|
| Does not match / needs review / wrongly passed as match | 10 / 3 / 0 | 5 / 8 / 0 |

Front and back together (9 products). This mode did not exist before.

| | After |
|---|---|
| Overall: match / needs review / does not match | 0 / 9 / 0 |
| Items clearly legible on either photo (25): match / review / mismatch | 16 / 9 / 0 |
| Complete, correct warning (8 products): match / review / mismatch | 3 / 5 / 0 |
| One wrong value (6 runs): does not match / needs review / passed | 4 / 2 / 0 |
| Server time for the two photos, median and slowest | 4.5 s, 6.4 s |

**What this says, plainly.**

- The app no longer rejects a correct label in this set, and it has never
  passed a wrong value or matched something that was not in the picture.
- It still cannot clear a real bottle photo on its own. No photo and no front
  and back pair came out as a full match. The best pairs had four of five items
  matched and one sent to review (for example a blackletter brand read as
  "Jost uervo", and "750ML" read as "75IML").
- About a third of the items a person can read at a glance still come back
  Needs review, because the text reader misses lines of small, tightly spaced
  print and stumbles on script, arched and embossed lettering. That is a limit
  of the small OCR model, not of the checks.
- Being more careful cost some catches. Of the 13 wrong values, 5 are now a
  mismatch and 8 are Needs review. Two reasons: a brand or class that is simply
  absent from one picture is now "add the other side" instead of a mismatch
  (with both sides sent it is a mismatch again), and a number read from a
  picture that did not read cleanly is shown with a "check by eye" note instead
  of being rejected. None passed.
- Two real photos together took over 5 seconds for 3 of the 9 products.
- 34 photos is a small sample and I chose them. Treat the percentages as a
  guide, not as a measurement of field accuracy.

**The two optional items on real photos.** I added these after the run above
and checked them on the photos where they are printed, one picture at a time:

| | Match | Needs review | Does not match |
|---|---|---|---|
| Bottler or producer, clearly legible (10 photos) | 4 | 6 | 0 |
| Bottler or producer, hard to read (4 photos) | 0 | 4 | 0 |
| Country of origin, clearly legible (10 photos) | 4 | 6 | 0 |
| Country of origin, hard to read (1 photo) | 1 | 0 | 0 |
| Wrong country typed in (6 photos) | 0 | 3 | 3 |

No wrong country passed. The reviews are mostly the same OCR problem as above:
the importer line was not read ("Imported by Crown Imports, Chicago, IL" on the
Corona and Modelo back labels), or the statement was misread ("MADE IN MEXICO"
read as "WADEINMEXICO", which still finds "Mexico" and asks for a look).

## Repository layout

```
app/main.py        API routes, input validation, static hosting
app/ocr.py         image loading, orientation, OCR
app/checks.py      brand, class/type, alcohol content and net contents checks
app/warning.py     government warning text and its wording, capitals and bold checks
app/producer.py    optional bottler/producer and country of origin checks
static/            index.html, style.css, app.js (no build step, no outside resources)
samples/           generated test labels (including a front and back pair and an import), batch.csv, make_samples.py
tests/             unit tests and end-to-end tests; test_real_photo_fixes.py covers the real-photo failures
Dockerfile, requirements.txt, requirements-dev.txt, APPROACH.md
```
