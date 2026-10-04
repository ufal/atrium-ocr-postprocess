"""
tests/test_ab_constant_eval_clear_demoted.py
============================================
(#3 stages 13-14) What the A/B tool reports about gold `Clear` lines, and what its
discordant dump can see.

Stage 13 exposed a blind spot: ``Clear-loss`` counts gold `Clear` sent to `Trash` or
`Non-text`, and the D47 floor answers `Noisy`, so the gate "Clear-loss does not rise" was
satisfied before any data arrived. Stage 14d showed the same blind spot from the other
side: a gold-`Clear` line that was wrong in BOTH arms moved `Noisy` -> `Trash`, raised
Clear-loss by one, and was in no discordant row because correctness did not change.

These tests pin the three repairs:

  * ``Clear-demoted`` (any class) is reported next to ``Clear-loss`` and gates nothing,
  * a boolean flag's live value is printed instead of ``None``,
  * the dump names the gold-`Clear` lines newly sent to Trash/Non-text without a
    correctness change, and carries each arm's own label.
"""

import csv
import io
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ab_constant_eval as ab  # noqa: E402
from recategorize_from_csv import evaluate_dataframe, load_csvs  # noqa: E402

SAMPLE = _ROOT / "data_samples" / "DOC_LINE_CATEG" / "CTX000000001.csv"


def _confusion(clear_row):
    labels = ["Clear", "Noisy", "Trash", "Non-text", "Empty"]
    table = {r: {c: 0 for c in labels} for r in labels}
    table["Clear"].update(clear_row)
    return {"confusion": table}


def test_clear_demoted_counts_every_class_but_clear():
    metrics = _confusion({"Clear": 90, "Noisy": 7, "Trash": 2, "Non-text": 1, "Empty": 3})
    assert ab._clear_demoted(metrics) == 13
    assert ab._clear_loss(metrics) == 3, "the gate's definition is unchanged"


def test_clear_demoted_is_zero_without_a_clear_row():
    assert ab._clear_demoted({}) == 0
    assert ab._clear_demoted({"confusion": {}}) == 0


def _arm(value, *, demoted, loss=0, errors=10):
    return {
        "value": value,
        "macro_f1": 0.5,
        "errors": errors,
        "costed_score": 0.3,
        "clear_loss": loss,
        "clear_demoted": demoted,
        "clear_support": 100,
        "baseline_vs_gold_macro_f1": 0.5,
        "correct_mask": None,
    }


def test_verdict_reports_demoted_count_without_gating(capsys):
    rows = [_arm(False, demoted=0), _arm(True, demoted=4)]
    ab._print_gold_verdict(rows, "gold_categ")
    out = capsys.readouterr().out
    assert "Clear-demoted=0/100" in out
    assert "Clear-demoted=4/100" in out
    assert "REGRESSION" not in out and "REJECT" not in out, "Clear-demoted is reported, not gated"
    assert "Clear-loss counts gold Clear sent to Trash/Non-text only" in out


def test_floor_demotes_a_gold_clear_line_while_clear_loss_stays_zero(tmp_path):
    """The stage-13 blind spot, end to end: the floor takes a gold-Clear line to Noisy."""
    corpus = tmp_path / "c"
    corpus.mkdir()
    rows = list(csv.DictReader(io.StringIO("\n".join(SAMPLE.read_text(encoding="utf-8").splitlines()[:2]))))
    fine = dict(rows[0], gold_categ="Clear")
    # A valid sample row with only the text swapped for the witness's canonical garbage
    # example: the cascade still says `Clear` (the floor-off arm), the floor says `Noisy`.
    garbled = dict(
        fine, text="oueussd", original_text="oueussd", word_count="1", char_count="7", page_num="9", line_num="1"
    )
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(fine))
    writer.writeheader()
    writer.writerows([fine, garbled])
    (corpus / "CTX000000001.csv").write_text(buf.getvalue(), encoding="utf-8")
    df = load_csvs(corpus)

    off = evaluate_dataframe(df, {"SHORT_GARBAGE_WITNESS_FLOOR": False}, gold_category_column="gold_categ")
    on = evaluate_dataframe(df, {"SHORT_GARBAGE_WITNESS_FLOOR": True}, gold_category_column="gold_categ")
    assert ab._clear_loss(off) == ab._clear_loss(on) == 0
    assert ab._clear_demoted(on) - ab._clear_demoted(off) == 1


def test_live_flag_value_reads_a_boolean_and_refuses_a_number():
    assert ab._live_flag_value("SHORT_GARBAGE_WITNESS_FLOOR") in (True, False)
    assert ab._live_flag_value("CATEG_TRASH_SCORE_MAX") is None, "numbers come from the config reader"
    assert ab._live_flag_value("NO_SUCH_CONSTANT") is None


def test_run_ab_header_prints_the_flag_value_not_none(capsys, monkeypatch):
    import pandas as pd

    class _Frame(pd.DataFrame):
        pass

    metrics = {
        "macro_f1": 0.5,
        "weighted_f1": 0.5,
        "costed_score": 0.3,
        "flip_rate": 0.0,
        "trash_rate": 0.0,
        "clear_rate": 0.0,
        "kl_divergence": 0.0,
        "confusion": _confusion({"Clear": 1})["confusion"],
        "correct_mask": np.array([True]),
    }
    monkeypatch.setattr(ab, "evaluate_dataframe", lambda *a, **k: dict(metrics))
    ab.run_ab(_Frame({"text": ["x"]}), "SHORT_GARBAGE_WITNESS_FLOOR", [False, True], {}, {})
    out = capsys.readouterr().out
    assert "(current config value: None)" not in out
    assert "(current config value: False)" in out


def test_predicted_labels_are_opt_in_and_gold_scoped(tmp_path):
    corpus = tmp_path / "c"
    corpus.mkdir()
    (corpus / "CTX000000014.csv").write_text(
        "categ,file,page_num,line_num,text,word_count,gold_categ\n"
        "Clear,CTX000000014,1,1,ordinary text here,3,Clear\n"
        "Clear,CTX000000014,1,2,rragment,1,Trash\n",
        encoding="utf-8",
    )
    df = load_csvs(corpus)
    plain = evaluate_dataframe(df, {}, gold_category_column="gold_categ")
    assert "predicted_labels" not in plain, "save_json serialises this dict; no arrays by default"
    with_labels = evaluate_dataframe(df, {}, gold_category_column="gold_categ", return_correctness=True)
    assert len(with_labels["predicted_labels"]) == 2, "one label per ANNOTATED row"


def test_dump_names_a_wrong_in_both_arms_clear_loss_line(tmp_path):
    """Stage 14d's shape: one gold-Noisy break in the CSV, and a gold-Clear line that is
    wrong either way (Noisy -> Trash) that no correctness mask can show."""
    corpus = tmp_path / "c"
    corpus.mkdir()
    (corpus / "CTX000000020.csv").write_text(
        "categ,file,page_num,line_num,text,word_count,gold_categ\n"
        "Noisy,CTX000000020,1,1,clearly fine line,3,Clear\n"
        "Clear,CTX000000020,1,2,another fine line,3,Clear\n"
        "Noisy,CTX000000020,1,3,* AY010,2,Noisy\n",
        encoding="utf-8",
    )
    df = load_csvs(corpus)
    ref = {
        "value": 0.55,
        "correct_mask": np.array([False, True, True]),
        "predicted": np.array(["Noisy", "Clear", "Noisy"], dtype=object),
    }
    arm = {
        "value": 0.62,
        "correct_mask": np.array([False, True, False]),
        "predicted": np.array(["Trash", "Clear", "Trash"], dtype=object),
    }
    out = tmp_path / "dump.csv"
    ab._dump_discordant(out, df, [ref, arm], "gold_categ")

    with out.open(newline="", encoding="utf-8") as handle:
        got = list(csv.DictReader(handle))
    by_dir = {r["direction"]: r for r in got}
    assert sorted(by_dir) == ["break", "clear_loss"], got
    assert by_dir["break"]["text"] == "* AY010"
    assert by_dir["break"]["ref_pred"] == "Noisy" and by_dir["break"]["arm_pred"] == "Trash"
    assert by_dir["clear_loss"]["text"] == "clearly fine line"
    assert by_dir["clear_loss"]["gold_categ"] == "Clear"
    assert by_dir["clear_loss"]["ref_pred"] == "Noisy" and by_dir["clear_loss"]["arm_pred"] == "Trash"


def test_dump_does_not_double_list_a_break_that_is_also_a_clear_loss(tmp_path):
    corpus = tmp_path / "c"
    corpus.mkdir()
    (corpus / "CTX000000021.csv").write_text(
        "categ,file,page_num,line_num,text,word_count,gold_categ\nClear,CTX000000021,1,1,fine line,2,Clear\n",
        encoding="utf-8",
    )
    df = load_csvs(corpus)
    ref = {"value": 0, "correct_mask": np.array([True]), "predicted": np.array(["Clear"], dtype=object)}
    arm = {"value": 1, "correct_mask": np.array([False]), "predicted": np.array(["Trash"], dtype=object)}
    out = tmp_path / "dump.csv"
    ab._dump_discordant(out, df, [ref, arm], "gold_categ")
    with out.open(newline="", encoding="utf-8") as handle:
        got = list(csv.DictReader(handle))
    assert [r["direction"] for r in got] == ["break"], "one row, listed once, as the break"


def test_dump_still_works_without_predicted_labels(tmp_path):
    """Callers that never passed labels (older rows, the quality_model copy) keep the old shape."""
    corpus = tmp_path / "c"
    corpus.mkdir()
    (corpus / "CTX000000022.csv").write_text(
        "categ,file,page_num,line_num,text,word_count,gold_categ\nClear,CTX000000022,1,1,fine line,2,Clear\n",
        encoding="utf-8",
    )
    df = load_csvs(corpus)
    ref = {"value": 0, "correct_mask": np.array([True])}
    arm = {"value": 1, "correct_mask": np.array([False])}
    out = tmp_path / "dump.csv"
    ab._dump_discordant(out, df, [ref, arm], "gold_categ")
    with out.open(newline="", encoding="utf-8") as handle:
        got = list(csv.DictReader(handle))
    assert [r["direction"] for r in got] == ["break"]
    assert got[0]["ref_pred"] == "" and got[0]["arm_pred"] == ""


@pytest.mark.parametrize("labels", [("Trash",), ("Non-text",)])
def test_isin_matches_both_demoting_classes(labels):
    arr = np.array(["Clear", "Trash", "Non-text", "Noisy"], dtype=object)
    assert ab._isin(arr, labels).sum() == 1
