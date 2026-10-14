# Approach

This note explains what was built, why it was built this way, what was assumed,
and where the prototype falls short.

## What the stakeholders asked for and how it is met

| Requirement | Source in the interview notes | How the prototype meets it |
|---|---|---|
| Check that the label matches the application: brand name, class/type, alcohol content, net contents, government warning | Sarah Chen ("Brand name matches? ABV is correct? Warning is there?"); sample label fields in the instructions | The label is read with OCR, then one check runs per item. Each returns Match, Needs review or Does not match, the two values side by side, and a one-line reason. |
| Name and address of the bottler or producer; country of origin for imports | Listed in the instructions among the common label elements | Covered as two optional fields, checked only when filled in (see Assumptions for why optional). The bottler name is compared like the brand and the address by its city and state; the country must appear in a statement such as "Product of Mexico". |
| Results in about 5 seconds, or nobody will use it | Sarah Chen, on the failed scanning vendor pilot | OCR runs on the server with the models loaded once at start. Large images are scaled down first. Measured time per sample label is 0.5 to 1.6 seconds on the development machine, and about 2 seconds for a phone photo of a real bottle. The time is shown on screen. Tests fail if a label takes 5 seconds. Two real photos checked together took 4.5 seconds at the median and went over 5 seconds for 3 of 9 products (see "Tested on real photos"). |
| Something a 73-year-old who is not technical could use; no hunting for buttons | Sarah Chen; Dave Morrison ("don't make my life harder") | One page. Two large buttons choose the mode. Numbered steps. Large type, high contrast, plain words. Results are coloured and also labelled in words with a symbol, so colour is not the only signal. |
| Batches of 200 to 300 labels at once | Sarah Chen, Janet in Seattle | "Check many labels": choose all the pictures and one CSV list. A progress bar counts labels as they finish. The results table puts problems first, shows details on request, and downloads as CSV. |
| The network blocks outside ML endpoints | Marcus Williams | No outside calls of any kind. The OCR models are files inside an installed Python package. The page loads no outside fonts, scripts or styles. A test checks the page for outside links. |
| Judgment on near matches such as "STONE'S THROW" against "Stone's Throw" | Dave Morrison | Names are compared after ignoring capital letters, punctuation, accents and spacing. That case is a Match with the note "Only capital letters, spacing or punctuation differ." A close but different name is Needs review, not a rejection. |
| The warning must be exact, word for word, with "GOVERNMENT WARNING:" in capitals and bold | Jenny Park | The text is compared against the wording in 27 CFR 16.21 and the first differing word is reported. The heading must be in capitals; title case is a mismatch. Bold is estimated from the image (see below). |
| Photos at odd angles or in poor light | Jenny Park (marked as possibly out of scope) | Sideways, upside-down and tilted pictures are handled; a picture whose text runs up the side is turned upright before it is read. A second read with boosted contrast is tried when the first is poor. When text still cannot be read the item is Needs review with "try a clearer photo", never a pass and never an automatic rejection. The page can also take the picture with the device camera. On 34 real photos this held: no correct label was rejected, but about a third of legible items still needed review. |
| Front and back labels | Not asked for directly. Found when I tested on real bottles: the brand is on the front and the warning on the back | One application can have up to 6 pictures. An item counts as found if it is on any of them, and the result names the picture. |
| No sensitive data stored; standalone, not tied into COLA | Marcus Williams | Images are held in memory for the length of one request and never written to disk. There is no database and no COLA connection. |

## Design choices

**One small Python service.** FastAPI serves both the API and the page. The
front end is plain HTML, CSS and JavaScript with no build step, so there is
nothing to compile and the whole thing can be read in one sitting.

**Local OCR: RapidOCR (PP-OCR models on ONNX Runtime).** The firewall rules out
hosted vision models, and the 5 second limit rules out anything heavy on a CPU.
PP-OCR is a small neural text detector and recogniser that runs in well under a
second on a CPU and is generally better than classic Tesseract on stylised
type. The models are about 14 MB and ship inside the pip package.

**Text checks are ordinary code, not a language model.** Once the text is read,
comparing "45%" with "45%" does not need AI. Deterministic rules are fast, can
be unit tested, and give the same answer every time, which matters for a
compliance tool. The "AI" in this prototype is the OCR.

**Three outcomes, and doubt goes to the agent.** A mismatch is only reported
when the label was read well and clearly says something different. Anything
uncertain is Needs review with the reason. The tool is there to clear the
routine matches quickly, not to make the decision.

### How each check works

- **Brand name and class/type.** Both sides are reduced to letters and digits.
  The application value is compared (RapidFuzz similarity) with every run of
  consecutive words on the label, within a line or across up to three lines,
  so a brand printed on two lines is found. Equal is Match; 70% or more similar
  is Needs review; otherwise it is not found. The first match in reading order
  is reported, so the brand as displayed is shown rather than a later mention
  in the bottler statement. Three additions came from real photos. Punctuation
  is treated as a word break, so "KOMOS, INC." and "komos.com" contain the
  brand "Komos". If every word of the value is on the label within a few lines
  but with other text between ("TEQUILA", then the brand, then "ANEJO
  CRISTALINO"), it is a Match with a note to check the order. A name found only
  inside a longer unbroken run of letters is Needs review. "Not found" shows no
  "closest text", because on real labels the closest text was noise.
- **Alcohol content.** Percent and proof are parsed from both sides and compared
  as numbers. Percentages near the words alcohol, vol, ABV or proof are
  preferred, so "100% agave" is not mistaken for the strength. If the label's
  own proof is not twice its percent, the item is flagged for review. If the
  application gives a proof and the label's proof differs, it is a mismatch.
  Two guards came from real photos. If the number read and the number expected
  differ only by a decimal point (42 for 4.2), it is Needs review, because
  glare ate the point on a real can. And a different number read from a picture
  that did not read cleanly is Needs review with the number shown, not a
  mismatch.
- **Net contents.** The amount and unit are parsed and converted to millilitres
  (mL, cL, L, fl oz), then compared with 1% tolerance. "12 FL OZ" against
  "355 mL" is a Match with a note about the units. A serving size ("Serving
  size: 1.5 fl. oz.") is ignored. "12 OZ.", "12FL-OZ", the word "LITER" on its
  own and common misreads of "mL" are accepted. The decimal point and
  unclean-read guards apply here too.
- **Government warning.** Three parts, each shown separately:
  1. *Wording.* Compared character by character against the statutory text,
     ignoring punctuation, spacing and capital letters in the body, so that OCR
     running words together does not matter. A word that is missing, added, or
     changed by two or more letters is a mismatch and is named. A single-letter
     difference is Needs review, because it may be a misread; it never passes.
     Text that is missing exactly where one read line ends and the next begins,
     or after the last line read, is treated as "part of the warning was not
     read" (Needs review). On real photos the reader often skips a whole line or
     clips the start of one, and the first version reported that as changed
     wording on correct labels. A word changed or dropped inside a line is
     still a mismatch.
  2. *Capital letters.* The heading as read must be all capitals.
  3. *Bold.* See the next section.
- **Bottler or producer (optional).** Words such as "Bottled by", "Distilled
  and bottled by" or "Imported by" are dropped from the application value. The
  text before the first comma is the name and is compared like the brand name.
  The rest are address parts (city, state, country); each counts as found if it
  is on the label as a word, and a two-letter US state code also matches the
  state's full name. Name and every address part found is a Match. Only some of
  it is Needs review. Nothing found follows the same rules as the brand.
- **Country of origin (optional).** The label is searched for an origin
  statement: "Product of", "Produce of", "Made in", "Imported ... from",
  "Produced in", "Bottled in", "Hecho en", "Produit de" and a few others,
  followed by a country. Spellings are matched against a list of about 70
  countries with common variants (USA, U.S.A., United States, America; UK,
  United Kingdom, Great Britain; México). Scotland, England, Wales and Northern
  Ireland count as the United Kingdom. A statement naming the application's
  country is a Match. A statement naming another country is a mismatch on a
  clean read. The country named on its own without a statement, or not found,
  is Needs review.
- **Poor reads.** If the text was read with low confidence, or fewer than about
  40 letters were read in all, "not found" results become Needs review with a
  request for a clearer photo. Three fragments read confidently say nothing
  about the rest of the label.
- **Several pictures.** Each picture is read and checked on its own, then the
  results for each item are merged: a match on any picture wins; next a value
  read from a label that contradicts the application; next a near miss; and
  "not found" only when every picture read well and none shows the item. With
  one picture, an absent brand, class, number or warning is Needs review with
  "Add the other side of the label", because one picture cannot show that
  something is missing from the container.
- **A second look.** When an item is still unconfirmed, the app looks again for
  text boxes the first read skipped, using looser detector settings, and reads
  only those. Lines already read are kept as they were. The fuller text can
  confirm an item, or locate a warning the first read did not see, or show that
  the picture has text that could not be read (which turns "not found" into
  Needs review). It can never turn an item into a mismatch. It reuses the
  detector's output from the first read, so it costs little.

### The bold check, and how far to trust it

OCR does not report font weight. The app estimates it: it cuts out the heading
and the body text next to it, measures the average pen-stroke thickness in each
(ink area divided by half the outline length), and takes the ratio. A ratio of
1.25 or more is "appears bold". A ratio of 1.10 or less is "does not look
bold". In between is "could not confirm". Because this is an estimate, the bold
part can only produce Match or Needs review, never a mismatch.

Measured on 350 generated warning paragraphs (7 typefaces, 5 sizes, 5 conditions
including dark backgrounds and blurred JPEGs, each with a bold and a regular
heading):

| Heading really was | Said bold | Said not bold | Could not confirm |
|---|---|---|---|
| Bold (175) | 149 | 4 | 22 |
| Regular (175) | 0 | 168 | 7 |

It never called a regular heading bold. It is weakest on serif faces such as
Times, where bold is only slightly heavier, and on very small blurred text. All
of this was measured on computer-drawn images. It has not been measured on real
label photographs, where I expect it to answer "could not confirm" more often.

On the same 175 correctly worded paragraphs, the wording check returned Match
for 133 and Needs review for 42 (single misread characters, mostly at small
sizes), and never returned a mismatch.

## Measured performance

Development machine: Windows 11 desktop, 16 logical cores, with ONNX Runtime
limited to 2 threads to imitate a small server. Images are the 12 samples
(about 1000 by 1400 pixels, 7 to 12 lines of text).

| Setting | Server time per label | Memory |
|---|---|---|
| Default (2 threads) | 0.36 to 0.89 s, median 0.55 to 0.75 s across runs | about 700 MB |
| `OCR_MEMORY_ARENA=0` | 0.53 to 1.58 s, median 1.08 s on first sight of a label | about 150 MB |
| `OCR_THREADS=1` | 0.57 to 1.31 s, median 0.98 s | about 700 MB |

Timings on this machine varied by up to a factor of two between runs depending
on what else was running. A real 2-core cloud server will be slower per core. I
have not measured one. Labels with much more text (a full back label) take
longer, because recognition time grows with the amount of text.

A batch is sent three labels at a time, but the server reads one label at a
time, so 300 labels take roughly 300 times the single-label time: several
minutes. Each row appears as it finishes.

When I re-measured after the real-photo work, the same machine gave 0.5 to 0.6
seconds per sample label for the first few reads and 1.4 to 1.6 seconds once it
had been busy for a while, with the original code and the changed code alike.
So the table above is the good case. A sample turned on its side now takes
about 1.9 seconds, because it is detected twice (once to see that the text runs
sideways, once upright).

Real phone photos are slower than the samples: more pixels to decode, more
text, and a second read more often.

| Real photos, 2 threads | Median | 90th percentile | Slowest |
|---|---|---|---|
| One picture, first version (34 photos) | 1.2 s | 2.4 s | 2.8 s |
| One picture, now (34 photos) | 2.1 s | 3.1 s | 4.7 s |
| Front and back together, now (9 products) | 4.5 s | 5.2 s | 6.4 s |

Pictures of one application are read one after another, so the time adds up.
Three of the nine pairs went over 5 seconds.

## Tested on real photos

The first version was only ever run on labels drawn by a script. I then
collected 34 photos of real bottles and cans (31 from Open Food Facts, 3 from
Wikimedia Commons), wrote down by eye what each prints, and ran them. I could
not use the TTB Public COLA Registry: it answered my script with a browser
challenge and I did not try to get around it. So this test has consumer phone
photos, not the flat artwork an applicant submits, and no agency data.

The first run was bad. 18 of 34 correct labels came back "Does not match", 4 of
10 correct warnings were rejected with a confident reason, and nothing came
back as a full match. The causes, and what I changed:

| What went wrong on real photos | Change |
|---|---|
| The text detector skips whole lines of small print. A clipped line start or a missing last line was reported as changed warning wording. | Missing text at a line edge or after the last line read is "not read", Needs review. |
| A front label has no warning and a back label has no brand, so every photo ended as a mismatch. | Several pictures per application. With one picture, an absent item asks for the other side. |
| "4.2%" under glare was read as "42%" and rejected. A serving size was taken for the net contents. | Decimal point guard, serving sizes ignored, no numeric mismatch on an unclean read. |
| Four fragments read with high confidence counted as a good read, so "not found" was trusted. | A read needs a minimum amount of text to be trusted. |
| Warnings printed up the side of the bottle read badly or not at all. | The picture is turned upright first when most text boxes run up or down. |
| "BUSHMILLS&ASSOCIATED" and "komos.com" did not contain the brand; a class printed in pieces was not found; "Jost uervo" scored just under the cut-off. | Punctuation breaks words, all-words-present match, review from 70% similar. |
| "12FL-OZ", "12 OZ." and "LITER" were not understood. | More spellings accepted. |
| Lines of the warning were assembled in the wrong order on a tilted can. | Line height is taken from the detected box, not its upright rectangle. Collecting stops at "PROBLEMS." |

Results after the changes, same photos and same application values:

| One picture at a time (34 photos) | Before | After |
|---|---|---|
| Overall: match / review / mismatch | 0 / 16 / 18 | 0 / 34 / 0 |
| Legible items (76): match / review / mismatch | 39 / 28 / 9 | 49 / 27 / 0 |
| Complete, correct warning (10): match / review / mismatch | 0 / 6 / 4 | 3 / 7 / 0 |
| Absent items marked as a match | 0 | 0 |
| One wrong value (13 runs): mismatch / review / passed | 10 / 3 / 0 | 5 / 8 / 0 |

| Front and back together (9 products) | After |
|---|---|
| Overall: match / review / mismatch | 0 / 9 / 0 |
| Legible items (25): match / review / mismatch | 16 / 9 / 0 |
| Complete, correct warning (8): match / review / mismatch | 3 / 5 / 0 |
| One wrong value (6 runs): mismatch / review / passed | 4 / 2 / 0 |

What I take from this:

- The false rejections are gone on this set, and nothing wrong has passed.
- The app still does not clear a real bottle photo by itself. No photo and no
  pair was a full match. The three best pairs had four items matched and one in
  review.
- The remaining reviews are mostly the OCR model not reading text a person
  reads easily: dense small print, script, arched or embossed lettering, print
  on clear glass. I tried looser detector settings for the whole read. They
  found more text but also turned one correct warning into a mismatch, so I
  only use them for the second look, where they cannot reject anything. I did
  not change the OCR model.
- Catching wrong values got weaker in exchange. Before, 10 of 13 wrong values
  were a mismatch; now 5 are and 8 need review. The 5 that moved: a wrong brand
  and a wrong class on a single front photo (now "add the other side"; with
  two photos both are a mismatch again), two wrong net contents on photos that
  did not read cleanly (the number read is shown with "check by eye"), and one
  whose earlier mismatch had only come from a misread serving size. I think
  this is the right side to err on for a tool that advises an agent, but it is
  a real cost.
- 34 photos chosen by me is a small sample. The numbers are a guide.

The two optional items were added afterwards and checked on the photos where
they are printed. Bottler or producer: 4 match, 6 review, 0 mismatch on 10
clearly legible photos, and 4 review on 4 hard ones. Country of origin: 4 match,
6 review, 0 mismatch on 10 clearly legible photos, and 1 match on 1 hard one.
With a wrong country typed in (6 photos): 3 mismatch, 3 review, none passed. The
reviews come from the same OCR misses (importer lines not read) and misreads
("WADEINMEXICO").

Every one of these failures has a unit test in `tests/test_real_photo_fixes.py`
that names the photo it came from. The photos are third-party images and are
not in the repository.

## Assumptions

- **One picture may not show everything.** With a single picture, an item that
  is not on it is Needs review and the message asks for the other side. With
  two or more pictures the set is taken as complete.
- **A warning that is on none of several well-read pictures is a mismatch**,
  because it is mandatory. The same goes for a brand or class/type that is on
  none of them. Alcohol content and net contents that cannot be found are Needs
  review, since small print is often unreadable to OCR.
- **Beverage type only changes which fields are required.** Alcohol content is
  required for spirits and optional for wine and beer, since some wines and most
  malt beverages need not state it. Wine- and beer-specific rules (appellation,
  vintage, sulfite statement and so on) are not checked.
- **Capital letters in the warning body are not checked.** Many compliant labels
  print the whole warning in capitals. Only the heading's capitals are enforced.
- **Warning punctuation is not checked.** OCR drops commas and periods too often
  for that to be reliable.
- **The warning text itself** was confirmed against the eCFR text of 27 CFR
  16.21 on the day of writing. A test holds a second copy to catch accidental
  edits.
- **Bottler and country of origin are optional.** Not every review needs them,
  the form already asks for five values, and the exact wording rules differ by
  product (a domestic spirit needs no country statement; an import names the
  importer, not the distiller). So they are checked only when the agent fills
  them in. A different country clearly stated is a mismatch; a country not found
  is only Needs review, even with several pictures, because origin statements
  are small print and often missed by the OCR.
- Not checked at all: type size and legibility rules, placement of the warning
  "separate and apart", whether the bottler wording ("Bottled by" against
  "Imported by") is the right one for the product.

## Trade-offs

- **Review rather than reject.** The rules lean towards Needs review when the
  read is doubtful. This costs some automation (a correct label on a dim photo
  may come back yellow) in exchange for not rejecting correct labels.
- **Single-letter tolerance in the warning.** One wrong letter is Needs review,
  not a mismatch, so a real one-letter change ("of" to "or") is flagged yellow
  rather than red. It is never passed.
- **A warning that stops early is Needs review, not a mismatch.** The reader
  cannot tell a label that leaves off the second sentence from a photo where it
  failed to read the last lines, and on real photos the second is far more
  common. A truncated warning is therefore flagged yellow with "part of the
  warning was not read". It is never passed. The same holds for a word missing
  exactly at the start or end of a printed line.
- **One picture cannot prove absence.** A wrong brand typed against a single
  front photo is Needs review ("add the other side"), not a mismatch. Send both
  sides and it is a mismatch.
- **More passes on hard photos.** Turning sideways pictures upright and the
  second look for skipped text cost time: the median real photo went from 1.2
  to 2.1 seconds.
- **Speed against memory.** The default settings favour speed and use about
  700 MB. `OCR_MEMORY_ARENA=0` cuts that to about 150 MB and roughly doubles the
  time for labels with unfamiliar line lengths.
- **One read at a time.** Simple and predictable on a small server; it wastes
  spare cores on a large one.
- **Batch runs in the browser.** No job queue or storage is needed and progress
  is real, but the page must stay open and results are lost on reload unless
  downloaded.
- **RapidOCR internals.** Version 1.2.3 has no setting for thread count and its
  line-orientation step proved unreliable on long lines of small text (it read
  some upright lines as upside down). The app calls the detector, orientation
  classifier and recogniser itself, uses the classifier only as a vote, and
  confirms orientation by how well the text reads. This depends on the pinned
  version.

## Limitations

- **Real photos mostly end in Needs review.** On 34 real photos about a third
  of the items a person can read at a glance were not confirmed, and no photo
  or front and back pair was a full match (see "Tested on real photos"). The
  app is useful there for pointing at what it could confirm and what it could
  not, not for clearing the label.
- **Not tested on real label artwork.** The COLA registry was not reachable by
  script, so I have no measurement on the flat artwork applicants submit. I
  expect it to read better than phone photos and cannot say by how much.
- **The OCR model misses dense small print.** On real back labels the detector
  skipped whole lines of the warning while reading the rest well. One clear,
  in-focus neck label produced no text at all.
- **Decorative brand lettering** often fails to read: script, blackletter,
  foil, embossed metal. The brand check will then say needs review.
- **Two real photos can take more than 5 seconds** on two threads.
- **The camera needs HTTPS** (or localhost), and the phone camera path was not
  tested on a real phone. I tested the in-page live view in headless Chrome
  with a simulated camera.
- **Bold detection is an estimate** (see above).
- **Layout assumptions.** The warning is assumed to be one paragraph. Text
  wrapped around a curve, or split across panels, may be assembled wrongly.
- **Small OCR slips happen.** For example a zero in an old-style typeface read
  as the letter o. The number checks correct that one case. Others will surface
  as Needs review.
- **English only**, and US units plus metric. Pints, quarts and gallons are not
  parsed.
- **No sign-in, audit trail or saved history.** Suitable for a demonstration,
  not for production use with real application data.
- **The Dockerfile has not been built**; no Docker was available on the
  development machine.
- **Python version.** Developed and tested on Python 3.12. A pre-release build
  of 3.14 could not import FastAPI's dependencies, so use a released version.

## Tools used

- Python 3.12, FastAPI, Uvicorn, python-multipart
- RapidOCR 1.2.3 with ONNX Runtime (PP-OCRv3 detection and recognition models)
- OpenCV, Pillow and NumPy for image handling
- RapidFuzz for text similarity; Python's difflib for the warning comparison
- pytest and the FastAPI test client
- Pillow to draw the sample labels
- A headless Chromium browser (Playwright) was used during development to walk
  through the page; it is not a project dependency. The several-pictures and
  camera flows were checked in headless Chrome driven over its DevTools
  protocol, with a simulated camera
- Real-photo test images came from Open Food Facts and Wikimedia Commons. They
  are not redistributed here
- **AI coding assistance was used to build this.** Claude Code (Anthropic)
  was used to write much of the code, tests and documentation, working from a
  written design plan. No AI service is used by the running application.

## What would come next

1. **Test on real label artwork** from the public COLA registry, with the real
   application data, through an approved route rather than a script. The
   real-photo test here used consumer photos only.
2. **Azure deployment** in the existing tenant: a container on App Service or
   Container Apps, with Entra ID sign-in, private networking and logging that
   meet the agency's requirements.
3. **COLA integration**: pull the application values and label images by
   application ID instead of typing them, and write results back. This needs its
   own authorisation, as Marcus noted.
4. **A stronger text detector.** Front and back together is now done. What
   holds real photos back is the small OCR model skipping lines. A larger
   detection model, tried as a fallback when the small one leaves an item
   unconfirmed, is the first thing I would measure on the real-photo set.
5. **More of the checklist**: type
   size of the warning, wine and beer specific items.
6. **Better handling of hard photos**: perspective correction for curved
   bottles, glare detection, and reading pictures of one application in
   parallel so two photos fit in 5 seconds.
7. **Server-side batch jobs** with saved results for very large submissions, and
   parallel reads on larger servers.
8. **Accessibility review** against Section 508 with real assistive technology,
   and a usability session with agents.
