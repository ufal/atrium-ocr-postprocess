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

Since v1.14.0-beta:

* `reading_order=alto` reads the page with alto-tools, as `--method alto-tools`: the same lines and
  line breaks, a hyphenated line joined to the next;

* a blank line is an `Empty` entry numbered with the rest wherever the batch keeps one: the
  paragraph breaks of an ALTO page, and the blank lines of a text-lines input with
  `[TEXT_INGEST] KEEP_BLANK_LINES = true` (both drop them without it), so `line_num` is the batch's;
* a page's `quality_score` is the mean of its `Clear` and `Noisy` lines on both sides.

What differs on purpose is pinned here too, with the table of docs/usage.md
("The command line and the service") that explains it: the paradata (a file on the command line,
a `CreateAction` in the response, hub #71).

No model runs: the line classifier is replaced by one that answers every line from its text.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import alto_tools  # noqa: E402
import atrium_openapi  # noqa: E402
import extract_ALTO_2_TXT  # noqa: E402
import extract_TEXT_2_TXT  # noqa: E402
import page_split  # noqa: E402
import score_record as score_record_cli  # noqa: E402
import text_split  # noqa: E402
import text_stats_create  # noqa: E402
from atrium_document import DocumentRecord, validate_document  # noqa: E402
from extract_JSON_2_TXT import process_json_to_txt  # noqa: E402
from record_scoring import page_records_from_lines  # noqa: E402
from service import text_api  # noqa: E402
from service.utils import AltoUnreadable  # noqa: E402
from text_util import join_hyphenated_lines, merge_split_words  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
client = TestClient(text_api.app)
_SPEC = atrium_openapi.load(ROOT / "service" / "openapi.json")

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
    """`(page, line_num, text)` of each line as classify_TEXT.py reads a document's page files: the
    lines of each file (`readlines()`), the split words joined (`merge_split_words`), numbered from 1
    per page, a blank line included."""
    rows = []
    files = sorted(page_dir.glob(f"*{suffix}"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
    for page_file in files:
        page = page_file.stem.rsplit("-", 1)[1]
        with open(page_file, encoding="utf-8") as fh:
            lines = fh.readlines()
        rows.extend((page, n, text.strip()) for n, (text, _ws, _we) in enumerate(merge_split_words(lines), 1))
    return rows


def _service_lines(body: dict) -> list:
    return [(e.get("page", "1"), e["line_num"], e["text"]) for e in body["cleaned_lines"]]


@pytest.fixture
def ingest(workdir, monkeypatch):
    """Both sides read one config: the batch stages and the service (LANGID_CONFIG)."""
    ti = sys.modules[type(text_api.text_manager).__module__]

    def write(keep_blank: bool) -> Path:
        config = workdir / "config.txt"
        config.write_text(
            "[DOCUMENT]\nJSON_DIR =\nSOURCE_ORIGIN =\n\n"
            f"[EXTRACT]\nINPUT_CSV = {workdir / 'stats.csv'}\nOUTPUT_TXT_TEXT = {workdir / 'txt'}\n"
            f"OUTPUT_LINES_TEXT = {workdir / 'lines'}\n\n"
            f"[TEXT_INGEST]\nKEEP_BLANK_LINES = {str(keep_blank).lower()}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(extract_TEXT_2_TXT, "CONFIG_PATH", str(config))
        monkeypatch.setenv("LANGID_CONFIG", str(config))
        ti.ingest_settings.cache_clear()
        return config

    yield write
    ti.ingest_settings.cache_clear()


def _text_lines_batch(workdir: Path) -> list:
    """The batch text-lines method up to classify_TEXT.py: text_split.py, text_stats_create.py,
    extract_TEXT_2_TXT.py, then the page files read as classify reads them."""
    pytest.importorskip("pandas")  # extract_TEXT_2_TXT reads the statistics CSV with it
    assert text_split.main([str(workdir / "in"), str(workdir / "out")]) == 0
    assert text_stats_create.main([str(workdir / "out"), "-o", str(workdir / "stats.csv")]) == 0
    assert extract_TEXT_2_TXT.main([]) == 0
    return _batch_lines(workdir / "txt" / "DOC1")


def _process(name: str, data: bytes, media_type: str = "text/plain", **form) -> dict:
    response = client.post("/process", files={"file": (name, data, media_type)}, data=form)
    assert response.status_code == 200, response.text
    return response.json()


# ── reading ──────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("keep_blank", [False, True], ids=["blank-lines-dropped", "blank-lines-kept"])
def test_a_text_with_form_feeds_is_paged_and_numbered_the_same_on_both_sides(workdir, ingest, echo, keep_blank):
    ingest(keep_blank)
    (workdir / "in").mkdir()
    (workdir / "in" / "DOC1.txt").write_text(TXT, encoding="utf-8")
    batch = _text_lines_batch(workdir)

    body = _process("DOC1.txt", TXT.encode("utf-8"))
    atrium_openapi.validate_response(_SPEC, "/process", "post", 200, body)
    service = _service_lines(body)

    # The same pages, lines and line numbers, the split words joined the same way ...
    assert service == batch
    assert service[:2] == [("1", 1, "Zpráva o archeologickém výzkumu"), ("1", 2, "na lokalitě Hradiště.")]
    assert [p["page"] for p in body["pages"]] == ["1", "2"]
    # ... and a blank line on both sides or on neither: an `Empty` entry that counts in `line_num`.
    blank = [(page, n) for page, n, text in service if not text]
    assert blank == ([("1", 3), ("2", 1)] if keep_blank else [])
    assert {e["category"] for e in body["cleaned_lines"] if not e["text"]} <= {"Empty"}


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
    assert _service_lines(body) == batch
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
    # (Read upwards, every line starts a new paragraph: the blank lines between are left out here.)
    assert [e["text"] for e in default["cleaned_lines"] if e["text"]] == ["Third line", "Second line", "First line"]
    assert [e["text"] for e in alto["cleaned_lines"]] == ["First line", "Second line", "Third line"]
    assert "layoutlmv3" in json.dumps(default["paradata"])
    assert "layoutlmv3" not in json.dumps(alto["paradata"])


#: Two paragraphs: the third line starts well below the second, so the line joiner puts a blank line
#: before it.
ALTO_PARAGRAPHS = ALTO.replace(b'VPOS="70"', b'VPOS="200"')


def _batch_function(path: Path, name: str, **namespace):
    """A top-level function of a batch script, run without importing the script (the LayoutReader
    extraction imports torch and LayoutReader at load time; these functions need neither)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    (node,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


@pytest.fixture
def keeping_layout_reader(monkeypatch):
    """A LayoutReader that keeps the ALTO's order."""
    inference = sys.modules[type(text_api.text_manager).__module__]
    monkeypatch.setattr(inference, "boxes2inputs", object())
    monkeypatch.setattr(text_api.text_manager, "layout_model", object())
    monkeypatch.setattr(inference, "_run_layout_reader", lambda lines, boxes, *_args, **_kw: (lines, boxes))


def test_an_alto_paragraph_break_is_an_empty_line_on_both_sides(tmp_path, echo, keeping_layout_reader):
    """The page text extract_LytRdr_ALTO_2_TXT.py writes for classify_TEXT.py, and the service's lines
    for the same ALTO in the same order: the blank line of the paragraph break is in both, numbered."""
    np = pytest.importorskip("numpy")
    script = ROOT / "extract_LytRdr_ALTO_2_TXT.py"
    parse = _batch_function(script, "parse_alto_xml", ET=ET)
    normalize = _batch_function(script, "normalize_boxes")
    join = _batch_function(script, "post_process_text", np=np)
    alto = tmp_path / "DOC4-1.alto.xml"
    alto.write_bytes(ALTO_PARAGRAPHS)
    lines, boxes, (width, height) = parse(str(alto))
    page_dir = tmp_path / "txt"
    page_dir.mkdir()
    (page_dir / "DOC4-1.txt").write_text(join(lines, normalize(boxes, width, height)), encoding="utf-8")
    batch = _batch_lines(page_dir)

    body = _process("DOC4-1.alto.xml", ALTO_PARAGRAPHS, "application/xml")
    assert batch == [("1", 1, "First line"), ("1", 2, "Second line"), ("1", 3, ""), ("1", 4, "Third line")]
    assert _service_lines(body) == batch
    assert body["cleaned_lines"][2]["category"] == "Empty"


#: Two text blocks, the first with a word hyphenated across its two lines.
ALTO_BLOCKS = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout><Page ID="p1" WIDTH="1000" HEIGHT="1000"><PrintSpace>
    <TextBlock ID="b1">
      <TextLine ID="l1" HPOS="10" VPOS="10" WIDTH="500" HEIGHT="20"><String CONTENT="Zpráva" HPOS="10" VPOS="10" WIDTH="100" HEIGHT="20"/><SP/><String CONTENT="o" HPOS="120" VPOS="10" WIDTH="10" HEIGHT="20"/><SP/><String CONTENT="vý" SUBS_TYPE="HypPart1" SUBS_CONTENT="výzkumu" HPOS="140" VPOS="10" WIDTH="30" HEIGHT="20"/><HYP CONTENT="-"/></TextLine>
      <TextLine ID="l2" HPOS="10" VPOS="40" WIDTH="500" HEIGHT="20"><String CONTENT="zkumu" SUBS_TYPE="HypPart2" SUBS_CONTENT="výzkumu" HPOS="10" VPOS="40" WIDTH="100" HEIGHT="20"/><SP/><String CONTENT="na" HPOS="120" VPOS="40" WIDTH="30" HEIGHT="20"/><SP/><String CONTENT="lokalitě" HPOS="160" VPOS="40" WIDTH="100" HEIGHT="20"/></TextLine>
    </TextBlock>
    <TextBlock ID="b2">
      <TextLine ID="l3" HPOS="10" VPOS="70" WIDTH="500" HEIGHT="20"><String CONTENT="Druhý" HPOS="10" VPOS="70" WIDTH="100" HEIGHT="20"/><SP/><String CONTENT="blok" HPOS="120" VPOS="70" WIDTH="100" HEIGHT="20"/></TextLine>
    </TextBlock>
  </PrintSpace></Page></Layout>
</alto>
""".encode("utf-8")


def test_reading_order_alto_gives_the_lines_of_the_alto_tools_method(tmp_path, echo):
    """extract_ALTO_2_TXT.py's page text, read as classify_TEXT.py reads it, and the service's lines with
    `reading_order=alto`: a blank line before the second block, the hyphenated line joined to the next."""
    alto = tmp_path / "DOC5-1.alto.xml"
    alto.write_bytes(ALTO_BLOCKS)
    assert extract_ALTO_2_TXT.extract_single_page(("DOC5", "1", str(alto), str(tmp_path / "txt")))
    batch = _batch_lines(tmp_path / "txt" / "DOC5")

    body = _process("DOC5-1.alto.xml", ALTO_BLOCKS, "application/xml", reading_order="alto")
    assert batch == [("1", 1, "Zpráva o výzkumu na lokalitě"), ("1", 2, ""), ("1", 3, "Druhý blok")]
    assert _service_lines(body) == batch


@pytest.mark.parametrize("name", ["idnext.alto.xml", "readingorder.alto.xml"])
def test_the_service_runs_alto_tools_over_its_hardened_parse_with_the_same_text(name):
    """alto-tools' own parse (the batch) and the service's hardened one give alto-tools the same tree:
    the `<ReadingOrder>` and `@IDNEXT` orders of tests/fixtures/alto included."""
    inference = sys.modules[type(text_api.text_manager).__module__]
    path = ROOT / "tests" / "fixtures" / "alto" / name
    assert inference.alto_tools_text(str(path)) == join_hyphenated_lines(alto_tools.text_from_file(path))


def test_an_alto_namespace_alto_tools_does_not_read_is_refused_with_reading_order_alto(tmp_path):
    inference = sys.modules[type(text_api.text_manager).__module__]
    path = tmp_path / "p.alto.xml"
    path.write_bytes(ALTO.replace(b"http://www.loc.gov/standards/alto/ns-v4#", b"urn:example:alto"))
    with pytest.raises(alto_tools.AltoToolsError):  # the batch skips the page
        alto_tools.text_from_file(path)
    with pytest.raises(AltoUnreadable, match="namespace"):
        inference.alto_tools_text(str(path))


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


# ── the page score ───────────────────────────────────────────────────────────────────────────


def test_a_page_quality_score_is_the_mean_of_its_clear_and_noisy_lines_on_both_sides():
    """A page's `quality_score`: the service and score_record (record_scoring.py) and the batch
    aggregation (aggregate_STAT.py) all average the page's Clear and Noisy lines; Trash counts in
    the band only, Empty and Non-text in neither (atrium-project#72)."""
    pd = pytest.importorskip("pandas")
    import aggregate_STAT

    rows = [
        {"page": "1", "line": 1, "categ": "Clear", "quality_score": 0.9},
        {"page": "1", "line": 2, "categ": "Noisy", "quality_score": 0.5},
        {"page": "1", "line": 3, "categ": "Trash", "quality_score": 0.1},
        {"page": "1", "line": 4, "categ": "Empty", "quality_score": 0.0},
    ]
    (service,) = page_records_from_lines(rows)
    assert service["quality_score"] == pytest.approx(0.7)

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
    assert stats["avg_quality_score"].iloc[0] == pytest.approx(service["quality_score"])
    assert service["quality_band"] == aggregate_STAT._page_records_from_stats(stats)[0]["quality_band"]


def test_a_page_of_trash_alone_has_a_band_and_no_score_on_both_sides():
    pd = pytest.importorskip("pandas")
    import aggregate_STAT

    (service,) = page_records_from_lines([{"page": "1", "line": 1, "categ": "Trash", "quality_score": 0.1}])
    frame = pd.DataFrame(
        [{"file": "DOC", "page_num": 1, "categ": "Trash", "quality_score": 0.1, "word_count": 1, "char_count": 1}]
    )
    stats = aggregate_STAT._sum_metrics(frame, ["Clear", "Noisy", "Trash", "Non-text", "Empty"])
    (batch,) = aggregate_STAT._page_records_from_stats(stats)
    assert service == batch == {"page": "1", "quality_band": "Trash"}


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([os.path.abspath(__file__), "-q"]))


#: A page without its size, and a word without its coordinates.
ALTO_NO_COORDS = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout><Page ID="p1"><PrintSpace><TextBlock ID="b1">
    <TextLine ID="l1"><String CONTENT="Bez"/><SP/><String CONTENT="souřadnic" HPOS="10" VPOS="10" WIDTH="90" HEIGHT="20"/></TextLine>
  </TextBlock></PrintSpace></Page></Layout>
</alto>
""".encode("utf-8")


def test_an_alto_without_coordinates_keeps_its_text_on_both_sides(tmp_path):
    """v1.15.0-beta: the batch's LayoutReader parse dropped the page without WIDTH/HEIGHT, and a String
    without coordinates; the service kept both (a missing value counts as 0). Now both keep them."""
    from service.utils import parse_alto_xml_lines

    alto = tmp_path / "DOC7-1.alto.xml"
    alto.write_bytes(ALTO_NO_COORDS)
    batch = _batch_function(ROOT / "extract_LytRdr_ALTO_2_TXT.py", "parse_alto_xml", ET=ET)(str(alto))
    assert batch == parse_alto_xml_lines(str(alto))
    assert batch[0] == ["Bez souřadnic"]


def test_a_layoutreader_out_of_memory_keeps_the_alto_order_on_both_sides(tmp_path):
    """v1.15.0-beta: when even the smallest LayoutReader window runs out of memory, the batch keeps the
    page in the ALTO's order, as the service does (`_run_layout_reader`); it skipped the page."""
    np = pytest.importorskip("numpy")
    import contextlib
    import types

    from service.utils import normalize_boxes, post_process_text

    class OutOfMemory(Exception):
        pass

    def model(**_):
        raise OutOfMemory("CUDA out of memory")

    script = ROOT / "extract_LytRdr_ALTO_2_TXT.py"
    namespace = {
        "torch": types.SimpleNamespace(
            cuda=types.SimpleNamespace(OutOfMemoryError=OutOfMemory, empty_cache=lambda: None),
            Tensor=type(None),
            no_grad=contextlib.nullcontext,
        ),
        "model": model,
        "device": "cpu",
        "boxes2inputs": lambda boxes: {},
        "prepare_inputs": lambda inputs, model: inputs,
        "parse_logits": None,
        "parse_alto_xml": _batch_function(script, "parse_alto_xml", ET=ET),
        "normalize_boxes": _batch_function(script, "normalize_boxes"),
        "post_process_text": _batch_function(script, "post_process_text", np=np),
        "document_hook": __import__("document_hook"),
        "LR_CHUNK_SIZE": 4,
        "LR_MIN_CHUNK_SIZE": 2,
        "Path": Path,
    }
    extract = _batch_function(script, "extract_single_page", **namespace)
    alto = tmp_path / "DOC8-1.alto.xml"
    alto.write_bytes(ALTO_BLOCKS)
    assert extract(("DOC8", "1", str(alto), str(tmp_path / "out"))) is True
    written = (tmp_path / "out" / "DOC8" / "DOC8-1.txt").read_text(encoding="utf-8")
    lines, boxes, (width, height) = namespace["parse_alto_xml"](str(alto))
    assert written == post_process_text(lines, normalize_boxes(boxes, width, height))


def test_a_pages_quality_score_has_four_decimals_on_both_sides():
    """v1.15.0-beta: the batch record carried aggregate_STAT's mean unrounded; record_scoring (/process,
    /score_record) rounds it to 4 decimals. Both now give 0.1673 for these lines."""
    pd = pytest.importorskip("pandas")
    import aggregate_STAT
    import record_scoring

    lines = [
        {"page": "1", "line": 1, "categ": "Clear", "quality_score": 0.1},
        {"page": "1", "line": 2, "categ": "Noisy", "quality_score": 0.2345678},
        {"page": "1", "line": 3, "categ": "Trash", "quality_score": 0.9},
    ]
    stats = pd.DataFrame([{"page_num": 1, "Clear": 1, "Noisy": 1, "Trash": 1, "avg_quality_score": 0.1672839}])
    (batch,) = aggregate_STAT._page_records_from_stats(stats)
    (service,) = record_scoring.page_records_from_lines(lines)
    assert batch["quality_score"] == service["quality_score"] == 0.1673
    assert batch["quality_band"] == service["quality_band"]


def test_the_service_reads_the_configs_perplexity_model(tmp_path, monkeypatch):
    """v1.15.0-beta: `[CLASSIFY] MODEL_NAME`, what classify_TEXT.py reads, now reaches the service too;
    GPT2_MODEL_NAME still wins."""
    inference = sys.modules[type(text_api.text_manager).__module__]
    config = tmp_path / "config.txt"
    config.write_text("[CLASSIFY]\nMODEL_NAME = distilgpt2\n", encoding="utf-8")
    monkeypatch.setenv("LANGID_CONFIG", str(config))
    monkeypatch.delenv("GPT2_MODEL_NAME", raising=False)
    assert inference.ppl_model_name() == "distilgpt2"
    monkeypatch.setenv("GPT2_MODEL_NAME", "Qwen/Qwen2.5-0.5B")
    assert inference.ppl_model_name() == "Qwen/Qwen2.5-0.5B"
