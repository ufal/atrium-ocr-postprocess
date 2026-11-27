"""tests/test_docproc_merge.py — the record of a mixed document from its two docprocs (atrium-project#84, H6).

The fixtures `tests/fixtures/docproc/mixed.digital-convert.docproc.json` and `mixed.atr.docproc.json`
are one document, hand-written:

* page 1: a text layer digital-convert reads (a heading, a two-line paragraph with a decode flag, a page
  number); the ATR holds the heading and the paragraph in one block (`p1-b0`), with OCR text that lost
  its diacritics, and the page number in `p1-b1`;
* page 2: no text layer (`needsAtr`); the ATR, forced on it, reads two blocks and echoes the force;
* page 3: a text-layer paragraph and caption, and a figure the layer has no text for, whose OCR child
  (`p3-b1.t0`) the ATR reads;
* page 4: blank, routed `empty` by both.

The ATR measures pages a fraction of a point larger than digital-convert (595.276 x 841.89 vs 595 x 842).
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import atrium_openapi
import atrium_rocrate
import build_record
from atrium_document import validate_document
from atrium_paradata import ParadataLogger
from docproc import DocprocError, read_docproc
from docproc_merge import merge
from service.text_api import app

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docproc"
_SPEC = atrium_openapi.load(Path(__file__).resolve().parent.parent / "service" / "openapi.json")
SEED = {
    "doc_id": "C-MIXED-1",
    "source": {"sha512": "b" * 128, "filename": "C-MIXED-1.pdf", "media_type": "application/pdf"},
}
client = TestClient(app)


def _docproc(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.docproc.json").read_text(encoding="utf-8"))


def _bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _merged(atr: dict | None = None, digital: dict | None = None):
    return merge(
        read_docproc(atr if atr is not None else _docproc("mixed.atr")),
        read_docproc(digital if digital is not None else _docproc("mixed.digital-convert")),
    )


def _rows(document) -> list[tuple]:
    return [
        (index, line.block, line.text_source, line.text)
        for index in sorted(document.pages)
        for line in document.pages[index].lines
    ]


# ── the merge ──────────────────────────────────────────────────────────────────────────────────


def test_the_text_layers_exact_lines_are_kept_and_linked_to_the_atr_blocks():
    document, atr_pages = _merged()
    assert atr_pages == [2]
    page1 = [row for row in _rows(document) if row[0] == 1]
    assert page1 == [
        (1, "p1-b0", "text-layer", "Zpráva o výzkumu"),
        (1, "p1-b0", "text-layer", "Sonda S1 byla vyhloubena u severní hradby."),
        (1, "p1-b0", "text-layer", "V hloubce 40 cm se objevila kulturní vrstva."),
        (1, "p1-b1", "text-layer", "1"),
    ]
    first = document.pages[1].lines[0]
    assert first.bbox == [72.0, 60.0, 260.0, 78.0] and first.bbox_precision == "measured"
    assert document.pages[1].lines[2].flags == ["decode:garbled"]  # the decode flag stays a line flag (rule 2)


def test_an_older_docprocs_decode_flags_read_as_the_new_spelling():
    """digital-convert wrote `Garbage`/`Inverted` before 2026-11-16 (atrium-project#84 round 8); the
    reader takes them as `decode:garbled`/`decode:inverted` and leaves other flags alone."""
    data = _docproc("mixed.digital-convert")
    lines = data["pages"][0]["blocks"][1]["lines"]
    lines[0]["flags"], lines[1]["flags"] = ["Garbage"], ["Inverted", "level_source:font"]
    flags = {line.text: line.flags for line in read_docproc(data).pages[1].lines}
    assert flags[lines[0]["text"]] == ["decode:garbled"]
    assert flags[lines[1]["text"]] == ["decode:inverted", "level_source:font"]


def test_a_forced_page_takes_the_atrs_lines():
    document, _ = _merged()
    assert [row for row in _rows(document) if row[0] == 2] == [
        (2, "p2-b0", "atr", "Nálezy jsou uloženy v depozitáři."),
        (2, "p2-b0", "atr", "Inventární čísla 12–48."),
        (2, "p2-b1", "atr", "Podpis vedoucího výzkumu"),
    ]
    assert {line.bbox_precision for line in document.pages[2].lines} == {"derived"}


# ── an office document's forced page keeps its own text (atrium-project#84 decision 3, R4) ────────


def _line(text: str, box: list) -> dict:
    return {"text": text, "bbox": box, "bboxPrecision": "measured"}


def _office(*blocks: dict) -> dict:
    """mixed.digital-convert as a DOCX whose forced page 2 holds `blocks` (no running text: object text,
    a table's cells, a header), as digital-convert routes such a page to the ATR."""
    digital = _docproc("mixed.digital-convert")
    digital["sourceFormat"] = "docx"
    page = digital["pages"][1]
    page["textLayer"] = "digital"
    page["needsAtrReason"] = "an office page without running text: it shows 1 graphic(s)"
    page["blocks"] = [dict(block, readingOrder=n) for n, block in enumerate(blocks)]
    return digital


def _scan_page(*extra: dict) -> dict:
    """mixed.atr whose page 2 is the ATR's reading of a pasted scan: a page-sized figure and its `.t0` OCR
    child at the top, as the ATR writes figure OCR, and `extra` blocks after them."""
    atr = _docproc("mixed.atr")
    atr["pages"][1]["blocks"] = [
        {
            "id": "p2-b0",
            "type": "figure",
            "bbox": [72.0, 150.0, 520.0, 700.0],
            "readingOrder": 0,
            "imageRef": "assets/p2-b0.png",
            "children": ["p2-b0.t0"],
        },
        {
            "id": "p2-b0.t0",
            "type": "text",
            "bbox": [72.0, 150.0, 520.0, 300.0],
            "readingOrder": 1,
            "parent": "p2-b0",
            "content": "Sonda II profil A–B\nnivelační bod 245,30",
        },
        *[dict(block, readingOrder=2 + n) for n, block in enumerate(extra)],
    ]
    return atr


def test_a_forced_office_page_keeps_its_cells_and_adds_the_scans_ocr():
    """A table and a scan on a page with no running text: the native cells are kept, linked to the
    ATR's table, whose own OCR of them is dropped; the scan's OCR, which no native line holds, is
    added."""
    digital = _office(
        {
            "id": "p2-b0",
            "type": "table",
            "bbox": [80.0, 720.0, 500.0, 760.0],
            "content": "Objekt\tDatace",
            "lines": [_line("Objekt", [80.0, 720.0, 200.0, 735.0]), _line("Datace", [300.0, 720.0, 420.0, 735.0])],
        },
    )
    atr = _scan_page(
        {"id": "p2-b1", "type": "table", "bbox": [75.0, 715.0, 505.0, 765.0], "content": "0bjekt Datace"},
    )
    document, atr_pages = _merged(atr=atr, digital=digital)
    assert atr_pages == [2]
    assert [row for row in _rows(document) if row[0] == 2] == [
        (2, "p2-b0.t0", "atr", "Sonda II profil A–B"),
        (2, "p2-b0.t0", "atr", "nivelační bod 245,30"),
        (2, "p2-b1", "text-layer", "Objekt"),
        (2, "p2-b1", "text-layer", "Datace"),
    ]


def test_a_caption_over_the_scan_keeps_the_scans_ocr():
    """R8: the caption's text box lies inside the scan's OCR child, so it links to it; the caption does
    not hold the OCR's words, so both are kept, the native line first."""
    digital = _office(
        {
            "id": "p2-b0",
            "type": "paragraph",
            "bbox": [100.0, 200.0, 400.0, 212.0],
            "content": "Obr. 2: Plán sondy II",
            "lines": [_line("Obr. 2: Plán sondy II", [100.0, 200.0, 400.0, 212.0])],
        },
    )
    document, _ = _merged(atr=_scan_page(), digital=digital)
    assert [row for row in _rows(document) if row[0] == 2] == [
        (2, "p2-b0.t0", "text-layer", "Obr. 2: Plán sondy II"),
        (2, "p2-b0.t0", "atr", "Sonda II profil A–B"),
        (2, "p2-b0.t0", "atr", "nivelační bod 245,30"),
    ]


def test_a_header_linked_by_distance_keeps_the_scans_ocr():
    """A running header above the scan links to the nearest block, the OCR child (a tie with the
    figure, which the smaller block wins); its words are not the OCR's, so the OCR stays."""
    digital = _office(
        {
            "id": "p2-b0",
            "type": "header",
            "bbox": [72.0, 100.0, 300.0, 112.0],
            "content": "Archeologický ústav",
            "lines": [_line("Archeologický ústav", [72.0, 100.0, 300.0, 112.0])],
        },
    )
    document, _ = _merged(atr=_scan_page(), digital=digital)
    rows = [row for row in _rows(document) if row[0] == 2]
    assert (2, "p2-b0.t0", "text-layer", "Archeologický ústav") in rows
    assert (2, "p2-b0.t0", "atr", "Sonda II profil A–B") in rows


def test_an_atr_block_whose_words_the_native_lines_hold_is_not_doubled():
    """At least `KEEP_NATIVE_MIN_RECALL` of a block's words in the native lines linked to it: the
    native lines stand for it."""
    digital = _office(
        {
            "id": "p2-b0",
            "type": "paragraph",
            "bbox": [100.0, 200.0, 400.0, 212.0],
            "content": "Sonda II profil A–B",
            "lines": [_line("Sonda II profil A–B", [100.0, 200.0, 400.0, 212.0])],
        },
    )
    atr = _scan_page()
    atr["pages"][1]["blocks"][1]["content"] = "Sonda II profil A-B"
    document, _ = _merged(atr=atr, digital=digital)
    assert [row for row in _rows(document) if row[0] == 2] == [(2, "p2-b0.t0", "text-layer", "Sonda II profil A–B")]


def test_a_forced_office_page_with_lines_and_no_atr_block_fails():
    digital = _office(
        {
            "id": "p2-b0",
            "type": "paragraph",
            "bbox": [100.0, 200.0, 400.0, 212.0],
            "content": "Obr. 2",
            "lines": [_line("Obr. 2", [100.0, 200.0, 400.0, 212.0])],
        },
    )
    atr = _scan_page()
    atr["pages"][1]["blocks"] = []
    with pytest.raises(DocprocError) as info:
        _merged(atr=atr, digital=digital)
    assert info.value.reason == "atr_page_missing"


def test_a_forced_pdf_page_with_lines_still_takes_the_atrs():
    """The keep-native rule is an office document's: a PDF page goes to the ATR because its layer
    cannot be used, so its lines give way to the ATR's."""
    digital = _docproc("mixed.digital-convert")
    digital["pages"][1]["blocks"] = [
        {
            "id": "p2-b0",
            "type": "paragraph",
            "bbox": [72.0, 80.0, 520.0, 92.0],
            "readingOrder": 0,
            "content": "Nlezy jsou ulo eny",
            "lines": [_line("Nlezy jsou ulo eny", [72.0, 80.0, 520.0, 92.0])],
        },
    ]
    document, _ = _merged(digital=digital)
    assert {row[2] for row in _rows(document) if row[0] == 2} == {"atr"}


def test_an_atr_block_no_text_layer_line_falls_into_adds_its_lines_in_reading_order():
    """The figure's OCR child has no text-layer line: its text is the ATR's, between the paragraph and
    the caption, as the ATR orders them (H8). The figure itself gives no line."""
    document, _ = _merged()
    assert [row for row in _rows(document) if row[0] == 3] == [
        (3, "p3-b0", "text-layer", "Obr. 1 ukazuje plán sondy."),
        (3, "p3-b1.t0", "atr", "S1  M 1:50"),
        (3, "p3-b2", "text-layer", "Obr. 1: Plán sondy S1"),
    ]


def test_a_nested_block_wins_over_its_parent():
    """A text-layer line inside the figure's OCR child links to the child, not to the figure, so the
    child's OCR text is not added a second time."""
    digital = _docproc("mixed.digital-convert")
    digital["pages"][2]["blocks"].append(
        {
            "id": "p3-b2",
            "type": "paragraph",
            "bbox": [310.0, 384.0, 380.0, 396.0],
            "readingOrder": 2,
            "content": "S1 M 1:50",
            "lines": [{"text": "S1 M 1:50", "bbox": [310.0, 384.0, 380.0, 396.0], "bboxPrecision": "measured"}],
        }
    )
    document, _ = _merged(digital=digital)
    page3 = [row for row in _rows(document) if row[0] == 3]
    assert (3, "p3-b1.t0", "text-layer", "S1 M 1:50") in page3
    assert all(row[3] != "S1  M 1:50" for row in page3)


def test_a_line_outside_every_block_goes_to_the_nearest():
    digital = _docproc("mixed.digital-convert")
    digital["pages"][0]["blocks"][2]["lines"][0]["bbox"] = [295.0, 780.0, 300.0, 790.0]  # above the footer box
    document, _ = _merged(digital=digital)
    assert document.pages[1].lines[-1].block == "p1-b1"


def test_a_line_without_a_box_follows_its_digital_convert_block():
    digital = _docproc("mixed.digital-convert")
    row = digital["pages"][0]["blocks"][1]["lines"][1]
    del row["bbox"]
    row["bboxPrecision"] = "absent"
    document, _ = _merged(digital=digital)
    line = document.pages[1].lines[2]
    assert line.block == "p1-b0" and line.bbox is None and line.bbox_precision == "absent"


def test_an_empty_page_stays_empty():
    document, _ = _merged()
    assert 4 not in document.pages and document.routes[4] == "empty" and document.page_count == 4


def test_the_canvas_is_the_stored_docprocs():
    atr = _docproc("mixed.atr")
    atr["pages"][1]["pixelRatio"] = 2.777
    document, _ = _merged(atr=atr)
    page = document.pages[2]
    assert (page.width, page.height, page.unit, page.pixel_ratio) == (595.276, 841.89, "pt", 2.777)


def test_the_merged_origin_is_digital_while_a_text_layer_line_is_kept():
    document, _ = _merged()
    assert document.origin == "digital"
    digital = _docproc("mixed.digital-convert")
    for page in digital["pages"]:
        page["needsAtr"], page["needsAtrReason"], page["blocks"] = True, "forced", []
    atr = _docproc("mixed.atr")
    for row in atr["metadata"]["routing"]["pages"]:
        if row["route"] == "atr":
            row.update(forced=True, forced_reason="forced")
    document, _ = _merged(atr=atr, digital=digital)
    assert document.origin == "atr"


# ── what fails the job ─────────────────────────────────────────────────────────────────────────


def _no_atr_page(data: dict) -> None:
    for page in data["pages"]:
        page.pop("needsAtr", None)
        page.pop("needsAtrReason", None)
    data["pages"][1]["blocks"] = [
        {"id": "p2-b0", "type": "paragraph", "bbox": [72.0, 80.0, 300.0, 92.0], "readingOrder": 0, "content": "Text."}
    ]


@pytest.mark.parametrize(
    "change_atr, change_digital, reason",
    [
        (None, _no_atr_page, "not_mixed"),
        (lambda d: d["pages"][0].update(needsAtr=True, needsAtrReason="x"), None, "docproc_roles"),
        (lambda d: d["metadata"]["routing"]["pages"].append({"page": 5, "route": "empty"}), None, "page_mismatch"),
        (
            lambda d: d["metadata"]["routing"]["pages"][0].update(forced=True, forced_reason="x"),
            None,
            "forced_mismatch",
        ),
        (lambda d: d["pages"][0].update(width=612.0, height=792.0), None, "coordinate_mismatch"),
        (lambda d: d["pages"][2].update(coordinateUnit="px"), None, "coordinate_mismatch"),
        (lambda d: d["pages"][0].update(blocks=[]), None, "atr_page_missing"),
    ],
)
def test_two_docprocs_that_disagree_fail_the_job(change_atr, change_digital, reason):
    atr, digital = _docproc("mixed.atr"), _docproc("mixed.digital-convert")
    if change_atr:
        change_atr(atr)
    if change_digital:
        change_digital(digital)
    with pytest.raises(DocprocError) as info:
        _merged(atr=atr, digital=digital)
    assert info.value.reason == reason


def test_an_atr_that_echoes_no_force_is_not_checked_for_one():
    atr = _docproc("mixed.atr")
    for row in atr["metadata"]["routing"]["pages"]:
        row.pop("forced", None)
        row.pop("forced_reason", None)
    _, atr_pages = _merged(atr=atr)
    assert atr_pages == [2]


# ── the builder ────────────────────────────────────────────────────────────────────────────────


def _build(tmp_path, seed=SEED, **kwargs):
    return build_record.build(
        _bytes(_docproc("mixed.atr")),
        str(tmp_path),
        seed=seed,
        text_layer_docproc_data=_bytes(_docproc("mixed.digital-convert")),
        **kwargs,
    )


def test_the_mixed_record_is_valid_and_complete(tmp_path):
    built = _build(tmp_path)
    record = json.loads(built.data)
    validate_document(record)
    assert record["source"]["origin"] == "digital" and record["source"]["page_count"] == 4
    assert [(p["page_index"], p["content_status"]) for p in record["pages"]] == [
        (1, "text"),
        (2, "text"),
        (3, "text"),
        (4, "empty"),
    ]
    assert {row["text_source"] for row in record["lines"]} == {"text-layer", "atr"}
    assert [row["line"] for row in record["lines"] if row["page"] == "1"] == [1, 2, 3, 4]
    assert record["content"]["text"].startswith("Zpráva o výzkumu\nSonda S1")
    assert "S1  M 1:50" in record["content"]["text"]


def test_the_paradata_name_digital_converts_docproc_as_not_stored(tmp_path):
    run = ParadataLogger(program="ocr-postprocess", config={}, paradata_dir=None)
    _build(tmp_path, logger=run)
    run.finalize()
    assert [(i["name"], i["stored"]) for i in run.record["inputs"]] == [
        ("atr/docproc-json", True),
        ("atr/docproc-json", False),
        ("atrium/document-json", True),
    ]
    digital = _bytes(_docproc("mixed.digital-convert"))
    assert run.record["inputs"][1]["sha512"] == hashlib.sha512(digital).hexdigest()
    assert run.record["parameters"]["atr_pages"] == [2]


def test_a_baseline_from_page_classification_keeps_its_categories(tmp_path):
    """page-classification runs beside the ATR on the forced pages (H3), so its record can be the
    builder's baseline: its rows gain the builder's fields and keep their category."""
    seed = copy.deepcopy(SEED)
    seed["schema_version"], seed["record_type"] = "2.0", "atrium-document"
    seed["page_categories"] = {"2": "TEXT_T"}
    seed["pages"] = [{"page_index": 2, "category": "TEXT_T", "category_confidence": 0.93}]
    record = json.loads(_build(tmp_path, seed=seed).data)
    validate_document(record)
    page2 = next(p for p in record["pages"] if p["page_index"] == 2)
    assert page2["category"] == "TEXT_T" and page2["content_status"] == "text"


def test_a_baseline_row_for_a_page_the_original_lacks_fails(tmp_path):
    seed = copy.deepcopy(SEED)
    seed["pages"] = [{"page_index": 9, "category": "TEXT_T"}]
    with pytest.raises(DocprocError) as info:
        _build(tmp_path, seed=seed)
    assert info.value.reason == "baseline_pages"


def test_the_command_takes_the_text_layer_docproc(tmp_path):
    out, para = tmp_path / "records", tmp_path / "para"
    argv = [
        str(FIXTURES / "mixed.atr.docproc.json"),
        "--text-layer-docproc",
        str(FIXTURES / "mixed.digital-convert.docproc.json"),
        "--doc-id",
        "M-1",
        "--out-dir",
        str(out),
        "--paradata-dir",
        str(para),
        "--no-score",
    ]
    assert build_record.main(argv) == 0
    record = json.loads((out / "M-1.document.json").read_text(encoding="utf-8"))
    assert {row["text_source"] for row in record["lines"]} == {"text-layer", "atr"}
    (written,) = para.glob("*_ocr-postprocess.json")
    inputs = json.loads(written.read_text(encoding="utf-8"))["inputs"]
    assert [(entry["name"], entry["stored"]) for entry in inputs] == [
        ("atr/docproc-json", True),
        ("atr/docproc-json", False),
    ]


def test_the_command_writes_nothing_when_the_two_disagree(tmp_path):
    out, para = tmp_path / "records", tmp_path / "para"
    argv = [
        str(FIXTURES / "mixed.digital-convert.docproc.json"),
        "--text-layer-docproc",
        str(FIXTURES / "mixed.atr.docproc.json"),
        "--doc-id",
        "M-1",
        "--out-dir",
        str(out),
        "--paradata-dir",
        str(para),
        "--no-score",
    ]
    assert build_record.main(argv) == 1
    assert not out.exists() or list(out.iterdir()) == []


# ── the service ────────────────────────────────────────────────────────────────────────────────


def _post(atr: dict, digital: dict | None, **data):
    files = {
        "docproc": ("doc.atr.docproc.json", _bytes(atr), "application/json"),
        "document_json": ("seed.document.json", _bytes(SEED), "application/json"),
    }
    if digital is not None:
        files["text_layer_docproc"] = ("doc.digital-convert.docproc.json", _bytes(digital), "application/json")
    return client.post("/build_record", files=files, data=data)


def test_build_record_merges_a_mixed_document():
    response = _post(_docproc("mixed.atr"), _docproc("mixed.digital-convert"), score="false")
    assert response.status_code == 200, response.text
    body = response.json()
    atrium_openapi.validate_response(_SPEC, "/build_record", "post", 200, body)
    validate_document(body["document_json"])
    action = body["paradata"]
    assert atrium_rocrate.action_problems(action) == []
    assert [(e["name"], e.get("stored")) for e in action["object"]] == [
        ("atr/docproc-json", None),
        ("atr/docproc-json", False),
        ("atrium/document-json", None),
    ]


def test_build_record_refuses_a_mixed_document_whose_docprocs_disagree():
    atr = _docproc("mixed.atr")
    atr["pages"][0]["width"] = 612.0
    response = _post(atr, _docproc("mixed.digital-convert"), score="false")
    assert response.status_code == 422, response.text
    assert response.json()["cause"] == "coordinate_mismatch"
