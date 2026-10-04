"""tests/test_score_record.py — `POST /score_record` and `document_hook.write_scores()`.

atrium-digital-convert#4 W3: the common line-quality model scores a record another tool
originated. A born-digital record's lines are digital-convert's; whether their text reads is
this repository's question, and the pilot wants one answer for scanned and born-digital
documents alike. The record gets the scoring fields only (`atrium_document.SCORING_FIELDS`),
on the rows it already has; digital-convert's decode verdict (`Garbage`, `Inverted`) stands.

The classifier is patched (`text_manager.classify_line_texts`), so no model is loaded; what
is under test is the plumbing: which lines are scored, how answers map back onto rows, what
the record may receive, the limits, and the published contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import atrium_openapi
import document_hook
from atrium_document import DocumentRecord, validate_document
from service.text_api import app

client = TestClient(app)
_SPEC = atrium_openapi.load(Path(__file__).resolve().parent.parent / "service" / "openapi.json")
SHA256 = "9" * 64


def _answer(text: str, category: str, score: float) -> dict:
    """One classifier entry with every field the published `AltoLine` declares."""
    return {
        "line_num": 1,
        "text": text,
        "lang": "ces",
        "lang_score": 0.97,
        "original_lang": "cs",
        "orig_lang_score": 0.97,
        "perplexity": 42.0,
        "garbage_density": 0.0,
        "sym_count": 0,
        "upper_count": 0,
        "repeated_count": 0,
        "ldl_fuses": 0,
        "gibberish": 0,
        "word_weird": 0.05,
        "quality_score": score,
        "category": category,
    }


def _fake_classifier(texts, notes=None):
    """Clear for a line with 'ok' in it, Trash otherwise — deterministic and order-revealing."""
    return [_answer(t, "Clear", 0.9) if "ok" in t else _answer(t, "Trash", 0.1) for t in texts]


def _digital_record(needs_ocr: bool = False) -> dict:
    """A born-digital record as digital-convert writes it (built by the digital-convert originator)."""
    doc = DocumentRecord("C-202000543A-DT-27", "digital-convert")
    doc.set_source(SHA256, filename="report.pdf", media_type="application/pdf", origin="digital-born-pdf")
    doc.merge_block(
        "pages",
        [
            {"page": "i", "page_index": 1, "needs_ocr": needs_ocr},
            {"page": "1", "page_index": 2},
            {"page": "2", "page_index": 3},
        ],
        key_fields=["page"],
    )
    doc.merge_block(
        "lines",
        [
            {"page": "i", "line": 0, "text": "ok: Zpráva o výzkumu", "bbox": [72, 64, 388, 80]},
            {"page": "i", "line": 1, "text": "sondI", "categ": "Garbage"},
            {"page": "i", "line": 2, "text": "xx#&@"},
            {"page": "1", "line": 0, "text": "ok: Hradiště"},
            {"page": "1", "line": 1, "text": "   "},
            {"page": "2", "line": 0, "text": "Ä‚Ä", "categ": "Inverted"},
        ],
        key_fields=["page", "line"],
    )
    record = doc.to_dict()
    validate_document(record)
    return record


def _post(record, **data):
    files = {"document_json": ("r.document.json", json.dumps(record).encode("utf-8"), "application/json")}
    return client.post("/score_record", files=files, data=data)


@pytest.fixture
def classifier():
    with patch("service.text_api.text_manager.classify_line_texts", create=True) as mock:
        mock.side_effect = _fake_classifier
        yield mock


# ── the endpoint ──────────────────────────────────────────────────────────────────────────────


def test_a_born_digital_record_gets_the_scoring_fields_only(classifier):
    record = _digital_record()
    response = _post(record)
    assert response.status_code == 200, response.text
    body = response.json()
    atrium_openapi.validate_response(_SPEC, "/score_record", "post", 200, body)

    # exactly the lines with text and no decode verdict, in record order, as they are
    assert classifier.call_args.args[0] == ["ok: Zpráva o výzkumu", "xx#&@", "ok: Hradiště"]
    assert [(e["page"], e["line"], e["category"]) for e in body["cleaned_lines"]] == [
        ("i", 0, "Clear"),
        ("i", 2, "Trash"),
        ("1", 0, "Clear"),
    ]
    assert [e["line_num"] for e in body["cleaned_lines"]] == [1, 2, 1], "per page"
    assert {p["page"]: (p["lines_scored"], p["skipped_decode_verdict"], p["skipped_empty"]) for p in body["pages"]} == {
        "i": (2, 1, 0),
        "1": (1, 0, 1),
        "2": (0, 1, 0),
    }

    out = body["document_json"]
    validate_document(out)
    lines = {(r["page"], r["line"]): r for r in out["lines"]}
    assert lines[("i", 0)]["categ"] == "Clear" and lines[("i", 0)]["lang"] == "ces"
    assert lines[("i", 0)]["text"] == "ok: Zpráva o výzkumu" and lines[("i", 0)]["bbox"] == [72, 64, 388, 80]
    assert lines[("i", 1)] == {"page": "i", "line": 1, "text": "sondI", "categ": "Garbage"}, "the verdict stands"
    assert lines[("2", 0)]["categ"] == "Inverted"
    assert "categ" not in lines[("1", 1)], "a line without text is not scored"
    assert len(out["lines"]) == len(record["lines"]) and len(out["pages"]) == len(record["pages"])
    pages = {p["page"]: p for p in out["pages"]}
    assert pages["1"]["quality_band"] == "Clear" and pages["i"]["page_index"] == 1
    assert "quality_score" not in pages["2"], "nothing scored there, nothing written"
    assert out["source"] == record["source"] and out["doc_id"] == record["doc_id"]

    stamp = out["assembled"]["blocks"]["lines"]
    assert (stamp["program"], stamp["contribution"]) == ("ocr-postprocess", "scoring")
    assert body["paradata"]["@id"] == stamp["run_uuid"]


def test_the_pages_field_selects_by_the_records_own_keys(classifier):
    body = _post(_digital_record(), pages="1").json()
    assert [(e["page"], e["line"]) for e in body["cleaned_lines"]] == [("1", 0)]
    assert [p["page"] for p in body["pages"]] == ["1"]
    lines = {(r["page"], r["line"]): r for r in body["document_json"]["lines"]}
    assert "categ" not in lines[("i", 0)]


def test_a_page_key_the_record_does_not_have_is_422(classifier):
    response = _post(_digital_record(), pages="1, zz")
    assert response.status_code == 422
    assert response.json()["detail"] == "pages: the record has no page zz."
    classifier.assert_not_called()


def test_an_ocr_record_is_re_scored_as_its_own(classifier):
    doc = DocumentRecord("CTX01", "ocr-postprocess")
    doc.set_source(SHA256, filename="CTX01.alto.xml", origin="ABBYY-ALTO")
    doc.merge_block("lines", [{"page": "1", "line": 1, "text": "ok", "categ": "Noisy", "quality_score": 0.5}])
    body = _post(doc.to_dict()).json()
    line = body["document_json"]["lines"][0]
    assert (line["categ"], line["quality_score"]) == ("Clear", 0.9)
    assert "contribution" not in body["document_json"]["assembled"]["blocks"]["lines"]


def test_a_record_with_nothing_to_score_comes_back_as_sent(classifier):
    record = _digital_record()
    record["lines"] = [row for row in record["lines"] if row.get("categ") in ("Garbage", "Inverted")]
    body = _post(record).json()
    classifier.assert_not_called()
    assert body["cleaned_lines"] == [] and body["document_json"] == record


@pytest.mark.parametrize(
    "part, detail",
    [
        (b"", "empty"),
        (json.dumps({"schema_version": "1.0", "lines": []}).encode(), "no doc_id"),
        (b"{not json", "not valid JSON"),
    ],
)
def test_a_record_that_cannot_be_scored_is_422_invalid_record(classifier, part, detail):
    response = client.post("/score_record", files={"document_json": ("r.json", part, "application/json")})
    assert response.status_code == 422
    assert response.json()["reason"] == "invalid_record" and detail in response.json()["detail"]


def test_the_record_part_is_required():
    assert client.post("/score_record").status_code == 422


def test_a_page_over_max_lines_per_page_is_413(classifier, monkeypatch):
    monkeypatch.setenv("ATRIUM_TEXT_INGEST_MAX_LINES_PER_PAGE", "1")
    response = _post(_digital_record())
    assert response.status_code == 413
    assert response.json()["reason"] == "limit_exceeded" and "MAX_LINES_PER_PAGE" in response.json()["detail"]
    classifier.assert_not_called()


def test_more_pages_than_max_pages_is_413(classifier, monkeypatch):
    monkeypatch.setenv("ATRIUM_TEXT_INGEST_MAX_PAGES", "2")
    response = _post(_digital_record())
    assert response.status_code == 413 and "MAX_PAGES" in response.json()["detail"]


def test_a_classifier_that_loses_lines_is_a_500_not_a_misaligned_record(classifier):
    classifier.side_effect = lambda texts, notes=None: _fake_classifier(texts)[:-1]
    response = _post(_digital_record())
    assert response.status_code == 500 and "answered 2 lines for 3" in response.json()["detail"]


def test_info_lists_the_endpoint():
    assert "/score_record" in client.get("/info").json()["endpoints"]


# ── document_hook.write_scores ────────────────────────────────────────────────────────────────


def _write(tmp_path, record) -> str:
    path = tmp_path / f"{record['doc_id']}.document.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return record["doc_id"]


def test_write_scores_never_adds_a_row_and_respects_the_verdict(tmp_path):
    record = _digital_record()
    doc_id = _write(tmp_path, record)
    written = document_hook.write_scores(
        str(tmp_path),
        doc_id,
        "run",
        pages=[{"page": "i", "quality_score": 0.5}, {"page": "zz", "quality_score": 0.1}],
        lines=[
            {"page": "i", "line": 1, "categ": "Clear"},  # Garbage: stands
            {"page": "i", "line": 9, "categ": "Clear"},  # no such row
            {"page": "1", "line": 0, "categ": "Noisy", "quality_score": 0.5},
        ],
    )
    out = json.loads(Path(written).read_text(encoding="utf-8"))
    lines = {(r["page"], r["line"]): r for r in out["lines"]}
    assert lines[("i", 1)]["categ"] == "Garbage" and ("i", 9) not in lines
    assert lines[("1", 0)]["categ"] == "Noisy"
    assert [p["page"] for p in out["pages"]] == ["i", "1", "2"]


def test_write_scores_refuses_to_write_text(tmp_path):
    doc_id = _write(tmp_path, _digital_record())
    before = (tmp_path / f"{doc_id}.document.json").read_bytes()
    with pytest.raises(ValueError, match=r"not \['text'\]"):
        document_hook.write_scores(str(tmp_path), doc_id, "run", lines=[{"page": "1", "line": 0, "text": "new"}])
    with pytest.raises(ValueError, match=r"not \['ocr'\]"):
        document_hook.write_scores(str(tmp_path), doc_id, "run", pages=[{"page": "1", "ocr": {"engine": "x"}}])
    assert (tmp_path / f"{doc_id}.document.json").read_bytes() == before


def test_write_scores_needs_a_record(tmp_path):
    assert document_hook.write_scores(str(tmp_path), "absent", "run", lines=[{"page": "1", "line": 0}]) is None
    assert document_hook.write_scores("", "absent", "run") is None


def test_the_scoring_fields_are_the_shared_modules():
    from atrium_document import SCORING_FIELDS

    assert set(document_hook.SCORE_LINE_FIELDS) == SCORING_FIELDS["lines"]
    assert set(document_hook.SCORE_PAGE_FIELDS) == SCORING_FIELDS["pages"]
