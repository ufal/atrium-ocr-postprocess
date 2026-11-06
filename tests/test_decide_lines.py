"""tests/test_decide_lines.py — the ambiguity selector for decision models (#10; atrium-project#83).

The tagging rules are tested as a pure function; the driver is tested end to end on a small CSV,
re-scored by the production scorer, with a gold sidecar, in one process and in two.
"""

import csv
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import classify_TEXT  # noqa: E402
import text_util  # noqa: E402
from tools import decide_lines as dl  # noqa: E402

SAMPLES = _ROOT / "data_samples" / "DOC_LINE_CATEG"


def _stored(**kw):
    row = {"categ": "Clear", "text": "slovo", "original_lang": "ces_Latn", "orig_lang_score": "0.99"}
    row.update(kw)
    return row


def _rescored(**kw):
    row = {"categ": "Clear", "text": "slovo", "word_count": 1, "valid_word_ratio": "1.0000"}
    row.update(kw)
    return row


# ── tag_line ──────────────────────────────────────────────────────────────────────────────────


def test_s1_short_lines_without_czech_diacritics_read_clear_or_noisy():
    assert "S1" in dl.tag_line(_stored(categ="Noisy"), _rescored(text="olie mx"), [], 0.9)
    assert "S1" not in dl.tag_line(_stored(), _rescored(text="říše"), [], 0.9)  # a Czech diacritic
    assert "S1" not in dl.tag_line(_stored(categ="Trash"), _rescored(text="olie"), [], 0.9)
    four = _rescored(text="a b c d", word_count=4)
    assert "S1" not in dl.tag_line(_stored(), four, [], 0.9)


def test_s2_a_late_gate_fired_without_a_conviction():
    for rule in dl.LATE_GATE_RULES:
        assert "S2" in dl.tag_line(_stored(), _rescored(categ="Noisy"), [rule], 0.9)
        assert "S2" not in dl.tag_line(_stored(), _rescored(categ="Trash"), [rule], 0.9)
    assert "S2" not in dl.tag_line(_stored(), _rescored(), ["rule_short_line"], 0.9)


def test_s3_near_either_band_edge():
    edge = text_util.CATEG_TRASH_SCORE_MAX
    assert "S3" in dl.tag_line(_stored(), _rescored(), [], edge + 0.04)
    assert "S3" not in dl.tag_line(_stored(), _rescored(), [], edge + 0.06)
    assert "S3" not in dl.tag_line(_stored(), _rescored(), [], None)
    valid = text_util.MOSTLY_READABLE_VALID_MIN
    assert "S3" in dl.tag_line(_stored(), _rescored(valid_word_ratio=str(valid - 0.03)), [], 0.95)
    assert "S3" in dl.tag_line(_stored(), _rescored(), [], edge + 0.06, margin=0.1)


def test_s4_a_confident_foreign_line_read_noisy():
    english = _stored(categ="Noisy", original_lang="eng_Latn", orig_lang_score="0.95")
    assert "S4" in dl.tag_line(english, _rescored(text="a clean English sentence", word_count=4), [], 0.9)
    slovak = _stored(categ="Noisy", original_lang="slk_Latn", orig_lang_score="0.95")
    assert "S4" not in dl.tag_line(slovak, _rescored(word_count=4), [], 0.9)
    unsure = _stored(categ="Noisy", original_lang="eng_Latn", orig_lang_score="0.70")
    assert "S4" not in dl.tag_line(unsure, _rescored(word_count=4), [], 0.9)
    assert "S4" in dl.tag_line(unsure, _rescored(word_count=4), [], 0.9, foreign_min=0.6)


@pytest.mark.parametrize("column", dl.PP_COLUMNS)
@pytest.mark.parametrize("value, tagged", [("True", True), ("1", True), ("False", False), ("", False), (None, False)])
def test_s5_a_document_pass_changed_the_line(column, value, tagged):
    assert ("S5" in dl.tag_line(_stored(**{column: value}), _rescored(), [], 0.9)) is tagged


def test_tags_come_in_a_fixed_order():
    stored = _stored(categ="Noisy", original_lang="eng_Latn", orig_lang_score="0.95", pp_dedup="True")
    rescored = _rescored(categ="Noisy", text="wqx mmm", word_count=2)
    assert dl.tag_line(stored, rescored, ["rule_wqx_rot"], 0.56) == ["S1", "S2", "S3", "S4", "S5"]


# ── the raw score ─────────────────────────────────────────────────────────────────────────────


def test_capture_raw_quality_records_and_restores():
    original = classify_TEXT.categorize_line
    with dl.capture_raw_quality() as seen:
        assert classify_TEXT.categorize_line is not original
        categ, score, reason = classify_TEXT.categorize_line(0.60, "slovo", 1, 0.4, 900.0, return_reason=True)
        assert seen["qs"] == pytest.approx(0.60) and seen["reason"] == reason
    assert classify_TEXT.categorize_line is original


# ── the driver ────────────────────────────────────────────────────────────────────────────────

ROWS = [
    # (line, stored categ, text, raw label, FastText score, perplexity, extra columns)
    (1, "Clear", "olie", "ces_Latn", "0.9", "900", {}),
    (2, "Noisy", "The excavation was documented in detail by the team.", "eng_Latn", "0.95", "400", {}),
    (3, "Clear", "Mocnost kulturní vrstvy činila 40 cm.", "ces_Latn", "0.99", "90", {"pp_dedup": "True"}),
    (4, "Clear", "Sonda byla zaměřena geodeticky přesně.", "ces_Latn", "0.96", "95", {}),
    (5, "Empty", "", "", "0", "0", {"word_count": "0"}),
]
COLUMNS = [
    "categ",
    "quality_score",
    "file",
    "page_num",
    "line_num",
    "text",
    "original_text",
    "original_lang",
    "orig_lang_score",
    "perplex",
    "word_count",
    "pp_dedup",
]


def _write_doc(folder: Path, name: str = "CTXTEST01") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for line, categ, text, lang, score, ppl, extra in ROWS:
            row = {
                "categ": categ,
                "quality_score": "0.9",
                "file": name,
                "page_num": "1",
                "line_num": str(line),
                "text": text,
                "original_text": text,
                "original_lang": lang,
                "orig_lang_score": score,
                "perplex": ppl,
                "word_count": str(len(text.split())),
                "pp_dedup": "False",
            }
            row.update(extra)
            writer.writerow(row)
    return path


def _gold(path: Path, name: str = "CTXTEST01") -> Path:
    path.write_text(
        "file,page_num,line_num,gold_categ,gold_source\n"
        f"{name},1,1,Trash,test\n{name},1,2,Clear,test\n{name},1,4,Clear,test\n",
        encoding="utf-8",
    )
    return path


def test_run_tags_writes_and_counts(tmp_path):
    _write_doc(tmp_path / "in")
    out = tmp_path / "out"
    report = dl.run(dl.input_files(tmp_path / "in"), out, _ROOT / "setup" / "config.txt")
    assert report["lines"] == 5 and report["fast_track"] == 1 and report["scored"] == 4
    assert report["per_tag"]["S1"] >= 1 and report["per_tag"]["S4"] == 1 and report["per_tag"]["S5"] == 1
    with (out / "DOC_LINE_DECIDE_SELECT.csv").open(newline="", encoding="utf-8") as handle:
        selected = {row["line_num"]: row for row in csv.DictReader(handle)}
    assert "S1" in selected["1"]["tags"] and "S4" in selected["2"]["tags"] and selected["3"]["tags"] == "S5"
    assert "4" not in selected  # clean Czech prose is not asked about
    assert selected["1"]["raw_quality_score"] and selected["1"]["reason"]
    assert json.loads((out / "decide_lines_counts.json").read_text(encoding="utf-8"))["scored"] == 4


def test_gold_restricts_to_gold_lines_and_reports_the_rules_error(tmp_path):
    _write_doc(tmp_path / "in")
    _write_doc(tmp_path / "in", name="CTXNOGOLD")
    gold = _gold(tmp_path / "gold.csv")
    report = dl.run(dl.input_files(tmp_path / "in"), tmp_path / "out", _ROOT / "setup" / "config.txt", gold)
    assert report["documents"] == 1 and report["lines"] == 3
    assert report["gold"]["lines"] == 3
    assert report["gold"]["S1"]["rule_wrong"] == 1  # `olie` is stored Clear, gold Trash
    assert report["gold"]["S4"]["stored→gold"] == {"Noisy→Clear": 1}
    assert report["gold"]["untagged"] == {"lines": 1, "rule_wrong": 0, "rule_error": 0.0}


def test_two_workers_count_what_one_counts(tmp_path):
    for name in ("CTXA01", "CTXA02", "CTXA03"):
        _write_doc(tmp_path / "in", name)
    config = _ROOT / "setup" / "config.txt"
    one = dl.run(dl.input_files(tmp_path / "in"), tmp_path / "o1", config, workers=1)
    two = dl.run(dl.input_files(tmp_path / "in"), tmp_path / "o2", config, workers=2)
    assert {k: v for k, v in one.items() if k != "inputs"} == {k: v for k, v in two.items() if k != "inputs"}


def test_the_shipped_samples_run_and_add_up(tmp_path):
    report = dl.run(dl.input_files(SAMPLES), tmp_path / "out", _ROOT / "setup" / "config.txt")
    assert report["documents"] == 3
    assert report["scored"] + report["fast_track"] == report["lines"]
    assert report["tagged"] <= report["scored"]


def test_main_needs_input(tmp_path):
    assert dl.main(["--input", str(tmp_path), "--out-dir", str(tmp_path / "o")]) == 2
