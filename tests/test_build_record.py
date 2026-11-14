"""tests/test_build_record.py — the record builder: `docproc.py`, `build_record.py`, `POST /build_record`.

Since ARÚP's output contract of 12 November 2026 (atrium-project#84 §1, D20) ocr-postprocess builds
every record from the stored docproc (`atr/docproc-json`), whichever producer wrote it. The fixtures
under `tests/fixtures/docproc/` are:

* `scan.atr.docproc.json`: hand-written in the ATR's shape (#84 comment 1; valid against the actual
  schema): blocks with `content` and a box, no block `lines[]`, a table with a `cellGrid`, a figure,
  and a third page routed `empty`;
* `table.digital-convert.docproc.json`, `rich.digital-convert.docproc.json`,
  `garbled.digital-convert.docproc.json`: written by digital-convert from its fixture PDF and DOCX
  (the ATRIUM proposal schema: block `lines[]`, the page verdict). The garbled one has a page that
  needs the ATR.

The classifier is a stub, so no model is loaded.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import atrium_openapi
import atrium_rocrate
import build_record
from atrium_document import validate_document
from atrium_paradata import ParadataLogger
from docproc import DocprocError, read_docproc
from service.text_api import app

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docproc"
_SPEC = atrium_openapi.load(Path(__file__).resolve().parent.parent / "service" / "openapi.json")
SEED = {
    "doc_id": "C-202000543A-DT-27",
    "source": {"sha512": "a" * 128, "filename": "C-202000543A-DT-27.pdf", "media_type": "application/pdf"},
}
client = TestClient(app)


def _docproc(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.docproc.json").read_text(encoding="utf-8"))


def _bytes(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def _classify(texts, notes=None):
    """Clear for a line with a capital letter at its start, Trash otherwise: deterministic."""
    return [
        {"category": "Clear", "quality_score": 0.9, "lang": "ces"}
        if text[:1].isupper()
        else {"category": "Trash", "quality_score": 0.1, "lang": "ces"}
        for text in texts
    ]


# ── the reader ─────────────────────────────────────────────────────────────────────────────────


def test_the_atrs_docproc_is_read_block_by_block():
    document = read_docproc(_docproc("scan.atr"))
    assert (document.text_source, document.origin, document.language, document.page_count) == ("atr", "atr", "cs", 3)
    page = document.pages[1]
    assert [(line.block, line.text) for line in page.lines] == [
        ("p1-b0", "Zpráva o archeologickém výzkumu"),  # one line: no line break (rule 5)
        ("p1-b1", "Sonda S1 byla vyhloubena u severní hradby."),
        ("p1-b1", "V hloubce 40 cm se objevila kulturní vrstva."),
        ("p1-b3", "Sonda\tNálezy"),  # a table: one line per cellGrid row
        ("p1-b3", "S1\tkeramika"),
    ], "the figure gives no line"
    assert {line.bbox_precision for line in page.lines} == {"derived"}, "every box is its block's"
    assert page.lines[1].bbox == [72.0, 100.0, 520.0, 140.0]
    assert 3 not in document.pages and document.routes[3] == "empty"


def test_digital_converts_docproc_keeps_its_measured_line_boxes():
    document = read_docproc(_docproc("table.digital-convert"))
    assert (document.text_source, document.origin) == ("text-layer", "digital")
    lines = document.pages[1].lines
    assert [(line.block, line.bbox_precision) for line in lines] == [
        ("p1-b0", "measured"),
        ("p1-b1", "derived"),
        ("p1-b1", "derived"),
        ("p1-b2", "measured"),
    ]
    assert lines[1].text == "Vrstva\tMocnost\tNalezy"


def test_a_flow_document_has_lines_without_boxes():
    document = read_docproc(_docproc("rich.digital-convert"))
    assert document.page_count == 4
    lines = [line for page in document.pages.values() for line in page.lines]
    assert lines and all(line.bbox is None and line.bbox_precision == "absent" for line in lines)
    assert document.pages[1].width is None


def test_a_page_neither_listed_nor_routed_empty_fails_the_build():
    """H15: a page of the original missing from pages[] would make the record partial."""
    data = _docproc("scan.atr")
    data["pages"] = data["pages"][:1]
    with pytest.raises(DocprocError) as info:
        read_docproc(data)
    assert info.value.reason == "unread_pages" and "[2]" in str(info.value)


def test_a_block_whose_text_could_not_be_read_fails_the_build():
    data = _docproc("scan.atr")
    data["pages"][1]["blocks"][0]["flags"] = ["all_engines_failed"]
    with pytest.raises(DocprocError) as info:
        read_docproc(data)
    assert info.value.reason == "all_engines_failed"


@pytest.mark.parametrize("version", ["2.0", "one"])
def test_a_docproc_of_another_major_is_refused(version):
    data = _docproc("scan.atr") | {"version": version}
    with pytest.raises(DocprocError) as info:
        read_docproc(data)
    assert info.value.reason == "invalid_docproc"


# ── the builder ────────────────────────────────────────────────────────────────────────────────


def test_the_record_is_built_from_the_atrs_docproc_and_the_seed(tmp_path):
    run = ParadataLogger(program="ocr-postprocess", config={}, paradata_dir=None)
    docproc = _bytes(_docproc("scan.atr"))
    built = build_record.build(docproc, str(tmp_path), seed=copy.deepcopy(SEED), logger=run)
    record = json.loads(built.data)
    validate_document(record)

    assert record["doc_id"] == SEED["doc_id"]
    assert record["source"] == {**SEED["source"], "origin": "atr", "page_count": 3, "language": ["cs"]}
    assert [(p["page_index"], p["page"], p["content_status"]) for p in record["pages"]] == [
        (1, "1", "text"),
        (2, "2", "text"),
        (3, "3", "empty"),
    ], "a row for every page of the original, the empty one included (D29)"
    assert record["pages"][0]["canvas"] == {"width": 595.0, "height": 842.0, "unit": "pt"}
    assert "canvas" not in record["pages"][2]
    assert [(row["page"], row["line"], row["block"]) for row in record["lines"]] == [
        ("1", 1, "p1-b0"),
        ("1", 2, "p1-b1"),
        ("1", 3, "p1-b1"),
        ("1", 4, "p1-b3"),
        ("1", 5, "p1-b3"),
        ("2", 1, "p2-b0"),
    ], "from 1 per page, in reading order"
    assert {(row["text_source"], row["bbox_precision"]) for row in record["lines"]} == {("atr", "derived")}
    assert record["content"]["text"].splitlines()[0] == "Zpráva o archeologickém výzkumu"
    assert not any("categ" in row for row in record["lines"]), "not scored without a classifier"
    assert "assembled" not in record and "provenance" not in record

    run.finalize()
    assert [(i["name"], len(i["sha512"])) for i in run.record["inputs"]] == [
        ("atr/docproc-json", 128),
        ("atrium/document-json", 128),
    ]
    (output,) = run.record["outputs"]
    assert output["name"] == "atrium/document-json"
    assert {"source", "pages", "lines", "content"} <= set(output["record_fields"])
    assert run.record["parameters"] == {"docproc_version": "1.0", "scored": False}


def test_every_line_is_scored_and_an_empty_page_gets_no_score(tmp_path):
    built = build_record.build(_bytes(_docproc("scan.atr")), str(tmp_path), seed=SEED, classify=_classify)
    record = json.loads(built.data)
    validate_document(record)
    assert [row["categ"] for row in record["lines"]] == ["Clear", "Clear", "Clear", "Clear", "Clear", "Clear"]
    pages = {p["page_index"]: p for p in record["pages"]}
    assert (pages[1]["quality_band"], pages[1]["quality_score"]) == ("Clear", 0.9)
    assert not {"quality_score", "quality_band"} & set(pages[3]), "rule 7"
    assert record["quality_summary"]["pages"]["empty"] == 1


def test_a_block_without_line_breaks_is_one_marked_unit(tmp_path):
    """D18 rule 5: a block whose text has no line break is scored as one unit, marked `unit: block`,
    and kept out of the page score and of quality_summary's line counts."""
    document = read_docproc(_docproc("scan.atr"))
    assert [line.unit for line in document.pages[1].lines] == ["block", None, None, None, None]
    built = build_record.build(_bytes(_docproc("scan.atr")), str(tmp_path), seed=SEED, classify=_classify)
    record = json.loads(built.data)
    validate_document(record)
    units = {(row["page"], row["line"]): row.get("unit") for row in record["lines"]}
    assert units == {
        ("1", 1): "block",
        ("1", 2): None,
        ("1", 3): None,
        ("1", 4): None,
        ("1", 5): None,
        ("2", 1): "block",
    }
    assert all("categ" in row and "quality_score" in row for row in record["lines"]), "rule 1: scored all the same"
    pages = {p["page_index"]: p for p in record["pages"]}
    assert pages[1]["quality_score"] == 0.9
    assert not {"quality_score", "quality_band"} & set(pages[2]), "a page of block units only has no page score"
    assert record["quality_summary"]["lines"] == {"total": 6, "by_categ": {"Clear": 4}, "block_units": 2}


def _answer(text, category, score=0.6, lang_score=0.95):
    """A classifier answer with the signals the document passes read (as the common model gives)."""
    return {
        "text": text,
        "category": category,
        "quality_score": score,
        "lang": "ces",
        "lang_score": lang_score,
        "original_lang": "cs",
        "perplexity": 40.0,
        "word_weird": 0.0,
    }


def test_the_batchs_document_passes_run_on_the_built_record(tmp_path):
    """D18 rule 4: identical text gets its modal category, as the batch's dedup pass gives it."""
    docproc = _docproc("scan.atr")
    docproc["pages"][1]["blocks"][0]["content"] = "Sonda S1 byla vyhloubena u severní hradby."

    def classify(texts, notes=None):
        return [_answer(t, "Noisy" if i == len(texts) - 1 else "Clear", 0.9) for i, t in enumerate(texts)]

    record = json.loads(build_record.build(_bytes(docproc), str(tmp_path), seed=SEED, classify=classify).data)
    repeated = [row["categ"] for row in record["lines"] if row["text"] == "Sonda S1 byla vyhloubena u severní hradby."]
    assert repeated == ["Clear", "Clear"], "the last line's Noisy follows its twin's Clear"


def test_answers_without_the_models_signals_pass_through_the_passes():
    from record_scoring import document_passes

    answers = [{"category": "Trash", "quality_score": 0.1}, {"category": "Clear", "quality_score": 0.9}]
    assert document_passes(answers, ["1", "1"]) == answers


def test_the_inverted_scan_sweep_runs_on_scored_lines():
    """The batch's page sweep: most lines of a page without diacritics and with a weak language id."""
    from record_scoring import document_passes

    texts = ["ouesuod plo eqt", "ʇxǝʇ uʍop ǝpısdn", "ssnpʇɐ ʎʞıʎ", "ppo ɐpuɐ ʎʇ", "Hradba stála dál."]
    answers = [_answer(t, "Noisy", 0.3, lang_score=0.2) for t in texts[:4]] + [_answer(texts[4], "Clear", 0.9)]
    out = document_passes(answers, ["1"] * 5)
    assert [a["category"] for a in out[:4]] == ["Trash"] * 4
    assert out[4]["category"] == "Clear"


def test_a_born_digital_docproc_gives_text_layer_lines(tmp_path):
    built = build_record.build(_bytes(_docproc("table.digital-convert")), str(tmp_path), doc_id="T-1")
    record = json.loads(built.data)
    validate_document(record)
    assert record["source"] == {"origin": "digital", "page_count": 1}
    assert [row["bbox_precision"] for row in record["lines"]] == ["measured", "derived", "derived", "measured"]
    assert {row["text_source"] for row in record["lines"]} == {"text-layer"}
    assert record["pages"][0]["canvas"]["width"] == 612.0


def test_a_flow_documents_record_has_no_boxes(tmp_path):
    record = json.loads(build_record.build(_bytes(_docproc("rich.digital-convert")), str(tmp_path), doc_id="R-1").data)
    validate_document(record)
    assert all("bbox" not in row and row["bbox_precision"] == "absent" for row in record["lines"])
    assert all("canvas" not in page for page in record["pages"])


def test_a_page_that_needs_the_atr_fails_the_build(tmp_path):
    """digital-convert's docproc of a page the ATR must read has no text for it: the ATR's docproc
    is the stored one, and the merge of a mixed document (H6) is not built yet."""
    with pytest.raises(DocprocError) as info:
        build_record.build(_bytes(_docproc("garbled.digital-convert")), str(tmp_path), seed=SEED)
    assert info.value.reason == "needs_atr"
    assert list(tmp_path.iterdir()) == []


def test_the_record_needs_a_doc_id(tmp_path):
    with pytest.raises(ValueError, match="doc_id"):
        build_record.build(_bytes(_docproc("scan.atr")), str(tmp_path))


def test_a_classifier_that_loses_lines_fails_the_build(tmp_path):
    with pytest.raises(RuntimeError, match="answered 5 lines for 6"):
        build_record.build(
            _bytes(_docproc("scan.atr")), str(tmp_path), seed=SEED, classify=lambda texts: _classify(texts)[:-1]
        )


# ── the command line ───────────────────────────────────────────────────────────────────────────


def test_the_command_writes_the_record_and_its_paradata(tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps(SEED), encoding="utf-8")
    out, para = tmp_path / "records", tmp_path / "para"
    argv = [
        str(FIXTURES / "scan.atr.docproc.json"),
        "--seed",
        str(seed),
        "--out-dir",
        str(out),
        "--paradata-dir",
        str(para),
    ]
    assert build_record.main(argv, classify=_classify) == 0
    record = json.loads((out / f"{SEED['doc_id']}.document.json").read_text(encoding="utf-8"))
    validate_document(record)
    assert record["lines"][0]["categ"] == "Clear"
    (written,) = para.glob("*_ocr-postprocess.json")
    paradata = json.loads(written.read_text(encoding="utf-8"))
    assert paradata["action_status"] == "completed"
    assert [o["name"] for o in paradata["outputs"]] == ["atrium/document-json"]


def test_a_failed_build_writes_no_record_and_says_so_in_its_paradata(tmp_path, capsys):
    out, para = tmp_path / "records", tmp_path / "para"
    argv = [
        str(FIXTURES / "garbled.digital-convert.docproc.json"),
        "--doc-id",
        "G-1",
        "--out-dir",
        str(out),
        "--paradata-dir",
        str(para),
        "--no-score",
    ]
    assert build_record.main(argv) == 1
    assert not out.exists() or list(out.iterdir()) == []
    (written,) = para.glob("*_ocr-postprocess.json")
    paradata = json.loads(written.read_text(encoding="utf-8"))
    assert paradata["action_status"] == "failed" and "needs_atr" in paradata["error"]
    assert "needs_atr" in capsys.readouterr().err


# ── the service ────────────────────────────────────────────────────────────────────────────────


def _post(docproc: dict, seed: dict | None = SEED, **data):
    files = {"docproc": ("doc.docproc.json", _bytes(docproc), "application/json")}
    if seed is not None:
        files["document_json"] = ("seed.document.json", _bytes(seed), "application/json")
    return client.post("/build_record", files=files, data=data)


def test_build_record_returns_the_record_and_its_action():
    response = _post(_docproc("scan.atr"), score="false")
    assert response.status_code == 200, response.text
    body = response.json()
    atrium_openapi.validate_response(_SPEC, "/build_record", "post", 200, body)
    record = body["document_json"]
    validate_document(record)
    assert body["doc_id"] == record["doc_id"] == SEED["doc_id"]
    action = body["paradata"]
    assert atrium_rocrate.action_problems(action) == []
    assert [e["name"] for e in action["object"]] == ["atr/docproc-json", "atrium/document-json"]
    assert [e["name"] for e in action["result"]] == ["atrium/document-json"]


def test_build_record_scores_with_the_common_model():
    with patch("service.text_api.text_manager.classify_line_texts", create=True, side_effect=_classify):
        response = _post(_docproc("table.digital-convert"), seed=None, doc_id="T-1")
    assert response.status_code == 200, response.text
    lines = response.json()["document_json"]["lines"]
    assert [row["categ"] for row in lines] == ["Clear", "Clear", "Clear", "Clear"]


@pytest.mark.parametrize(
    "name, change, cause",
    [
        ("garbled.digital-convert", None, "needs_atr"),
        ("scan.atr", lambda d: d.update(pages=d["pages"][:1]), "unread_pages"),
        ("scan.atr", lambda d: d.update(version="2.0"), "invalid_docproc"),
    ],
)
def test_a_docproc_the_builder_cannot_complete_is_422(name, change, cause):
    data = _docproc(name)
    if change:
        change(data)
    response = _post(data, score="false")
    assert response.status_code == 422, response.text
    assert response.json()["cause"] == cause


def test_build_record_needs_a_doc_id():
    response = _post(_docproc("scan.atr"), seed=None, score="false")
    assert response.status_code == 422 and "doc_id" in response.json()["detail"]


def test_info_lists_build_record():
    assert "/build_record" in client.get("/info").json()["endpoints"]
