"use strict";

const MAX_BYTES = 15 * 1024 * 1024;
const MAX_PICTURES = 6;
const PARALLEL_REQUESTS = 3;
const CSV_COLUMNS = ["filename", "brand_name", "class_type", "alcohol_content", "net_contents"];
/** Application values that may be left out of the form and the CSV list. */
const OPTIONAL_COLUMNS = ["bottler_name_address", "country_of_origin"];
const STATUS_WORDS = {
  match: "✓ Match",
  review: "! Needs review",
  mismatch: "✗ Does not match",
  error: "✗ Could not check",
};
const VERDICTS = {
  match: ["Everything matches", "All checked items on the label agree with the application."],
  review: ["Needs a closer look", "Some items could not be confirmed. Please check the yellow items by eye."],
  mismatch: ["Something does not match", "At least one item on the label differs from the application. See the red items."],
};
const CSV_LINE_END = String.fromCharCode(13, 10);
const PART_NAMES = { wording: "Wording", capitals: "Capital letters", bold: "Bold heading" };

const $ = (id) => document.getElementById(id);

/* ---------- Small helpers ---------- */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showError(box, message) {
  box.textContent = message;
  box.hidden = !message;
  if (message) box.scrollIntoView({ block: "nearest" });
}

function imageProblem(file) {
  const looksLikeImage = file.type ? file.type.startsWith("image/") : /\.(jpe?g|png|webp|bmp|gif|tiff?)$/i.test(file.name);
  if (!looksLikeImage) return `"${file.name}" is not a picture. Please choose a JPG or PNG photo of the label.`;
  if (file.size > MAX_BYTES) return `"${file.name}" is too big. The limit is 15 MB.`;
  return "";
}

function seconds(ms) {
  return (ms / 1000).toFixed(1);
}

function setUpDropZone(zone, input) {
  ["dragenter", "dragover"].forEach((name) =>
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.add("is-over");
    })
  );
  ["dragleave", "drop"].forEach((name) =>
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.remove("is-over");
    })
  );
  zone.addEventListener("drop", (event) => {
    if (!event.dataTransfer.files.length) return;
    input.files = event.dataTransfer.files;
    input.dispatchEvent(new Event("change"));
  });
}

/** Send one label (one or more pictures) to the server. Always resolves, with either the result or a plain error message. */
async function verifyLabel(files, values) {
  const body = new FormData();
  files.forEach((file) => body.append("image", file));
  Object.entries(values).forEach(([name, value]) => body.append(name, value || ""));
  let response;
  try {
    response = await fetch("/api/verify", { method: "POST", body });
  } catch (error) {
    return { error: "Could not reach the server. Check your connection and try again." };
  }
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (response.ok && data) return data;
  if (data && typeof data.detail === "string") return { error: data.detail };
  return { error: "The server had a problem checking this label. Please try again." };
}

/* ---------- Showing results ---------- */

function badge(status) {
  return el("span", `badge is-${status}`, STATUS_WORDS[status]);
}

function valueBox(title, value) {
  const box = el("div");
  box.append(el("dt", "", title), el("dd", "", value || "(nothing found)"));
  return box;
}

function checkCard(check, pictureCount) {
  const card = el("article", `check is-${check.status}`);
  const head = el("div", "check-head");
  head.append(el("h3", "", check.label), badge(check.status));
  card.append(head, el("p", "check-reason", check.reason));
  if (pictureCount > 1 && check.picture) {
    const name = check.picture_name ? ` (${check.picture_name})` : "";
    card.append(el("p", "check-where", `Found on picture ${check.picture}${name}.`));
  }

  if (check.parts && check.parts.length) {
    const list = el("ul", "check-parts");
    check.parts.forEach((part) => {
      const item = el("li");
      item.append(badge(part.status), ` ${PART_NAMES[part.name] || part.name}: ${part.reason}`);
      list.append(item);
    });
    card.append(list);
  }

  const values = el("dl", "check-values");
  if (check.field === "government_warning") {
    values.append(valueBox("Label says", check.found), valueBox("Required wording", check.expected));
  } else {
    values.append(valueBox("Application says", check.expected), valueBox("Label says", check.found));
  }
  card.append(values);
  return card;
}

function resultDetails(data) {
  const wrap = el("div");
  const pictures = data.pictures || [];
  data.fields.forEach((check) => wrap.append(checkCard(check, pictures.length)));
  if (data.skipped.length) {
    wrap.append(el("p", "hint", `Not checked because the application value was left empty: ${data.skipped.join(", ")}.`));
  }
  const raw = el("details");
  raw.append(el("summary", "", "Show all the text read from the label"));
  if (pictures.length > 1) {
    pictures.forEach((picture) => {
      raw.append(el("p", "check-where", `Picture ${picture.number} (${picture.filename || "no name"})`));
      raw.append(el("pre", "", picture.text || "(no text was read)"));
    });
  } else {
    raw.append(el("pre", "", data.ocr.text || "(no text was read)"));
  }
  wrap.append(raw);
  return wrap;
}

function renderSingle(data) {
  const box = $("single-result");
  box.replaceChildren();
  const [title, explanation] = VERDICTS[data.overall];
  const verdict = el("div", `verdict is-${data.overall}`);
  const count = (data.pictures || []).length;
  const what = count > 1 ? `Checked ${count} pictures` : "Checked";
  verdict.append(el("h2", "", title), el("p", "", explanation), el("p", "", `${what} in ${seconds(data.elapsed_ms)} seconds.`));
  box.append(verdict, resultDetails(data));
  box.hidden = false;
  box.focus();
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------- Mode switch ---------- */

const tabs = [$("tab-single"), $("tab-batch")];

function selectTab(tab) {
  tabs.forEach((other) => {
    const selected = other === tab;
    other.setAttribute("aria-selected", String(selected));
    other.tabIndex = selected ? 0 : -1;
    $(other.getAttribute("aria-controls")).hidden = !selected;
  });
}

tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const next = tabs[(index + 1) % tabs.length];
    selectTab(next);
    next.focus();
  });
});

/* ---------- Check one label ---------- */

const singleForm = $("single-form");
const singleImage = $("single-image");
const singleError = $("single-error");
/** The pictures added for this label, in the order they were added: { file, url }. */
let singlePictures = [];

function requiredFields() {
  const type = singleForm.elements.beverage_type.value;
  const fields = [["brand_name", "Brand name"], ["class_type", "Class or type"], ["net_contents", "Net contents"]];
  if (type === "spirits") fields.splice(2, 0, ["alcohol_content", "Alcohol content"]);
  return fields;
}

function updateOptionalNote() {
  $("alcohol-optional").hidden = singleForm.elements.beverage_type.value === "spirits";
}

/** Show a small picture, its name and a remove button for every picture added so far. */
function renderPictures() {
  const list = $("single-pictures");
  list.replaceChildren();
  singlePictures.forEach((picture, index) => {
    const item = el("li");
    const image = el("img");
    image.src = picture.url;
    image.alt = `Picture ${index + 1}: ${picture.file.name}`;
    const remove = el("button", "button button-secondary button-small", "Remove");
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove picture ${index + 1}, ${picture.file.name}`);
    remove.addEventListener("click", () => removePicture(index));
    item.append(image, el("p", "picture-name", `${index + 1}. ${picture.file.name}`), remove);
    list.append(item);
  });
  const count = singlePictures.length;
  $("single-file-name").textContent = count ? `${count} picture${count === 1 ? "" : "s"} added.` : "";
  $("camera-open").textContent = count ? "Take another picture" : "Take a picture";
  document.querySelector('label[for="single-image"]').textContent = count ? "Add another picture" : "Choose a picture";
}

/** Add pictures to the label being checked. Pictures already added stay. */
function addPictures(files) {
  showError(singleError, "");
  for (const file of files) {
    const problem = imageProblem(file);
    if (problem) {
      showError(singleError, problem);
      continue;
    }
    if (singlePictures.length >= MAX_PICTURES) {
      showError(singleError, `You can add up to ${MAX_PICTURES} pictures for one label. Remove one to add another.`);
      break;
    }
    singlePictures.push({ file, url: URL.createObjectURL(file) });
  }
  renderPictures();
}

function removePicture(index) {
  const [removed] = singlePictures.splice(index, 1);
  if (removed) URL.revokeObjectURL(removed.url);
  showError(singleError, "");
  renderPictures();
}

function clearPictures() {
  singlePictures.forEach((picture) => URL.revokeObjectURL(picture.url));
  singlePictures = [];
  renderPictures();
}

singleImage.addEventListener("change", () => {
  const files = Array.from(singleImage.files);
  // Empty the input so choosing the same file again, after removing it, still works.
  singleImage.value = "";
  if (files.length) addPictures(files);
});

singleForm.addEventListener("change", updateOptionalNote);

/* ---------- Camera ---------- */

let cameraStream = null;
let cameraShots = 0;

/** Add a picture from the camera to the label, the same way as a chosen file. */
function useCapturedFile(file) {
  addPictures([file]);
}

function closeCamera() {
  if (cameraStream) cameraStream.getTracks().forEach((track) => track.stop());
  cameraStream = null;
  $("camera-video").srcObject = null;
  $("camera-panel").hidden = true;
}

async function openCamera() {
  showError(singleError, "");
  // Phones and tablets: the built-in camera app focuses better and gives a full-size photo.
  const liveViewAvailable = navigator.mediaDevices && navigator.mediaDevices.getUserMedia;
  if (window.matchMedia("(pointer: coarse)").matches || !liveViewAvailable) {
    $("single-capture").click();
    return;
  }
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } },
      audio: false,
    });
  } catch (error) {
    const blocked = error && error.name === "NotAllowedError";
    showError(singleError, blocked
      ? "The camera is blocked for this page. Allow camera access in your browser, or choose a picture instead."
      : "No camera was found. Please choose a picture instead.");
    return;
  }
  $("camera-video").srcObject = cameraStream;
  $("camera-panel").hidden = false;
  $("camera-snap").focus();
}

function snapPicture() {
  const video = $("camera-video");
  if (!video.videoWidth) return;
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  canvas.toBlob((blob) => {
    closeCamera();
    if (!blob) return;
    cameraShots += 1;
    useCapturedFile(new File([blob], `camera-picture-${cameraShots}.jpg`, { type: "image/jpeg" }));
  }, "image/jpeg", 0.92);
}

$("camera-open").addEventListener("click", openCamera);
$("camera-snap").addEventListener("click", snapPicture);
$("camera-cancel").addEventListener("click", closeCamera);
$("single-capture").addEventListener("change", (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (file) useCapturedFile(file);
});
window.addEventListener("pagehide", closeCamera);

singleForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError(singleError, "");
  $("single-result").hidden = true;

  const files = singlePictures.map((picture) => picture.file);
  if (!files.length) return showError(singleError, "Please add a label picture first (step 1).");
  const empty = requiredFields().filter(([name]) => !singleForm.elements[name].value.trim());
  if (empty.length) {
    singleForm.elements[empty[0][0]].focus();
    return showError(singleError, `Please fill in: ${empty.map(([, label]) => label).join(", ")}.`);
  }

  const values = { beverage_type: singleForm.elements.beverage_type.value };
  [...CSV_COLUMNS.slice(1), ...OPTIONAL_COLUMNS].forEach((name) => (values[name] = singleForm.elements[name].value.trim()));

  $("single-submit").disabled = true;
  $("single-busy").hidden = false;
  const data = await verifyLabel(files, values);
  $("single-busy").hidden = true;
  $("single-submit").disabled = false;

  if (data.error) return showError(singleError, data.error);
  renderSingle(data);
});

$("single-reset").addEventListener("click", () => {
  singleForm.reset();
  closeCamera();
  clearPictures();
  updateOptionalNote();
  showError(singleError, "");
  $("single-result").hidden = true;
  window.scrollTo({ top: 0, behavior: "smooth" });
});

setUpDropZone($("single-drop"), singleImage);

/* ---------- Check many labels ---------- */

const batchForm = $("batch-form");
const batchImages = $("batch-images");
const batchCsv = $("batch-csv");
const batchError = $("batch-error");
let batchResults = [];
let stopRequested = false;

/** Parse CSV text into rows of cells. Handles quoted cells, doubled quotes and both line endings. */
function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  const source = text.replace(/^﻿/, "");
  for (let i = 0; i < source.length; i += 1) {
    const char = source[i];
    if (quoted) {
      if (char === '"' && source[i + 1] === '"') {
        cell += '"';
        i += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        cell += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(cell);
      cell = "";
    } else if (char === "\n" || char === "\r") {
      if (char === "\r" && source[i + 1] === "\n") i += 1;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += char;
    }
  }
  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((cells) => cells.some((value) => value.trim()));
}

/** Turn the CSV into one object per row, or throw an Error with a plain message. */
function readList(text) {
  const rows = parseCsv(text);
  if (rows.length < 2) throw new Error("The list is empty. It needs a heading row and one row for each picture.");
  const header = rows[0].map((name) => name.trim().toLowerCase());
  const missing = CSV_COLUMNS.filter((name) => !header.includes(name));
  if (missing.length) {
    throw new Error(`The list is missing these columns: ${missing.join(", ")}.\nDownload the blank list to see the layout.`);
  }
  return rows.slice(1).map((cells) => {
    const entry = {};
    header.forEach((name, index) => (entry[name] = (cells[index] || "").trim()));
    return entry;
  });
}

function csvCell(value) {
  let text = String(value === undefined || value === null ? "" : value);
  if (/^[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

function resultsCsv() {
  const fields = ["brand_name", "class_type", "alcohol_content", "net_contents", "government_warning", ...OPTIONAL_COLUMNS];
  const header = ["filename", "overall", "seconds", "note"];
  fields.forEach((name) => header.push(`${name}_result`, `${name}_on_label`, `${name}_reason`, `${name}_picture`));
  const lines = [header.map(csvCell).join(",")];
  batchResults.forEach((item) => {
    const cells = [item.filename, item.status, item.data ? seconds(item.data.elapsed_ms) : "", item.message || ""];
    fields.forEach((name) => {
      const check = item.data ? item.data.fields.find((field) => field.field === name) : null;
      if (check) cells.push(check.status, check.found, check.reason, check.picture_name || "");
      else cells.push(item.data ? "not checked" : "", "", "", "");
    });
    lines.push(cells.map(csvCell).join(","));
  });
  return lines.join(CSV_LINE_END) + CSV_LINE_END;
}

function batchRow(item) {
  const row = el("tr");
  const name = el("th", "", item.filename);
  name.scope = "row";
  const result = el("td");
  result.append(badge(item.status));
  const details = el("td");
  row.append(name, el("td", "", item.brand), result, details);

  if (!item.data) {
    details.textContent = item.message;
    return [row];
  }
  const detailRow = el("tr");
  const cell = el("td", "detail-cell");
  cell.colSpan = 4;
  cell.append(resultDetails(item.data));
  detailRow.append(cell);
  detailRow.hidden = true;

  const toggle = el("button", "button button-secondary button-small", "Show details");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", "false");
  toggle.addEventListener("click", () => {
    detailRow.hidden = !detailRow.hidden;
    toggle.setAttribute("aria-expanded", String(!detailRow.hidden));
    toggle.textContent = detailRow.hidden ? "Show details" : "Hide details";
  });
  const firstProblem = item.data.fields.find((field) => field.status === item.status && item.status !== "match");
  if (firstProblem) details.append(el("p", "", `${firstProblem.label}: ${firstProblem.reason}`));
  details.append(toggle);
  return [row, detailRow];
}

function renderBatch(totalMs) {
  const counts = { match: 0, review: 0, mismatch: 0, error: 0 };
  batchResults.forEach((item) => (counts[item.status] += 1));
  const summary = $("batch-summary");
  summary.replaceChildren();
  Object.keys(counts).forEach((status) => {
    if (counts[status]) summary.append(el("span", `badge is-${status}`, `${STATUS_WORDS[status]}: ${counts[status]}`));
  });
  const checked = batchResults.filter((item) => item.data).length;
  summary.append(el("p", "", `Checked ${checked} labels in ${seconds(totalMs)} seconds.`));

  const order = { mismatch: 0, error: 1, review: 2, match: 3 };
  const rows = $("batch-rows");
  rows.replaceChildren();
  batchResults
    .slice()
    .sort((a, b) => order[a.status] - order[b.status])
    .forEach((item) => rows.append(...batchRow(item)));
  $("batch-result").hidden = false;
}

async function runBatch(jobs) {
  const started = performance.now();
  const bar = $("batch-bar");
  let next = 0;
  let done = 0;
  bar.max = jobs.length;
  bar.value = 0;

  async function worker() {
    while (next < jobs.length && !stopRequested) {
      const job = jobs[next];
      next += 1;
      const data = await verifyLabel(job.files, job.values);
      batchResults.push(
        data.error
          ? { filename: job.name, brand: job.values.brand_name, status: "error", message: data.error }
          : { filename: job.name, brand: job.values.brand_name, status: data.overall, data }
      );
      done += 1;
      bar.value = done;
      $("batch-progress-text").textContent = `Checked ${done} of ${jobs.length} labels`;
    }
  }
  await Promise.all(Array.from({ length: Math.min(PARALLEL_REQUESTS, jobs.length) }, worker));
  return performance.now() - started;
}

batchImages.addEventListener("change", () => {
  const count = batchImages.files.length;
  $("batch-images-name").textContent = count ? `Chosen: ${count} picture${count === 1 ? "" : "s"}` : "";
});

batchCsv.addEventListener("change", () => {
  $("batch-csv-name").textContent = batchCsv.files[0] ? `Chosen: ${batchCsv.files[0].name}` : "";
});

batchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError(batchError, "");
  $("batch-result").hidden = true;

  const images = Array.from(batchImages.files);
  if (!images.length) return showError(batchError, "Please choose the label pictures first (step 1).");
  if (!batchCsv.files[0]) return showError(batchError, "Please choose the list of application details (step 2).");
  if (!/\.csv$/i.test(batchCsv.files[0].name)) {
    return showError(batchError, "The list must be a CSV file. In Excel, use Save As and pick CSV.");
  }

  let entries;
  try {
    entries = readList(await batchCsv.files[0].text());
  } catch (error) {
    return showError(batchError, error.message);
  }

  const byName = new Map(images.map((file) => [file.name.toLowerCase(), file]));
  const listed = new Set();
  const jobs = [];
  batchResults = [];
  entries.forEach((entry) => {
    // One label can have several pictures: "front.jpg;back.jpg" in the filename column.
    const names = entry.filename.split(";").map((name) => name.trim()).filter(Boolean);
    names.forEach((name) => listed.add(name.toLowerCase()));
    const files = names.map((name) => byName.get(name.toLowerCase()));
    const missing = names.filter((name, index) => !files[index]);
    let problem = "";
    if (!names.length) problem = "This row has no picture file name.";
    else if (missing.length) problem = `The list names ${missing.join(", ")}, but it was not among the pictures you chose.`;
    else if (names.length > MAX_PICTURES) problem = `This row lists ${names.length} pictures. The limit is ${MAX_PICTURES} for one label.`;
    else problem = files.map(imageProblem).find(Boolean) || "";
    if (problem) {
      batchResults.push({ filename: entry.filename || "(no file name)", brand: entry.brand_name, status: "error", message: problem });
      return;
    }
    const values = { beverage_type: entry.beverage_type || "spirits" };
    [...CSV_COLUMNS.slice(1), ...OPTIONAL_COLUMNS].forEach((name) => (values[name] = entry[name] || ""));
    jobs.push({ files, name: names.join("; "), values });
  });
  images
    .filter((file) => !listed.has(file.name.toLowerCase()))
    .forEach((file) =>
      batchResults.push({ filename: file.name, brand: "", status: "error", message: "This picture is not in the list, so it was not checked." })
    );

  stopRequested = false;
  $("batch-submit").disabled = true;
  $("batch-stop").hidden = false;
  $("batch-progress").hidden = false;
  $("batch-progress-text").textContent = `Checked 0 of ${jobs.length} labels`;
  const totalMs = await runBatch(jobs);
  $("batch-submit").disabled = false;
  $("batch-stop").hidden = true;
  if (stopRequested) $("batch-progress-text").textContent += " (stopped)";
  renderBatch(totalMs);
});

$("batch-stop").addEventListener("click", () => {
  stopRequested = true;
});

$("batch-download").addEventListener("click", () => {
  const url = URL.createObjectURL(new Blob([resultsCsv()], { type: "text/csv" }));
  const link = el("a");
  link.href = url;
  link.download = "label-check-results.csv";
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
});

setUpDropZone($("batch-drop"), batchImages);
setUpDropZone($("batch-csv-drop"), batchCsv);
