"""The OCR hand-off through the service: `POST /process` with a born-digital record and `page`.

atrium-digital-convert#4 W4. digital-convert flags the pages of a born-digital PDF whose text
layer does not decode (`pages[].needs_ocr`); the pipeline OCRs them; the ATR ALTO of one such page
comes here with the record. The classified lines then REPLACE that page's lines, its `pages[]` row
gets the page's quality and `ocr.engine`, and every other page, `content` and the original's
`source` stay as the converter wrote them.

The shared module's half of the rule (`replace_page_rows`, the `ocr-handoff` stamp, the fan-in) is
covered by tests/test_document_originators.py, vendored from the hub. This file covers this repo's
half: the `page` form field, the page mapping, the refusals that come before any model runs, and
`document_hook.write_document_block`, which the batch stages share with the service.
"""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from atrium_document import OCR_HANDOFF, DocumentRecord, merge_document_records, validate_document
from document_hook import write_document_block
from service.text_api import app

client = TestClient(app)

_DOC_ID = "CTX000000001"
_ALTO_NS = "http://www.loc.gov/standards/alto/ns-v3#"

# The converter's three lines on page ii: a subset font without /ToUnicode, so `Garbage`.
_GARBLED = [
    {"page": "ii", "line": 0, "text": "sondI", "categ": "Garbage"},
    {"page": "ii", "line": 1, "text": "hIeby", "categ": "Garbage"},
    {"page": "ii", "line": 2, "text": "��", "categ": "Garbage"},
]

# What text_inference returns for the ATR ALTO of page ii.
_OCR_RESULT_LINES = [
    {
        "text": "sondě",
        "lang": "ces",
        "lang_score": 0.98,
        "perplexity": 40.0,
        "quality_score": 0.91,
        "category": "Clear",
        "line_num": 1,
    },
    {
        "text": "hřeby",
        "lang": "ces",
        "lang_score": 0.97,
        "perplexity": 55.0,
        "quality_score": 0.88,
        "category": "Clear",
        "line_num": 2,
    },
]


def _alto(*physical_img_nrs, software=None):
    """An ALTO with one <Page> per PHYSICAL_IMG_NR, naming `software` in its Description."""
    description = ""
    if software:
        description = (
            '<Description><OCRProcessing ID="OCR_0"><ocrProcessingStep><processingSoftware>'
            f"<softwareName>{software}</softwareName>"
            "</processingSoftware></ocrProcessingStep></OCRProcessing></Description>"
        )
    pages = "".join(
        f'<Page ID="P{i}" PHYSICAL_IMG_NR="{nr}" WIDTH="1000" HEIGHT="2000"><PrintSpace/></Page>'
        for i, nr in enumerate(physical_img_nrs, 1)
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><alto xmlns="{_ALTO_NS}">{description}<Layout>{pages}</Layout></alto>'
    )


def _born_digital_record(tmp_path, flag_page_ii=True):
    """digital-convert's record of a PDF labelled i, ii, 1 whose page ii does not decode (flagged
    `needs_ocr` unless `flag_page_ii` is False); page-classification has categorised page ii."""
    with DocumentRecord(_DOC_ID, "digital-convert", out_dir=str(tmp_path)) as doc:
        doc.set_source(
            sha256="b" * 64, filename=f"{_DOC_ID}.pdf", origin="digital-born-pdf", media_type="application/pdf"
        )
        page_ii = {
            "page": "ii",
            "page_index": 2,
            "canvas": {"width": 595, "height": 842, "unit": "pt"},
            "text_layer": "garbled",
        }
        if flag_page_ii:
            page_ii.update(needs_ocr=True, needs_ocr_reason="garbled text layer")
        doc.merge_block(
            "pages",
            [
                {"page": "i", "page_index": 1, "text_layer": "digital"},
                page_ii,
                {"page": "1", "page_index": 3, "text_layer": "digital"},
            ],
        )
        doc.merge_block(
            "lines",
            [
                {"page": "i", "line": 0, "text": "Zpráva o výzkumu"},
                *_GARBLED,
                {"page": "1", "line": 0, "text": "Nálezy"},
            ],
        )
        doc.set_block("content", {"text": "Zpráva o výzkumu\nNálezy", "reading_order": "layout"})
        baseline = doc.to_dict()
    with DocumentRecord(_DOC_ID, "page-classification", baseline=baseline, out_dir=str(tmp_path)) as pc:
        pc.merge_block("pages", [{"page": "ii", "category": "TEXT_P", "category_confidence": 0.9}])
    return tmp_path / f"{_DOC_ID}.document.json"


def _post(
    upload, record_path=None, page=None, task_type="alto", filename=f"{_DOC_ID}.alto.xml", media_type="application/xml"
):
    files = {"file": (filename, upload.encode("utf-8") if isinstance(upload, str) else upload, media_type)}
    if record_path is not None:
        files["document_json"] = (f"{_DOC_ID}.document.json", record_path.read_bytes(), "application/json")
    data = {"task_type": task_type}
    if page is not None:
        data["page"] = page
    return client.post("/process", files=files, data=data)


def _rows(record, page):
    return [(row["line"], row["text"]) for row in record["lines"] if row["page"] == page]


def _page(record, page):
    return next(row for row in record["pages"] if row["page"] == page)


@pytest.fixture
def ocr_pass():
    """process_alto, mocked: the classified lines of the ATR ALTO."""
    with patch("service.text_api.text_manager.process_alto", create=True) as mocked:
        mocked.return_value = {"type": "alto_xml", "cleaned_lines": json.loads(json.dumps(_OCR_RESULT_LINES))}
        yield mocked


# ── the hand-off ─────────────────────────────────────────────────────────────


def test_the_flagged_page_is_replaced_and_every_other_page_is_left_alone(ocr_pass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    record_path = _born_digital_record(tmp_path)
    before = json.loads(record_path.read_text(encoding="utf-8"))

    response = _post(_alto("2", software="PERO-OCR"), record_path, page="ii")
    assert response.status_code == 200, response.text
    body = response.json()
    record = body["document_json"]

    # The page holds what the OCR read, and nothing of the converter's undecodable layer.
    assert _rows(record, "ii") == [(1, "sondě"), (2, "hřeby")]
    assert [row["categ"] for row in record["lines"] if row["page"] == "ii"] == ["Clear", "Clear"]
    assert _rows(record, "i") == _rows(before, "i") and _rows(record, "1") == _rows(before, "1")
    assert [row["page"] for row in record["lines"]] == ["i", "ii", "ii", "1"]

    # The flagged page's row: the converter's and page-classification's fields stay, the OCR adds its own.
    page_ii = _page(record, "ii")
    assert (page_ii["page_index"], page_ii["needs_ocr"], page_ii["text_layer"]) == (2, True, "garbled")
    assert (page_ii["canvas"]["unit"], page_ii["category"]) == ("pt", "TEXT_P")
    assert (page_ii["quality_score"], page_ii["quality_band"]) == (0.895, "Clear")
    assert page_ii["ocr"] == {"engine": "ocr:pero"}
    assert _page(record, "i") == _page(before, "i") and _page(record, "1") == _page(before, "1")

    # The record stays the converter's: its source (not the ALTO's) and its content.
    assert record["source"] == before["source"]
    assert record["content"] == before["content"]
    for block in ("lines", "pages"):
        stamp = record["assembled"]["blocks"][block]
        assert (stamp["program"], stamp["contribution"]) == ("ocr-postprocess", OCR_HANDOFF)
    assert record["assembled"]["blocks"]["content"]["program"] == "digital-convert"

    assert body["ocr_handoff"] == {"page": "ii", "lines_replaced": 3, "lines_written": 2}
    assert body["paradata"]["@id"] == record["assembled"]["blocks"]["lines"]["run_uuid"]
    validate_document(record)


def test_without_page_the_alto_position_is_mapped_through_page_index(ocr_pass, tmp_path, monkeypatch):
    """PHYSICAL_IMG_NR 2 is the PDF's second page, labelled `ii`: matched by position, never by label."""
    monkeypatch.chdir(tmp_path)
    response = _post(_alto("2"), _born_digital_record(tmp_path))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ocr_handoff"]["page"] == "ii"
    assert _rows(body["document_json"], "ii") == [(1, "sondě"), (2, "hřeby")]


def test_the_engine_falls_back_to_the_configured_alto_origin(ocr_pass, tmp_path, monkeypatch):
    """An ALTO that names no software: the configured ALTO origin (setup/config.txt, default ABBYY-ALTO)."""
    monkeypatch.chdir(tmp_path)
    response = _post(_alto("2"), _born_digital_record(tmp_path), page="ii")
    assert response.status_code == 200, response.text
    assert _page(response.json()["document_json"], "ii")["ocr"] == {"engine": "ABBYY-ALTO"}


def test_an_empty_ocr_pass_empties_the_page(tmp_path, monkeypatch):
    """OCR found no text: the converter's rows go, nothing comes, and the page still records the engine."""
    monkeypatch.chdir(tmp_path)
    with patch("service.text_api.text_manager.process_alto", create=True) as mocked:
        mocked.return_value = {"type": "alto_xml", "cleaned_lines": []}
        response = _post(_alto("2", software="Tesseract"), _born_digital_record(tmp_path), page="ii")
    assert response.status_code == 200, response.text
    body = response.json()
    record = body["document_json"]
    assert _rows(record, "ii") == []
    assert [row["page"] for row in record["lines"]] == ["i", "1"]
    assert _page(record, "ii")["ocr"] == {"engine": "ocr:tesseract"}
    assert record["assembled"]["blocks"]["lines"]["contribution"] == OCR_HANDOFF
    assert body["ocr_handoff"] == {"page": "ii", "lines_replaced": 3, "lines_written": 0}


def test_a_json_upload_with_page_re_acquires_that_page(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("service.text_api.text_manager.process_json", create=True) as mocked:
        mocked.return_value = {"type": "json", "cleaned_lines": json.loads(json.dumps(_OCR_RESULT_LINES))}
        response = _post(
            b'{"lines": []}',
            _born_digital_record(tmp_path),
            page="ii",
            task_type="json",
            filename=f"{_DOC_ID}.json",
            media_type="application/json",
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ocr_handoff"]["page"] == "ii"
    assert _rows(body["document_json"], "ii") == [(1, "sondě"), (2, "hřeby")]
    assert "engine" in _page(body["document_json"], "ii")["ocr"]


def test_a_json_upload_without_page_writes_nothing(tmp_path, monkeypatch, caplog):
    """A JSON page carries no page identity: on a record that flags pages, `page` must name one."""
    monkeypatch.chdir(tmp_path)
    record_path = _born_digital_record(tmp_path)
    with patch("service.text_api.text_manager.process_json", create=True) as mocked:
        mocked.return_value = {"type": "json", "cleaned_lines": json.loads(json.dumps(_OCR_RESULT_LINES))}
        response = _post(
            b'{"lines": []}', record_path, task_type="json", filename=f"{_DOC_ID}.json", media_type="application/json"
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "ocr_handoff" not in body
    assert body["document_json"] == json.loads(record_path.read_text(encoding="utf-8"))
    assert len(body["cleaned_lines"]) == 2
    assert "send `page`" in caplog.text


def test_page_on_a_record_of_this_repos_own_origin_only_names_the_key(ocr_pass, tmp_path, monkeypatch):
    """An OCR record is this repo's plane: `page` picks the row key, and nothing is a hand-off."""
    monkeypatch.chdir(tmp_path)
    with DocumentRecord(_DOC_ID, "page-classification", out_dir=str(tmp_path)) as doc:
        doc.set_source(sha256="a" * 64, filename=f"{_DOC_ID}.alto.xml", origin="ABBYY-ALTO")
        doc.merge_block("pages", [{"page": p, "category": "Text", "category_confidence": 0.91} for p in ("7", "8")])
    record_path = tmp_path / f"{_DOC_ID}.document.json"

    response = _post(_alto("7"), record_path, page="8")
    assert response.status_code == 200, response.text
    body = response.json()
    record = body["document_json"]
    assert "ocr_handoff" not in body
    assert {row["page"] for row in record["lines"]} == {"8"}
    assert "contribution" not in record["assembled"]["blocks"]["lines"]


def test_the_hand_off_passes_the_fan_in(ocr_pass, tmp_path, monkeypatch):
    """The converter's record and the OCR'd one fold into one record (merge_document_records)."""
    monkeypatch.chdir(tmp_path)
    record_path = _born_digital_record(tmp_path)
    digital = tmp_path / "digital.document.json"
    digital.write_text(record_path.read_text(encoding="utf-8"), encoding="utf-8")

    response = _post(_alto("2", software="PERO-OCR"), record_path, page="ii")
    assert response.status_code == 200, response.text
    ocrd = tmp_path / "ocrd.document.json"
    ocrd.write_text(json.dumps(response.json()["document_json"]), encoding="utf-8")

    merged = json.loads(
        open(merge_document_records([str(digital), str(ocrd)], str(tmp_path / "merged.json")), encoding="utf-8").read()
    )
    assert _rows(merged, "ii") == [(1, "sondě"), (2, "hřeby")]
    assert merged["assembled"]["blocks"]["lines"]["contribution"] == OCR_HANDOFF


# ── refused before any model runs ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("alto", "page", "detail"),
    [
        (_alto("2"), "iii", "the record has no page 'iii'"),
        (_alto("1"), "i", "page 'i' does not ask for OCR"),
        (_alto("2", "3"), "ii", "one ALTO page per request; this upload has 2 pages"),
        (_alto("9"), None, "the ALTO page '9' (PHYSICAL_IMG_NR, a physical position) is no page of the record"),
    ],
    ids=["unknown-page", "unflagged-page", "multi-page-alto", "position-outside-the-record"],
)
def test_a_page_that_cannot_be_re_acquired_is_422(ocr_pass, tmp_path, monkeypatch, alto, page, detail):
    monkeypatch.chdir(tmp_path)
    response = _post(alto, _born_digital_record(tmp_path), page=page)
    assert response.status_code == 422, response.text
    assert detail in response.json()["detail"]
    ocr_pass.assert_not_called()


def test_page_without_a_record_is_422(ocr_pass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    response = _post(_alto("2"), page="ii")
    assert response.status_code == 422, response.text
    assert "send it as `document_json`" in response.json()["detail"]
    ocr_pass.assert_not_called()


def test_page_on_a_document_upload_is_422(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch("service.text_api.text_manager.process_document", create=True) as mocked:
        response = _post(
            b"%PDF-1.4",
            _born_digital_record(tmp_path),
            page="ii",
            task_type="document",
            filename=f"{_DOC_ID}.pdf",
            media_type="application/pdf",
        )
        mocked.assert_not_called()
    assert response.status_code == 422, response.text
    assert "a document upload has pages of its own" in response.json()["detail"]


def test_page_on_a_record_that_flags_nothing_is_422(ocr_pass, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    response = _post(_alto("2"), _born_digital_record(tmp_path, flag_page_ii=False), page="ii")
    assert response.status_code == 422, response.text
    assert "flags no page needs_ocr" in response.json()["detail"]
    ocr_pass.assert_not_called()


# ── the batch path: document_hook.write_document_block ──────────────────────


def _hook_rows():
    pages = [
        {"page": "2", "quality_score": 0.9, "quality_band": "Clear", "ocr": {"engine": "ocr:pero"}},
        {"page": "3", "quality_score": 0.1, "quality_band": "Trash"},
    ]
    lines = [
        {"page": "2", "line": 1, "text": "sondě", "categ": "Clear", "quality_score": 0.91, "lang": "ces"},
        {"page": "3", "line": 1, "text": "not this page", "categ": "Clear"},
    ]
    return pages, lines


def test_the_batch_write_keys_by_position_and_holds_back_unflagged_pages(tmp_path):
    """The batch stages key pages by PHYSICAL_IMG_NR: 2 is `ii`, 3 is `1` (not flagged, held back)."""
    record_dir = tmp_path / "records"
    record_dir.mkdir()
    record_path = _born_digital_record(record_dir)
    before = json.loads(record_path.read_text(encoding="utf-8"))
    pages, lines = _hook_rows()

    written = write_document_block(
        str(record_dir),
        _DOC_ID,
        run_id=None,
        source={"sha256": "c" * 64, "filename": f"{_DOC_ID}.alto.xml", "origin": "ABBYY-ALTO"},
        merge_blocks={"pages": pages, "lines": lines},
    )
    assert written is not None
    record = json.loads(open(written, encoding="utf-8").read())
    assert _rows(record, "ii") == [(1, "sondě")]
    assert _rows(record, "1") == _rows(before, "1")
    assert _page(record, "1") == _page(before, "1")
    assert _page(record, "ii")["ocr"] == {"engine": "ocr:pero"}
    assert record["source"] == before["source"]


def test_the_batch_write_refuses_to_re_acquire_an_unflagged_page(tmp_path):
    record_dir = tmp_path / "records"
    record_dir.mkdir()
    _born_digital_record(record_dir)
    with pytest.raises(ValueError, match=r"not flagged needs_ocr"):
        write_document_block(str(record_dir), _DOC_ID, run_id=None, ocr_pages=["i"])
