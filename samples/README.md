# Sample labels

These images are drawn by `make_samples.py` with Pillow. They are made-up
products, not real labels. `batch.csv` holds the application values for each
one, so the whole folder can be used with "Check many labels": choose all the
images, then choose `batch.csv`.

One row of `batch.csv` shows how to give one label several pictures: the
`filename` column lists them with a semicolon between,
`label_front.png;label_back.png`. Both pictures are checked as one application.

`batch.csv` also has the two optional columns, `bottler_name_address` and
`country_of_origin`. They are filled in for `label_correct.png`, the front and
back pair (bottler only) and `label_import.png` (both), and empty elsewhere, so
those other rows check the five usual items only.

To regenerate the images (fonts differ between operating systems, so the pixels
will differ slightly from the committed files, which were made on Windows):

```
python samples/make_samples.py
```

## Expected results

Application values for the Old Tom labels are: OLD TOM DISTILLERY, Kentucky
Straight Bourbon Whiskey, 45% Alc./Vol. (90 Proof), 750 mL, and where given,
bottler "Old Tom Distillery, Bardstown, Kentucky". The Old Tom labels print
"Distilled and Bottled by Old Tom Distillery, Bardstown, Kentucky".

| File | What is different about it | Expected overall | Expected detail |
|---|---|---|---|
| `label_correct.png` | Nothing. Correct label with a bold, capitalised warning heading. | Match | All five items match. |
| `label_wrong_abv.png` | Label prints 40% Alc./Vol. (80 Proof). | Does not match | Alcohol content is a mismatch. Everything else matches. |
| `label_titlecase_warning.png` | Heading printed as "Government Warning:". | Does not match | Warning is a mismatch on capital letters. Wording still matches. |
| `label_reworded_warning.png` | Warning ends "can cause health issues." | Does not match | Warning is a mismatch on wording. First differing word reported: "may" (label has "can"). |
| `label_missing_warning.png` | No warning at all. | Needs review | Warning needs review: "No government warning on this picture. Add the other side of the label." One picture cannot show the warning is missing from the bottle. Checked together with a second picture that also has no warning (for example `label_front.png`), it is a mismatch. |
| `label_warning_not_bold.png` | Warning heading in regular weight. | Needs review | Warning needs review: heading does not look bold. |
| `label_brand_case.png` | Label prints STONE'S THROW. Application says Stone's Throw. | Match | Brand matches, with a note that only capital letters differ. |
| `label_rotated.jpg` | Correct label turned on its side (90 degrees). | Match | All five items match. |
| `label_tilted.jpg` | Correct label tilted 7 degrees on a dark background. | Match | All five items match. |
| `label_low_contrast.jpg` | Correct label, washed out, blurred, with noise. | Needs review | Nothing is reported as a mismatch. The warning heading needs review because one letter reads unclearly. Other items match. |
| `label_wine.png` | Wine label, 13.5% Alc. by Vol. | Match | All five items match. |
| `label_beer.png` | Beer label on a dark background, 12 FL OZ. Application says 355 mL. | Match | Net contents matches with a note that the units differ. |
| `label_front.png` and `label_back.png` together | Front label with no warning, back label with the bottler and the warning. One row in `batch.csv`. | Match | All five items match. Brand, class, alcohol and net contents are found on picture 1, the warning on picture 2. |
| `label_front.png` alone | Same front label, checked by itself. | Needs review | Four items match. The warning asks for the other side. |
| `label_back.png` alone | Same back label, checked by itself. | Needs review | The warning matches, and so does the brand, because the bottler line names it. Class, alcohol and net contents ask for the other side. |
| `label_import.png` | Imported tequila: Casa Solana, Tequila Blanco, 40% Alc./Vol. (80 Proof), 750 mL, "PRODUCT OF MEXICO" at the top and "Imported by Solana Spirits, San Antonio, Texas". The application gives bottler "Solana Spirits, San Antonio, Texas" and country "Mexico". | Match | All seven items match. |

The automated tests in `tests/test_api.py` assert these results. For the
low-contrast label the test only asserts that nothing is reported as a
mismatch and that the numbers are read correctly, because which letters blur
can change between machines.

## Notes on the samples

- The labels use Georgia, which has old-style numerals. The OCR reads the zero
  in "90 Proof" as a letter o. The app corrects a letter o that directly
  follows a digit before comparing numbers, and these samples exercise that.
- These are clean, flat, synthetic images. They show that the checks behave as
  intended. They do not show how the app performs on photos of curved bottles
  with glare. For that, see "Tested on real photos" in `APPROACH.md`. The real
  photos used there are third-party images and are not in this folder.
