import socket
import time

import pymupdf
import pytest

from app.reader import DEFAULT_OCR_TIMEOUT_SECONDS, OcrSettings, read_pdf


def write_pdf(path, texts: list[str]) -> str:
    """One page per entry; an empty entry is a page with no text layer, like a scan."""
    doc = pymupdf.open()
    for text in texts:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(path)
    return str(path)


@pytest.fixture
def pdf(tmp_path):
    return write_pdf(tmp_path / "doc.pdf", ["first page", "second page", "third page"])


def entry(page, text, method="tesseract", error=None):
    return {"page": page, "text": text, "method": method, "error": error}


def test_settings_from_env(monkeypatch):
    monkeypatch.delenv("OCR_URL", raising=False)
    monkeypatch.delenv("OCR_TIMEOUT_SECONDS", raising=False)
    assert OcrSettings.from_env() == OcrSettings(url=None, timeout=DEFAULT_OCR_TIMEOUT_SECONDS)
    assert DEFAULT_OCR_TIMEOUT_SECONDS == 120

    monkeypatch.setenv("OCR_URL", "http://ocr:8000/")
    monkeypatch.setenv("OCR_TIMEOUT_SECONDS", "7.5")
    assert OcrSettings.from_env() == OcrSettings(url="http://ocr:8000/", timeout=7.5)

    monkeypatch.setenv("OCR_URL", "  ")
    assert OcrSettings.from_env().url is None


def test_without_ocr_url_reads_the_pymupdf_text_layer(pdf):
    pages = read_pdf(pdf, OcrSettings(url=None))

    assert [p.page for p in pages] == [1, 2, 3]
    assert [p.text.strip() for p in pages] == ["first page", "second page", "third page"]
    assert {p.method for p in pages} == {"pymupdf"}
    assert all(p.ocr_error is None and p.error is None and not p.failed for p in pages)


def test_page_with_no_text_and_no_ocr_is_failed_not_empty(tmp_path):
    path = write_pdf(tmp_path / "scan.pdf", ["has text", ""])

    pages = read_pdf(path, OcrSettings(url=None))

    assert not pages[0].failed
    blank = pages[1]
    assert blank.failed
    assert blank.text is None
    assert blank.method is None
    assert "no text" in blank.error


def test_ocr_success_uses_ocr_text_and_method(pdf, ocr):
    ocr.body = [
        entry(1, "OCR one", method="text_layer"),
        entry(2, "OCR two"),
        entry(3, "OCR three"),
    ]

    pages = read_pdf(pdf, OcrSettings(url=ocr.url))

    assert [(p.page, p.text, p.method) for p in pages] == [
        (1, "OCR one", "text_layer"),
        (2, "OCR two", "tesseract"),
        (3, "OCR three", "tesseract"),
    ]
    assert all(p.ocr_error is None and not p.failed for p in pages)
    (request,) = ocr.requests
    assert request["path"] == "/v1/extract"
    assert request["content_type"].startswith("multipart/form-data")
    assert b"%PDF" in request["body"]
    assert b'name="format"' in request["body"] and b"json" in request["body"]


def test_ocr_url_with_trailing_slash(pdf, ocr):
    ocr.body = [entry(i, f"p{i}") for i in (1, 2, 3)]

    read_pdf(pdf, OcrSettings(url=ocr.url + "/"))

    assert ocr.requests[0]["path"] == "/v1/extract"


def test_ocr_response_wrapped_in_pages_object(pdf, ocr):
    ocr.body = {"pages": [entry(i, f"p{i}") for i in (1, 2, 3)]}

    pages = read_pdf(pdf, OcrSettings(url=ocr.url))

    assert [p.text for p in pages] == ["p1", "p2", "p3"]


def test_ocr_per_page_error_falls_back_to_pymupdf_with_reason(pdf, ocr):
    ocr.body = [
        entry(1, "OCR one"),
        entry(2, None, method="tesseract", error="tesseract crashed"),
        entry(3, "OCR three"),
    ]

    pages = read_pdf(pdf, OcrSettings(url=ocr.url))

    assert pages[0].method == "tesseract" and pages[2].method == "tesseract"
    fallback = pages[1]
    assert fallback.method == "pymupdf"
    assert fallback.text.strip() == "second page"
    assert "tesseract crashed" in fallback.ocr_error
    assert not fallback.failed


def test_ocr_error_on_page_without_text_layer_is_failed_with_both_reasons(tmp_path, ocr):
    path = write_pdf(tmp_path / "scan.pdf", [""])
    ocr.body = [entry(1, None, error="image too blurry")]

    (page,) = read_pdf(path, OcrSettings(url=ocr.url))

    assert page.failed
    assert page.text is None and page.method is None
    assert "image too blurry" in page.ocr_error
    assert "no text" in page.error


def test_ocr_empty_text_without_error_is_treated_as_an_ocr_failure(pdf, ocr):
    ocr.body = [entry(1, "  "), entry(2, "OCR two"), entry(3, "OCR three")]

    pages = read_pdf(pdf, OcrSettings(url=ocr.url))

    assert pages[0].method == "pymupdf"
    assert "empty" in pages[0].ocr_error


def test_page_missing_from_ocr_response_falls_back(pdf, ocr):
    ocr.body = [entry(1, "OCR one"), entry(3, "OCR three"), entry(9, "no such page")]

    pages = read_pdf(pdf, OcrSettings(url=ocr.url))

    assert [p.method for p in pages] == ["tesseract", "pymupdf", "tesseract"]
    assert "no entry" in pages[1].ocr_error


def test_ocr_timeout_falls_back_for_every_page_with_reason(pdf, ocr):
    ocr.delay = 2.0
    ocr.body = [entry(i, f"p{i}") for i in (1, 2, 3)]

    started = time.monotonic()
    pages = read_pdf(pdf, OcrSettings(url=ocr.url, timeout=0.3))

    assert time.monotonic() - started < 1.5
    assert [p.method for p in pages] == ["pymupdf"] * 3
    assert [p.text.strip() for p in pages] == ["first page", "second page", "third page"]
    for page in pages:
        assert "timed out" in page.ocr_error
        assert "0.3" in page.ocr_error


def test_ocr_service_down_falls_back_for_every_page_with_reason(pdf):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # Nothing listens on the port any more.

    pages = read_pdf(pdf, OcrSettings(url=f"http://127.0.0.1:{port}"))

    assert [p.method for p in pages] == ["pymupdf"] * 3
    for page in pages:
        assert "unreachable" in page.ocr_error


def test_ocr_http_error_falls_back_with_status(pdf, ocr):
    ocr.status = 503
    ocr.body = {"detail": "overloaded"}

    pages = read_pdf(pdf, OcrSettings(url=ocr.url))

    assert [p.method for p in pages] == ["pymupdf"] * 3
    assert all("503" in p.ocr_error for p in pages)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        {"unexpected": True},
        [{"page": "one", "text": "x"}],
        ["nope"],
        # Not a list of pages. An empty dict would otherwise iterate as "no entries".
        {"pages": "not-a-list"},
        {"pages": {}},
        # A bool is an int in Python; `true` must not be taken as page 1.
        [{"page": True, "text": "x", "method": "tesseract", "error": None}],
    ],
)
def test_ocr_malformed_response_falls_back(pdf, ocr, body):
    ocr.body = body

    pages = read_pdf(pdf, OcrSettings(url=ocr.url))

    assert [p.method for p in pages] == ["pymupdf"] * 3
    assert all("invalid" in p.ocr_error for p in pages)


@pytest.mark.parametrize("value", ["soon", "0", "-5", "nan", "inf"])
def test_settings_reject_bad_timeout(monkeypatch, value):
    monkeypatch.setenv("OCR_TIMEOUT_SECONDS", value)

    with pytest.raises(ValueError, match="OCR_TIMEOUT_SECONDS"):
        OcrSettings.from_env()


def test_read_pdf_defaults_to_env_settings(pdf, ocr, monkeypatch):
    monkeypatch.setenv("OCR_URL", ocr.url)
    ocr.body = [entry(i, f"p{i}") for i in (1, 2, 3)]

    pages = read_pdf(pdf)

    assert [p.method for p in pages] == ["tesseract"] * 3


def test_ocr_entry_without_method_is_still_labelled_ocr(pdf, ocr):
    ocr.body = [{"page": i, "text": f"p{i}", "error": None} for i in (1, 2, 3)]

    pages = read_pdf(pdf, OcrSettings(url=ocr.url))

    assert [p.method for p in pages] == ["ocr"] * 3


def test_pymupdf_error_on_a_page_is_failed_with_reason(pdf, monkeypatch):
    real_get_text = pymupdf.Page.get_text

    def get_text(page, *args, **kwargs):
        if page.number == 1:
            raise RuntimeError("broken content stream")
        return real_get_text(page, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_text", get_text)

    pages = read_pdf(pdf, OcrSettings(url=None))

    assert [p.failed for p in pages] == [False, True, False]
    assert pages[1].text is None
    assert "broken content stream" in pages[1].error


def test_duplicate_ocr_entries_for_a_page_keep_the_first(pdf, ocr):
    ocr.body = [
        entry(1, "OCR one"),
        entry(2, "first answer"),
        entry(2, "second answer"),
        entry(2, None, error="late error"),
        entry(3, "OCR three"),
    ]

    pages = read_pdf(pdf, OcrSettings(url=ocr.url))

    assert pages[1].text == "first answer"
    assert pages[1].method == "tesseract" and pages[1].ocr_error is None
