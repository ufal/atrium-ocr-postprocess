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


def test_config_defaults_to_langid_config_like_the_service(tmp_path, monkeypatch):
    """--config used to default to setup/config.txt whatever LANGID_CONFIG said, so a run beside the
    service or the batch stages could score with other constants than theirs."""
    import tool_limits

    monkeypatch.delenv("LANGID_CONFIG", raising=False)
    assert dl.build_parser().parse_args([]).config == _ROOT / "setup" / "config.txt" == tool_limits.config_path()
    other = tmp_path / "other_config.txt"
    monkeypatch.setenv("LANGID_CONFIG", str(other))
    assert dl.build_parser().parse_args([]).config == other == tool_limits.config_path()
    assert dl.build_parser().parse_args(["--config", "given.txt"]).config == Path("given.txt")


def test_main_scores_with_the_langid_config_file(tmp_path, monkeypatch):
    _write_doc(tmp_path / "in")
    config = tmp_path / "config.txt"
    config.write_bytes((_ROOT / "setup" / "config.txt").read_bytes())
    monkeypatch.setenv("LANGID_CONFIG", str(config))
    assert dl.main(["--input", str(tmp_path / "in"), "--out-dir", str(tmp_path / "out")]) == 0
    counts = json.loads((tmp_path / "out" / "decide_lines_counts.json").read_text(encoding="utf-8"))
    assert counts["config"]["path"] == str(config)


# ── the decision run (--decide, #10 Phase B/C) ────────────────────────────────────────────────

import decision_client as dc  # noqa: E402


def _client(fake):
    return dc.DecisionClient(fake.url, "fake-model", timeout_s=20, retries=0)


def _read(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.mark.parametrize("path", sorted(dl.question_sets(), key=str), ids=lambda p: p.name)
def test_committed_question_sets_match_the_generator(path):
    expected = dl.render_question_set(dl.question_sets()[path])
    assert path.read_text(encoding="utf-8") == expected, (
        f"{path.name} drifted from tools/decide_lines.py; run `python3 tools/decide_lines.py --write-question-sets`"
    )


def test_the_quality_set_has_the_factors_the_context_and_a_five_level_score():
    quality = dl.quality_questions()
    assert list(quality) == [*dl.FACTORS, *dl.CONTEXT, "quality"]
    assert all(quality[q]["type"] == "noul" for q in (*dl.FACTORS, *dl.CONTEXT))
    assert quality["quality"]["type"] == "score" and len(quality["quality"]["criteria"]) == 5
    assert len(dl.line_questions(True)) == len(quality) + 1 <= dc.MAX_QUESTIONS


def test_the_category_choice_is_the_vocabularys_without_empty():
    import atrium_vocab

    criteria = dl.category_questions()["line_category"]["criteria"]
    assert list(criteria) == ["Clear", "Noisy", "Trash", "Non-text"]
    concepts = atrium_vocab.CONCEPTS["line-category"]
    assert all(criteria[name] == concepts[name]["definition"]["en"] for name in criteria)


def test_every_rule_view_names_real_rules_and_columns():
    source = Path(text_util.__file__).read_text(encoding="utf-8")
    sample = next(SAMPLES.glob("*.csv")).read_text(encoding="utf-8").splitlines()[0].split(",")
    for factor, (rules, columns) in dl.RULE_VIEW.items():
        assert factor in dl.FACTORS
        for rule in rules:
            assert f'_fire("{rule}")' in source, rule
        for column in columns:
            assert column == "_strange_symbols" or column in sample, column
    assert set(dl.FACTORS) - set(dl.RULE_VIEW) == {"f_handwriting"}


def test_rule_view_reads_fired_rules_detector_columns_and_the_language():
    clean = {"text": "pohled na sondu", "original_lang": "ces_Latn", "orig_lang_score": "0.99"}
    view = dl.rule_view(clean, [])
    assert view["f_handwriting"] == "" and set(view.values()) <= {"0", ""}
    assert dl.rule_view(clean, ["rule_bigram_run"])["f_repeated"] == "1"
    assert dl.rule_view({**clean, "fused_words": "2"}, [])["f_split_merged"] == "1"
    assert dl.rule_view({**clean, "damaged": "True"}, [])["f_misread_letters"] == "1"
    assert dl.rule_view({**clean, "text": "nález ¦ § keramiky"}, [])["f_symbols"] == "1"
    assert dl.rule_view({**clean, "original_lang": "deu_Latn", "orig_lang_score": "0.95"}, [])["f_foreign"] == "1"


def test_line_state_sends_the_text_and_neighbours_on_the_same_page_only():
    rows = [
        {"page_num": "1", "text": "a1"},
        {"page_num": "1", "text": ""},
        {"page_num": "1", "text": "a3"},
        {"page_num": "1", "text": "a4", "original_text": "a4 raw", "original_lang": "ces_Latn"},
        {"page_num": "1", "text": "a5"},
        {"page_num": "2", "text": "b1"},
    ]
    state = dl.line_state(rows, 3, {"text": "a4"})
    assert state["line"] == "a4" and state["original_text"] == "a4 raw"
    assert state["before"] == ["a1", "a3"] and state["after"] == ["a5"]
    assert state["detected_language"] == "ces_Latn"
    assert "categ" not in json.dumps(state) and "quality" not in json.dumps(state)


def test_sampling_is_reproducible_and_roughly_the_rate():
    keys = [("CTX", 1, i) for i in range(2000)]
    first = [dl._sampled(k, 0.25, 7) for k in keys]
    assert first == [dl._sampled(k, 0.25, 7) for k in keys]
    assert 0.2 < sum(first) / len(first) < 0.3
    assert all(dl._sampled(k, None, 7) for k in keys[:5])


def test_decide_asks_the_quality_set_and_the_category_only_on_tagged_lines(tmp_path):
    _write_doc(tmp_path / "in")
    gold = _gold(tmp_path / "gold.csv")
    with dc.FakeSystemOne() as fake:
        summary, counts = dl.run_decide(
            dl.input_files(tmp_path / "in"), tmp_path / "out", _ROOT / "setup" / "config.txt", _client(fake), gold
        )
        sent = [set(r["questions"]) for r in fake.requests]
    rows = _read(tmp_path / "out" / "DOC_LINE_DECIDE.csv")
    assert list(rows[0]) == dl.DECIDE_COLUMNS
    assert counts["asked"] == len(rows) == len(sent) == 3  # the three gold lines
    for row, questions in zip(rows, sent, strict=True):
        assert ("line_category" in questions) == bool(row["tags"])
        assert (row["category"] != "") == bool(row["tags"])
        assert 0 <= float(row["quality"]) <= 1 and row["gold_categ"]
        assert float(row["damage_index"]) == pytest.approx(
            sum(float(row[f"P_{f}"]) for f in dl.FACTORS) / len(dl.FACTORS), abs=1e-3
        )
    assert summary["statuses"] == {"ok": 3}
    assert summary["quality_vs_gold"]["lines"] == 3
    assert "S1" in summary["category_per_tag"] and summary["category_per_tag"]["S1"]["gold_lines"] == 1
    assert set(summary["factors"]) == {*dl.FACTORS, *dl.CONTEXT}


def test_decide_tags_only_resume_and_a_failing_server(tmp_path):
    _write_doc(tmp_path / "in")
    config = _ROOT / "setup" / "config.txt"
    inputs = dl.input_files(tmp_path / "in")
    with dc.FakeSystemOne(lambda r: (500, {"error": "down"})) as fake:
        summary, counts = dl.run_decide(inputs, tmp_path / "out", config, _client(fake), tags_only=True)
    rows = _read(tmp_path / "out" / "DOC_LINE_DECIDE.csv")
    assert rows and all(r["tags"] for r in rows) and counts["not_selected"] >= 1
    assert {r["status"] for r in rows} == {"error"} and all(r["quality"] == "" for r in rows)
    with pytest.raises(SystemExit):
        dl.run_decide(inputs, tmp_path / "out", config, _client(fake))
    with dc.FakeSystemOne() as fake:
        _, again = dl.run_decide(inputs, tmp_path / "out", config, _client(fake), tags_only=True, resume=True)
    assert again["asked"] == 0 and again["resumed"] == len(rows)


def test_summary_metrics():
    assert dl.auc([0.9, 0.8, 0.1, 0.2], [True, True, False, False]) == 1.0
    assert dl.auc([0.5, 0.5], [True, False]) == 0.5
    assert dl.auc([0.5], [True]) is None
    assert dl.kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert dl.kappa([True, True], [True, True]) is None
    assert dl.spearman([1, 2, 3], [3, 2, 1]) == -1.0


def test_main_decide_end_to_end_on_the_samples_names_the_server_by_kind(tmp_path):
    out = tmp_path / "out"
    with dc.FakeSystemOne() as fake:
        code = dl.main(["--input", str(SAMPLES), "--out-dir", str(out), "--decide", "--url", fake.url, "--model", "m"])
    assert code == 0
    summary = json.loads((out / "DOC_LINE_DECIDE.summary.json").read_text(encoding="utf-8"))
    assert summary["served_on"] == "local" and fake.url not in json.dumps(summary)
    assert summary["counts"]["asked"] == summary["lines"] > 0
    records = list((out / "paradata").glob("*.json"))
    assert records and "decision_model" in records[0].read_text(encoding="utf-8")


def test_main_decide_refuses_an_unconfigured_server(tmp_path, monkeypatch):
    monkeypatch.delenv("DECISION_MODEL_URL", raising=False)
    with pytest.raises(SystemExit):
        dl.main(["--input", str(SAMPLES), "--out-dir", str(tmp_path / "o"), "--decide", "--url", "", "--model", "m"])


_IN_UNIT = "--accept-at and --review-below are probabilities in [0, 1]"


@pytest.mark.parametrize(
    ("accept_at", "review_below", "message"),
    [
        ("1.5", "0.5", _IN_UNIT),
        ("0.9", "-0.1", _IN_UNIT),
        ("nan", "0.5", _IN_UNIT),
        ("0.6", "0.8", "--review-below must not exceed --accept-at"),
    ],
    ids=["accept-above-1", "review-below-0", "nan", "review-above-accept"],
)
def test_main_decide_refuses_bad_lane_thresholds_before_asking(tmp_path, capsys, accept_at, review_below, message):
    """decision_client.lane() raised on the first answer only, after the model had been called."""
    argv = ["--input", str(SAMPLES), "--out-dir", str(tmp_path / "o"), "--decide", "--model", "m"]
    with dc.FakeSystemOne() as fake:
        with pytest.raises(SystemExit) as exc:
            dl.main([*argv, "--url", fake.url, "--accept-at", accept_at, "--review-below", review_below])
        assert fake.requests == []
    assert exc.value.code == 2
    assert message in capsys.readouterr().err


def test_main_decide_takes_equal_lane_thresholds(tmp_path):
    _write_doc(tmp_path / "in")
    argv = ["--input", str(tmp_path / "in"), "--out-dir", str(tmp_path / "o"), "--decide", "--model", "m"]
    with dc.FakeSystemOne() as fake:
        assert dl.main([*argv, "--url", fake.url, "--accept-at", "0.7", "--review-below", "0.7"]) == 0
    lanes = {r["lane"] for r in _read(tmp_path / "o" / "DOC_LINE_DECIDE.csv") if r["category"]}
    assert lanes and lanes <= {"accept", "review"}


@pytest.mark.parametrize("rate", ["0", "-0.5", "1.5"])
def test_main_refuses_a_sample_rate_outside_the_share(tmp_path, capsys, rate):
    with pytest.raises(SystemExit) as exc:
        dl.main(["--input", str(SAMPLES), "--out-dir", str(tmp_path / "o"), "--decide", "--sample-rate", rate])
    assert exc.value.code == 2
    assert "--sample-rate is a share in (0, 1]" in capsys.readouterr().err


def test_main_sample_rate_asks_the_same_share_of_the_lines_each_time(tmp_path):
    def asked(out, *extra):
        with dc.FakeSystemOne() as fake:
            argv = ["--input", str(SAMPLES), "--out-dir", str(out), "--decide", "--url", fake.url, "--model", "m"]
            assert dl.main([*argv, *extra]) == 0
        return [dl._gold_key(r) for r in _read(out / "DOC_LINE_DECIDE.csv")]

    every = asked(tmp_path / "all")
    share = asked(tmp_path / "a", "--sample-rate", "0.5", "--seed", "7")
    assert share == asked(tmp_path / "b", "--sample-rate", "0.5", "--seed", "7")
    assert share == [key for key in every if dl._sampled(key, 0.5, 7)]
    assert 0 < len(share) < len(every)
    summary = json.loads((tmp_path / "a" / "DOC_LINE_DECIDE.summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["not_selected"] == len(every) - len(share)
