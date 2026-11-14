"""tests/test_score_record.py — `POST /score_record` and `document_hook.write_scores()`.

atrium-digital-convert#4 W3, atrium-project#84 (D18): the common line-quality model scores a
record's lines, one model and one band scheme whatever their text source. The record gets the
scoring fields only (`atrium_document.SCORING_FIELDS`), on the rows it already has, and every line
with text is scored: digital-convert's decode verdict is a docproc flag, not a reason to skip a
line (rule 2). An empty page gets no page score (rule 7).

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
SHA512 = "9" * 128


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


def _line(page: str, line: int, text: str, block: str, bbox=None) -> dict:
    row = {"page": page, "line": line, "text": text, "block": block, "text_source": "text-layer"}
    row["bbox_precision"] = "measured" if bbox else "absent"
    if bbox:
        row["bbox"] = bbox
    return row


def _digital_record() -> dict:
    """A born-digital record as the record builder writes it (`build_record.py`, atrium-project#84):
    four pages, the last one empty."""
    doc = DocumentRecord("C-202000543A-DT-27", "ocr-postprocess")
    doc.set_source(sha512=SHA512, filename="report.pdf", media_type="application/pdf", origin="digital", page_count=4)
    doc.merge_block(
        "pages",
        [
            {"page_index": 1, "page": "i", "content_status": "text"},
            {"page_index": 2, "page": "1", "content_status": "text"},
            {"page_index": 3, "page": "2", "content_status": "text"},
            {"page_index": 4, "page": "3", "content_status": "empty"},
        ],
    )
    doc.merge_block(
        "lines",
        [
            _line("i", 1, "ok: Zpráva o výzkumu", "p1-b1", bbox=[72, 64, 388, 80]),
            _line("i", 2, "sondI", "p1-b2"),  # digital-convert flagged it Garbage in docproc
            _line("i", 3, "xx#&@", "p1-b2"),
            _line("1", 1, "ok: Hradiště", "p2-b1"),
            _line("1", 2, "   ", "p2-b1"),
            _line("2", 1, "Ä‚Ä", "p3-b1"),  # flagged Inverted in docproc
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


def test_every_line_with_text_gets_the_scoring_fields_only(classifier):
    record = _digital_record()
    response = _post(record)
    assert response.status_code == 200, response.text
    body = response.json()
    atrium_openapi.validate_response(_SPEC, "/score_record", "post", 200, body)

    # every line with text, flagged or not (D18 rule 2), in record order, as it is
    assert classifier.call_args.args[0] == ["ok: Zpráva o výzkumu", "sondI", "xx#&@", "ok: Hradiště", "Ä‚Ä"]
    assert [(e["page"], e["line"], e["category"]) for e in body["cleaned_lines"]] == [
        ("i", 1, "Clear"),
        ("i", 2, "Trash"),
        ("i", 3, "Trash"),
        ("1", 1, "Clear"),
        ("2", 1, "Trash"),
    ]
    assert [e["line_num"] for e in body["cleaned_lines"]] == [1, 2, 3, 1, 1], "per page"
    assert {p["page"]: (p["lines_scored"], p["skipped_empty"]) for p in body["pages"]} == {
        "i": (3, 0),
        "1": (1, 1),
        "2": (1, 0),
    }
    assert all("skipped_decode_verdict" not in p for p in body["pages"])

    out = body["document_json"]
    validate_document(out)
    lines = {(r["page"], r["line"]): r for r in out["lines"]}
    assert lines[("i", 1)]["categ"] == "Clear" and lines[("i", 1)]["lang"] == "ces"
    assert lines[("i", 1)]["text"] == "ok: Zpráva o výzkumu" and lines[("i", 1)]["bbox"] == [72, 64, 388, 80]
    assert lines[("i", 2)]["categ"] == "Trash" and lines[("2", 1)]["categ"] == "Trash"
    assert lines[("i", 2)]["block"] == "p1-b2" and lines[("i", 2)]["text_source"] == "text-layer"
    assert "categ" not in lines[("1", 2)], "a line without text is not scored"
    assert len(out["lines"]) == len(record["lines"]) and len(out["pages"]) == len(record["pages"])
    pages = {p["page_index"]: p for p in out["pages"]}
    assert pages[2]["quality_band"] == "Clear" and pages[1]["page"] == "i"
    assert pages[3]["quality_band"] == "Trash" and "quality_score" not in pages[3], "no Clear or Noisy line"
    assert not {"quality_score", "quality_band"} & set(pages[4]), "an empty page has no page score (rule 7)"
    assert out["source"] == record["source"] and out["doc_id"] == record["doc_id"]
    assert "assembled" not in out, "record 2.0: run data is the paradata's"

    action = body["paradata"]
    names = [entity.get("name") for entity in action["object"] + action["result"]]
    assert names == ["atrium/document-json", "atrium/document-json"]
    assert all(len(entity["sha512"]) == 128 for entity in action["object"] + action["result"])


def test_the_batchs_document_passes_run_on_a_scored_record(classifier):
    """D18 rule 4: the endpoint runs the batch's document passes: a repeated text gets its modal
    category, as the batch's dedup gives it, whatever the line-by-line answer was."""
    record = _digital_record()
    record["lines"][3]["text"] = "ok: Zpráva o výzkumu"  # page 1 repeats page i's heading
    classifier.side_effect = lambda texts, notes=None: [
        _answer(t, "Noisy", 0.6) if i == 3 else _fake_classifier([t])[0] for i, t in enumerate(texts)
    ]
    body = _post(record).json()
    repeated = [e["category"] for e in body["cleaned_lines"] if e["text"] == "ok: Zpráva o výzkumu"]
    assert repeated == ["Clear", "Clear"]
    assert [
        row.get("categ") for row in body["document_json"]["lines"] if row.get("text") == "ok: Zpráva o výzkumu"
    ] == [
        "Clear",
        "Clear",
    ]


def test_a_block_unit_keeps_its_score_but_not_its_pages(classifier):
    """D18 rule 5: a row marked `unit: block` is scored, but the page's score leaves it out."""
    record = _digital_record()
    record["lines"][3]["unit"] = "block"  # page 1's only scored line
    body = _post(record).json()
    out = body["document_json"]
    assert out["lines"][3]["unit"] == "block" and out["lines"][3]["categ"] == "Clear"
    page_1 = next(p for p in out["pages"] if p["page"] == "1")
    assert "quality_score" not in page_1 and "quality_band" not in page_1
    assert out["quality_summary"]["lines"]["block_units"] == 1


def test_the_pages_field_selects_by_the_records_own_keys(classifier):
    body = _post(_digital_record(), pages="1").json()
    assert [(e["page"], e["line"]) for e in body["cleaned_lines"]] == [("1", 1)]
    assert [p["page"] for p in body["pages"]] == ["1"]
    lines = {(r["page"], r["line"]): r for r in body["document_json"]["lines"]}
    assert "categ" not in lines[("i", 1)]


def test_a_page_key_the_record_does_not_have_is_422(classifier):
    response = _post(_digital_record(), pages="1, zz")
    assert response.status_code == 422
    assert response.json()["detail"] == "pages: the record has no page zz."
    classifier.assert_not_called()


def test_a_scored_record_is_re_scored(classifier):
    doc = DocumentRecord("CTX01", "ocr-postprocess")
    doc.set_source(sha512=SHA512, filename="CTX01.alto.xml", origin="legacy")
    doc.merge_block("pages", [{"page_index": 1, "page": "1", "content_status": "text"}])
    doc.merge_block(
        "lines",
        [{"page": "1", "line": 1, "text": "ok", "text_source": "legacy", "categ": "Noisy", "quality_score": 0.5}],
    )
    body = _post(doc.to_dict()).json()
    line = body["document_json"]["lines"][0]
    assert (line["categ"], line["quality_score"]) == ("Clear", 0.9)
    assert "assembled" not in body["document_json"]


def test_a_record_with_nothing_to_score_comes_back_as_sent(classifier):
    record = _digital_record()
    record["lines"] = [row for row in record["lines"] if not row["text"].strip()]
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
    assert response.status_code == 500 and "answered 4 lines for 5" in response.json()["detail"]


def test_info_lists_the_endpoint():
    assert "/score_record" in client.get("/info").json()["endpoints"]


# ── document_hook.write_scores ────────────────────────────────────────────────────────────────


def _write(tmp_path, record) -> str:
    path = tmp_path / f"{record['doc_id']}.document.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return record["doc_id"]


def test_write_scores_never_adds_a_row_and_leaves_an_empty_page_unscored(tmp_path):
    record = _digital_record()
    doc_id = _write(tmp_path, record)
    written = document_hook.write_scores(
        str(tmp_path),
        doc_id,
        pages=[
            {"page": "i", "quality_score": 0.5},
            {"page": "zz", "quality_score": 0.1},
            {"page": "3", "quality_score": 0.2},  # the empty page
        ],
        lines=[
            {"page": "i", "line": 2, "categ": "Clear"},
            {"page": "i", "line": 9, "categ": "Clear"},  # no such row
            {"page": "1", "line": 1, "categ": "Noisy", "quality_score": 0.5},
        ],
    )
    out = json.loads(Path(written).read_text(encoding="utf-8"))
    lines = {(r["page"], r["line"]): r for r in out["lines"]}
    assert lines[("i", 2)]["categ"] == "Clear" and ("i", 9) not in lines
    assert lines[("1", 1)]["categ"] == "Noisy"
    pages = {p["page_index"]: p for p in out["pages"]}
    assert list(pages) == [1, 2, 3, 4]
    assert pages[1]["quality_score"] == 0.5 and "quality_score" not in pages[4]


def test_write_scores_refuses_to_write_text(tmp_path):
    doc_id = _write(tmp_path, _digital_record())
    before = (tmp_path / f"{doc_id}.document.json").read_bytes()
    with pytest.raises(ValueError, match=r"not \['text'\]"):
        document_hook.write_scores(str(tmp_path), doc_id, lines=[{"page": "1", "line": 1, "text": "new"}])
    with pytest.raises(ValueError, match=r"not \['ocr'\]"):
        document_hook.write_scores(str(tmp_path), doc_id, pages=[{"page": "1", "ocr": {"engine": "x"}}])
    assert (tmp_path / f"{doc_id}.document.json").read_bytes() == before


def test_write_scores_needs_a_record(tmp_path):
    assert document_hook.write_scores(str(tmp_path), "absent", lines=[{"page": "1", "line": 1}]) is None
    assert document_hook.write_scores("", "absent") is None


def test_the_scoring_fields_are_record_fields():
    """The fields /score_record writes are the record builder's (D18: it owns the line scores)."""
    from atrium_document import BLOCK_FIELD_OWNERS, RECORD_BUILDER

    assert RECORD_BUILDER == document_hook.PROGRAM_NAME
    assert set(document_hook.SCORE_LINE_FIELDS) <= set(BLOCK_FIELD_OWNERS["lines"][RECORD_BUILDER])
    assert set(document_hook.SCORE_PAGE_FIELDS) <= set(BLOCK_FIELD_OWNERS["pages"][RECORD_BUILDER])


# ── 2026-10-16: the CPU pre-filter's answers, the limits as /info reports them, the licence ─────


def _prefiltered(text: str, category: str = "Non-text") -> dict:
    """What the classifier returns for a line the CPU pre-filter settled (service.text_inference)."""
    from service.text_inference import _fast_track_entry

    return _fast_track_entry(text, category) | {"line_num": 1}


def test_a_line_the_prefilter_settled_gets_no_language_and_no_weight_in_the_page_score(classifier):
    doc = DocumentRecord("C-202000543A-DT-27", "ocr-postprocess")
    doc.set_source(sha512=SHA512, filename="report.pdf", media_type="application/pdf", origin="digital")
    doc.merge_block("pages", [{"page_index": 1, "page": "1", "content_status": "text"}])
    doc.merge_block(
        "lines",
        [_line("1", 1, "ok: Hradiště", "p1-b1"), _line("1", 2, "1998", "p1-b2")],
        key_fields=["page", "line"],
    )
    record = doc.to_dict()
    classifier.side_effect = lambda texts, notes=None: [
        _answer(t, "Clear", 0.9) if "ok" in t else _prefiltered(t) for t in texts
    ]

    response = _post(record)
    assert response.status_code == 200, response.text
    out = response.json()["document_json"]
    lines = {r["line"]: r for r in out["lines"]}
    assert lines[2]["categ"] == "Non-text" and lines[2]["quality_score"] == 0.0
    assert "lang" not in lines[2], "'N/A' is not a language: the batch path leaves it out of the record"
    assert lines[1]["lang"] == "ces"
    page = out["pages"][0]
    assert page["quality_score"] == 0.9, "the 0.0 placeholder of a pre-filter answer is not evidence of low quality"
    assert page["quality_band"] == "Clear"


def test_the_page_limit_is_the_one_info_reports_the_config_file_included(classifier, tmp_path, monkeypatch):
    """`MAX_PAGES` set in `[TEXT_INGEST]` of the config file refused a record in /info's table but not
    at /score_record, which read the default (or the environment) alone."""
    config = tmp_path / "config.txt"
    config.write_text("[TEXT_INGEST]\nMAX_PAGES = 1\n", encoding="utf-8")
    monkeypatch.setenv("LANGID_CONFIG", str(config))
    import service.text_inference as ti

    ti.ingest_settings.cache_clear()
    try:
        assert client.get("/info").json()["limits"]["max_pages"] == 1
        response = _post(_digital_record())  # three pages with text
        assert response.status_code == 413, response.text
        assert response.json()["reason"] == "limit_exceeded"
        classifier.assert_not_called()
    finally:
        ti.ingest_settings.cache_clear()


def test_a_scoring_run_records_the_perplexity_model_in_its_paradata(classifier, monkeypatch):
    from atrium_paradata import ParadataLogger

    logged = []
    monkeypatch.setattr(ParadataLogger, "log_component", lambda self, name, license=None: logged.append(name))
    assert _post(_digital_record()).status_code == 200
    assert logged == ["qwen2.5_0.5b"]


def test_a_run_where_the_prefilter_answered_every_line_used_no_model(classifier, monkeypatch):
    from atrium_paradata import ParadataLogger

    logged = []
    monkeypatch.setattr(ParadataLogger, "log_component", lambda self, name, license=None: logged.append(name))
    classifier.side_effect = lambda texts, notes=None: [_prefiltered(t) for t in texts]
    assert _post(_digital_record()).status_code == 200
    assert logged == []
