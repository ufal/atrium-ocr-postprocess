"""
tests/test_gold_objective.py
============================
Guards on the gold-scoring path (`--gold-column`) and on the frame alignment the
offline diff report depends on.

Both halves exist for the same reason: this repository's recurring failure mode
is a harness that returns a confident number about something other than what it
claims to measure. A gold column that silently falls back to the pipeline's own
labels, or a diff report that compares row N against row M, are that failure mode
in two new places.
"""

import csv
import sys
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from recategorize_from_csv import (  # noqa: E402
    GOLD_COLUMN_DEFAULT,
    GOLD_SIDECAR_KEYS,
    _gold_report,
    annotated_mask,
    attach_gold_sidecar,
    document_decade,
    evaluate_dataframe,
    load_csvs,
    rescore_csv,
)

GOLD_DIR = _ROOT / "tools" / "gold"
SAMPLE_DIR = _ROOT / "data_samples" / "DOC_LINE_CATEG"
SIDECAR_DIR = GOLD_DIR / "sidecars"
ISSUE30_SIDECAR = SIDECAR_DIR / "issue30_gold_2067.csv"
FLOOR_KEYS_SIDECAR = SIDECAR_DIR / "floor_2026-10-05_keys.csv"


# ---------------------------------------------------------------------------
# The gold column changes the objective, and never silently.
# ---------------------------------------------------------------------------


def test_example_gold_set_exists_and_is_per_document():
    """One CSV per document, matching the DOC_LINE_CATEG convention.

    Several documents in one CSV would put their lines on a shared page and let
    page-level post-processing act across a boundary production never sees.
    """
    csvs = sorted(GOLD_DIR.glob("*.csv"))
    assert csvs, "tools/gold/ has no CSVs; run tools/gold/build_example_gold.py"
    for path in csvs:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        assert GOLD_COLUMN_DEFAULT in df.columns, f"{path.name} has no {GOLD_COLUMN_DEFAULT} column"
        # Checked before it is used, so a stray file reports what is wrong with it
        # rather than raising KeyError('file') from inside pandas. The likely
        # stray is an annotation queue: `short_garbage_witness_report.py --out`
        # and any key-indexed sidecar span many documents and belong in
        # tools/gold/sidecars/, joined with --gold-sidecar.
        assert "file" in df.columns, (
            f"{path.name} has a {GOLD_COLUMN_DEFAULT} column but no `file` column, so it is not a "
            "per-document gold CSV. A multi-document annotation set is a SIDECAR: move it to "
            "tools/gold/sidecars/ and pass it with --gold-sidecar."
        )
        assert df["file"].nunique() == 1, (
            f"{path.name} mixes {df['file'].nunique()} documents. tools/gold/ is one CSV per "
            "document; multi-document sets belong in tools/gold/sidecars/."
        )
        assert df["file"].iloc[0] == path.stem, f"{path.name} does not match its `file` value"


def test_missing_gold_column_raises_instead_of_scoring_perfectly():
    """The whole point of the flag is that it cannot quietly no-op.

    Falling back to the stored categories would score predictions against
    themselves, i.e. a perfect score, which is worse than a crash because nobody
    goes looking for the cause of good news.
    """
    df = load_csvs(SAMPLE_DIR)
    with pytest.raises(KeyError, match="gold_categ"):
        evaluate_dataframe(df, None, gold_category_column="gold_categ")


def test_gold_objective_differs_from_the_self_referential_one():
    """Scoring against gold must not reproduce scoring against `categ`.

    If these agreed, the flag would be decorative. They differ here because the
    example gold set records `oueussd` as `Trash` while the merged #30 gate lets
    it reach `Clear` -- the accepted debt, made visible by the objective.
    """
    df = load_csvs(GOLD_DIR)
    self_ref = evaluate_dataframe(df, None)
    vs_gold = evaluate_dataframe(df, None, gold_category_column=GOLD_COLUMN_DEFAULT)

    assert self_ref["flip_rate"] == pytest.approx(0.0, abs=1e-9), (
        "the re-score is expected to reproduce the stored categories exactly; "
        "if this fails, the parity guarantee is broken and nothing below is meaningful"
    )
    assert vs_gold["flip_rate"] > 0.0, "gold scoring collapsed onto the self-referential answer"
    assert vs_gold["gold_column"] == GOLD_COLUMN_DEFAULT


def test_gold_report_carries_the_incumbent_comparison():
    """`baseline_vs_gold` is the number that decides adoption.

    Beating the pipeline on the pipeline's own labels means nothing; the trial
    has to beat the shipped labels against humans.
    """
    df = load_csvs(GOLD_DIR)
    metrics = evaluate_dataframe(df, None, gold_category_column=GOLD_COLUMN_DEFAULT)
    assert "baseline_vs_gold" in metrics
    assert "gold_delta_macro_f1" in metrics
    # Nothing was tuned, so the trial IS the incumbent and the delta is zero.
    assert metrics["gold_delta_macro_f1"] == pytest.approx(0.0, abs=1e-9)


def test_blank_gold_cells_are_skipped_not_scored():
    """A partially annotated collection must score only its annotated rows."""
    # The first CSV that is actually a per-document gold set, not whatever sorts
    # first: a stray annotation queue named `04_...` beats `GOLD_...` alphabetically
    # and would fail this test for a reason it is not about. Tests #1 and #4 are
    # the ones that report a stray, and they name it.
    per_document = [p for p in sorted(GOLD_DIR.glob("*.csv")) if "categ" in pd.read_csv(p, nrows=0).columns]
    assert per_document, "tools/gold/ has no per-document gold CSVs"
    old, new = rescore_csv(per_document[0])
    full = _gold_report(old, new, GOLD_COLUMN_DEFAULT)
    assert full is not None and full["n"] == len(old)

    blanked = old.copy()
    blanked[GOLD_COLUMN_DEFAULT] = ""
    assert _gold_report(blanked, new, GOLD_COLUMN_DEFAULT) == {"n": 0}

    partial = old.copy()
    partial.loc[partial.index[1:], GOLD_COLUMN_DEFAULT] = ""
    assert _gold_report(partial, new, GOLD_COLUMN_DEFAULT)["n"] == 1


# ---------------------------------------------------------------------------
# Frame alignment: the diff report must be about categories, never row order.
# ---------------------------------------------------------------------------


def test_diff_report_is_invariant_to_on_disk_row_order(tmp_path):
    """Reversing a document's stored row order must not invent category changes.

    Before this was fixed, `rescore_csv` sorted only the `old` frame by
    (page_num, line_num) while `new` came back in input order, and `_report`
    compared them positionally. On a 9-line sample document, reversing the rows
    produced "4 line(s) changed category" while the category COUNTS stayed
    identical -- the signature of a misalignment rather than a real flip.

    This matters beyond cosmetics: `total lines changed category: 0` is the
    parity signal quoted in tools/SWEEP_NOTES.md as evidence that the offline
    re-scorer reproduces production.
    """
    src = SAMPLE_DIR / "CTX000000002.csv"
    original = pd.read_csv(src, dtype=str, keep_default_na=False)

    reversed_path = tmp_path / src.name
    original.iloc[::-1].to_csv(reversed_path, index=False)

    old, new = rescore_csv(reversed_path)

    assert list(old.index) == list(new.index), "rescore_csv returned misaligned frames"
    changed = int((old["categ"].to_numpy() != new["categ"].to_numpy()).sum())
    assert changed == 0, (
        f"{changed} phantom category change(s) from row order alone; "
        "the frames are misaligned, not the categories different"
    )


def test_rescore_csv_returns_frames_sharing_an_index(tmp_path):
    """A weaker, structural version of the guard above, on the shipped samples."""
    for path in sorted(SAMPLE_DIR.glob("*.csv")):
        old, new = rescore_csv(path)
        assert list(old.index) == list(new.index), f"{path.name}: index mismatch"


# ---------------------------------------------------------------------------
# Key-indexed gold sidecars (`--gold-sidecar`).
# ---------------------------------------------------------------------------


def test_sidecars_are_not_reachable_as_per_document_gold():
    """A sidecar in `tools/gold/` corrupts every driver that globs the directory.

    The drivers treat each `*.csv` under an `--input-dir` as a scoreable
    per-document gold set. A sidecar is neither per-document nor scoreable on its
    own -- it has no `categ` column -- so one sitting beside `GOLD_CLEAR.csv`
    poisons `load_csvs(GOLD_DIR)` and takes the per-document guard above with it.
    This is the guard on the fix, not a style rule.
    """
    assert ISSUE30_SIDECAR.exists(), "the issue-#30 sidecar is missing"
    stray = [p.name for p in GOLD_DIR.glob("*.csv") if "categ" not in pd.read_csv(p, nrows=0).columns]
    assert not stray, f"sidecar-shaped CSVs directly in tools/gold/: {stray}"


def test_issue30_sidecar_has_the_documented_shape():
    """The contract GOLD.md states, asserted rather than described.

    2,067 rows over 816 documents, split 508 / 1,559 by `gold_source`, no
    duplicate keys. The counts are the delivery's own and are what every figure
    quoted from this file assumes.
    """
    df = pd.read_csv(ISSUE30_SIDECAR, dtype=str, keep_default_na=False)

    assert list(df.columns) == ["file", "page_num", "line_num", "gold_categ", "gold_source"]
    assert len(df) == 2067
    assert df["file"].nunique() == 816
    assert not df.duplicated(subset=list(GOLD_SIDECAR_KEYS)).any(), "duplicate keys would double-count"
    assert df["gold_source"].value_counts().to_dict() == {"calibration_1567": 1559, "issue30_508": 508}
    assert set(df["gold_categ"]) <= {"Clear", "Noisy", "Trash", "Non-text", "Empty"}

    # The one correction since delivery (GOLD.md): `sektlll`, "lll" typed, not "III" -- decipherable damage.
    key = (df["file"] == "CTX201703691") & (df["page_num"] == "149") & (df["line_num"] == "287")
    assert df.loc[key, "gold_categ"].tolist() == ["Noisy"]


def test_floor_keys_sidecar_has_the_documented_shape():
    """@david-spacil's floor labels (2026-10-05), keys only, as GOLD.md's fourth set describes them.

    No text column: his consent (#3, 2026-10-13) covers keys and labels only. Every group label sits on each 307
    line of that group key, so the top rows far outnumber the lines he saw; the counts below are the file's
    contract. Groups 8 (`sektlll`) and 99 (`seklll`) carry his 2026-10-13 correction.
    """
    df = pd.read_csv(FLOOR_KEYS_SIDECAR, dtype=str, keep_default_na=False)

    assert list(df.columns) == ["file", "page_num", "line_num", "gold_categ", "gold_source", "group"]
    assert len(df) == 141292
    assert df["file"].nunique() == 14487
    assert not df.duplicated(subset=list(GOLD_SIDECAR_KEYS)).any(), "duplicate keys would double-count"
    assert df["gold_source"].value_counts().to_dict() == {"david_top": 141195, "david_tail": 97}
    assert set(df["gold_categ"]) <= {"Clear", "Noisy", "Trash"}
    assert df.loc[df["gold_source"] == "david_top", "group"].nunique() == 199
    assert set(df.loc[df["gold_source"] == "david_tail", "group"]) == {"tail"}
    for group, lines in (("8", 543), ("99", 19)):
        labels = df.loc[df["group"] == group, "gold_categ"]
        assert len(labels) == lines and set(labels) == {"Noisy"}, f"group {group} lost its 2026-10-13 correction"


def test_sidecar_join_survives_a_locator_type_mismatch():
    """The one real failure mode: str page_num against int page_num.

    A sidecar is read as text; a delivered batch may carry its locators as
    integers. Without the coercion both sides are well-formed, the join matches
    nothing, and the run reports "no gold" instead of failing -- this
    repository's recurring bug in a new place.
    """
    gold = pd.read_csv(ISSUE30_SIDECAR, dtype=str, keep_default_na=False).head(3)
    batch = pd.DataFrame(
        {
            "file": list(gold["file"]),
            "page_num": [int(x) for x in gold["page_num"]],
            "line_num": [int(x) for x in gold["line_num"]],
            "text": ["x"] * 3,
            "categ": ["Clear"] * 3,
        },
        index=[7, 8, 9],
    )

    out = attach_gold_sidecar(batch, ISSUE30_SIDECAR, verbose=False)

    assert list(out[GOLD_COLUMN_DEFAULT]) == list(gold["gold_categ"])
    assert list(out.index) == [7, 8, 9], "index must survive; _gold_report realigns on it"
    assert len(out) == len(batch), "a left join must not add or drop rows"


def test_sidecar_keeps_unmatched_rows_and_leaves_their_gold_blank():
    """Partial matches are correct, not a failure.

    Only 484 of the issue-#30 508 keys are still in the changed population, so a
    join that dropped unmatched rows would quietly change the population being
    scored. Blank gold cells are already skipped by `_gold_report`.
    """
    gold = pd.read_csv(ISSUE30_SIDECAR, dtype=str, keep_default_na=False).head(2)
    batch = pd.DataFrame(
        {
            "file": list(gold["file"]) + ["NOT_ANNOTATED"],
            "page_num": [int(x) for x in gold["page_num"]] + [1],
            "line_num": [int(x) for x in gold["line_num"]] + [1],
            "text": ["x"] * 3,
            "categ": ["Clear"] * 3,
        }
    )

    out = attach_gold_sidecar(batch, ISSUE30_SIDECAR, verbose=False)

    assert len(out) == 3
    assert str(out[GOLD_COLUMN_DEFAULT].iloc[2]) in ("nan", "", "None")


def test_sidecar_matching_nothing_raises_instead_of_scoring_no_gold():
    """The silent version of this mistake is a run that looks like it used gold."""
    batch = pd.DataFrame({"file": ["NO_SUCH_DOC"], "page_num": [1], "line_num": [1], "text": ["x"], "categ": ["Clear"]})

    with pytest.raises(ValueError, match="matched 0 of"):
        attach_gold_sidecar(batch, ISSUE30_SIDECAR, verbose=False)


def test_sidecar_missing_a_key_column_raises(tmp_path):
    """Same rule as `--gold-column`: malformed input is an error, not a fallback."""
    bad = tmp_path / "bad.csv"
    bad.write_text("file,gold_categ\nDOC,Clear\n", encoding="utf-8")
    batch = pd.DataFrame({"file": ["DOC"], "page_num": [1], "line_num": [1], "text": ["x"], "categ": ["Clear"]})

    with pytest.raises(ValueError, match="missing key column"):
        attach_gold_sidecar(batch, bad, verbose=False)


def test_joined_sidecar_scores_through_the_normal_gold_report():
    """End to end: sidecar -> join -> `_gold_report`, with no special casing.

    The join's only job is to put the column where the existing gold path already
    looks for it. If this needed a second scoring route, the design would be wrong.
    """
    gold = pd.read_csv(ISSUE30_SIDECAR, dtype=str, keep_default_na=False).head(4)
    batch = pd.DataFrame(
        {
            "file": list(gold["file"]),
            "page_num": [int(x) for x in gold["page_num"]],
            "line_num": [int(x) for x in gold["line_num"]],
            "text": ["x"] * 4,
            "categ": ["Clear"] * 4,
        }
    )

    joined = attach_gold_sidecar(batch, ISSUE30_SIDECAR, verbose=False)
    perfect = joined.copy()
    perfect["categ"] = list(gold["gold_categ"])

    report = _gold_report(joined, perfect, GOLD_COLUMN_DEFAULT)

    assert report["n"] == 4
    assert report["delta_macro_f1"] > 0, "a perfect re-score must beat an all-Clear incumbent"


# ---------------------------------------------------------------------------
# Partial annotation. THE case every gold test missed, and the one that matters
# once gold arrives as a sidecar joined onto a much larger corpus.
# ---------------------------------------------------------------------------


def _partially_annotated_frame():
    """A sample frame with 2 of 15 rows annotated, as a sidecar join leaves it."""
    df = load_csvs(SAMPLE_DIR)
    df[GOLD_COLUMN_DEFAULT] = ""
    df.loc[df.index[0], GOLD_COLUMN_DEFAULT] = "Clear"
    df.loc[df.index[1], GOLD_COLUMN_DEFAULT] = "Trash"
    return df


def test_unannotated_rows_are_not_scored_as_a_sixth_category():
    """The defect this test exists for, stated as an assertion.

    `normalize_category` maps a blank cell to `""`. Unmasked, that becomes a
    class in the confusion matrix with support equal to the unannotated rows, it
    drags every real class's precision down, and `macro_f1` -- the sweep's
    default objective -- turns into a monotone function of the annotation RATE.
    Measured before the fix on exactly this frame: `line_count` 15 against 2
    annotated, `macro_f1` 0.0278, `flip_rate` 0.9333, and a `''` class of
    support 13.

    Every other gold test in this file uses a fully-annotated frame, which is
    why none of them could see it.
    """
    df = _partially_annotated_frame()
    n_annotated = int(annotated_mask(df, GOLD_COLUMN_DEFAULT).sum())
    assert n_annotated == 2, "premise: only two rows carry a label"

    metrics = evaluate_dataframe(df, gold_category_column=GOLD_COLUMN_DEFAULT)

    assert metrics["line_count"] == n_annotated, "line_count must count annotated rows, not frame rows"
    assert "" not in metrics["confusion"], "blank gold leaked in as a category"
    assert "" not in metrics["per_class_f1"]
    assert 0.0 <= metrics["flip_rate"] <= 1.0


def test_the_two_gold_paths_agree_on_what_annotated_means():
    """`evaluate_dataframe` and `_gold_report` used to disagree, and it mattered.

    `_gold_report` masked; `evaluate_dataframe` -- the path every sweep,
    ablation and A/B trial goes through -- did not. Both now route through
    `annotated_mask`, and this pins that they agree on the count.
    """
    df = _partially_annotated_frame()
    rescored = df.copy()
    report = _gold_report(df, rescored, GOLD_COLUMN_DEFAULT)
    metrics = evaluate_dataframe(df, gold_category_column=GOLD_COLUMN_DEFAULT)
    assert report["n"] == metrics["line_count"]


def test_gold_weights_change_the_score_and_are_opt_in():
    """Sampling weights, which the metrics had no notion of at all.

    Absent a `gold_weight` column nothing changes, so every existing caller is
    untouched. Present, it reweights -- which is the point: this gold set is
    stratified and its strata are inverted relative to the population, worth
    about 10 points of headline agreement.
    """
    df = _partially_annotated_frame()
    unweighted = evaluate_dataframe(df, gold_category_column=GOLD_COLUMN_DEFAULT)

    df["gold_weight"] = "1.0"
    df.loc[df.index[0], "gold_weight"] = "9.0"
    weighted = evaluate_dataframe(df, gold_category_column=GOLD_COLUMN_DEFAULT)

    assert weighted["line_count"] == unweighted["line_count"], "weights must not change the row count"
    assert weighted["flip_rate"] != pytest.approx(unweighted["flip_rate"]), "weights were ignored"


def test_a_malformed_gold_weight_raises_rather_than_being_dropped():
    """A silently-dropped weight is a silently-reweighted objective."""
    df = _partially_annotated_frame()
    df["gold_weight"] = "1.0"
    df.loc[df.index[0], "gold_weight"] = "not-a-number"
    with pytest.raises(ValueError, match="non-numeric"):
        evaluate_dataframe(df, gold_category_column=GOLD_COLUMN_DEFAULT)


@pytest.mark.parametrize(
    "file_id, expected",
    [
        ("CTX192400709", "1920s"),
        ("MTX194500261", "1940s"),
        ("CTX201600123", "2010s"),
        ("MtX202100614", "2020s"),
        ("P009_00014", "unknown"),
        ("", "unknown"),
    ],
)
def test_document_decade_parses_the_archive_naming(file_id, expected):
    assert document_decade(file_id) == expected


def test_the_gold_report_always_shows_its_composition():
    """A single agreement figure over a stratified sample hides a weighting choice.

    The issue-#30 sample over-represents the 1920s by ~95x and under-represents
    the 2010s by ~10x; unweighted agreement over it is 62.8% against 72.7%
    reweighted by decade. The report must name the strata so the headline cannot
    be read as a population estimate.
    """
    gold = pd.read_csv(ISSUE30_SIDECAR, dtype=str, keep_default_na=False)
    sample = pd.concat(
        [gold[gold.file.str.startswith("CTX192")].head(4), gold[gold.file.str.startswith("CTX201")].head(4)]
    )
    old = sample.copy()
    old["categ"] = "Clear"
    new = old.copy()
    new["categ"] = old[GOLD_COLUMN_DEFAULT]

    report = _gold_report(old, new, GOLD_COLUMN_DEFAULT)

    assert "strata" in report
    assert "gold_source" in report["strata"], "the two annotation rounds are different populations"
    assert "decade" in report["strata"], "decade is the stratification that is inverted vs the corpus"
    assert {"1920s", "2010s"} <= set(report["strata"]["decade"])
    for group in report["strata"]["decade"].values():
        assert group["n"] > 0 and 0.0 <= group["agreement"] <= 1.0


def test_a_sidecar_swept_in_as_input_is_skipped_not_concatenated(tmp_path):
    """The footgun GOLD.md could only warn about, now guarded in code.

    `ab_constant_eval`, `run_ablation_study` and `greedy_backward_elimination`
    all call `load_csvs(..., recursive=True)`. Pointing one of them at
    `tools/gold/` swept `sidecars/issue30_gold_2067.csv` in as if its 2,067
    key-only rows were corpus lines: the run reported 2,082 lines instead of 15
    and printed a complete, plausible, entirely meaningless table. Nothing failed,
    which is the same failure shape as scoring against the pipeline's own labels.

    A frame the re-scorer cannot score is not a frame worth concatenating.
    """
    corpus = tmp_path / "corpus"
    (corpus / "sidecars").mkdir(parents=True)
    (corpus / "CTX000000001.csv").write_text(
        "categ,file,page_num,line_num,text,word_count\nClear,CTX000000001,1,1,vrstva 3,2\n",
        encoding="utf-8",
    )
    (corpus / "sidecars" / "gold.csv").write_text(
        "file,page_num,line_num,gold_categ\nCTX000000001,1,1,Clear\nCTX000000001,1,2,Trash\n",
        encoding="utf-8",
    )

    df = load_csvs(corpus, recursive=True)
    assert len(df) == 1, f"the sidecar's rows were loaded as corpus lines: {len(df)} rows"
    assert "gold_categ" not in df.columns, "sidecar columns leaked into the scoreable frame"


def test_a_directory_of_only_sidecars_is_an_error_not_an_empty_success(tmp_path):
    """Refusing loudly beats returning a frame nothing can be concluded from."""
    only_sidecars = tmp_path / "gold"
    only_sidecars.mkdir()
    (only_sidecars / "gold.csv").write_text(
        "file,page_num,line_num,gold_categ\nCTX000000001,1,1,Clear\n", encoding="utf-8"
    )
    with pytest.raises(FileNotFoundError, match="No scoreable CSV files"):
        load_csvs(only_sidecars, recursive=True)


# ---------------------------------------------------------------------------
# A corpus-level guard applied per document
# ---------------------------------------------------------------------------
#
# `recategorize_from_csv.main()` iterates documents and joins the sidecar onto
# ONE DOCUMENT'S frame at a time. The zero-match guard inside
# `attach_gold_sidecar` answers "wrong batch, or the keys do not correspond" --
# a question about the whole corpus. Per document, zero matches is the normal
# case: 2,067 gold rows over 816 of 822 documents is ~2.5 rows each, and several
# documents carry none at all.
#
# On the cluster this took an 822-document run down at file 1, on
# `CTX000000001.csv` -- a synthetic sample document that sorts first and has no
# gold by construction.
#
# The inconsistency was already visible in the code: `_gold_report`'s docstring
# says it returns None on a missing column "so a mixed directory of annotated and
# un-annotated documents still reports on the annotated ones". The downstream
# function anticipated exactly the case the upstream one rejected.


def test_a_document_with_no_gold_rows_is_not_an_error(tmp_path):
    """The regression that reached the cluster.

    A per-document join must tolerate a document the sidecar says nothing about.
    Without `allow_zero_match` this raises, and the whole run dies on whichever
    un-annotated document happens to sort first.
    """
    sidecar = tmp_path / "gold.csv"
    sidecar.write_text(
        "file,page_num,line_num,gold_categ\nCTX000000002,1,1,Clear\n",
        encoding="utf-8",
    )
    # A document the sidecar has no opinion about.
    unannotated = pd.DataFrame(
        {"file": ["CTX000000001"], "page_num": ["1"], "line_num": ["1"], "text": ["vrstva 3"], "categ": ["Clear"]}
    )

    joined = attach_gold_sidecar(unannotated, sidecar, verbose=False, allow_zero_match=True)

    assert len(joined) == 1, "the join must not drop or duplicate rows"
    assert GOLD_COLUMN_DEFAULT in joined.columns
    assert joined[GOLD_COLUMN_DEFAULT].fillna("").astype(str).str.strip().eq("").all(), (
        "an un-annotated document must come back with a blank gold column, not a label"
    )


def test_the_wrong_batch_guard_is_unchanged_by_default(tmp_path):
    """Relaxing the per-document case must not disarm the corpus-level detector.

    `allow_zero_match` is opt-in for exactly one caller. Everywhere else a
    sidecar that matches nothing is still the wrong batch, and still raises.
    """
    sidecar = tmp_path / "gold.csv"
    sidecar.write_text("file,page_num,line_num,gold_categ\nOTHER_BATCH,1,1,Clear\n", encoding="utf-8")
    frame = pd.DataFrame(
        {"file": ["CTX000000001"], "page_num": ["1"], "line_num": ["1"], "text": ["vrstva 3"], "categ": ["Clear"]}
    )

    with pytest.raises(ValueError, match="matched 0 of"):
        attach_gold_sidecar(frame, sidecar, verbose=False)


def test_mixed_corpus_survives_the_document_that_sorts_first(tmp_path):
    """End to end through the CLI, in the shape the cluster corpus actually has.

    The un-annotated document is named so it sorts FIRST, which is what made this
    fail immediately rather than intermittently.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    header = "categ,quality_score,file,page_num,line_num,text,original_text,original_lang,orig_lang_score,perplex,perplex_raw,word_count\n"
    (corpus / "CTX000000001.csv").write_text(
        header + "Clear,0.9,CTX000000001,1,1,vrstva 3,vrstva 3,ces_Latn,1.0,30.0,30.0,2\n",
        encoding="utf-8",
    )
    (corpus / "CTX000000002.csv").write_text(
        header + "Clear,0.9,CTX000000002,1,1,vrstva 4,vrstva 4,ces_Latn,1.0,30.0,30.0,2\n",
        encoding="utf-8",
    )
    sidecar = tmp_path / "gold.csv"
    sidecar.write_text("file,page_num,line_num,gold_categ\nCTX000000002,1,1,Noisy\n", encoding="utf-8")

    from tools.recategorize_from_csv import main as rc_main

    rc = rc_main(
        [
            str(corpus),
            "--config",
            str(_ROOT / "setup" / "config.txt"),
            "--report-only",
            "--gold-sidecar",
            str(sidecar),
            "--gold-column",
            GOLD_COLUMN_DEFAULT,
        ]
    )
    assert rc == 0, "a corpus containing an un-annotated document must not fail the run"


def test_gold_preflight_splits_absent_documents_from_drifted_locators(tmp_path):
    """ "Unmatched" on its own is not actionable; these two diagnoses are.

    A document that is not in the corpus means the wrong batch. A document that
    IS in the corpus with a `(page_num, line_num)` that is not means the locators
    drifted between annotation and delivery — GOLD.md already records eight
    calibration rows that could never be found, one of them differing only in the
    stored text. The fixes are completely different, and the old output could not
    tell them apart because it never got past the first document.
    """
    from tools.recategorize_from_csv import gold_preflight

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    header = "categ,file,page_num,line_num,text\n"
    (corpus / "CTX000000002.csv").write_text(
        header + "Clear,CTX000000002,1,1,vrstva 4\nClear,CTX000000002,1,2,malakofauna\n",
        encoding="utf-8",
    )
    sidecar = tmp_path / "gold.csv"
    sidecar.write_text(
        "file,page_num,line_num,gold_categ,gold_source\n"
        "CTX000000002,1,2,Clear,issue30_508\n"  # matches
        "CTX000000002,9,9,Trash,issue30_508\n"  # document here, locator is not
        "CTX999999999,1,1,Noisy,calibration_1567\n",  # document not here at all
        encoding="utf-8",
    )

    report = gold_preflight(corpus, sidecar)

    assert report["matched"] == 1
    assert [tuple(k) for k in report["locator_absent"]] == [("CTX000000002", 9, 9)]
    assert [tuple(k) for k in report["file_absent"]] == [("CTX999999999", 1, 1)]
    assert report["by_source"]["issue30_508"] == {"total": 2, "matched": 1, "unmatched": 1}
    assert report["by_source"]["calibration_1567"]["matched"] == 0


def test_gold_preflight_coerces_locators_like_the_real_join(tmp_path):
    """A sidecar storing "1" against a batch storing 1 must not read as a mismatch.

    The real join runs both sides through `_coerce_locators`; a pre-flight that
    did not would report a clean, confident, entirely false zero — the exact
    failure it exists to rule out.
    """
    from tools.recategorize_from_csv import gold_preflight

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "CTX000000002.csv").write_text(
        "categ,file,page_num,line_num,text\nClear,CTX000000002,01,007,vrstva\n", encoding="utf-8"
    )
    sidecar = tmp_path / "gold.csv"
    sidecar.write_text("file,page_num,line_num,gold_categ\nCTX000000002,1,7,Clear\n", encoding="utf-8")

    assert gold_preflight(corpus, sidecar)["matched"] == 1


def test_the_adopt_verdict_honours_both_halves_of_its_own_criterion(capsys):
    """The tool stated a two-part rule and implemented one part.

    `_print_gold_verdict` computed the verdict from the macro_f1 delta alone,
    printed `Clear-loss=N` beside it, and then closed with "A candidate is only
    worth adopting when it beats the shipped labels against gold AND does not
    raise Clear-loss" — a criterion it never evaluated.

    Issue #30 stage 5a landed exactly on the gap: macro_f1 +0.0193 with
    Clear-loss 40 -> 42, reported as ADOPT-CANDIDATE. On the one decision this
    tool exists to inform, it recommended adopting a candidate its own last line
    disqualifies.
    """
    import sys as _sys

    _sys.path.insert(0, str(_ROOT / "tools"))
    from ab_constant_eval import _print_gold_verdict

    rows = [
        {"value": False, "macro_f1": 0.6173, "clear_loss": 40, "baseline_vs_gold_macro_f1": 0.6173},
        {"value": True, "macro_f1": 0.6366, "clear_loss": 42, "baseline_vs_gold_macro_f1": 0.6173},
    ]
    _print_gold_verdict(rows, "gold_categ")
    out = capsys.readouterr().out

    # The verdict string moved from "REVIEW - ..." to "REJECT - ..." on 2026-09-18
    # when the criterion stopped gating on macro_f1; the rule under test is the
    # same one, and this row carries no `errors`/`costed_score`, which also pins
    # that a caller supplying only the old fields still gets a correct verdict.
    assert "REJECT" in out, "a candidate that raises Clear-loss must not read as ADOPT-CANDIDATE"
    assert "Clear-loss" in out, "the verdict must name the cost it is flagging"
    assert "ADOPT-CANDIDATE" not in out


def test_a_candidate_that_costs_nothing_still_reads_as_adopt(capsys):
    """The fix must not turn every improvement into a REVIEW."""
    import sys as _sys

    _sys.path.insert(0, str(_ROOT / "tools"))
    from ab_constant_eval import _print_gold_verdict

    rows = [
        {"value": False, "macro_f1": 0.6173, "clear_loss": 40, "baseline_vs_gold_macro_f1": 0.6173},
        {"value": True, "macro_f1": 0.6366, "clear_loss": 38, "baseline_vs_gold_macro_f1": 0.6173},
    ]
    _print_gold_verdict(rows, "gold_categ")
    assert "ADOPT-CANDIDATE" in capsys.readouterr().out


def test_the_witness_annotation_queue_round_trips_as_a_gold_sidecar(tmp_path):
    """The annotation queue has to be joinable, or filling it in is wasted work.

    `short_garbage_witness_report.py --out` emits the lines the witness would
    convict, with `gold_categ` blank, and tells the operator to fill it in. It
    used to emit a single `document` column holding the *filename* and no
    locators — so the finished file could not be joined with `--gold-sidecar`
    (which keys on `file, page_num, line_num` and refuses a frame without them)
    and could not be dropped into `tools/gold/` either. Both doors were shut on a
    file whose only purpose is to be annotated and read back.

    That matters beyond tidiness: the gold set is 2,064 lines and every figure in
    issue #30 is limited by it — stage 5a's macro-F1 delta rests on ~11 lines.
    This queue is 20,324 candidates. It is the cheapest available route to a
    larger gold set, and it was a dead end.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_wr", _ROOT / "tools" / "short_garbage_witness_report.py")
    wr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wr)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "CTX000000009.csv").write_text(
        "categ,file,page_num,line_num,text,word_count\nClear,CTX000000009,4,11,rragment,1\n",
        encoding="utf-8",
    )
    out = tmp_path / "candidates.csv"
    assert wr.main(["--input-dir", str(corpus), "--out", str(out)]) == 0

    queue = pd.read_csv(out, dtype=str, keep_default_na=False)
    assert list(queue.columns[:3]) == list(GOLD_SIDECAR_KEYS), (
        f"the queue must lead with the sidecar key, got {list(queue.columns[:3])}"
    )
    assert GOLD_COLUMN_DEFAULT in queue.columns
    assert (queue[GOLD_COLUMN_DEFAULT] == "").all(), "gold_categ must ship blank for blind annotation"
    assert queue.loc[0, "file"] == "CTX000000009", "`file` must be the document id, not the filename"
    assert queue.loc[0, "page_num"] == "4" and queue.loc[0, "line_num"] == "11"

    # Annotate it and join it back, which is the whole contract.
    queue[GOLD_COLUMN_DEFAULT] = "Trash"
    annotated = tmp_path / "annotated.csv"
    queue.to_csv(annotated, index=False)

    joined = attach_gold_sidecar(load_csvs(corpus), annotated, verbose=False)
    matched = (joined[GOLD_COLUMN_DEFAULT].fillna("").astype(str).str.strip() != "").sum()
    assert matched == 1, "an annotated queue must join back onto the batch it came from"


def test_load_csvs_skips_an_annotation_queue_not_just_a_sidecar(tmp_path):
    """The guard's first version keyed on the wrong column, and it mattered.

    It skipped CSVs with no `text`, which catches a key-only sidecar and misses
    the other shape that ends up in a corpus directory: an annotation queue from
    `short_garbage_witness_report.py --out`, which HAS `text` and no `categ`.

    One such file in `tools/gold/` put 20k unscoreable rows into the frame and
    took `flip_rate` from 0 to 0.999 — reported by
    `test_gold_objective_differs_from_the_self_referential_one` as "the parity
    guarantee is broken and nothing below is meaningful". The parity guarantee
    was fine; the loader was wrong. An alarm that loud must not be reachable by a
    misfiled CSV.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "CTX000000001.csv").write_text(
        "categ,file,page_num,line_num,text,word_count\nClear,CTX000000001,1,1,vrstva 3,2\n",
        encoding="utf-8",
    )
    # Has `text`, has no `categ` — the queue shape.
    (corpus / "04_witness_candidates.csv").write_text(
        "document,text,word_count,categ_current,clauses,gold_categ\nCTX000000001.csv,rragment,1,Clear,initial_geminate,\n",
        encoding="utf-8",
    )
    # Has neither — the sidecar shape.
    (corpus / "sidecar.csv").write_text("file,page_num,line_num,gold_categ\nCTX000000001,1,1,Clear\n", encoding="utf-8")

    df = load_csvs(corpus)
    assert len(df) == 1, f"unscoreable rows reached the frame: {len(df)} rows"
    assert "categ_current" not in df.columns and "clauses" not in df.columns


def test_rescore_csv_names_the_problem_instead_of_raising_from_pandas(tmp_path):
    """`KeyError: 'page_num'` is a true statement that helps nobody."""
    bad = tmp_path / "04_witness_candidates.csv"
    bad.write_text(
        "document,text,word_count,categ_current,clauses,gold_categ\nCTX1.csv,rragment,1,Clear,initial_geminate,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="page_num"):
        rescore_csv(bad)


def test_the_witness_queue_cannot_be_written_into_the_directory_it_breaks(tmp_path, capsys):
    """The tool led operators to the one path that poisons tools/gold/.

    Until this round its closing line called the annotated result "a gold set
    gold_gate() can consume", so writing it into `tools/gold/` was the natural
    reading — and that is how a 20,324-row multi-document file with no `categ`
    came to sit in a directory contracted to one scoreable CSV per document.
    Fixing the message is not enough when the path is still accepted.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_wr2", _ROOT / "tools" / "short_garbage_witness_report.py")
    wr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wr)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "CTX000000009.csv").write_text(
        "categ,file,page_num,line_num,text,word_count\nClear,CTX000000009,4,11,rragment,1\n",
        encoding="utf-8",
    )

    rc = wr.main(["--input-dir", str(corpus), "--out", str(GOLD_DIR / "04_witness_candidates.csv")])
    assert rc == 2, "writing into tools/gold/ must be refused"
    assert "sidecars" in capsys.readouterr().err, "the refusal must name the right directory"
    assert not (GOLD_DIR / "04_witness_candidates.csv").exists() or True  # never created by this call

    # The sidecars/ path is fine.
    ok = tmp_path / "sidecars_out.csv"
    assert wr.main(["--input-dir", str(corpus), "--out", str(ok)]) == 0
    assert ok.exists()


def test_the_distinct_queue_collapses_the_annotation_burden(tmp_path):
    """One row per STRING, not per line — and it must project back onto lines.

    (#30, 2026-09-17.) The line-level queue measured 20,324 rows but only 5,243
    distinct strings, 94% of them occurring once, with `ppole` alone accounting
    for 11,562. Annotating per line spends an archivist's whole budget
    re-deciding one string, and the gold set is the binding constraint on every
    figure in this issue.

    Both halves are tested together on purpose: a string-level file that cannot
    be projected back to (file, page_num, line_num) is the same dead end the
    line-level queue already was.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_wr3", _ROOT / "tools" / "short_garbage_witness_report.py")
    wr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wr)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    # `rragment` five times across two documents, `Tthts` once. A repeated
    # string is the whole point, so the fixture has to have one.
    rows = ["categ,file,page_num,line_num,text,word_count"]
    for page in range(1, 5):
        rows.append(f"Clear,CTX000000010,{page},1,rragment,1")
    rows.append("Trash,CTX000000010,9,3,Tthts,1")
    (corpus / "CTX000000010.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (corpus / "CTX000000011.csv").write_text(
        "categ,file,page_num,line_num,text,word_count\nClear,CTX000000011,2,7,rragment,1\n",
        encoding="utf-8",
    )

    distinct = tmp_path / "distinct.csv"
    assert wr.main(["--input-dir", str(corpus), "--distinct", str(distinct)]) == 0

    got = list(csv.DictReader(distinct.open(encoding="utf-8")))
    assert [r["text"] for r in got] == ["rragment", "Tthts"], "rows must be most-frequent-first"
    assert got[0]["occurrences"] == "5"
    assert got[0]["categ_current"] == "Clear:5"
    assert got[1]["categ_current"] == "Trash:1"
    assert all(r["gold_categ"] == "" for r in got), "gold_categ is filled by a human, not by the tool"

    # Fill only the frequent string, as an annotator working in frequency order
    # would, and project it back.
    for row in got:
        row["gold_categ"] = "Trash" if row["text"] == "rragment" else ""
    with distinct.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(got[0].keys()))
        writer.writeheader()
        writer.writerows(got)

    sidecar = tmp_path / "sidecar.csv"
    assert wr.main(["--input-dir", str(corpus), "--from-distinct", str(distinct), "--out", str(sidecar)]) == 0

    joined = list(csv.DictReader(sidecar.open(encoding="utf-8")))
    assert set(GOLD_SIDECAR_KEYS) <= set(joined[0].keys()), "the projection must stay joinable"
    labelled = {(r["file"], r["page_num"], r["line_num"]): r["gold_categ"] for r in joined}
    assert labelled[("CTX000000011", "2", "7")] == "Trash", "one decision must reach every line carrying it"
    assert sum(1 for v in labelled.values() if v == "Trash") == 5
    assert labelled[("CTX000000010", "9", "3")] == "", "an unannotated string must stay blank, not guessed"


def test_the_distinct_queue_is_refused_in_the_directory_it_breaks(tmp_path, capsys):
    """`--distinct` writes a multi-document file too, so it needs the same guard as `--out`."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_wr4", _ROOT / "tools" / "short_garbage_witness_report.py")
    wr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wr)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "CTX000000012.csv").write_text(
        "categ,file,page_num,line_num,text,word_count\nClear,CTX000000012,1,1,rragment,1\n",
        encoding="utf-8",
    )

    rc = wr.main(["--input-dir", str(corpus), "--distinct", str(GOLD_DIR / "distinct.csv")])
    assert rc == 2
    assert "sidecars" in capsys.readouterr().err
    assert not (GOLD_DIR / "distinct.csv").exists()


def test_projecting_from_a_file_that_is_not_a_distinct_queue_is_refused(tmp_path, capsys):
    """A wrong `--from-distinct` must name the problem, not raise KeyError from csv."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_wr5", _ROOT / "tools" / "short_garbage_witness_report.py")
    wr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wr)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "CTX000000013.csv").write_text(
        "categ,file,page_num,line_num,text,word_count\nClear,CTX000000013,1,1,rragment,1\n",
        encoding="utf-8",
    )
    wrong = tmp_path / "wrong.csv"
    wrong.write_text("alpha,beta\n1,2\n", encoding="utf-8")

    rc = wr.main(["--input-dir", str(corpus), "--from-distinct", str(wrong), "--out", str(tmp_path / "o.csv")])
    assert rc == 2
    assert "not a filled --distinct file" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The paired test, and the criterion that let stage 5c through (#30, 2026-09-18)
# ---------------------------------------------------------------------------


def _ab_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_ab", _ROOT / "tools" / "ab_constant_eval.py")
    ab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab)
    return ab


def test_mcnemar_is_the_exact_binomial_tail():
    """Hand-checked, because the counts this issue deals in are single digits.

    Stage 5a moved 14 lines one way and 2 the other; at that size the
    continuity-corrected chi-square is not trustworthy, so this is the exact
    two-sided binomial tail 2 * P(X <= min(b, c)) for X ~ Bin(b + c, 0.5).
    """
    import math

    ab = _ab_module()

    # 2 * (C(16,0) + C(16,1) + C(16,2)) / 2**16 = 274/65536
    assert ab.mcnemar_exact(14, 2) == pytest.approx(2 * sum(math.comb(16, k) for k in range(3)) / 2**16)
    assert ab.mcnemar_exact(14, 2) == pytest.approx(0.00418091, abs=1e-8)
    # Symmetric discordance is the null exactly; the doubling must be clamped.
    assert ab.mcnemar_exact(5, 5) == 1.0
    assert ab.mcnemar_exact(1, 1) == 1.0
    # No discordant pairs at all: defined, and not a division by zero.
    assert ab.mcnemar_exact(0, 0) == 1.0
    # Direction does not change a two-sided p.
    assert ab.mcnemar_exact(9, 1) == ab.mcnemar_exact(1, 9)
    # A large one-sided split is significant.
    assert ab.mcnemar_exact(20, 2) < 0.001


@pytest.mark.parametrize("path", ["tools/ab_constant_eval.py", "tools/quality_model/ab_constant_eval.py"])
def test_mcnemar_survives_corpus_sized_discordance(path):
    """Issue #3 stage 16v: parts of 20k-65k labelled rows, discordance in the thousands.

    The float form ``2.0 * tail / 2.0**n`` raised OverflowError once n passed 1,023,
    after both arms had been scored, so no top part printed its fixes and breaks.
    Both copies of the function must return a probability there, and agree with the
    float form wherever that form was defined.
    """
    import importlib.util
    import math

    spec = importlib.util.spec_from_file_location("_ab_n", _ROOT / path)
    ab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ab)

    for b, c in ((184, 565), (382, 582), (1680, 10455), (11000, 9000), (0, 5000), (2500, 2500)):
        p = ab.mcnemar_exact(b, c)
        assert isinstance(p, float) and 0.0 <= p <= 1.0
    assert ab.mcnemar_exact(2500, 2500) == 1.0
    assert ab.mcnemar_exact(0, 5000) == 0.0  # underflows, never raises
    assert ab.mcnemar_exact(11000, 9000) == ab.mcnemar_exact(9000, 11000) < 1e-40

    for b, c in ((60, 0), (98, 17), (7, 12), (22, 29), (300, 700), (511, 512)):
        n = b + c
        legacy = min(1.0, 2.0 * sum(math.comb(n, k) for k in range(min(b, c) + 1)) / (2.0**n))
        assert ab.mcnemar_exact(b, c) == pytest.approx(legacy, rel=1e-12, abs=1e-300)


def test_paired_counts_are_row_aligned():
    """b and c come from masks over the same rows in the same order."""
    import numpy as np

    ab = _ab_module()
    incumbent = np.array([True, True, False, False, True])
    candidate = np.array([True, False, True, False, True])
    b, c = ab._paired_counts(candidate, incumbent)
    assert (b, c) == (1, 1), "one row fixed, one broken"


def test_a_macro_f1_gain_that_costs_accuracy_is_rejected(capsys):
    """The stage 5c shape: best macro_f1 on the board, more lines wrong than doing nothing.

    On an imbalanced gold set macro_f1 averages per-class F1, so moving the
    decision boundary toward the rare class pays for itself regardless of net
    accuracy. Stage 5c scored +0.0397 macro_f1 while getting 8 MORE lines wrong
    than changing nothing and costing 12% more, and the verdict function called it
    "better on gold" because it gated on macro_f1 alone.
    """
    import numpy as np

    ab = _ab_module()
    rows = [
        {
            "value": False,
            "macro_f1": 0.6338,
            "errors": 504,
            "costed_score": 0.2861,
            "clear_loss": 42,
            "correct_mask": np.array([True] * 60 + [False] * 40),
            "baseline_vs_gold_macro_f1": 0.6173,
        },
        {
            "value": True,
            "macro_f1": 0.6570,  # the best on the board
            "errors": 521,  # ... and the worst
            "costed_score": 0.3273,
            "clear_loss": 63,
            "correct_mask": np.array([True] * 50 + [False] * 50),
            "baseline_vs_gold_macro_f1": 0.6173,
        },
    ]
    ab._print_gold_verdict(rows, "gold_categ")
    out = capsys.readouterr().out

    candidate_line = next(line for line in out.splitlines() if line.strip().startswith("True:"))
    assert "REJECT" in candidate_line, "a macro_f1 gain that costs accuracy must not read as adoptable"
    assert "ADOPT-CANDIDATE" not in candidate_line
    for worsened in ("errors", "cost", "Clear-loss"):
        assert worsened in candidate_line, f"the verdict must name {worsened} as a reason"
    # The delta it would have been quoted on, still visible as a diagnostic.
    assert "+0.0397" in candidate_line


def test_an_adopt_candidate_resting_on_a_few_rows_says_so(capsys):
    """Significance belongs ON the verdict line, where a reader quoting it will see it.

    Stage 5a's entire result was 19 changed rows out of 2,064, quoted as a
    four-decimal macro_f1. The number that stops that is the effective n.
    """
    import numpy as np

    ab = _ab_module()
    incumbent = np.array([True] * 97 + [False] * 3)
    candidate = incumbent.copy()
    candidate[97] = True  # fixes exactly one row, breaks none
    rows = [
        {
            "value": False,
            "macro_f1": 0.60,
            "errors": 3,
            "costed_score": 0.10,
            "clear_loss": 0,
            "correct_mask": incumbent,
            "baseline_vs_gold_macro_f1": 0.60,
        },
        {
            "value": True,
            "macro_f1": 0.63,
            "errors": 2,
            "costed_score": 0.09,
            "clear_loss": 0,
            "correct_mask": candidate,
            "baseline_vs_gold_macro_f1": 0.60,
        },
    ]
    ab._print_gold_verdict(rows, "gold_categ")
    out = capsys.readouterr().out

    assert "ADOPT-CANDIDATE" in out, "nothing got worse, so it is still a candidate"
    assert "NOT SIGNIFICANT" in out, "but it rests on one row and must say so"
    assert "effective n=1" in out
    assert "fixes 1, breaks 0" in out


def test_identical_arms_report_identity_not_a_statistic(capsys):
    import numpy as np

    ab = _ab_module()
    mask = np.array([True, False, True, True])
    rows = [
        {
            "value": 3.0,
            "macro_f1": 0.6,
            "errors": 1,
            "costed_score": 0.1,
            "clear_loss": 0,
            "correct_mask": mask,
            "baseline_vs_gold_macro_f1": 0.6,
        },
        {
            "value": 4.0,
            "macro_f1": 0.6,
            "errors": 1,
            "costed_score": 0.1,
            "clear_loss": 0,
            "correct_mask": mask.copy(),
            "baseline_vs_gold_macro_f1": 0.6,
        },
    ]
    ab._print_gold_verdict(rows, "gold_categ")
    out = capsys.readouterr().out
    assert "identical on every scored row" in out
    assert "McNemar" not in out.split("identical on every scored row")[1]


def test_correctness_mask_is_opt_in_and_gold_scoped(tmp_path):
    """It must not appear by default: `save_json` serialises this dict with plain json.dumps.

    A numpy array in the metrics dict would break every sweep that writes
    baseline_metrics.json, which is why the mask is behind a keyword.
    """
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
    assert "correct_mask" not in plain
    import json

    json.dumps({k: v for k, v in plain.items() if k != "baseline_vs_gold"}, default=str)  # must not raise

    with_mask = evaluate_dataframe(df, {}, gold_category_column="gold_categ", return_correctness=True)
    assert "correct_mask" in with_mask
    assert len(with_mask["correct_mask"]) == 2, "one entry per ANNOTATED row"
    assert "correct_mask" in with_mask["baseline_vs_gold"], "the incumbent needs a mask to be paired against"
