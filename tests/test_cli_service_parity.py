"""
tests/test_cli_service_parity.py
================================
The command line and the service read the same inputs and give the same results
(atrium-project#72, the service–CLI alignment of 2026-10-26).

Since v1.13.0-beta:

* a plain-text upload with form feeds is paged as `text_split.py` pages it, and a paged JSON as
  `page_split.py` splits it; both sides join split words with `text_util.merge_split_words`;
* `POST /score_record` and `score_record.py` run `record_scoring.py`;
* `include_record=true` gives a record without a baseline, as the batch path writes one;
* `reading_order=alto` keeps an ALTO's own order, as `--method alto-tools`.

What differs on purpose is pinned here too, with the table of docs/usage.md
("The command line and the service") that explains it: blank lines, the page quality mean, and
the paradata (a file on the command line, a `CreateAction` in the response, hub #71).

No model runs: the line classifier is replaced by one that answers every line from its text.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import atrium_openapi  # noqa: E402
import page_split  # noqa: E402
import score_record as score_record_cli  # noqa: E402
import text_split  # noqa: E402
from atrium_document import DocumentRecord, validate_document  # noqa: E402
from extract_JSON_2_TXT import process_json_to_txt  # noqa: E402
from record_scoring import page_records_from_lines  # noqa: E402
from service import text_api  # noqa: E402
from text_util import merge_split_words  # noqa: E402

client = TestClient(text_api.app)
_SPEC = atrium_openapi.load(Path(__file__).resolve().parent.parent / "service" / "openapi.json")

#: Two pages; a word split across a line end in the ALTO extraction's notation (`prefix- {word}`) on
#: each; a blank line inside page 1 and at the top of page 2.
TXT = (
    "Zpráva o archeologickém vý- {výzkumu}\n"
    "zkumu na lokalitě Hradiště.\n"
    "\n"
    "Druhý odstavec první strany.\f\n"
    "Strana dvě: sonda a ano- {anotace}\n"
    "tace konec.\n"
)

#: A paged JSON (Family A: a page list whose items carry their page number).
PAGED_JSON = {
    "pages": [
        {"pageNumber": 1, "lines": [{"text": "Hradiště, sonda 1"}, {"text": "keramika a mazanice"}]},
        {"pageNumber": 2, "lines": [{"text": "Plán výzkumu"}]},
        {"pageNumber": 3, "lines": [{"text": "Závěr zprávy"}, {"text": "podpis vedoucího"}]},
    ]
}


def _answer(text: str, num: int = 1) -> dict:
    """The classifier's entry for a line, with every field the published `AltoLine` declares."""
    empty = not text.strip()
    return {
        "line_num": num,
        "text": text,
        "lang": "N/A" if empty else "ces",
        "lang_score": 0.0 if empty else 0.97,
        "original_lang": "N/A" if empty else "cs",
        "orig_lang_score": 0.0 if empty else 0.97,
        "perplexity": 0.0 if empty else 42.0,
        "garbage_density": 0.0,
        "sym_count": 0,
        "upper_count": 0,
        "repeated_count": 0,
        "ldl_fuses": 0,
        "gibberish": 0,
        "word_weird": 0.0,
        "quality_score": 0.0 if empty else (0.9 if "ok" in text or len(text) > 12 else 0.4),
        "category": "Empty" if empty else ("Clear" if "ok" in text or len(text) > 12 else "Noisy"),
    }


def _classify(lines, notes=None):
    return [_answer(line, n) for n, line in enumerate(lines, 1)]


@pytest.fixture
def echo():
    """The service's classifier, answering from the text alone."""
    with patch.object(text_api.text_manager, "_classify_lines", side_effect=_classify) as mocked:
        yield mocked


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOCUMENT_JSON_DIR", raising=False)
    monkeypatch.delenv("DOCUMENT_SOURCE_ORIGIN", raising=False)
    config = tmp_path / "config.txt"
    config.write_text("[DOCUMENT]\nJSON_DIR =\nSOURCE_ORIGIN =\n\n[TEXT_INGEST]\n", encoding="utf-8")
    monkeypatch.setattr(text_split, "CONFIG_PATH", str(config))
    return tmp_path


def _batch_lines(page_dir: Path, suffix: str = ".txt") -> list:
    """`(page, text)` of each line as classify_TEXT.py reads a document's page files: the lines of
    each file, the split words joined (`merge_split_words`)."""
    rows = []
    files = sorted(page_dir.glob(f"*{suffix}"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
    for page_file in files:
        page = page_file.stem.rsplit("-", 1)[1]
        lines = page_file.read_text(encoding="utf-8").splitlines(keepends=True)
        rows.extend((page, text.strip()) for text, _ws, _we in merge_split_words(lines))
    return rows


def _process(name: str, data: bytes, media_type: str = "text/plain", **form) -> dict:
    response = client.post("/process", files={"file": (name, data, media_type)}, data=form)
    assert response.status_code == 200, response.text
    return response.json()


# ── reading ──────────────────────────────────────────────────────────────────────────────────


def test_a_text_with_form_feeds_is_paged_the_same_on_both_sides(workdir, echo):
    (workdir / "in").mkdir()
    (workdir / "in" / "DOC1.txt").write_text(TXT, encoding="utf-8")
    assert text_split.main([str(workdir / "in"), str(workdir / "out")]) == 0
    batch = _batch_lines(workdir / "out" / "DOC1")

    body = _process("DOC1.txt", TXT.encode("utf-8"))
    atrium_openapi.validate_response(_SPEC, "/process", "post", 200, body)
    service = [(e["page"], e["text"]) for e in body["cleaned_lines"]]

    # The same pages and the same lines, the split words joined the same way ...
    assert service == [row for row in batch if row[1]]
    assert service[:2] == [("1", "Zpráva o archeologickém výzkumu"), ("1", "na lokalitě Hradiště.")]
    assert ("2", "konec.") in service
    assert [p["page"] for p in body["pages"]] == ["1", "2"]
    # ... but the batch keeps a blank line as an `Empty` row and the service drops it, so the line
    # numbers differ by the blank lines (docs/usage.md, "The command line and the service").
    assert [row for row in batch if not row[1]] == [("1", ""), ("2", "")]
    assert [e["line_num"] for e in body["cleaned_lines"] if e["page"] == "2"] == [1, 2]


def test_a_text_without_form_feeds_answers_as_before(echo):
    body = _process("one.txt", b"Jedna strana.\nDruhy radek.\n")
    assert "pages" not in body
    assert all("page" not in e for e in body["cleaned_lines"])


def test_a_paged_json_is_paged_the_same_on_both_sides(workdir, echo):
    src = workdir / "DOC2.json"
    src.write_text(json.dumps(PAGED_JSON), encoding="utf-8")
    assert page_split.split_json_document(str(src), str(workdir / "pages")) == 3
    txt_dir = workdir / "txt"
    txt_dir.mkdir()
    for page_file in (workdir / "pages" / "DOC2").glob("*.json"):
        process_json_to_txt(page_file, txt_dir / f"{page_file.stem}.txt")
    batch = _batch_lines(txt_dir)

    body = _process("DOC2.json", src.read_bytes(), "application/json")
    assert [(e["page"], e["text"]) for e in body["cleaned_lines"]] == batch
    assert [(p["page"], p["lines"]) for p in body["pages"]] == [("1", 2), ("2", 1), ("3", 2)]


def test_page_is_refused_for_a_text_of_several_pages_before_any_model_runs(echo, tmp_path):
    with DocumentRecord("DOC1", "ocr-postprocess", out_dir=str(tmp_path)) as doc:
        doc.set_source(sha256="a" * 64, filename="DOC1.txt", origin="ocr:unknown")
    record = (tmp_path / "DOC1.document.json").read_bytes()
    response = client.post(
        "/process",
        files={"file": ("DOC1.txt", TXT.encode("utf-8"), "text/plain"), "document_json": ("r.json", record)},
        data={"page": "1"},
    )
    assert response.status_code == 422
    assert "2 (form feeds" in response.json()["detail"]
    echo.assert_not_called()


def test_a_paged_text_writes_each_page_into_the_record(echo, tmp_path):
    with DocumentRecord("DOC1", "ocr-postprocess", out_dir=str(tmp_path)) as doc:
        doc.set_source(sha256="a" * 64, filename="DOC1.txt", origin="ocr:unknown")
    record = (tmp_path / "DOC1.document.json").read_bytes()
    response = client.post(
        "/process",
        files={"file": ("DOC1.txt", TXT.encode("utf-8"), "text/plain"), "document_json": ("r.json", record)},
    )
    assert response.status_code == 200, response.text
    returned = response.json()["document_json"]
    assert sorted({row["page"] for row in returned["lines"]}) == ["1", "2"]
    assert [row["page"] for row in returned["pages"]] == ["1", "2"]


# ── the record without a baseline ────────────────────────────────────────────────────────────


def test_include_record_returns_a_new_record_as_the_batch_writes_one(echo):
    body = _process("DOC1.txt", TXT.encode("utf-8"), include_record="true")
    record = body["document_json"]
    validate_document(record)
    assert record["doc_id"] == "DOC1"
    assert record["source"]["filename"] == "DOC1.txt"
    assert [(row["page"], row["line"]) for row in record["lines"]] == [
        (e["page"], e["line_num"]) for e in body["cleaned_lines"]
    ]
    # The record is an output of the call, not an input.
    inputs = body["paradata"]["object"]
    assert [entity.get("name") for entity in inputs] == ["DOC1.txt"]


def test_without_include_record_no_record_comes_back(echo):
    assert "document_json" not in _process("DOC1.txt", TXT.encode("utf-8"))


# ── the reading order of an ALTO ─────────────────────────────────────────────────────────────

ALTO = b"""<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout><Page ID="p1" PHYSICAL_IMG_NR="1" WIDTH="1000" HEIGHT="1000"><PrintSpace>
    <TextBlock ID="b1">
      <TextLine ID="l1" HPOS="10" VPOS="10" WIDTH="500" HEIGHT="20"><String CONTENT="First" HPOS="10" VPOS="10" WIDTH="100" HEIGHT="20"/><SP/><String CONTENT="line" HPOS="120" VPOS="10" WIDTH="100" HEIGHT="20"/></TextLine>
      <TextLine ID="l2" HPOS="10" VPOS="40" WIDTH="500" HEIGHT="20"><String CONTENT="Second" HPOS="10" VPOS="40" WIDTH="100" HEIGHT="20"/><SP/><String CONTENT="line" HPOS="120" VPOS="40" WIDTH="100" HEIGHT="20"/></TextLine>
      <TextLine ID="l3" HPOS="10" VPOS="70" WIDTH="500" HEIGHT="20"><String CONTENT="Third" HPOS="10" VPOS="70" WIDTH="100" HEIGHT="20"/><SP/><String CONTENT="line" HPOS="120" VPOS="70" WIDTH="100" HEIGHT="20"/></TextLine>
    </TextBlock>
  </PrintSpace></Page></Layout>
</alto>
"""


@pytest.fixture
def reversing_layout_reader(monkeypatch):
    """A LayoutReader that reverses the lines, so its use shows in the order."""
    inference = sys.modules[type(text_api.text_manager).__module__]
    monkeypatch.setattr(inference, "boxes2inputs", object())
    monkeypatch.setattr(text_api.text_manager, "layout_model", object())
    monkeypatch.setattr(inference, "_run_layout_reader", lambda lines, boxes, *_args, **_kw: (lines[::-1], boxes[::-1]))


def test_reading_order_alto_keeps_the_alto_order_and_runs_no_layoutreader(echo, reversing_layout_reader):
    default = _process("p.alto.xml", ALTO, "application/xml")
    alto = _process("p.alto.xml", ALTO, "application/xml", reading_order="alto")
    assert [e["text"] for e in default["cleaned_lines"]] == ["Third line", "Second line", "First line"]
    assert [e["text"] for e in alto["cleaned_lines"]] == ["First line", "Second line", "Third line"]
    assert "layoutlmv3" in json.dumps(default["paradata"])
    assert "layoutlmv3" not in json.dumps(alto["paradata"])


def test_a_reading_order_other_than_the_two_is_refused():
    response = client.post(
        "/process", files={"file": ("p.alto.xml", ALTO, "application/xml")}, data={"reading_order": "llm"}
    )
    assert response.status_code == 422


# ── /score_record and score_record.py ────────────────────────────────────────────────────────


def _record(tmp_path: Path) -> Path:
    """An OCR record of two pages: a decode verdict, a blank line, lines to score."""
    with DocumentRecord("DOC3", "ocr-postprocess", out_dir=str(tmp_path)) as doc:
        doc.set_source(sha256="b" * 64, filename="DOC3.alto.xml", origin="ocr:pero")
        doc.merge_block("pages", [{"page": "1"}, {"page": "2"}])
        doc.merge_block(
            "lines",
            [
                {"page": "1", "line": 1, "text": "ok: Zpráva o výzkumu"},
                {"page": "1", "line": 2, "text": "xx#"},
                {"page": "1", "line": 3, "text": "   "},
                {"page": "2", "line": 1, "text": "ok: Hradiště, sonda 2"},
            ],
        )
    return tmp_path / "DOC3.document.json"


def _without_run_stamps(record: dict) -> dict:
    return {key: record.get(key) for key in ("doc_id", "source", "pages", "lines")}


def test_score_record_gives_the_same_answer_on_both_sides(tmp_path):
    path = _record(tmp_path)
    sent = path.read_bytes()
    out = tmp_path / "scored"
    assert (
        score_record_cli.main(
            [str(path), "--out-dir", str(out), "--scores", "--paradata-dir", str(tmp_path / "para")], classify=_classify
        )
        == 0
    )
    cli_record = json.loads((out / "DOC3.document.json").read_text(encoding="utf-8"))
    cli_answer = json.loads((out / "DOC3.scores.json").read_text(encoding="utf-8"))

    with patch("service.text_api.text_manager.classify_line_texts", create=True, side_effect=_classify):
        response = client.post(
            "/score_record", files={"document_json": ("DOC3.document.json", sent, "application/json")}
        )
    assert response.status_code == 200, response.text
    body = response.json()

    for key in ("type", "doc_id", "cleaned_lines", "pages", "limits_applied"):
        assert cli_answer[key] == body[key], key
    assert _without_run_stamps(cli_record) == _without_run_stamps(body["document_json"])
    validate_document(cli_record)
    assert path.read_bytes() == sent, "--out-dir leaves the record it read as it was"


def test_score_record_in_place_and_the_paradata_file(tmp_path):
    """Kept apart on purpose (hub #71): the command line writes the run's paradata beside its
    outputs and updates the record in place, as the batch stages do; the service returns both."""
    path = _record(tmp_path)
    para = tmp_path / "para"
    assert score_record_cli.main([str(tmp_path), "--paradata-dir", str(para)], classify=_classify) == 0
    record = json.loads(path.read_text(encoding="utf-8"))
    assert [row.get("categ") for row in record["lines"]] == ["Clear", "Noisy", None, "Clear"]
    (written,) = list(para.glob("*_ocr-postprocess.json"))
    assert json.loads(written.read_text(encoding="utf-8"))["config"]["command"] == "score_record"


def test_unknown_pages_are_refused_by_both(tmp_path, capsys):
    path = _record(tmp_path)
    sent = path.read_bytes()
    assert (
        score_record_cli.main([str(path), "--pages", "9", "--paradata-dir", str(tmp_path / "p")], classify=_classify)
        == 1
    )
    assert "no page 9" in capsys.readouterr().err
    assert path.read_bytes() == sent
    with patch("service.text_api.text_manager.classify_line_texts", create=True, side_effect=_classify):
        response = client.post(
            "/score_record", files={"document_json": ("r.json", sent, "application/json")}, data={"pages": "9"}
        )
    assert response.status_code == 422


def test_a_missing_path_is_a_usage_error(tmp_path):
    assert score_record_cli.main([str(tmp_path / "nothing.document.json")], classify=_classify) == 2


# ── kept apart, and pinned ───────────────────────────────────────────────────────────────────


def test_the_page_quality_mean_differs_by_design():
    """A page's `quality_score`: the service (and score_record) average the Clear, Noisy and Trash
    lines; the batch aggregation (aggregate_STAT.py) averages the Clear and Noisy lines only.
    docs/usage.md, "The command line and the service", names the difference; nothing changes it."""
    pd = pytest.importorskip("pandas")
    import aggregate_STAT

    rows = [
        {"page": "1", "line": 1, "categ": "Clear", "quality_score": 0.9},
        {"page": "1", "line": 2, "categ": "Noisy", "quality_score": 0.5},
        {"page": "1", "line": 3, "categ": "Trash", "quality_score": 0.1},
        {"page": "1", "line": 4, "categ": "Empty", "quality_score": 0.0},
    ]
    (service,) = page_records_from_lines(rows)
    assert service["quality_score"] == 0.5

    frame = pd.DataFrame(
        [
            {
                "file": "DOC",
                "page_num": 1,
                "categ": r["categ"],
                "quality_score": r["quality_score"],
                "word_count": 1,
                "char_count": 1,
                "word_weird": 0.0,
                "lang_score": 1.0,
                "perplex": 1.0,
                "vowel_ratio": 0.4,
                "rot_ratio": 0.0,
                "lang": "ces",
            }
            for r in rows
        ]
    )
    stats = aggregate_STAT._sum_metrics(frame, ["Clear", "Noisy", "Trash", "Non-text", "Empty"])
    assert stats["avg_quality_score"].iloc[0] == pytest.approx(0.7)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([os.path.abspath(__file__), "-q"]))
