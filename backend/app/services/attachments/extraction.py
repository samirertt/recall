"""Attachment text extraction (Section 17; docs/RESEARCH.md § Attachment Ingestion & OCR).

Contract: `extract_text_attachment` NEVER raises — every failure mode (missing Tesseract
binary, corrupt PDF, undecodable bytes) is caught and returned as a `failed`/`partial`
ExtractionResult, so an extraction problem can never block saving the original
attachment (Section 52). Extracted text is a rebuildable cache, never the source of
truth — the original bytes on disk are.
"""

import io
import re
from dataclasses import dataclass

from charset_normalizer import from_bytes

from app.models.enums import ExtractionStatus

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_NULL_BYTE = b"\x00"


@dataclass
class ExtractionResult:
    status: ExtractionStatus
    text: str | None
    confidence: float | None
    engine: str | None
    engine_version: str | None
    error: str | None


def _sniff_kind(filename: str, data: bytes) -> str:
    if data[:5] == b"%PDF-":
        return "pdf"
    try:
        from PIL import Image

        Image.open(io.BytesIO(data)).verify()
        return "image"
    except Exception:
        pass
    if _NULL_BYTE in data[:8000]:
        return "binary"
    return "text"


def extract_text_attachment(filename: str, data: bytes) -> ExtractionResult:
    kind = _sniff_kind(filename, data)
    try:
        if kind == "text":
            return _extract_text(data)
        if kind == "pdf":
            return _extract_pdf(data)
        if kind == "image":
            return _extract_image(data)
        return ExtractionResult(
            status=ExtractionStatus.skipped,
            text=None,
            confidence=None,
            engine=None,
            engine_version=None,
            error="binary file, extraction skipped",
        )
    except Exception as exc:  # belt and braces on top of each extractor's own try/except
        return ExtractionResult(
            status=ExtractionStatus.failed,
            text=None,
            confidence=None,
            engine=kind,
            engine_version=None,
            error=str(exc),
        )


def _extract_text(data: bytes) -> ExtractionResult:
    try:
        best = from_bytes(data).best()
        raw = str(best) if best is not None else data.decode("utf-8", errors="replace")
    except Exception:
        raw = data.decode("utf-8", errors="replace")
    cleaned = _ANSI_ESCAPE_RE.sub("", raw)
    return ExtractionResult(
        status=ExtractionStatus.success,
        text=cleaned,
        confidence=1.0,
        engine="charset-normalizer",
        engine_version=None,
        error=None,
    )


def _extract_pdf(data: bytes) -> ExtractionResult:
    try:
        import pymupdf
    except ImportError:
        return _extract_pdf_pypdf_fallback(data, reason="pymupdf not installed")

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        return _extract_pdf_pypdf_fallback(data, reason=f"pymupdf failed to open: {exc}")

    texts: list[str] = []
    ocr_used = False
    ocr_confidences: list[float] = []
    for page in doc:
        page_text = page.get_text().strip()
        if len(page_text) >= 20:
            texts.append(page_text)
            continue
        # Near-zero text: likely a scanned/image-only page — OCR fallback.
        pixmap = page.get_pixmap(dpi=300)
        image_bytes = pixmap.tobytes("png")
        ocr_result = _extract_image(image_bytes)
        ocr_used = True
        if ocr_result.text:
            texts.append(ocr_result.text)
        if ocr_result.confidence is not None:
            ocr_confidences.append(ocr_result.confidence)

    combined = "\n\n".join(t for t in texts if t)
    confidence = (
        sum(ocr_confidences) / len(ocr_confidences) if ocr_used and ocr_confidences else 1.0
    )
    return ExtractionResult(
        status=ExtractionStatus.success if combined else ExtractionStatus.partial,
        text=combined or None,
        confidence=confidence,
        engine="pymupdf+tesseract" if ocr_used else "pymupdf",
        engine_version=pymupdf.pymupdf_version,
        error=None,
    )


def _extract_pdf_pypdf_fallback(data: bytes, reason: str) -> ExtractionResult:
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(data))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
        return ExtractionResult(
            status=ExtractionStatus.success if text else ExtractionStatus.partial,
            text=text or None,
            confidence=0.8,  # no OCR fallback in this path — text-layer-only extraction
            engine="pypdf",
            engine_version=pypdf.__version__,
            error=reason,
        )
    except Exception as exc:
        return ExtractionResult(
            status=ExtractionStatus.failed,
            text=None,
            confidence=None,
            engine="pypdf",
            engine_version=None,
            error=f"{reason}; pypdf fallback also failed: {exc}",
        )


def _extract_image(data: bytes) -> ExtractionResult:
    try:
        import pytesseract
        from PIL import Image, ImageOps
    except ImportError as exc:
        return ExtractionResult(
            status=ExtractionStatus.failed,
            text=None,
            confidence=None,
            engine="tesseract",
            engine_version=None,
            error=f"OCR dependencies not installed: {exc}",
        )

    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:
        return ExtractionResult(
            status=ExtractionStatus.failed,
            text=None,
            confidence=None,
            engine="tesseract",
            engine_version=None,
            error=f"Tesseract binary not available: {exc}",
        )

    try:
        image = Image.open(io.BytesIO(data)).convert("L")  # grayscale
        if min(image.size) < 1000:
            scale = 1000 / min(image.size)
            image = image.resize((int(image.width * scale), int(image.height * scale)))
        # Dark-mode terminal screenshots: Tesseract expects dark text on light background.
        if _mean_luminance(image) < 128:
            image = ImageOps.invert(image)

        config = "--oem 1 --psm 6"
        text = pytesseract.image_to_string(image, config=config)
        data_out = pytesseract.image_to_data(
            image, config=config, output_type=pytesseract.Output.DICT
        )
        confidences = [
            int(c)
            for c in data_out.get("conf", [])
            if str(c).lstrip("-").isdigit() and int(c) >= 0
        ]
        mean_confidence = (sum(confidences) / len(confidences) / 100.0) if confidences else None

        return ExtractionResult(
            status=ExtractionStatus.success if text.strip() else ExtractionStatus.partial,
            text=text.strip() or None,
            confidence=mean_confidence,
            engine="tesseract",
            engine_version=str(pytesseract.get_tesseract_version()),
            error=None,
        )
    except Exception as exc:
        return ExtractionResult(
            status=ExtractionStatus.failed,
            text=None,
            confidence=None,
            engine="tesseract",
            engine_version=None,
            error=str(exc),
        )


def _mean_luminance(image) -> float:
    histogram = image.histogram()
    pixels = sum(histogram)
    if pixels == 0:
        return 255.0
    return sum(i * count for i, count in enumerate(histogram)) / pixels
