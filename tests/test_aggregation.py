"""
Tests for aggregate_STAT.py pure-logic helpers.
"""

import pandas as pd
import pytest

from aggregate_STAT import _sum_metrics, load_config

DEFAULT_CONFIG = "setup/config.txt"
config = load_config(DEFAULT_CONFIG)
STANDARD_COLS = frozenset(config.get("standard_cols", "Clear,Noisy,Trash,Non-text,Empty").split(","))


def test_sum_metrics_basic_with_new_columns():
    # Mock a dataframe representing a DOC_LINE_CATEG CSV containing new columns
    df = pd.DataFrame(
        {
            "file": ["doc1", "doc1", "doc1"],
            "page_num": [1, 1, 1],
            "categ": ["Clear", "Noisy", "Trash"],
            "word_count": [10, 5, 2],
            "char_count": [50, 20, 5],
            "quality_score": [0.9, 0.6, 0.2],
            "word_weird": [0.0, 0.1, 0.8],
            "lang_score": [0.95, 0.8, 0.4],
            "perplex": [150, 400, 2000],
            "symbol": [0, 1, 5],
            "vowel_ratio": [0.4, 0.3, 0.0],
            "rot_ratio": [0.0, 0.0, 0.1],
            "lang": ["ces", "ces", "deu"],
            "caps_header": [False, False, False],
            # --- New columns added to verify survival/no-crash ---
            "original_text": ["a", "b", "c"],
            "original_lang": ["ces", "ces", "deu"],
            "orig_lang_score": [0.95, 0.8, 0.4],
            "weird_wx": [0, 0, 1],
            "pp_dedup": [False, False, False],
            "pp_surrounded_trash": [False, False, False],
            "pp_inverted_run": [False, False, False],
        }
    )

    res = _sum_metrics(df, STANDARD_COLS)

    assert len(res) == 1
    # Only "Clear" and "Noisy" lines are aggregated for word/char counts and averages
    assert res.iloc[0]["Clear"] == 1
    assert res.iloc[0]["Noisy"] == 1
    assert res.iloc[0]["Trash"] == 1
    assert res.iloc[0]["num_lines"] == 3  # Clear + Noisy + Trash

    # 10 (Clear) + 5 (Noisy) = 15 words. The 2 Trash words are ignored.
    assert res.iloc[0]["total_word_count"] == 15
    assert res.iloc[0]["main_lang"] == "ces"


def test_sum_metrics_empty():
    df = pd.DataFrame()
    res = _sum_metrics(df, STANDARD_COLS)
    assert res.empty


def test_page_records_carry_no_band_for_a_page_without_text_lines():
    """(ocr-postprocess#3, stage 17) A page of only `Empty` / `Non-text` lines gets neither a
    score nor a band, as the service path does: the plurality's tie-break would call it Clear."""
    from aggregate_STAT import _page_records_from_stats

    categ = ["Clear", "Noisy", "Trash", "Trash", "Trash", "Empty", "Non-text"]
    df = pd.DataFrame(
        {
            "file": ["doc1"] * 7,
            "page_num": [1, 1, 1, 2, 2, 3, 3],
            "categ": categ,
            "word_count": [10, 5, 2, 2, 1, 0, 0],
            "char_count": [50, 20, 5, 5, 3, 0, 1],
            "quality_score": [0.9, 0.6, 0.2, 0.3, 0.1, 0.0, 0.0],
            "word_weird": [0.0] * 7,
            "lang_score": [0.9] * 7,
            "perplex": [100.0] * 7,
            "vowel_ratio": [0.4] * 7,
            "rot_ratio": [0.0] * 7,
            "lang": ["ces"] * 7,
        }
    )
    pages = {rec["page"]: rec for rec in _page_records_from_stats(_sum_metrics(df, STANDARD_COLS))}
    assert pages["1"] == {"page": "1", "quality_band": "Clear", "quality_score": pytest.approx(0.75)}
    assert pages["2"] == {"page": "2", "quality_band": "Trash"}  # Trash only: a band, no batch score
    assert pages["3"] == {"page": "3"}  # no text line: neither


def test_load_config_reads_standard_cols(tmp_path):
    """(#7 Phase 0) [AGGREGATE] STANDARD_COLS must round-trip from the config
    file instead of always falling back to the hardcoded default."""
    cfg = tmp_path / "config.txt"
    cfg.write_text("[AGGREGATE]\nSTANDARD_COLS = Clear,Noisy\n", encoding="utf-8")
    loaded = load_config(str(cfg))
    assert loaded["standard_cols"] == "Clear,Noisy"

    # Shipped config carries the full five-category set.
    shipped = load_config(DEFAULT_CONFIG)
    assert frozenset(shipped["standard_cols"].split(",")) == frozenset({"Clear", "Noisy", "Trash", "Non-text", "Empty"})

    # Missing-file fallback must include the key too (main() relies on it).
    missing = load_config(str(tmp_path / "nope.txt"))
    assert "standard_cols" in missing


# ── (#31 Phase 4) document ids stay text, the final order stays the same ─────


def test_natural_file_key_orders_numeric_ids_numerically():
    from aggregate_STAT import _natural_file_key

    assert sorted(["10", "9", "0001", "CTX2", "CTX10"], key=_natural_file_key) == ["0001", "9", "10", "CTX10", "CTX2"]


def test_sort_page_stats_accepts_mixed_ids():
    from aggregate_STAT import sort_page_stats

    df = pd.DataFrame({"file": ["report", "0001", "12", "0001"], "page_num": [1, 2, 1, 1]})
    out = sort_page_stats(df)
    assert list(zip(out["file"], out["page_num"], strict=True)) == [("0001", 1), ("0001", 2), ("12", 1), ("report", 1)]


def test_process_csv_file_keeps_leading_zero_and_na_ids(tmp_path):
    from aggregate_STAT import process_csv_file

    head = (
        "file,page_num,categ,word_count,char_count,quality_score,word_weird,lang_score,perplex,vowel_ratio,"
        "rot_ratio,lang,caps_header"
    )
    row = "1,Clear,1,5,0.9,0,0.9,10,0.5,0,ces,False"  # everything after the file id
    (tmp_path / "0001.csv").write_text(f"{head}\n0001,{row}\n", encoding="utf-8")
    (tmp_path / "NA.csv").write_text(f"{head}\nNA,{row}\n", encoding="utf-8")
    assert list(process_csv_file(tmp_path / "0001.csv", STANDARD_COLS)["file"]) == ["0001"]
    assert list(process_csv_file(tmp_path / "NA.csv", STANDARD_COLS)["file"]) == ["NA"]


def test_the_final_sort_is_byte_identical_to_the_old_one_on_the_samples():
    """Numeric ids read the old way (int) and the new way (str) sort into the same CSV
    text, and so do the committed CTX samples."""
    from pathlib import Path

    from aggregate_STAT import process_csv_file, sort_page_stats

    frames = [process_csv_file(p, STANDARD_COLS) for p in sorted(Path("data_samples/DOC_LINE_CATEG").glob("*.csv"))]
    frames = [f for f in frames if isinstance(f, pd.DataFrame)]
    if not frames:
        pytest.skip("no categorized samples")
    samples = pd.concat(frames, ignore_index=True).sample(frac=1, random_state=7)
    assert sort_page_stats(samples).to_csv(index=False) == samples.sort_values(by=["file", "page_num"]).to_csv(
        index=False
    )
    numeric = pd.DataFrame({"file": [10, 9, 100, 9], "page_num": [1, 2, 1, 1], "v": [1, 2, 3, 4]})
    as_text = numeric.assign(file=numeric["file"].astype(str))
    assert sort_page_stats(as_text).to_csv(index=False) == numeric.sort_values(by=["file", "page_num"]).to_csv(
        index=False
    )


# ── (#8) the category ratios ──────────────────────────────────────────────────


def _lines(rows):
    """A DOC_LINE_CATEG frame from (page, categ) pairs, every numeric column filled."""
    n = len(rows)
    return pd.DataFrame(
        {
            "file": ["doc1"] * n,
            "page_num": [page for page, _ in rows],
            "categ": [categ for _, categ in rows],
            "word_count": [3] * n,
            "char_count": [15] * n,
            "quality_score": [0.9] * n,
            "word_weird": [0.0] * n,
            "lang_score": [0.9] * n,
            "perplex": [100.0] * n,
            "vowel_ratio": [0.4] * n,
            "rot_ratio": [0.0] * n,
            "lang": ["ces"] * n,
            "caps_header": [False] * n,
        }
    )


def _ratios(res, page):
    from aggregate_STAT import EMPTY_RATIO, RATIO_COLUMNS

    row = res[res["page_num"] == page].iloc[0]
    return {col: row[col] for col in [*RATIO_COLUMNS, EMPTY_RATIO]}


def test_ratios_of_a_mixed_page_leave_empty_lines_out_of_the_text_shares():
    categ = ["Clear", "Clear", "Noisy", "Trash", "Non-text", "Empty"]
    res = _sum_metrics(_lines([(1, c) for c in categ]), STANDARD_COLS)
    assert _ratios(res, 1) == {
        "clear_ratio": 0.4,
        "noisy_ratio": 0.2,
        "trash_ratio": 0.2,
        "non-text_ratio": 0.2,
        "empty_ratio": 0.1667,  # 1 of 6, rounded to four places
    }


def test_ratios_of_a_page_without_empty_lines_are_shares_of_all_lines():
    res = _sum_metrics(_lines([(1, "Clear"), (1, "Noisy"), (1, "Noisy")]), STANDARD_COLS)
    ratios = _ratios(res, 1)
    assert (ratios["clear_ratio"], ratios["noisy_ratio"], ratios["empty_ratio"]) == (0.3333, 0.6667, 0.0)
    assert ratios["trash_ratio"] == ratios["non-text_ratio"] == 0.0


def test_an_empty_only_page_has_empty_ratio_one_and_the_other_four_undefined():
    # page 2 is Empty only, next to a page with text (the path with averages) ...
    res = _sum_metrics(_lines([(1, "Clear"), (2, "Empty"), (2, "Empty")]), STANDARD_COLS)
    ratios = _ratios(res, 2)
    assert ratios["empty_ratio"] == 1.0
    assert all(pd.isna(ratios[col]) for col in ("clear_ratio", "noisy_ratio", "trash_ratio", "non-text_ratio"))
    # ... and in a document with no Clear or Noisy line at all (the path without them)
    res = _sum_metrics(_lines([(1, "Empty")]), STANDARD_COLS)
    assert _ratios(res, 1)["empty_ratio"] == 1.0 and pd.isna(_ratios(res, 1)["clear_ratio"])


def test_a_page_with_no_counted_line_has_every_ratio_undefined_never_infinite():
    # a category outside STANDARD_COLS is not counted in num_lines
    res = _sum_metrics(_lines([(1, "Garbage"), (2, "Clear")]), STANDARD_COLS)
    assert res[res["page_num"] == 1].iloc[0]["num_lines"] == 0
    ratios = _ratios(res, 1)
    assert all(pd.isna(value) for value in ratios.values())
    assert not res.select_dtypes("number").isin([float("inf"), float("-inf")]).any().any()


def test_both_paths_write_the_same_columns_with_the_ratios_after_the_counts():
    from aggregate_STAT import PAGE_STATS_COLUMNS

    with_text = _sum_metrics(_lines([(1, "Clear"), (1, "Trash")]), STANDARD_COLS)
    without_text = _sum_metrics(_lines([(1, "Trash"), (1, "Empty")]), STANDARD_COLS)
    assert list(with_text.columns) == list(without_text.columns) == PAGE_STATS_COLUMNS
    assert PAGE_STATS_COLUMNS[8:13] == ["clear_ratio", "noisy_ratio", "trash_ratio", "non-text_ratio", "empty_ratio"]
    assert _ratios(without_text, 1) == {
        "clear_ratio": 0.0,
        "noisy_ratio": 0.0,
        "trash_ratio": 1.0,
        "non-text_ratio": 0.0,
        "empty_ratio": 0.5,
    }


def test_every_ratio_column_is_its_category_in_lower_case_then_ratio():
    """One suffix for every share, as the table's other ratios have it (avg_vowel_ratio, ch_ratio)."""
    from aggregate_STAT import EMPTY_RATIO, RATIO_COLUMNS

    names = {**RATIO_COLUMNS, EMPTY_RATIO: "Empty"}
    assert all(column == f"{category.lower()}_ratio" for column, category in names.items()), names
    assert sorted(names.values()) == sorted(STANDARD_COLS)


def test_ratios_are_fractions_in_zero_one_rounded_to_four_places_and_empty_when_undefined():
    categ = ["Clear", "Noisy", "Noisy", "Trash", "Trash", "Trash", "Empty"]
    res = _sum_metrics(_lines([(1, c) for c in categ] + [(2, "Empty")]), STANDARD_COLS)
    from aggregate_STAT import EMPTY_RATIO, RATIO_COLUMNS

    values = res[[*RATIO_COLUMNS, EMPTY_RATIO]].stack().dropna().tolist()  # NaN dropped
    assert all(0.0 <= v <= 1.0 and round(v, 4) == v for v in values)
    row = res.to_csv(index=False).splitlines()[2]  # page 2: Empty only
    assert ",,,,1.0," in row, row


def test_the_shipped_sample_stats_are_what_the_aggregation_writes():
    """The committed DOC_LINE_STATS files carry the ratios, and every other column is what the
    aggregation computes from the committed DOC_LINE_CATEG files (#8)."""
    from pathlib import Path

    from aggregate_STAT import process_csv_file

    pairs = [("data_samples/DOC_LINE_CATEG", "data_samples/DOC_LINE_STATS")]
    pairs.append(("data_samples/DOC_LINE_CATEG_gpt", "data_samples/DOC_LINE_STATS_gpt"))
    checked = 0
    for categ_dir, stats_dir in pairs:
        for path in sorted(Path(categ_dir).glob("*.csv")):
            committed = Path(stats_dir) / f"stats_{path.stem}.csv"
            assert process_csv_file(path, STANDARD_COLS).to_csv(index=False) == committed.read_text(encoding="utf-8")
            checked += 1
    if not checked:
        pytest.skip("no categorized samples")
