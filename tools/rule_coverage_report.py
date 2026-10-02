#!/usr/bin/env python3
"""
tools/rule_coverage_report.py
==============================
Analyzes rule-fire coverage (Increment B5) to establish which structural rules
and per-line penalties in the categorisation engine are:

  DEAD            — fire_count == 0 across all supplied documents AND the rule
                    is not gated by a config flag that is currently off. The
                    rule's action never executes; it is unreachable dead code
                    and can be permanently deleted without a gold label set,
                    because deletion provably changes nothing.

  INERT           — fire_count == 0 because the rule's fire site is gated by a
                    config flag that ships off (text_util.CONFIG_GATED_RULES).
                    This says NOTHING about the rule's population and is not a
                    retirement signal: no corpus can make it fire while the flag
                    is false. (#30 D35, after the stage-6 sweep classified
                    `rule_short_garbage_witness` DEAD and recommended retiring
                    it, while stage 08f had measured the same predicate reaching
                    100,824 lines and stage 08b had passed its adoption gate.)
                    To measure one of these, re-run with the flag on.

  REDUNDANT-HERE  — fire_count > 0 but decisive_count == 0. The rule fires
                    but is currently masked by an overlapping rule that catches
                    the same line first (entanglement).  Keep it: the masking
                    order may change with corpus or config, so the rule is a
                    real guard that just appears redundant on this sample.

  LOAD-BEARING    — decisive_count > 0. Removing the rule changes at least one
                    line's category vs. the frozen ground truth. Always keep.

Coverage columns
----------------
  fire_count      Raw execution count within rule_fire_capture().
  fire_rate       fire_count / n_scored_lines (excludes Empty / Non-text
                  fast-track rows that never pass through the scorer).
  decisive_count  LOO: lines whose final category changes when the rule is
                  disabled via DISABLED_RULES, measured against the stored
                  categ (flip_rate × n_lines).
  decisive_share  decisive_count / fire_count. The column to read for a rule
                  marked `gate_marker`, whose fire_count is a population size.
  clear_loss      LOO: lines that were Clear in the STORED categ but become
                  Trash or Non-text when the rule is removed — the most
                  operationally expensive failure mode. Always self-referential;
                  read `gold_clear_loss` instead when it is present. Note the two
                  are not on the same population: `clear_loss` is the whole
                  frame, `gold_clear_loss` only the annotated rows.
  class           Derived classification: DEAD / REDUNDANT-HERE / LOAD-BEARING
                  / INERT (see the class definitions above).
  gate_marker     True when the rule's _fire() sits at the entry of a gate that
                  always returns, so its count reports how many lines entered
                  the gate, not how often it decided. See GATE_MARKER_RULES.

With --split-cascade:
  decisive_line     LOO flips with document post-processing disabled.
  decisive_cascade  decisive_count − decisive_line. A RESIDUAL BETWEEN TWO FLIP
                    COUNTS, not a partition, and NOT "the cascade this rule's
                    removal sets off" (#30 D37 — this docstring said that for a
                    year and it is wrong). Both passes are scored against the
                    same stored `categ`, which is itself post-smoothing, and
                    there is no all-rules-on / smoothing-off baseline pass
                    anywhere in this tool. `decisive_line` therefore carries the
                    ENTIRE footprint of removing document smoothing, not this
                    rule's share of it.
                    Consequence: large negative values are the expected shape for
                    essentially every rule and are not a signal. The floor is
                    visible in any run — a rule with no effect at all reports
                    decisive_line = decisive_cascade × −1 and nets to zero, and
                    that common magnitude IS the smoothing footprint (165,482
                    lines in the stage-6 corpus). To get the quantity this column
                    was meant to be, a fourth pass would be needed.

With --gold-column (and --gold-sidecar):
  gold_clear_loss_baseline  The same Clear-loss count for the SHIPPED
                       configuration, so `gold_clear_loss` (an absolute count in
                       the rule-disabled arm) can be read as the delta it is
                       meant to be. Free — the gold pass already computes it as
                       `baseline_vs_gold` (#30 D36).
  gold_delta_macro_f1  macro-F1 against the HUMAN labels with the rule removed,
                       minus the shipped pipeline's macro-F1 against the same
                       labels. Negative = removing the rule costs correctness.
  gold_n               annotated rows scored.
  gold_clear_loss      the same failure mode as `clear_loss`, but counted over
                       lines the ANNOTATOR called Clear. This is the column the
                       adoption gate in tools/GOLD.md is written against; the
                       plain `clear_loss` beside it is not.

READ THIS BEFORE QUOTING A NUMBER FROM THIS TOOL
------------------------------------------------
`decisive_count` and `clear_loss` are scored against the pipeline's OWN stored
`categ` — with or WITHOUT --gold-column. The offline re-score reproduces the
stored labels exactly at the shipped config, so the baseline is zero by
construction and those figures mean "how much does this rule change what we
already output", never "is the output right".

For `decisive_count` that is the right measurement and needs no gold: how many
lines a rule moves is a property of the rule. DEAD / REDUNDANT-HERE /
LOAD-BEARING inherit it and are sound as they stand.

For `clear_loss` it is NOT, because that column is read as a correctness claim —
"true-Clear lines pushed to Trash" — and against stored `categ` it only counts
lines the pipeline already called Clear. Pass --gold-column and read
`gold_clear_loss`, which is computed from the gold pass that already runs.

The JSON payload records `gold_column`, `decisive_scored_against`,
`clear_loss_scored_against` and `gold_clear_loss_available` so a self-referential
run can never be mistaken for a gold one after the fact.

Usage
-----
  # Directory of per-document CSVs
  python tools/rule_coverage_report.py --input-dir data_samples/DOC_LINE_CATEG

  # Single CSV file
  python tools/rule_coverage_report.py data_samples/DOC_LINE_CATEG/some_doc.csv

  # With custom config and JSON output
  python tools/rule_coverage_report.py \\
      --input-dir data_samples/DOC_LINE_CATEG \\
      --config setup/config.txt \\
      --output rule_coverage.json

Exit codes
----------
  0  No dead rules found (or run completed normally).
  1  One or more dead rules detected; list printed to stdout.
  2  Bad arguments / missing path.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo root on sys.path
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd  # noqa: E402

from text_util import (  # noqa: E402
    CONFIG_GATED_RULES,
    override_constants,
    rule_fire_capture,
    rule_is_config_gated_off,
)
from tools.recategorize_from_csv import (  # noqa: E402
    _load_lang_config,
    add_gold_column_argument,
    annotated_mask,
    attach_gold_sidecar_from_args,
    coerce_constants,
    evaluate_dataframe,
    load_csvs,
    read_config_constants,
    recategorize_dataframe,
    validate_constants,
)

# ---------------------------------------------------------------------------
# Canonical rule / penalty registry
#
# Must match the _fire() call-sites in text_util.py exactly. That used to be a
# comment asking for manual upkeep, and it drifted: the five rules added by the
# issue #30 work (rule_short_line, rule_damaged_token, rule_reference_floor,
# rule_bigram_run, rule_fragment_tokens) were missing, so every coverage report
# produced after PR #32 silently omitted them. It is now enforced by
# tests/test_rule_coverage.py::test_rules_registry_matches_fire_call_sites.
# ---------------------------------------------------------------------------
RULES: list[str] = sorted(
    [
        "rule_hard_sweep",
        "rule_extreme_ppl",
        "rule_absolute_ppl",
        "rule_inverted",
        "rule_allcaps",
        "rule_garbage_density",
        "rule_trailing_fill_rescue",
        "rule_short_garbage",
        "rule_short_garbage_witness",
        "rule_short_garbage_witness_floor",
        "rule_domain_notation",
        "rule_domain_notation_categ",
        "rule_short_line",
        "rule_zero_alpha",
        "rule_lowppl_clear",
        "rule_mostly_readable_noisy",
        "rule_damaged_token",
        "rule_reference_floor",
        "rule_wqx_rot",
        "rule_vowelless",
        "rule_ledger_fragmentation",
        "rule_mid_uppercase",
        "rule_bigram_run",
        "rule_fragment_tokens",
        "rule_forgiven_headline",
    ]
)

# There used to be a _DETERMINE_RULES / _PENALTY_RULES split here, partitioning
# RULES on a "penalty_" prefix. Both were dead code and one was a lie: the
# penalties were renamed to rule_* long ago, so _PENALTY_RULES had been the
# empty list ever since, and _DETERMINE_RULES was just RULES again under another
# name. The same stale prefix survived in the two ablation drivers, where it did
# real damage -- see the comment above run_ablation_study.RULES_TO_ABLATE.

# Columns widths for terminal output
_W_NAME = 34
_W_COUNT = 10
_W_RATE = 10
_W_DEC = 10
_W_LOSS = 10
_W_CLASS = 17


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_dataframe(raw_path: str, gold_args=None) -> tuple[pd.DataFrame, Path]:
    """Load a single CSV or a directory of CSVs into one DataFrame.

    ``gold_args`` carries the gold flags (an argparse namespace, or None). This is step 1 of ``run_optim_pipeline.sh``
    and it was the one loader in the repository with no sidecar join, so a gold
    run would have reported coverage over an unannotated frame while every later
    stage scored against gold.
    """
    in_path = Path(raw_path)
    if in_path.is_dir():
        df = load_csvs(in_path)
    elif in_path.is_file():
        df = pd.read_csv(in_path, dtype=str, keep_default_na=False)
        df["_source_file"] = str(in_path.name)
        df["file"] = in_path.stem
    else:
        raise FileNotFoundError(f"Path not found: {in_path}")
    if gold_args is not None:
        df = attach_gold_sidecar_from_args(df, gold_args)
    return df, in_path


def _n_scored(df: pd.DataFrame) -> int:
    """Number of lines that pass through the scorer (excludes fast-track rows)."""
    if "categ" not in df.columns:
        return len(df)
    fast_track = df["categ"].isin(("Empty", "Non-text"))
    try:
        wc = pd.to_numeric(df.get("word_count", pd.Series(dtype=float)), errors="coerce").fillna(1)
        fast_track = fast_track & (wc == 0)
    except Exception:
        pass
    return int((~fast_track).sum())


# ---------------------------------------------------------------------------
# LOO decisive count
# ---------------------------------------------------------------------------


def _loo_metrics(
    df: pd.DataFrame,
    rule: str,
    expected_langs: list[str],
    known_bases: frozenset,
    constants: dict | None = None,
    gold_column: str | None = None,
    split_cascade: bool = False,
) -> dict[str, int | float | None]:
    """Leave-one-out metrics for a single disable of *rule*.

    Structural (always, and always self-referential — see the warning below):

    decisive_count — lines whose category changes vs. the stored categ when
                     this rule is removed (flip_count from evaluate_dataframe).
    clear_loss     — among those flips, how many go Clear → Trash / Non-text.

    ``constants`` is the resolved config for the run. Passing it matters: a rule
    is only DEAD or LOAD-BEARING *relative to a configuration*, and measuring
    that under the import-time defaults while the caller asked for another
    config answers a question nobody posed.

    SELF-REFERENCE, AND WHY ``gold_column`` IS NOT JUST FORWARDED
    ------------------------------------------------------------
    The two figures above are measured against the pipeline's OWN stored
    ``categ``. The offline re-score reproduces it exactly at the shipped config,
    so the baseline sits at ``flip_rate == 0`` by construction: these say how
    much a rule changes *what the pipeline currently outputs*, never whether the
    output is right. This file is what classifies rules DEAD / REDUNDANT-HERE /
    LOAD-BEARING and what ``RULE_COVERAGE.md`` cites as the retirement criterion,
    and it was the only one of the five ``evaluate_dataframe`` callers that never
    passed ``gold_category_column`` — while accepting ``--gold-sidecar`` and
    printing a reassuring "N labels matched" on the way past.

    The fix is not a forwarded keyword. With a gold column, ``flip_count`` counts
    DISAGREEMENTS WITH GOLD, not lines the rule moved, and the baseline is no
    longer zero — so forwarding it would have quietly redefined ``decisive_count``
    into a different quantity under the same name. Instead the structural pass is
    kept as-is and a second, gold-scored pass is added:

    gold_delta_macro_f1 — macro-F1 against gold with the rule removed, minus the
                          shipped pipeline's macro-F1 against gold. NEGATIVE means
                          removing the rule makes agreement with gold worse, i.e.
                          the rule earns its place on CORRECTNESS and not merely on
                          influence. POSITIVE means the corpus would agree with the
                          annotator better without it.
    gold_n              — annotated rows actually scored (the sidecar join is
                          partial by design; see tools/gold/GOLD.md).

    ``split_cascade`` adds a third pass with document post-processing disabled, to
    separate a rule's own per-line effect from the page-level cascade it triggers.
    That distinction is what makes ``decisive_count > fire_count`` readable: a rescue
    rule that stops firing pushes its line to Trash, the page's garbage ratio rises,
    and the page passes sweep the neighbours. It costs an extra full pass per rule,
    so it is opt-in.
    """
    out: dict[str, int | float | None] = {
        "gold_delta_macro_f1": None,
        "gold_n": None,
        "gold_clear_loss": None,
        "gold_clear_loss_baseline": None,
        "decisive_line": None,
        "decisive_cascade": None,
    }

    with override_constants({"DISABLED_RULES": frozenset([rule])}):
        metrics = evaluate_dataframe(
            df,
            constants=constants,
            expected_langs=expected_langs,
            known_bases=known_bases,
        )

        if gold_column:
            gold_metrics = evaluate_dataframe(
                df,
                constants=constants,
                expected_langs=expected_langs,
                known_bases=known_bases,
                gold_category_column=gold_column,
            )
            out["gold_delta_macro_f1"] = float(gold_metrics.get("gold_delta_macro_f1", 0.0))
            out["gold_n"] = int(gold_metrics.get("line_count", 0))
            # The gold pass already computed a confusion matrix against the HUMAN
            # labels and this function used to throw it away, keeping only the
            # macro-F1 delta. `clear_loss` below is therefore scored against the
            # pipeline's own `categ` even on a --gold-column run -- it counts lines
            # the pipeline CALLED Clear, not lines that ARE Clear, which is not the
            # question anyone reads that column for. Issue #30's next-step 3 ("every
            # DEAD / LOAD-BEARING / clear_loss verdict measured agreement with the
            # pipeline's own output; the plumbing is fixed") was half right: passing
            # the gold column fixed the macro-F1 and left this. Zero extra passes.
            gold_clear_row = gold_metrics.get("confusion", {}).get("Clear", {})
            out["gold_clear_loss"] = int(gold_clear_row.get("Trash", 0)) + int(gold_clear_row.get("Non-text", 0))

            # (#30 D36) `gold_clear_loss` above is an ABSOLUTE count in the arm
            # where this rule is disabled, and it used to be printed next to
            # `gold_delta_macro_f1`, which is a DELTA. Read together they invite
            # the wrong arithmetic: a rule reporting 40 may be contributing none
            # of those 40, and nothing in the output said which.
            #
            # The baseline it should be read against is already computed one
            # frame up -- `evaluate_dataframe` builds `baseline_vs_gold` from the
            # shipped stored labels against the same human labels -- and was
            # discarded here for the same reason the confusion matrix above was.
            # Taking it costs nothing; the pass has already run.
            baseline = gold_metrics.get("baseline_vs_gold") or {}
            baseline_clear_row = baseline.get("confusion", {}).get("Clear", {})
            if baseline_clear_row:
                out["gold_clear_loss_baseline"] = int(baseline_clear_row.get("Trash", 0)) + int(
                    baseline_clear_row.get("Non-text", 0)
                )

        if split_cascade:
            per_line = evaluate_dataframe(
                df,
                constants=constants,
                expected_langs=expected_langs,
                known_bases=known_bases,
                apply_postprocessing=False,
            )
            out["decisive_line"] = int(per_line["flip_count"])

    out["decisive_count"] = int(metrics["flip_count"])
    clear_row = metrics.get("confusion", {}).get("Clear", {})
    out["clear_loss"] = int(clear_row.get("Trash", 0)) + int(clear_row.get("Non-text", 0))
    if out["decisive_line"] is not None:
        # Not a subtraction of disjoint sets -- the per-line pass is scored against
        # the same stored categ, which is itself post-smoothing, so this is the
        # difference between two flip counts and not a partition. It is reported as
        # a residual for exactly that reason.
        out["decisive_cascade"] = int(out["decisive_count"]) - int(out["decisive_line"])
    return out


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _classify(fire_count: int, decisive_count: int, rule: str | None = None) -> str:
    """Classify one rule from its counts, and from whether it could fire at all.

    The `rule` argument is optional so the pure-counts contract this function
    had before (#30 D35) still holds for callers that only have numbers: with
    no name, the config gate cannot be consulted and the answer is the old one.
    """
    if rule is not None and rule_is_config_gated_off(rule) and fire_count == 0:
        # Ordered deliberately: the gate is only consulted when the count is
        # zero. A gated rule that somehow fired is a real finding and must not
        # be hidden behind its own flag.
        return "INERT"
    if fire_count == 0:
        return "DEAD"
    if decisive_count == 0:
        return "REDUNDANT-HERE"
    return "LOAD-BEARING"


# ---------------------------------------------------------------------------
# Gate-entry markers
# ---------------------------------------------------------------------------
#
# A rule whose `_fire()` sits at the ENTRY of a gate that always returns is not
# reporting how often it decided; it is reporting how large its population is.
# `rule_short_line` is the case that matters: gate 7 fires on entry for every
# `word_count <= 2` line and every branch below it returns, so on a corpus of
# archival tables it reads 44.6% of scored lines and sorts to the top of this
# table as if it were the hottest rule in the engine. It is not a rule
# temperature, it is the short-line population.
#
# Moving the `_fire()` call would not fix it -- the gate is a total function on
# its entry condition, so the count is the same wherever inside it the call
# sits. The fix is to say so in the output. `decisive_share` is the number that
# carries information for these rules.
#
# Kept as an explicit declaration rather than inferred, and pinned by
# tests/test_rule_coverage.py, so that a gate growing a fall-through path (which
# would make its fire count meaningful again) shows up as a failing test rather
# than as a quietly mislabelled row.
GATE_MARKER_RULES: frozenset[str] = frozenset({"rule_short_line"})


# ---------------------------------------------------------------------------
# Word-count breakdown (issue #30)
# ---------------------------------------------------------------------------

WC_BUCKETS = ("1", "2", "3", "4", "5+")


def _wc_bucket(wc: int) -> str:
    if wc <= 0:
        return "1"
    return str(wc) if wc <= 4 else "5+"


def run_wc_breakdown(
    raw_path: str,
    config_path: str | None = None,
    quiet: bool = False,
    gold_args=None,
) -> dict[str, dict[str, int]]:
    """Attribute every rule fire to the word count of the line that produced it.

    Why this exists
    ---------------
    @david-spacil observed in issue #30 that "59.4% of hard-sweep-family firings
    land exactly on `wc == 3`" and noted it was "not measured further". The
    observation matters because the short-line regimes are structurally
    different, not merely shorter: ``SHORT_PPL_CAP`` covers ``wc <= 2`` only, so
    at three tokens raw perplexity reaches the sweep for the first time. That,
    rather than "shorter is riskier", is what explains the error gradient the
    thread argued about. Nothing in the repository could reproduce the figure,
    so it stayed an anecdote.

    Method, and its one caveat
    --------------------------
    Each line is scored individually inside its own ``rule_fire_capture()``
    block, so a fire can be attributed to the line that caused it. This is the
    PER-LINE decision only: ``apply_document_postprocessing`` is not run, because
    page-level smoothing has no single owning line. Document post-processing
    changes the category of a substantial share of lines, so the categories here
    are not the pipeline's final answer -- the *fires* are, and those are what
    this reports.
    """
    df, in_path = _load_dataframe(raw_path, gold_args)
    resolved_config = config_path or str(_ROOT / "setup" / "config.txt")
    expected_langs, known_bases = _load_lang_config(resolved_config)
    constants = coerce_constants(read_config_constants(resolved_config))
    validate_constants(constants)

    from tools.recategorize_from_csv import _is_fast_track, _rescore_row  # noqa: PLC0415

    counts: dict[str, dict[str, int]] = {r: dict.fromkeys(WC_BUCKETS, 0) for r in RULES}
    lines_by_bucket: dict[str, int] = dict.fromkeys(WC_BUCKETS, 0)
    scored = 0

    with override_constants(constants):
        for _idx, row in df.iterrows():
            rd = row.to_dict()
            if _is_fast_track(rd):
                continue
            text = str(rd.get("text", "") or "")
            bucket = _wc_bucket(len(text.split()))
            lines_by_bucket[bucket] += 1
            scored += 1
            with rule_fire_capture() as fired:
                _rescore_row(rd, expected_langs, known_bases)
            for name in fired:
                if name in counts:
                    counts[name][bucket] += 1

    if not quiet:
        print(f"\n=== rule fires by word count (per-line; n_scored={scored:,}) ===")
        print(f"  {'rule':<34} " + " ".join(f"{b:>7}" for b in WC_BUCKETS) + f" {'total':>8}  {'peak':>6}")
        print("  " + "-" * 34 + "-" * (8 * len(WC_BUCKETS) + 18))
        for rule in RULES:
            row_counts = counts[rule]
            total = sum(row_counts.values())
            if not total:
                continue
            peak_bucket = max(WC_BUCKETS, key=lambda b: row_counts[b])
            peak_share = row_counts[peak_bucket] / total
            print(
                f"  {rule:<34} "
                + " ".join(f"{row_counts[b]:>7,}" for b in WC_BUCKETS)
                + f" {total:>8,}  {peak_bucket:>3} {peak_share:>5.0%}"
            )
        print("  " + "-" * 34 + "-" * (8 * len(WC_BUCKETS) + 18))
        print(f"  {'lines in bucket':<34} " + " ".join(f"{lines_by_bucket[b]:>7,}" for b in WC_BUCKETS))
        print(
            "\n  NOTE: per-line fires only -- document post-processing is not applied here,\n"
            "  so the categories these fires lead to are not the pipeline's final answer."
        )

    return counts


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------


def run_coverage(
    raw_path: str,
    config_path: str | None = None,
    output_path: str | None = None,
    quiet: bool = False,
    skip_loo: bool = False,
    gold_args=None,
    split_cascade: bool = False,
) -> dict[str, dict]:
    """Run coverage instrumentation + optional LOO analysis over *raw_path*.

    Parameters
    ----------
    raw_path:    Path to a CSV file or a directory of per-document CSVs.
    config_path: Optional path to config.txt INI.
    output_path: If given, write ``rule_coverage.json`` to this path.
    quiet:       Suppress the per-rule table.
    skip_loo:    Skip the LOO decisive-count pass (faster; coverage only).
    gold_args:   Namespace carrying --gold-column / --gold-sidecar. With a gold
                 column the LOO pass additionally scores each rule's removal
                 against the human labels; without one every figure it produces
                 is agreement with the pipeline's own output.
    split_cascade: Also measure each rule's per-line effect with document
                 post-processing disabled, so the page cascade can be separated
                 from the rule itself. One extra pass per rule.

    Returns
    -------
    dict mapping rule name → {fire_count, fire_rate, decisive_count,
                               clear_loss, class}.
    """
    df, in_path = _load_dataframe(raw_path, gold_args)
    resolved_config = config_path or str(_ROOT / "setup" / "config.txt")
    expected_langs, known_bases = _load_lang_config(resolved_config)

    # `--config` used to feed ONLY the language lists: this function never read
    # the file's constants, so every threshold came from whatever text_util
    # imported at start-up. Two runs with different `--config` files produced
    # byte-identical reports, and an INVALID config (one that makes
    # recategorize_from_csv raise) produced a clean table. Since this report is
    # what classifies rules DEAD / LOAD-BEARING and gates retirement decisions
    # in RULE_COVERAGE.md, it has to measure the configuration it was handed.
    constants = coerce_constants(read_config_constants(resolved_config))
    validate_constants(constants)

    n_total = len(df)
    n_scored = _n_scored(df)
    print(f"Loaded {n_total:,} lines ({n_scored:,} scored) from {in_path}")
    print(f"Config: {resolved_config}")

    # ------------------------------------------------------------------
    # Phase 1: fire-count capture
    # ------------------------------------------------------------------
    print("Phase 1 — fire-count pass …")
    with rule_fire_capture() as raw_counts:
        recategorize_dataframe(df, constants, expected_langs=expected_langs, known_bases=known_bases)

    # ------------------------------------------------------------------
    # Phase 2: LOO decisive count (one recategorize pass per rule)
    # ------------------------------------------------------------------
    gold_column = getattr(gold_args, "gold_column", None) if gold_args is not None else None
    gold_sidecar = getattr(gold_args, "gold_sidecar", None) if gold_args is not None else None
    n_annotated = 0
    if gold_column:
        try:
            n_annotated = int(annotated_mask(df, gold_column).sum())
        except KeyError:
            # evaluate_dataframe raises on this too; failing here is clearer and
            # happens before the multi-hour LOO pass rather than inside it.
            raise

    loo: dict[str, dict] = {}
    passes = 1 + (1 if gold_column else 0) + (1 if split_cascade else 0)
    if skip_loo:
        print("Phase 2 — LOO skipped (--skip-loo).")
        for rule in RULES:
            loo[rule] = {"decisive_count": 0, "clear_loss": 0, "gold_clear_loss": None}
    else:
        print(f"Phase 2 — LOO pass ({len(RULES)} rules × {passes} recategorize each) …")
        if gold_column:
            print(f"  scoring against gold column {gold_column!r} on {n_annotated:,} annotated row(s)")
        else:
            print(
                "  NOTE: no --gold-column. decisive_count / clear_loss are measured against the\n"
                "  pipeline's OWN stored categ, whose baseline is zero by construction. They say\n"
                "  how much each rule changes the current output, not whether it is right."
            )
        for i, rule in enumerate(RULES, 1):
            m = _loo_metrics(
                df,
                rule,
                expected_langs,
                known_bases,
                constants,
                gold_column=gold_column,
                split_cascade=split_cascade,
            )
            loo[rule] = m
            extra = ""
            if m.get("gold_delta_macro_f1") is not None:
                extra += f"  gold_dF1={m['gold_delta_macro_f1']:+.4f}"
            if m.get("gold_clear_loss") is not None:
                extra += f"  gold_clear_loss={m['gold_clear_loss']}"
            if m.get("decisive_line") is not None:
                extra += f"  line={m['decisive_line']}  cascade={m['decisive_cascade']}"
            print(
                f"  [{i:>2}/{len(RULES)}] {rule:<34} "
                f"decisive={m['decisive_count']}  clear_loss={m['clear_loss']}{extra}"
            )

    # ------------------------------------------------------------------
    # Assemble result dict
    # ------------------------------------------------------------------
    results: dict[str, dict] = {}
    for rule in RULES:
        fc = raw_counts.get(rule, 0)
        fr = fc / n_scored if n_scored > 0 else 0.0
        m = loo[rule]
        decisive = int(m["decisive_count"])
        closs = int(m["clear_loss"])
        cls = _classify(fc, decisive, rule)
        entry = {
            "fire_count": fc,
            "fire_rate": round(fr, 6),
            "decisive_count": decisive,
            "decisive_share": round(decisive / fc, 4) if fc else None,
            "clear_loss": closs,
            "class": cls,
            "gate_marker": rule in GATE_MARKER_RULES,
        }
        if cls == "INERT":
            entry["gated_by"] = CONFIG_GATED_RULES[rule]
        for key in (
            "decisive_line",
            "decisive_cascade",
            "gold_delta_macro_f1",
            "gold_n",
            "gold_clear_loss",
            "gold_clear_loss_baseline",
        ):
            if m.get(key) is not None:
                entry[key] = m[key]
        results[rule] = entry

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    if not quiet:
        _print_table(results, n_scored)

    _print_summary(results)

    if output_path:
        payload = {
            "input": str(in_path),
            "n_lines": n_total,
            "n_scored": n_scored,
            # Provenance, so a report can never again LOOK like a gold run while
            # being scored against the pipeline's own output. `gold_column: null`
            # is the honest reading of every figure below as self-referential.
            "gold_column": gold_column,
            "gold_sidecar": str(gold_sidecar) if gold_sidecar else None,
            "gold_annotated_rows": n_annotated if gold_column else 0,
            # `decisive_count` is ALWAYS scored against the stored `categ`, with or
            # without a gold column, and that is correct: it measures how many lines
            # a rule changes, which is a property of the rule and not a claim about
            # truth. DEAD / REDUNDANT-HERE / LOAD-BEARING inherit that and need
            # nothing else. This field used to read "gold" on a --gold-column run,
            # which mislabelled the one thing it exists to label honestly.
            "decisive_scored_against": "stored categ (self-referential by design: change magnitude, not correctness)",
            # `clear_loss` IS a correctness claim, so it gets its own provenance and
            # a gold-scored twin. Read `gold_clear_loss` when it is present.
            "clear_loss_scored_against": "stored categ (self-referential)",
            "gold_clear_loss_available": bool(gold_column),
            "cascade_split": bool(split_cascade),
            "config": resolved_config,
            "rules": results,
        }
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nJSON written → {out}")

    return results


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _print_table(results: dict[str, dict], n_scored: int) -> None:
    sep = "-" * (_W_NAME + _W_COUNT + _W_RATE + _W_DEC + _W_LOSS + _W_CLASS + 15)
    hdr = (
        f"  {'Rule / Penalty':<{_W_NAME}}"
        f" | {'fire_count':>{_W_COUNT}}"
        f" | {'fire_rate':>{_W_RATE}}"
        f" | {'decisive':>{_W_DEC}}"
        f" | {'dec/fire':>7}"
        f" | {'clr_loss':>{_W_LOSS}}"
        f" | {'class':<{_W_CLASS}}"
    )
    print(f"\n=== Rule Coverage Report (n_scored={n_scored:,}) ===")
    print(hdr)
    print(sep)

    for section_label, section_rules in [
        ("— determine_category rules —", RULES),
    ]:
        print(f"\n  {section_label}")
        for rule in section_rules:
            r = results[rule]
            flag = "  ← DEAD" if r["class"] == "DEAD" else ""
            if r["class"] == "INERT":
                flag = f"  ← INERT ({r.get('gated_by', 'flag')} is off — not a retirement signal)"
            if r.get("gate_marker"):
                flag += "  ← gate marker (fire_count is a population size)"
            share = r.get("decisive_share")
            share_txt = f"{share:>7.1%}" if share is not None else f"{'--':>7}"
            print(
                f"  {rule:<{_W_NAME}}"
                f" | {r['fire_count']:>{_W_COUNT}}"
                f" | {r['fire_rate']:>{_W_RATE}.4f}"
                f" | {r['decisive_count']:>{_W_DEC}}"
                f" | {share_txt}"
                f" | {r['clear_loss']:>{_W_LOSS}}"
                f" | {r['class']:<{_W_CLASS}}{flag}"
            )
            if r.get("decisive_line") is not None:
                print(
                    f"  {'':<{_W_NAME}} | per-line {r['decisive_line']:,}  smoothing residual {r['decisive_cascade']:,}"
                )
            if r.get("gold_delta_macro_f1") is not None:
                verdict = (
                    "worse without it"
                    if r["gold_delta_macro_f1"] < 0
                    else ("better without it" if r["gold_delta_macro_f1"] > 0 else "no gold effect")
                )
                gold_loss = r.get("gold_clear_loss")
                gold_base = r.get("gold_clear_loss_baseline")
                if gold_loss is None:
                    loss_txt = ""
                elif gold_base is None:
                    loss_txt = f"  gold clear_loss {gold_loss:,}"
                else:
                    # Absolute, then the delta against the shipped configuration,
                    # because the delta is the thing a reader wants and the
                    # absolute is the thing the arm measures (#30 D36).
                    loss_txt = f"  gold clear_loss {gold_loss:,} (baseline {gold_base:,}, {gold_loss - gold_base:+d})"
                print(
                    f"  {'':<{_W_NAME}} | vs gold: ΔmacroF1 {r['gold_delta_macro_f1']:+.4f}"
                    f" on {r['gold_n']:,} row(s) — {verdict}{loss_txt}"
                )
    print()


def _print_summary(results: dict[str, dict]) -> None:
    dead = [r for r, v in results.items() if v["class"] == "DEAD"]
    redund = [r for r, v in results.items() if v["class"] == "REDUNDANT-HERE"]
    bearing = [r for r, v in results.items() if v["class"] == "LOAD-BEARING"]
    inert = [r for r, v in results.items() if v["class"] == "INERT"]

    print(
        f"Summary: {len(bearing)} LOAD-BEARING  |  {len(redund)} REDUNDANT-HERE  |  "
        f"{len(dead)} DEAD  |  {len(inert)} INERT"
    )

    if inert:
        print("\nINERT rules (fire_count == 0 because a config flag is off — NOT retirement candidates):")
        for r in inert:
            print(f"  - {r}  (gated by {results[r].get('gated_by', '?')})")
        print(
            "\n  These cannot fire on any corpus while their flag is false, so a zero here\n"
            "     carries no information about the rule's population. To measure one, re-run\n"
            "     with the flag on. See tools/RULE_COVERAGE.md for why this is separate from DEAD."
        )

    if dead:
        print("\nDEAD rules (fire_count == 0 — safe to retire after full-corpus confirmation):")
        for r in dead:
            print(f"  - {r}")
        print(
            "\n  ⚠  A rule dead on the smoke fixture may still fire on unseen documents.\n"
            "     Run on the full corpus on the cluster before deleting. See\n"
            "     tools/RULE_COVERAGE.md for the retirement criterion."
        )
    else:
        print("\nAll rules fired at least once — no dead code detected on this dataset.")

    if redund:
        print("\nREDUNDANT-HERE rules (fire_count > 0, decisive_count == 0 — keep; entanglement suspected):")
        for r in redund:
            print(f"  - {r}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="rule_coverage_report.py",
        description=(
            "Rule-fire coverage + LOO decisive-count report (B5). "
            "Classifies each rule as DEAD / REDUNDANT-HERE / LOAD-BEARING / INERT."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "path",
        nargs="?",
        metavar="PATH",
        help="CSV file or directory of per-document CSVs.",
    )
    ap.add_argument(
        "--input-dir", dest="input_dir", metavar="DIR", help="Alias for the positional PATH (directory form)."
    )
    ap.add_argument("--config", metavar="FILE", help="config.txt-style INI.  Default: <repo>/config.txt.")
    ap.add_argument(
        "--output", metavar="JSON_FILE", help="Write full results to this JSON file (e.g. rule_coverage.json)."
    )
    ap.add_argument(
        "--skip-loo", action="store_true", help="Skip the LOO decisive-count pass; report fire counts only."
    )
    ap.add_argument("--quiet", "-q", action="store_true", help="Suppress the per-rule table; only print the summary.")
    ap.add_argument(
        "--by-wc",
        action="store_true",
        help=(
            "Instead of the coverage table, attribute every rule fire to the word count of the "
            "line that caused it (issue #30). Per-line only: document post-processing is not applied."
        ),
    )
    ap.add_argument(
        "--split-cascade",
        dest="split_cascade",
        action="store_true",
        help=(
            "Also measure each rule's per-line effect with document post-processing disabled, so "
            "the page cascade it triggers can be separated from the rule itself. This is what makes "
            "decisive_count > fire_count readable. Costs one extra pass per rule."
        ),
    )
    add_gold_column_argument(ap)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raw_path = args.path or args.input_dir
    if not raw_path:
        print(
            "error: provide a path to a CSV file or a directory via the positional argument or --input-dir.",
            file=sys.stderr,
        )
        return 2

    if args.by_wc:
        try:
            counts = run_wc_breakdown(raw_path=raw_path, config_path=args.config, quiet=args.quiet, gold_args=args)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.output:
            Path(args.output).write_text(json.dumps(counts, indent=2), encoding="utf-8")
            print(f"\nJSON written → {args.output}")
        return 0

    try:
        results = run_coverage(
            raw_path=raw_path,
            config_path=args.config,
            output_path=args.output,
            quiet=args.quiet,
            skip_loo=args.skip_loo,
            gold_args=args,
            split_cascade=args.split_cascade,
        )
    except (FileNotFoundError, ValueError) as exc:
        # ValueError is the gold-sidecar join refusing a zero-match or a
        # malformed sidecar. An operator running this as stage 1 of
        # run_optim_pipeline.sh needs the reason, not a traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # INERT is deliberately not in this list (#30 D35): a rule that cannot fire
    # because its flag ships off is not a finding, and making the tool exit
    # non-zero for it would fail any pipeline driver that runs at the shipped
    # configuration.
    dead_rules = [r for r, v in results.items() if v["class"] == "DEAD"]
    return 1 if dead_rules else 0


if __name__ == "__main__":
    sys.exit(main())
