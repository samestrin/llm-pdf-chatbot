"""Read the text of every page of a PDF.

When `OCR_URL` is set, the whole PDF goes to `llm-pdf-ocr-api` `POST /v1/extract`
(`format=json`), which returns one entry per page: `page` (1-based), `text`,
`method` (`text_layer` or `tesseract`) and `error` (null when the page was read).

Any page the OCR call did not read (the call failed, timed out, or that page has
an error or no text) is read from the PyMuPDF text layer instead, and keeps the
OCR failure as `ocr_error`. A page no method could read is failed: it has an
`error` and no text, never an empty string.
"""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
import pymupdf

DEFAULT_OCR_TIMEOUT_SECONDS = 120.0
PYMUPDF = "pymupdf"


@dataclass(frozen=True)
class OcrSettings:
    url: str | None
    timeout: float = DEFAULT_OCR_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "OcrSettings":
        env = os.environ if env is None else env
        raw_timeout = env.get("OCR_TIMEOUT_SECONDS", "").strip()
        timeout = DEFAULT_OCR_TIMEOUT_SECONDS
        if raw_timeout:
            try:
                timeout = float(raw_timeout)
            except ValueError:
                timeout = math.nan
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError(
                    f"OCR_TIMEOUT_SECONDS must be a positive number of seconds, got {raw_timeout!r}"
                )
        return cls(url=env.get("OCR_URL", "").strip() or None, timeout=timeout)


@dataclass(frozen=True)
class TextBox:
    """Where a span of the page text sits on the page, when the reader can say."""

    start: int
    end: int
    """Character offsets into `PageRead.text`, end exclusive."""
    bbox: tuple[float, float, float, float]
    """`(x0, y0, x1, y1)` in PDF points."""


@dataclass(frozen=True)
class PageRead:
    page: int
    """1-based page number."""
    text: str | None
    """The page text; None only when the page failed."""
    method: str | None
    """`text_layer` or `tesseract` (from OCR), `pymupdf` (fallback), or None when failed."""
    ocr_error: str | None = None
    """Why OCR did not read this page, when OCR was tried and failed."""
    error: str | None = None
    """Why no method could read this page."""
    boxes: tuple[TextBox, ...] = ()
    """Bounding boxes for spans of `text`. Neither the OCR contract nor the text layer read
    gives any yet, so this stays empty until a reader returns them."""

    @property
    def failed(self) -> bool:
        return self.error is not None


class OcrError(Exception):
    """The OCR call as a whole gave no usable answer."""


def read_pdf(path: str | Path, settings: OcrSettings | None = None) -> list[PageRead]:
    settings = OcrSettings.from_env() if settings is None else settings
    with pymupdf.open(path, filetype="pdf") as pdf:
        page_count = pdf.page_count
        ocr_pages: dict[int, PageRead] = {}
        call_error: str | None = None
        if settings.url:
            try:
                ocr_pages = _call_ocr(Path(path), settings, page_count)
            except OcrError as exc:
                call_error = str(exc)

        pages = []
        for number in range(1, page_count + 1):
            ocr_page = ocr_pages.get(number)
            if ocr_page is not None and ocr_page.ocr_error is None:
                pages.append(ocr_page)
                continue
            if not settings.url:
                ocr_error = None
            elif call_error is not None:
                ocr_error = call_error
            elif ocr_page is None:
                ocr_error = "OCR response had no entry for this page"
            else:
                ocr_error = ocr_page.ocr_error
            pages.append(_read_text_layer(pdf, number, ocr_error))
    return pages


def _read_text_layer(pdf: pymupdf.Document, number: int, ocr_error: str | None) -> PageRead:
    try:
        text = pdf[number - 1].get_text()
    except Exception as exc:  # noqa: BLE001 -- PyMuPDF raises many types; record any as the reason.
        return PageRead(number, None, None, ocr_error, f"PyMuPDF could not read the page: {exc}")
    if not text.strip():
        return PageRead(number, None, None, ocr_error, "page has no text layer")
    return PageRead(number, text, PYMUPDF, ocr_error)


def _call_ocr(path: Path, settings: OcrSettings, page_count: int) -> dict[int, PageRead]:
    """Each page the OCR service answered for: read, or with its `ocr_error` set."""
    url = settings.url.rstrip("/") + "/v1/extract"
    try:
        with path.open("rb") as file:
            response = httpx.post(
                url,
                files={"file": (path.name, file, "application/pdf")},
                data={"format": "json"},
                timeout=settings.timeout,
            )
    except httpx.TimeoutException as exc:
        raise OcrError(f"OCR request timed out after {settings.timeout:g}s") from exc
    except httpx.TransportError as exc:
        raise OcrError(f"OCR service unreachable: {exc}") from exc
    if response.is_error:
        raise OcrError(f"OCR service returned HTTP {response.status_code}")

    try:
        body = response.json()
        entries = body["pages"] if isinstance(body, dict) else body
        if not isinstance(entries, list):
            raise TypeError("expected a list of pages")
        results: dict[int, PageRead] = {}
        for entry in entries:
            number, text, method, error = (
                entry["page"],
                entry.get("text"),
                entry.get("method"),
                entry.get("error"),
            )
            if not isinstance(number, int) or isinstance(number, bool):
                raise TypeError(f"page number {number!r} is not an integer")
            if not 1 <= number <= page_count or number in results:
                continue
            if error is not None:
                results[number] = PageRead(number, None, None, f"OCR failed on this page: {error}")
            elif not isinstance(text, str) or not text.strip():
                results[number] = PageRead(number, None, None, "OCR returned empty text")
            else:
                results[number] = PageRead(
                    number, text, method if isinstance(method, str) else "ocr"
                )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise OcrError(f"OCR service returned an invalid response: {exc}") from exc
    return results
