"""tests/test_quality_summary.py — the record's `quality_summary` block (atrium-project#73 R6).

Both record writers of this repo, `document_hook.write_document_block()` and
`document_hook.write_scores()`, end by setting `quality_summary` to
`atrium_document.quality_summary()` of the record as written, whenever the call wrote `pages` or
`lines`. The block is this repo's on every origin (`BLOCK_OWNERS`), so a born-digital record
scored by `/score_record` carries one too. It is numbers only: no band, no threshold.
"""

from __future__ import annotations

import json
from pathlib import Path

import document_hook
from atrium_document import DocumentRecord, load_document, quality_summary, validate_document
from document_hook import document_path
from tests.test_score_record import SHA256, _digital_record, _post, _write, classifier  # noqa: F401


def _summary_of(record: dict) -> dict:
    return quality_summary({"pages": record.get("pages") or [], "lines": record.get("lines") or []})


def _assert_current(record: dict) -> None:
    """The block is there, stamped by this repo, and equal to a fresh computation."""
    validate_document(record)
    assert record["quality_summary"] == _summary_of(record)
    assert record["assembled"]["blocks"]["quality_summary"]["program"] == "ocr-postprocess"
    assert "band" not in json.dumps(record["quality_summary"])


# ── write_document_block (the OCR branch) ─────────────────────────────────────────────────────


def _ocr_write(doc_dir: str, doc_id: str = "CTX01", **blocks) -> dict:
    document_hook.write_document_block(
        doc_dir,
        doc_id,
        run_id="r1",
        source={"sha256": SHA256, "filename": f"{doc_id}.alto.xml", "origin": "ABBYY-ALTO"},
        **blocks,
    )
    return load_document(document_path(doc_dir, doc_id))


def test_an_ocr_write_of_pages_and_lines_sets_the_summary(tmp_path):
    record = _ocr_write(
        str(tmp_path),
        merge_blocks={
            "pages": [
                {"page": "1", "quality_score": 0.9, "quality_band": "Clear"},
                {"page": "2", "quality_score": 0.4, "quality_band": "Noisy"},
                {"page": "3"},
            ],
            "lines": [
                {"page": "1", "line": 1, "text": "Hradiště", "categ": "Clear", "quality_score": 0.9},
                {"page": "1", "line": 2, "text": "x#", "categ": "Trash", "quality_score": 0.1},
                {"page": "2", "line": 1, "text": "Sonda", "categ": "Noisy", "quality_score": 0.5},
            ],
        },
    )
    _assert_current(record)
    assert record["quality_summary"] == {
        "pages": {"total": 3, "scored": 2, "mean": 0.65, "median": 0.65, "min": 0.4, "max": 0.9},
        "lines": {"total": 3, "by_categ": {"Clear": 1, "Noisy": 1, "Trash": 1}},
    }


def test_a_later_stage_keeps_the_summary_in_step(tmp_path):
    """The aggregate stage scores pages after the split stage wrote lines: one summary, recomputed."""
    doc_dir = str(tmp_path)
    _ocr_write(doc_dir, merge_blocks={"lines": [{"page": "1", "line": 1, "text": "Hradiště", "categ": "Clear"}]})
    first = load_document(document_path(doc_dir, "CTX01"))
    assert first["quality_summary"]["pages"] == {"total": 0, "scored": 0}
    record = _ocr_write(doc_dir, merge_blocks={"pages": [{"page": "1", "quality_score": 0.8}]})
    _assert_current(record)
    assert record["quality_summary"]["pages"]["scored"] == 1
    assert record["quality_summary"]["lines"] == {"total": 1, "by_categ": {"Clear": 1}}


def test_a_write_without_pages_or_lines_leaves_no_summary(tmp_path):
    record = _ocr_write(str(tmp_path), set_blocks={"content": {"text": "full text"}})
    assert "quality_summary" not in record
    assert "quality_summary" not in record["assembled"]["blocks"]


def test_a_born_digital_record_gets_no_summary_from_the_ocr_branch(tmp_path):
    """The §1a guard drops the OCR rows, so nothing the summary reads was written."""
    record = _digital_record()
    _write(tmp_path, record)
    document_hook.write_document_block(
        str(tmp_path),
        record["doc_id"],
        run_id="r1",
        merge_blocks={"pages": [{"page": "1", "quality_score": 0.9}]},
        set_blocks={"content": {"text": "x"}},
    )
    out = load_document(document_path(str(tmp_path), record["doc_id"]))
    assert "quality_summary" not in out and out["pages"] == record["pages"]


# ── write_scores and /score_record (the born-digital branch) ──────────────────────────────────


def test_write_scores_sets_the_summary_on_a_born_digital_record(tmp_path):
    record = _digital_record()
    doc_id = _write(tmp_path, record)
    written = document_hook.write_scores(
        str(tmp_path),
        doc_id,
        "run",
        pages=[{"page": "1", "quality_score": 0.9, "quality_band": "Clear"}],
        lines=[{"page": "1", "line": 0, "categ": "Clear", "quality_score": 0.9}],
    )
    out = json.loads(Path(written).read_text(encoding="utf-8"))
    _assert_current(out)
    # digital-convert's decode verdicts are counted with the scored categories
    assert out["quality_summary"]["lines"]["by_categ"] == {"Clear": 1, "Garbage": 1, "Inverted": 1}
    assert out["quality_summary"]["pages"] == {
        "total": 3,
        "scored": 1,
        "mean": 0.9,
        "median": 0.9,
        "min": 0.9,
        "max": 0.9,
    }
    assert out["assembled"]["blocks"]["lines"]["contribution"] == "scoring", "the plane stays the converter's"


def test_score_record_returns_the_summary(classifier):  # noqa: F811
    body = _post(_digital_record()).json()
    out = body["document_json"]
    _assert_current(out)
    assert out["quality_summary"]["lines"]["by_categ"] == {"Clear": 2, "Garbage": 1, "Inverted": 1, "Trash": 1}
    assert out["quality_summary"]["pages"]["scored"] == 2


def test_the_summary_is_deterministic_and_ignores_other_blocks(tmp_path):
    record = _digital_record()
    doc_id = _write(tmp_path, record)
    rows = {"lines": [{"page": "i", "line": 0, "categ": "Clear", "quality_score": 0.9}]}
    first = json.loads(Path(document_hook.write_scores(str(tmp_path), doc_id, "a", **rows)).read_text())
    second = json.loads(Path(document_hook.write_scores(str(tmp_path), doc_id, "b", **rows)).read_text())
    assert first["quality_summary"] == second["quality_summary"]

    with_more = dict(second, content={"text": "anything"}, enrichment={"entities": []})
    assert _summary_of(with_more) == second["quality_summary"]


def test_an_ocr_record_rescored_by_score_record_keeps_one_summary(classifier):  # noqa: F811
    doc = DocumentRecord("CTX01", "ocr-postprocess")
    doc.set_source(SHA256, filename="CTX01.alto.xml", origin="ABBYY-ALTO")
    doc.merge_block("lines", [{"page": "1", "line": 1, "text": "ok", "categ": "Noisy", "quality_score": 0.5}])
    body = _post(doc.to_dict()).json()
    _assert_current(body["document_json"])
    assert body["document_json"]["quality_summary"]["lines"]["by_categ"] == {"Clear": 1}
