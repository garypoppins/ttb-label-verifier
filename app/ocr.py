"""Image cleanup and text recognition.

Uses RapidOCR (PP-OCR models on ONNX Runtime, CPU only). The models ship inside
the ``rapidocr-onnxruntime`` package, so nothing is downloaded at run time and
no outside service is called. Uploaded images are decoded and processed in
memory only.
"""

from __future__ import annotations

import io
import math
import os
import threading
from dataclasses import dataclass, field, replace
from functools import lru_cache

import cv2
import numpy as np
import onnxruntime
from PIL import Image, ImageOps
from rapidocr_onnxruntime import RapidOCR

MAX_SIDE = int(os.environ.get("OCR_MAX_SIDE", "1600"))
THREADS = int(os.environ.get("OCR_THREADS", "2"))
MEMORY_ARENA = os.environ.get("OCR_MEMORY_ARENA", "1") != "0"

MIN_LINE_CONFIDENCE = 0.5
GOOD_READ_CONFIDENCE = 0.80
MAX_UNREAD_SHARE = 0.3
FLIP_RETRY_CONFIDENCE = 0.7
# A read with very little text cannot support a "not found": three fragments
# read with high confidence say nothing about the rest of the label.
MIN_READ_LETTERS = 40
MIN_SOLID_LINE_LETTERS = 4
# A picture is turned a quarter turn before reading when more than this share of its
# text boxes, measured by length, runs up or down rather than across.
SIDEWAYS_SHARE = 0.5
LONG_BOX_RATIO = 1.5
# Looser text detection for the extra read (see OcrEngine.read_again).
LOOSE_BOX_THRESHOLD = 0.3
COVERED_SHARE = 0.5
# Unread boxes shorter than this (in pixels of the working image) are ignored when judging the read.
MIN_UNREAD_BOX_SIDE = 32
CLASSIFIER_MAX_ASPECT = 8
TALL_BOX_RATIO = 1.5

Rect = tuple[float, float, float, float]


class ImageError(ValueError):
    """Raised when the uploaded bytes are not an image we can open."""


@dataclass(frozen=True)
class Line:
    """One line of text read from the label.

    ``rect`` is (left, top, right, bottom) in the upright frame, which is the
    image rotated so that text reads left to right.
    """

    text: str
    confidence: float
    rect: Rect
    # Short side of the detected box. On curved or slightly tilted text the
    # upright rectangle is much taller than the print, so this is the better
    # measure of line height. Zero means "not known, use the rectangle".
    thickness: float = 0.0

    @property
    def height(self) -> float:
        return self.thickness or self.rect[3] - self.rect[1]


@dataclass(frozen=True)
class OcrResult:
    """Everything read from one image, with lines in reading order."""

    lines: list[Line]
    image: np.ndarray
    angle: float
    unread_regions: int
    passes: int
    # Raw output of the text detector for this picture, kept so the extra read
    # (OcrEngine.read_again) can look for more boxes without running it again.
    detector_output: object = field(default=None, repr=False, compare=False)

    @property
    def texts(self) -> list[str]:
        return [line.text for line in self.lines]

    @property
    def text(self) -> str:
        return "\n".join(self.texts)

    @property
    def confidence(self) -> float:
        """Average confidence, weighted by the length of each line."""
        total = sum(len(line.text) for line in self.lines)
        if total == 0:
            return 0.0
        return sum(line.confidence * len(line.text) for line in self.lines) / total

    @property
    def clean(self) -> bool:
        """True when what was read, was read with confidence. Says nothing about how much was read."""
        if not self.lines or self.confidence < GOOD_READ_CONFIDENCE:
            return False
        regions = len(self.lines) + self.unread_regions
        return self.unread_regions / regions <= MAX_UNREAD_SHARE

    @property
    def readable(self) -> bool:
        """True when the read is good enough to trust a "not found": clean, and with enough text."""
        solid = [sum(char.isalnum() for char in line.text) for line in self.lines]
        enough = sum(count for count in solid if count >= MIN_SOLID_LINE_LETTERS) >= MIN_READ_LETTERS
        return self.clean and enough

    @property
    def score(self) -> float:
        """Amount of confidently read text, used to pick the better of two passes."""
        return sum(line.confidence * len(line.text) for line in self.lines)


def load_image(data: bytes) -> np.ndarray:
    """Decode an upload to an RGB array, upright per EXIF and at most MAX_SIDE pixels long."""
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except Exception as error:  # Pillow raises many different types for bad files
        raise ImageError("This file is not a picture we can open.") from error
    longest = max(image.size)
    if longest > MAX_SIDE:
        scale = MAX_SIDE / longest
        size = (round(image.width * scale), round(image.height * scale))
        image = image.resize(size, Image.Resampling.LANCZOS)
    return np.asarray(image)


def enhance_contrast(image: np.ndarray) -> np.ndarray:
    """Return a grey, locally contrast-stretched copy for a second OCR attempt."""
    grey = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    stretched = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(grey)
    return cv2.cvtColor(stretched, cv2.COLOR_GRAY2RGB)


def crop_rect(result: OcrResult, rect: Rect) -> np.ndarray:
    """Cut an upright-frame rectangle out of the image, turned so the text is level."""
    left, top, right, bottom = rect
    cos, sin = math.cos(result.angle), math.sin(result.angle)
    corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
    source = np.float32([(u * cos - v * sin, u * sin + v * cos) for u, v in corners])
    width, height = max(int(right - left), 1), max(int(bottom - top), 1)
    target = np.float32([(0, 0), (width, 0), (width, height), (0, height)])
    matrix = cv2.getPerspectiveTransform(source, target)
    return cv2.warpPerspective(
        result.image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


class _RememberingSession:
    """Wraps the detector model so its last output can be kept and replayed.

    Finding text boxes has two steps: the model scores every pixel, then the
    scores are turned into boxes. Only the second step differs for the looser
    extra read, so the first step (most of the time) is not repeated.
    """

    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.last: object = None
        self.replay: object = None

    def __call__(self, content: np.ndarray) -> object:
        if self.replay is not None:
            return self.replay
        self.last = self.inner(content)
        return self.last


def _build_rapidocr() -> RapidOCR:
    """Create the RapidOCR pipeline, then reload its three models with our settings.

    RapidOCR 1.2.3 exposes no thread setting and turns the ONNX Runtime memory
    arena off. Capping threads keeps a small shared server responsive. With the
    arena on, text lines of a width not seen before measured about three times
    faster to read, at the cost of more memory. Set OCR_MEMORY_ARENA=0 to trade
    that speed back for a smaller footprint.
    """
    pipeline = RapidOCR()
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = THREADS
    options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = MEMORY_ARENA
    options.log_severity_level = 3
    for holder in (pipeline.text_detector.infer, pipeline.text_cls.infer, pipeline.text_recognizer.session):
        model_path = holder.session._model_path
        holder.session = onnxruntime.InferenceSession(
            model_path, sess_options=options, providers=["CPUExecutionProvider"]
        )
    pipeline.text_detector.infer = _RememberingSession(pipeline.text_detector.infer)
    return pipeline


def _is_tall(box: np.ndarray) -> bool:
    """True for boxes RapidOCR turns a quarter turn before reading (sideways text)."""
    width = max(np.linalg.norm(box[0] - box[1]), np.linalg.norm(box[2] - box[3]))
    height = max(np.linalg.norm(box[0] - box[3]), np.linalg.norm(box[1] - box[2]))
    return int(height) >= TALL_BOX_RATIO * max(int(width), 1)


def _to_line(box: np.ndarray, text: str, score: float, cos: float, sin: float) -> Line:
    """Describe a read box in the upright frame (the picture turned by the text angle)."""
    u = box[:, 0] * cos + box[:, 1] * sin
    v = -box[:, 0] * sin + box[:, 1] * cos
    rect = (float(u.min()), float(v.min()), float(u.max()), float(v.max()))
    thickness = float(min(np.linalg.norm(box[0] - box[1]), np.linalg.norm(box[0] - box[3])))
    return Line(text, score, rect, thickness)


def _covered(box: np.ndarray, lines: list[Line], cos: float, sin: float) -> bool:
    """True when a detected box lies on a line that has already been read."""
    u = box[:, 0] * cos + box[:, 1] * sin
    v = -box[:, 0] * sin + box[:, 1] * cos
    left, right, middle = float(u.min()), float(u.max()), float(v.mean())
    width = max(right - left, 1.0)
    for line in lines:
        if abs(middle - (line.rect[1] + line.rect[3]) / 2) > 0.5 * line.height:
            continue
        if min(right, line.rect[2]) - max(left, line.rect[0]) > COVERED_SHARE * width:
            return True
    return False


def _long_side(box: np.ndarray) -> float:
    return float(max(np.linalg.norm(box[0] - box[1]), np.linalg.norm(box[0] - box[3])))


def _runs_sideways(boxes: np.ndarray | None) -> bool:
    """True when most of the detected text, by length, runs up or down the picture.

    Uses the corner positions only, so it does not depend on which corner the
    detector happens to list first. Boxes that are nearly square say nothing.
    """
    if boxes is None or len(boxes) == 0:
        return False
    across = down = 0.0
    for box in boxes:
        first, second = box[1] - box[0], box[3] - box[0]
        long_side, short_side = (first, second) if np.linalg.norm(first) >= np.linalg.norm(second) else (second, first)
        length = float(np.linalg.norm(long_side))
        if length < LONG_BOX_RATIO * max(float(np.linalg.norm(short_side)), 1.0):
            continue
        if abs(long_side[1]) > abs(long_side[0]):
            down += length
        else:
            across += length
    return down > SIDEWAYS_SHARE * (across + down)


def _order_lines(lines: list[Line]) -> list[Line]:
    """Sort lines top to bottom, then left to right within a row."""
    if not lines:
        return []
    row_tolerance = 0.5 * float(np.median([line.height for line in lines]))
    rows: list[list[Line]] = []
    for line in sorted(lines, key=lambda item: (item.rect[1] + item.rect[3]) / 2):
        centre = (line.rect[1] + line.rect[3]) / 2
        last = rows[-1][-1] if rows else None
        if last is not None and abs(centre - (last.rect[1] + last.rect[3]) / 2) <= row_tolerance:
            rows[-1].append(line)
        else:
            rows.append([line])
    return [line for row in rows for line in sorted(row, key=lambda item: item.rect[0])]


class OcrEngine:
    """Loads the OCR models once and reads label images."""

    def __init__(self) -> None:
        self._ocr = _build_rapidocr()
        # Reading one line at a time avoids padding short lines to the longest
        # line in a batch, which measured about twice as fast on label text.
        self._ocr.text_recognizer.rec_batch_num = 1
        self._lock = threading.Lock()

    def warm_up(self) -> None:
        """Run one small read so the first real request is not slow."""
        canvas = np.full((240, 640, 3), 255, dtype=np.uint8)
        cv2.putText(canvas, "WARM UP 750 mL", (20, 140), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 4)
        self.read_again(self.read_array(canvas))

    def read(self, data: bytes) -> OcrResult:
        """Read all text from uploaded image bytes."""
        return self.read_array(load_image(data))

    def read_array(self, image: np.ndarray) -> OcrResult:
        """Read all text from an RGB array.

        A picture whose text mostly runs up or down is turned a quarter turn
        first, because sideways lines read much worse than level ones. A poor
        read is tried again with more contrast and the better read is kept.
        """
        with self._lock:
            passes = 1
            boxes, output = self._detect(image)
            if _runs_sideways(boxes):
                image = np.ascontiguousarray(cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE))
                boxes, output = self._detect(image)
                passes += 1
            best = self._single_pass(image, boxes, output)
            if not best.clean:
                enhanced = enhance_contrast(image)
                contrast = self._single_pass(enhanced, *self._detect(enhanced))
                passes += 1
                if contrast.score > best.score:
                    # Keep the unprocessed picture: the bold estimate measures real ink.
                    best = replace(contrast, image=image)
        return replace(best, passes=passes)

    def _detect(self, image: np.ndarray) -> tuple[np.ndarray | None, object]:
        """Find text boxes. Also returns the detector's raw output for a later extra read."""
        boxes, _ = self._ocr.text_detector(image)
        return boxes, self._ocr.text_detector.infer.last

    def read_again(self, result: OcrResult) -> OcrResult:
        """Look once more for text the normal read skipped, and add it to the result.

        The normal detector settings skip some lines of small, tightly spaced
        print on photos of real labels. A looser setting finds more of them but
        also more noise and worse boxes for lines already read. So the lines
        already read are kept as they are, and only boxes in places with no
        line are read and added. Callers use the fuller result to confirm text,
        never to reject a label.
        """
        with self._lock:
            detector = self._ocr.text_detector
            post = detector.postprocess_op
            saved = (post.dilation_kernel, post.box_thresh)
            post.dilation_kernel, post.box_thresh = None, LOOSE_BOX_THRESHOLD
            detector.infer.replay = result.detector_output
            try:
                boxes, _ = detector(result.image)
            finally:
                post.dilation_kernel, post.box_thresh = saved
                detector.infer.replay = None
            cos, sin = math.cos(result.angle), math.sin(result.angle)
            fresh = [] if boxes is None else [box for box in boxes if not _covered(box, result.lines, cos, sin)]
            if not fresh:
                return replace(result, passes=result.passes + 1)
            kept, _, dropped = self._read_boxes(result.image, np.array(fresh, dtype=np.float32))
        added = [_to_line(box, text, score, cos, sin) for box, text, score, _ in kept]
        # Boxes that look like text but could not be read count against the read: with many of
        # them, "not found" cannot be trusted.
        return replace(
            result,
            lines=_order_lines([*result.lines, *added]),
            unread_regions=result.unread_regions + dropped,
            passes=result.passes + 1,
        )

    def _read_boxes(
        self, image: np.ndarray, boxes: np.ndarray
    ) -> tuple[list[tuple[np.ndarray, str, float, bool]], dict[bool, bool], int]:
        """Read every detected box. Returns the confident ones, the upside-down guess and the number dropped."""
        crops = self._ocr.get_crop_img_list(image, boxes)
        tall = [_is_tall(box) for box in boxes]

        flipped = self._upside_down_groups(crops, tall)
        texts = self._recognize(crops, tall, flipped)
        if _mean_confidence(texts) < FLIP_RETRY_CONFIDENCE:
            other = {group: not value for group, value in flipped.items()}
            other_texts = self._recognize(crops, tall, other)
            if _mean_confidence(other_texts) > _mean_confidence(texts):
                flipped, texts = other, other_texts

        kept = [
            (box, text.strip(), float(score), is_tall)
            for box, is_tall, (text, score) in zip(boxes, tall, texts)
            if score >= MIN_LINE_CONFIDENCE and text.strip()
        ]
        # Specks and single characters (barcode digits, ornaments) that could not be read say
        # little about whether the label text was read, so only larger boxes count as unread.
        unread = sum(
            1
            for box, (text, score) in zip(boxes, texts)
            if (score < MIN_LINE_CONFIDENCE or not text.strip()) and _long_side(box) >= MIN_UNREAD_BOX_SIDE
        )
        return kept, flipped, unread

    def _single_pass(self, image: np.ndarray, boxes: np.ndarray | None, detector_output: object = None) -> OcrResult:
        """Read each detected box and put the lines in reading order."""
        if boxes is None or len(boxes) == 0:
            return OcrResult([], image, 0.0, 0, 1, detector_output)
        kept, flipped, dropped = self._read_boxes(image, boxes)

        direction = np.zeros(2)
        for box, _, score, is_tall in kept:
            along = (box[3] - box[0]) if is_tall else (box[1] - box[0])
            direction += (-along if flipped[is_tall] else along) * score

        angle = math.atan2(direction[1], direction[0]) if kept else 0.0
        cos, sin = math.cos(angle), math.sin(angle)
        lines = [_to_line(box, text, score, cos, sin) for box, text, score, _ in kept]
        return OcrResult(_order_lines(lines), image, angle, dropped, 1, detector_output)

    def _recognize(
        self, crops: list[np.ndarray], tall: list[bool], flipped: dict[bool, bool]
    ) -> list[tuple[str, float]]:
        """Read every crop, first turning the ones in an upside-down group the right way up."""
        turned = [cv2.rotate(crop, cv2.ROTATE_180) if flipped[is_tall] else crop for crop, is_tall in zip(crops, tall)]
        texts, _ = self._ocr.text_recognizer(turned)
        return texts

    def _upside_down_groups(self, crops: list[np.ndarray], tall: list[bool]) -> dict[bool, bool]:
        """Guess, by majority vote, whether level text and sideways text are upside down.

        The 0/180 degree classifier is often wrong on long lines of small text,
        so only short crops vote, and the caller double-checks the guess against
        how well the text actually reads.
        """
        _, turns, _ = self._ocr.text_cls(crops)
        votes = {False: 0.0, True: 0.0}
        for crop, is_tall, (label, score) in zip(crops, tall, turns):
            if crop.shape[1] <= CLASSIFIER_MAX_ASPECT * crop.shape[0]:
                votes[is_tall] += float(score) if "180" in label else -float(score)
        return {group: total > 0 for group, total in votes.items()}


def _mean_confidence(texts: list[tuple[str, float]]) -> float:
    """Average recognition confidence, weighted by text length."""
    total = sum(len(text) for text, _ in texts)
    return sum(len(text) * float(score) for text, score in texts) / total if total else 0.0


@lru_cache(maxsize=1)
def get_engine() -> OcrEngine:
    """Return the shared engine, loading the models on first use."""
    return OcrEngine()
