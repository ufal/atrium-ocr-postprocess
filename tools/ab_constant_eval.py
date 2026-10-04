#!/usr/bin/env python3
"""
tools/ab_constant_eval.py
=========================
A/B (or N-way) comparison of a single config constant against the frozen
ground-truth categories.

Unlike the importance sweep (which samples the whole space) this tool answers one
focused question: "if I move constant X to value V, how does agreement with the
stored categories change?" It runs the real production engine via
``evaluate_dataframe`` -- the same path as ``recategorize_from_csv`` -- so every
number is measured against the immutable ``categ`` ground truth.

Primary use: validate the importance sweep's one substantive high-impact
suggestion, ``CATEG_GARBAGE_DENSITY_HIGH`` 0.35 -> ~0.55, before touching the
production default.

CAVEAT: ``CATEG_GARBAGE_DENSITY_HIGH`` is reused in three places -- the hard
``rule_garbage_density`` gate, the quality-score garbage normalisation, and the
short-line penalty -- so moving it changes all three at once. Read the deltas
with that coupling in mind.

Example
-------
    python tools/ab_constant_eval.py \\
        --input-dir data_samples/DOC_LINE_CATEG --config setup/config.txt \\
        --const CATEG_GARBAGE_DENSITY_HIGH --values 0.35,0.55
"""

import argparse
import csv
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR.parent))

from recategorize_from_csv import (  # noqa: E402
    GOLD_SIDECAR_KEYS,
    _load_lang_config,
    add_gold_column_argument,
    annotated_mask,
    attach_gold_sidecar_from_args,
    evaluate_dataframe,
    load_csvs,
    normalize_category,
    read_config_constants,
)


def _clear_loss(metrics: Dict[str, Any]) -> int:
    """True Clear -> Trash/Non-text count vs. ground truth (from the confusion matrix)."""
    clear_row = metrics.get("confusion", {}).get("Clear", {})
    return int(clear_row.get("Trash", 0)) + int(clear_row.get("Non-text", 0))


def _clear_demoted(metrics: Dict[str, Any]) -> int:
    """Gold Clear lines the arm sends to ANY other class (from the confusion matrix).

    ``_clear_loss`` counts only Trash and Non-text, which is the gate's definition and
    stays the gate. A rule that answers ``Noisy`` -- the D47 floor -- cannot move it,
    so for such a rule "Clear-loss does not rise" is true before any data arrives
    (issue #3, stage 13: ``Svatoslavova`` was a gold-Clear line demoted by the floor
    while the table said ``Clear-loss +0``). This is the number that does move; it is
    REPORTED, not gated, because moving a doubtful Clear line to Noisy is the cheapest
    kind of change and the gate was set for the expensive kind.
    """
    clear_row = metrics.get("confusion", {}).get("Clear", {})
    return sum(int(v) for k, v in clear_row.items() if k != "Clear")


def _live_flag_value(const_name: str) -> bool | None:
    """The running value of a boolean flag, or None when it is not a boolean.

    ``read_config_constants`` returns the numeric keys only, so ``--const
    SHORT_GARBAGE_WITNESS_ENABLE`` printed ``(current config value: None)`` (stage 13a,
    12f, 13b-13e), which reads as "unset". The module attribute is the value the
    re-scorer starts from.
    """
    try:
        import text_util as _tu
    except ImportError:
        return None
    value = getattr(_tu, const_name, None)
    return value if isinstance(value, bool) else None


def _scored_row_count(df: Any, gold_column: str | None) -> int | None:
    """How many rows a gold-scored run actually scores, or None when it scores all of them."""
    if not gold_column:
        return None
    try:
        from recategorize_from_csv import annotated_mask

        return int(annotated_mask(df, gold_column).sum())
    except (KeyError, AttributeError, TypeError):
        return None


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value for discordant counts ``b`` and ``c``.

    Under the null the discordant pairs split 50/50, so the p-value is the
    two-sided binomial tail: ``2 * P(X <= min(b, c))`` for ``X ~ Bin(b + c, 0.5)``,
    clamped at 1.0 (the doubling overshoots when b == c).

    Exact rather than the chi-square approximation because the counts this issue
    deals in are single digits: stage 5a moved 14 lines one way and 2 the other,
    where the continuity-corrected chi-square is not trustworthy. No scipy --
    it is not a dependency of this repo and ``math.comb`` is enough.

    ``b + c == 0`` means the two arms agree on every scored row; the p-value is
    1.0 and the caller should say "identical", not quote a statistic.
    """
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(b, c) + 1))
    return min(1.0, 2.0 * tail / (2.0**n))


def _paired_counts(candidate: Any, incumbent: Any) -> Tuple[int, int]:
    """(b, c): rows the candidate fixes, and rows it breaks, against the incumbent.

    Both masks come from ``evaluate_dataframe(..., return_correctness=True)`` on the
    SAME frame, so they cover the same scored rows in the same order -- which is the
    whole reason this comparison can be paired rather than two independent rates.
    """
    b = int((candidate & ~incumbent).sum())
    c = int((incumbent & ~candidate).sum())
    return b, c


def _trash_counts(metrics: Dict[str, Any]) -> Tuple[int, int]:
    """(caught, support): ground-truth Trash lines still predicted Trash, and how many there are."""
    trash_row = metrics.get("confusion", {}).get("Trash", {})
    return int(trash_row.get("Trash", 0)), sum(int(v) for v in trash_row.values())


def _trash_recall(metrics: Dict[str, Any]) -> float:
    """Share of ground-truth Trash lines still predicted Trash (catches garbage leakage)."""
    caught, support = _trash_counts(metrics)
    return float(caught / support) if support else float("nan")


def wilson_interval(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval for k successes in n trials. Default z is 95%.

    Wilson rather than the normal approximation because the rates here sit near
    the ends of the range on a small n -- ``22/180`` -- where the textbook
    interval runs past zero and stops meaning anything.
    """
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _print_class_supports(metrics: Dict[str, Any]) -> None:
    """Print the gold class supports once, above the verdicts.

    THE DENOMINATOR IS NOT OPTIONAL. `Trash-recall` shipped as a bare four-decimal
    rate, and `0.1222` over this gold set is 22 of 180 -- but it is also exactly
    11 of 90, and nothing in the output said which. That is not a hypothetical
    ambiguity: a reader reconstructing the discordant counts from the recall delta
    picked the smaller denominator, halved b, and got an exact McNemar p of 0.18
    where the real figure is 0.004 -- an "underpowered, do not quote it" reading of
    a result that is significant at 0.01. One printed integer prevents it.

    The supports are also what makes `macro_f1` legible: it averages F1 over five
    classes regardless of their size, so on this set one gold-Trash line moves it
    roughly as much as seven gold-Clear lines. That is why `macro_f1` and
    `Clear-loss` disagreed on every arm of issue #30 stage 5, and why the gate
    above is built on errors/cost/Clear-loss instead.
    """
    supports = metrics.get("per_class_support") or {}
    if not supports:
        return
    total = sum(supports.values())
    if not total:
        return
    print("\n  Gold class support (what every rate below divides by):")
    for label in sorted(supports, key=lambda lab: -supports[lab]):
        n = int(supports[label])
        print(f"    {label:10} {n:6,}  ({n / total:5.1%})")
    print(f"    {'TOTAL':10} {int(total):6,}")


def _refuses_dump_inside_input(dump_path: Path, input_dir: Path) -> bool:
    """Refuse to write the dump anywhere `--input-dir` will glob it back. True == refused.

    `load_csvs(..., recursive=True)` takes every `*.csv` under the input directory,
    and the loader admits any file carrying both `text` and `categ` -- which the
    dump does, because those are the columns that make it readable. Writing it
    into the corpus therefore adds phantom lines to the NEXT run: measured on the
    15-line fixture, a second invocation loaded 16 rows and every support, rate
    and interval shifted with it, silently.

    This is the same defect `tools/short_garbage_witness_report.py::_refuses_gold_dir`
    exists for, one directory along. Refusing is cheap; a quietly reweighted
    objective is not.
    """
    try:
        dump_res, input_res = dump_path.resolve(), input_dir.resolve()
    except OSError:
        return False
    if input_res not in dump_res.parents:
        return False
    print(
        f"error: refusing to write {dump_path.name} inside --input-dir ({input_res}).\n"
        f"       That directory is globbed recursively for *.csv and this file has\n"
        f"       `text` and `categ` columns, so the next run would load it as corpus\n"
        f"       rows. Write it somewhere outside the corpus instead.",
        file=sys.stderr,
    )
    return True


def _isin(labels: Any, wanted: Tuple[str, ...]) -> Any:
    """Elementwise ``label in wanted`` over a numpy array of category strings."""
    import numpy as np

    return np.isin(labels, list(wanted))


def _dump_discordant(
    path: Path,
    df: Any,
    rows: List[Dict[str, Any]],
    gold_column: str,
) -> None:
    """Write the rows behind `fixes N, breaks M` — the ones the arms disagree on.

    The paired test reports HOW MANY rows a candidate fixes and breaks and never
    which, and on this issue's gold set those counts are single digits: stage 5a
    is 14 and 1. Identifying the one broken row had to be done by elimination
    against the witness's own candidate queue, which only worked because that
    queue happened to exist. This makes it a column instead.

    That mattered concretely. Stage 5a's single break turned out NOT to be a line
    the rule under test fires on at all -- it is produced by the modal dedup in
    ``apply_document_postprocessing``, which rewrites every occurrence of a
    repeated string in a document to that string's majority category. A rule that
    convicts a few occurrences can therefore flip the vote and carry a `Clear`
    line with it. `--no-postprocessing` is the other half of telling those apart.

    One row per (arm, discordant row). `direction` is `fix` when the arm is right
    where the reference is wrong and `break` the other way round, so the two
    counts in the verdict line are recoverable by grouping on it.

    A correctness mask cannot see a line that is wrong in BOTH arms, and such a line
    can still move the gate: issue #3 stage 14d raised Clear-loss by one with a single
    discordant row, and that row was a gold-Noisy break -- the Clear-loss line was a
    gold-Clear line wrong either way, moved Noisy -> Trash, present in no file. Those
    rows are therefore listed too, as `direction = clear_loss` (gold Clear, newly sent
    to Trash/Non-text by the arm, correctness unchanged). `ref_pred` and `arm_pred`
    carry each arm's own label on every row; `categ` is the STORED label, not either
    arm's output.
    """
    ref = rows[0]
    if ref.get("correct_mask") is None:
        print("  (no correctness masks — nothing to dump)", file=sys.stderr)
        return

    # `correct_mask` is ordered over the SCORED rows: `evaluate_dataframe` keeps
    # `df.loc[annotated_mask(...)]` in frame order, so the same mask indexes back.
    scored = df.loc[annotated_mask(df, gold_column)]
    locator_cols = [c for c in GOLD_SIDECAR_KEYS if c in scored.columns]
    carry = locator_cols + [c for c in ("text", "categ", gold_column) if c in scored.columns]
    ref_pred = ref.get("predicted")
    gold = scored[gold_column].map(normalize_category).to_numpy() if gold_column in scored.columns else None
    demoting = ("Trash", "Non-text")

    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    kinds: Dict[str, int] = {}
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["arm", "reference", "direction", *carry, "ref_pred", "arm_pred"])
        for r in rows[1:]:
            mask = r.get("correct_mask")
            if mask is None:
                continue
            arm_pred = r.get("predicted")
            selections = [("fix", mask & ~ref["correct_mask"]), ("break", ref["correct_mask"] & ~mask)]
            if gold is not None and ref_pred is not None and arm_pred is not None:
                lost = (gold == "Clear") & _isin(arm_pred, demoting) & ~_isin(ref_pred, demoting)
                selections.append(("clear_loss", lost & (mask == ref["correct_mask"])))
            for direction, sel in selections:
                positions = sel.nonzero()[0]
                for pos in positions:
                    row = scored.iloc[pos]
                    labels = (
                        ref_pred[pos] if ref_pred is not None else "",
                        arm_pred[pos] if arm_pred is not None else "",
                    )
                    writer.writerow([r["value"], ref["value"], direction, *(row[c] for c in carry), *labels])
                    written += 1
                    kinds[direction] = kinds.get(direction, 0) + 1
    detail = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
    print(f"\n  wrote {written} discordant row(s) to {path}" + (f" ({detail})" if detail else ""))
    print("     `break` rows are the ones the adoption gate turns on. If a break is not a line")
    print("     the rule fires on, look at the modal dedup in apply_document_postprocessing()")
    print("     and re-run with --no-postprocessing.")


def _print_gold_verdict(rows: List[Dict[str, Any]], gold_column: str, margin: float = 0.0) -> None:
    """Report each trial against gold, and against the shipped labels' own gold score.

    Mirrors ``tools/quality_model/evaluate.py::gold_gate()``: a candidate passes only
    when it is at least as good as the incumbent on human labels. Beating the
    incumbent on the incumbent's own labels is not evidence of anything.
    """
    baseline = next((r["baseline_vs_gold_macro_f1"] for r in rows if r["baseline_vs_gold_macro_f1"] is not None), None)
    print(f"\nScored against gold column {gold_column!r}.")
    if baseline is None:
        print("  No stored `categ` column alongside gold -- cannot compare against the shipped labels.")
        return
    print(f"  Shipped labels vs gold: macro_f1={baseline:.4f}")
    supports = next((r.get("per_class_support") for r in rows if r.get("per_class_support")), None)
    if supports:
        _print_class_supports({"per_class_support": supports})
    # The reference Clear-loss is the incumbent's: the first trial whose value
    # matches the shipped config, else the first row. `--values false,true` puts
    # the incumbent first by convention, so always pass the shipped value first.
    base_loss = rows[0]["clear_loss"] if rows else 0

    base_errors = rows[0].get("errors") if rows else None
    base_cost = rows[0].get("costed_score") if rows else None
    incumbent_mask = rows[0].get("correct_mask") if rows else None

    for r in rows:
        delta = r["macro_f1"] - baseline
        extra_loss = r["clear_loss"] - base_loss
        extra_errors = (
            (r["errors"] - base_errors) if (base_errors is not None and r.get("errors") is not None) else None
        )
        extra_cost = (
            (r["costed_score"] - base_cost) if (base_cost is not None and r.get("costed_score") is not None) else None
        )

        # macro_f1 is NOT the gate, and used to be. On an imbalanced gold set it
        # rewards moving the decision boundary toward the rare class whatever that
        # does to net accuracy: issue #30 stage 5c scored the BEST macro_f1 on the
        # board (+0.0397) while getting 8 MORE lines wrong than changing nothing
        # and costing 12% more, and this function called it "better on gold".
        # A candidate must not increase total errors, operational cost, or
        # Clear-loss. macro_f1 stays in the table as a diagnostic.
        worsens = [
            name
            for name, value in (("errors", extra_errors), ("cost", extra_cost), ("Clear-loss", extra_loss))
            if value is not None and value > 0
        ]
        # Paired stats first: an ADOPT-CANDIDATE that rests on three rows should
        # say so ON the verdict line, not two lines below it where a reader
        # quoting the headline will miss it.
        paired = None
        if incumbent_mask is not None and r.get("correct_mask") is not None and r is not rows[0]:
            b, c = _paired_counts(r["correct_mask"], incumbent_mask)
            paired = (b, c, mcnemar_exact(b, c))

        if not worsens and delta > margin:
            verdict = "ADOPT-CANDIDATE"
            if paired is not None and paired[2] >= 0.05:
                verdict += f" (NOT SIGNIFICANT: n={paired[0] + paired[1]}, p={paired[2]:.3g})"
        elif not worsens and delta >= -margin:
            verdict = "parity"
        elif not worsens:
            verdict = "REGRESSION"
        elif delta > margin:
            verdict = f"REJECT - macro_f1 rises but {', '.join(worsens)} worsen"
        else:
            verdict = f"REGRESSION - {', '.join(worsens)} worsen"

        bits = [f"macro_f1={r['macro_f1']:.4f} ({delta:+.4f} vs shipped)"]
        if r.get("errors") is not None:
            bits.append(f"errors={r['errors']:,}" + (f" ({extra_errors:+,})" if extra_errors else ""))
        bits.append(f"Clear-loss={r['clear_loss']:,}")
        if r.get("clear_support"):
            bits.append(f"Clear-demoted={r['clear_demoted']:,}/{r['clear_support']:,}")
        if extra_cost is not None:
            bits.append(f"cost={r['costed_score']:.4f} ({extra_cost:+.4f})")
        print(f"  {r['value']}: " + "  ".join(bits) + f"  -> {verdict}")

        # The paired test the closing line used to merely ask for. b + c is also
        # the number of rows this comparison actually rests on, which is the figure
        # that should stop a four-decimal macro_f1 being quoted off nineteen lines.
        if paired is not None:
            b, c, p = paired
            if b + c == 0:
                print(f"       paired vs {rows[0]['value']}: identical on every scored row")
            else:
                sig = "  significant at 0.05" if p < 0.05 else ""
                print(
                    f"       paired vs {rows[0]['value']}: fixes {b}, breaks {c} "
                    f"(effective n={b + c})  exact McNemar p={p:.4g}{sig}"
                )

    print(
        "  A candidate is only worth adopting when it beats the shipped labels against gold "
        "AND raises neither total errors, operational cost, nor Clear-loss."
    )
    print(
        "  Read `effective n` before the macro_f1 delta: it is how many scored rows the two "
        "arms actually disagree on, and it is usually far smaller than the gold set."
    )
    print(
        "  Clear-loss counts gold Clear sent to Trash/Non-text only; Clear-demoted counts gold Clear "
        "sent to ANY other class (reported, not gated). A rule that answers Noisy only cannot move "
        "Clear-loss."
    )

    # Trash-recall is the figure most often quoted out of this table, and on a
    # support in the low hundreds its interval is wide enough that two arms which
    # look ordered are not. Printing it beside the point estimate stops the table
    # being read as a ranking.
    if any(r.get("trash_support") for r in rows):
        print("\n  Trash-recall with a 95% Wilson interval (overlapping intervals are not a ranking):")
        for r in rows:
            support = r.get("trash_support") or 0
            if not support:
                continue
            lo, hi = wilson_interval(r["trash_caught"], support)
            print(
                f"    {str(r['value']):>10}: {r['trash_caught']:4d}/{support:<4d} = "
                f"{r['trash_recall']:6.1%}   95% CI [{lo:5.1%}, {hi:5.1%}]"
            )


_TRUEISH = {"true", "1", "yes", "on"}
_FALSEISH = {"false", "0", "no", "off"}


def _parse_values(raw: str) -> List[Any]:
    """Parse --values as numbers OR booleans.

    This used to be ``[float(v) for v in ...]``, which meant the tool could not
    express the one kind of constant the #30 decision actually turns on. Both
    `SHORT_GARBAGE_WITNESS_ENABLE` and `SHORT_GARBAGE_LEXICON_CONVICT` are flags,
    and "measure it against gold before flipping it" was the standing instruction
    for both -- with no way to say `--values false,true` to the tool written for
    exactly that comparison.

    `0`/`1` stay numeric: they are ambiguous, and a constant that is genuinely
    numeric is the commoner case. Spell a flag `false,true`.
    """
    out: List[Any] = []
    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        lowered = token.lower()
        if lowered in _TRUEISH - {"1"}:
            out.append(True)
        elif lowered in _FALSEISH - {"0"}:
            out.append(False)
        else:
            try:
                out.append(float(token))
            except ValueError as exc:
                raise ValueError(
                    f"--values entry {token!r} is neither a number nor a boolean "
                    f"({'/'.join(sorted(_TRUEISH | _FALSEISH))})"
                ) from exc
    return out


def run_ab(
    df,
    const_name: str,
    values: List[Any],
    base_constants: Dict[str, Any],
    eval_kwargs: Dict[str, Any],
    dump_discordant: Path | None = None,
) -> None:
    base_value = base_constants.get(const_name)
    if base_value is None:
        base_value = _live_flag_value(const_name)
    n_lines = len(df)
    gold_column = eval_kwargs.get("gold_category_column")
    scored = _scored_row_count(df, gold_column)
    # Say what n is. The header used to read "over 12,716,706 lines" above a table
    # in which EVERY figure was computed on the 2,064 annotated rows, because with
    # a gold column `evaluate_dataframe` scores the annotated subset only. Reading
    # `trash_rate` there as a corpus Trash rate is reading gold-subset data.
    scope = f"over {n_lines:,} lines"
    if scored is not None and scored != n_lines:
        scope += f" ({scored:,} of them scored against gold -- every figure below is on those rows)"
    print(f"A/B on `{const_name}` {scope} (current config value: {base_value})\n")

    rows: List[Dict[str, Any]] = []
    for value in values:
        trial = {**base_constants, const_name: value}
        metrics = evaluate_dataframe(df, trial, **{**eval_kwargs, "return_correctness": True})
        mask = metrics.get("correct_mask")
        rows.append(
            {
                "value": value,
                "correct_mask": mask,
                "errors": (int((~mask).sum()) if mask is not None else None),
                "macro_f1": float(metrics["macro_f1"]),
                "weighted_f1": float(metrics["weighted_f1"]),
                "costed_score": float(metrics["costed_score"]),
                "flip_rate": float(metrics["flip_rate"]),
                "trash_rate": float(metrics["trash_rate"]),
                "clear_rate": float(metrics["clear_rate"]),
                "kl": float(metrics["kl_divergence"]),
                "clear_loss": _clear_loss(metrics),
                "clear_demoted": _clear_demoted(metrics),
                "clear_support": int(sum(int(v) for v in metrics.get("confusion", {}).get("Clear", {}).values())),
                "predicted": metrics.get("predicted_labels"),
                "trash_recall": _trash_recall(metrics),
                "trash_caught": _trash_counts(metrics)[0],
                "trash_support": _trash_counts(metrics)[1],
                "per_class_support": metrics.get("per_class_support"),
                "baseline_vs_gold_macro_f1": (
                    float(metrics["baseline_vs_gold"]["macro_f1"]) if "baseline_vs_gold" in metrics else None
                ),
                "gold_delta_macro_f1": metrics.get("gold_delta_macro_f1"),
            }
        )

    # `errors` sits next to macro_f1 deliberately: it is the count macro_f1 can
    # move against. Issue #30 stage 5c had the best macro_f1 in its table and the
    # worst error count, and nothing on the row said so.
    show_errors = any(r.get("errors") is not None for r in rows)
    cols = (
        ["macro_f1"]
        + (["errors"] if show_errors else [])
        + [
            "weighted_f1",
            "costed_score",
            "flip_rate",
            "trash_rate",
            "clear_rate",
            "KL",
            "Clear-loss",
            "Trash-recall",
        ]
    )
    print(f"| {const_name} | " + " | ".join(cols) + " |")
    print("| " + " | ".join(["---"] * (len(cols) + 1)) + " |")
    for r in rows:
        cells = [f"{r['macro_f1']:.4f}"]
        if show_errors:
            cells.append(f"{r['errors']:,}" if r.get("errors") is not None else "-")
        cells += [
            f"{r['weighted_f1']:.4f}",
            f"{r['costed_score']:.4f}",
            f"{r['flip_rate']:.4f}",
            f"{r['trash_rate']:.4f}",
            f"{r['clear_rate']:.4f}",
            f"{r['kl']:.5f}",
            f"{r['clear_loss']:,}",
            # caught/support, not a bare rate: see _print_class_supports().
            f"{r['trash_recall']:.4f} ({r['trash_caught']}/{r['trash_support']})"
            if r.get("trash_support")
            else f"{r['trash_recall']:.4f}",
        ]
        print(f"| {r['value']} | " + " | ".join(cells) + " |")

    # Headline delta against the first value (treated as the reference).
    if len(rows) >= 2:
        ref, alt = rows[0], rows[-1]
        print(
            f"\nΔ ({alt['value']} vs {ref['value']}): "
            f"macro_f1 {alt['macro_f1'] - ref['macro_f1']:+.4f}, "
            + (
                f"errors {alt['errors'] - ref['errors']:+,}, "
                if alt.get("errors") is not None and ref.get("errors") is not None
                else ""
            )
            + f"costed_score {alt['costed_score'] - ref['costed_score']:+.4f}, "
            f"trash_rate {alt['trash_rate'] - ref['trash_rate']:+.4f}, "
            f"Clear-loss {alt['clear_loss'] - ref['clear_loss']:+d}"
        )
    if gold_column:
        _print_gold_verdict(rows, gold_column)
        if dump_discordant is not None:
            _dump_discordant(dump_discordant, df, rows, gold_column)
    else:
        if dump_discordant is not None:
            print(
                "\nNote: --dump-discordant needs --gold-column; without one there is no "
                "notion of a row being right or wrong to disagree about.",
                file=sys.stderr,
            )
        print(
            "\nNote: ground-truth flip_rate is ~0 at the current config by construction, so a "
            "non-zero flip_rate / macro_f1 < 1 here is deviation FROM the stored categories.\n"
            "These numbers cannot tell an improvement from a regression -- pass --gold-column "
            "to score against human labels instead (tools/GOLD.md).\n"
            "Run on the full DOC_LINE_CATEG corpus -- the bundled sample is a smoke fixture."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="A/B a single config constant vs. the stored categories.")
    parser.add_argument("--input-dir", type=Path, required=True, help="Directory of DOC_LINE_CATEG CSVs.")
    parser.add_argument("--config", type=str, default="setup/config.txt", help="setup/config.txt-style file.")
    parser.add_argument("--const", type=str, default="CATEG_GARBAGE_DENSITY_HIGH", help="Constant to vary.")
    parser.add_argument(
        "--values",
        type=str,
        default="0.35,0.55",
        help="Comma-separated values to test (first is the reference for deltas).",
    )
    parser.add_argument(
        "--dump-discordant",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Write the rows behind `fixes N, breaks M` to this CSV: locator, text, gold label, "
            "stored categ and direction. Needs --gold-column. The verdict counts them; this "
            "names them."
        ),
    )
    parser.add_argument(
        "--no-postprocessing",
        action="store_true",
        help=(
            "Score the per-line decision only, with apply_document_postprocessing() disabled. "
            "`evaluate_dataframe` has always taken this and no CLI exposed it except "
            "`rule_coverage_report --split-cascade`, which costs days. Run an arm both ways to "
            "separate a rule's own effect from the page cascade its convictions set off -- the "
            "modal dedup can move lines the rule never touched."
        ),
    )
    add_gold_column_argument(parser)
    args = parser.parse_args()

    try:
        values = _parse_values(args.values)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    if not values:
        print("error: provide at least one --values entry", file=sys.stderr)
        sys.exit(1)

    if args.dump_discordant is not None and _refuses_dump_inside_input(args.dump_discordant, args.input_dir):
        sys.exit(2)

    df = load_csvs(args.input_dir, recursive=True)
    df = attach_gold_sidecar_from_args(df, args)
    expected_langs, known_bases = _load_lang_config(args.config)
    base_constants = read_config_constants(args.config)
    eval_kwargs = {
        "expected_langs": expected_langs,
        "known_bases": known_bases,
        "gold_category_column": args.gold_column,
        "apply_postprocessing": not args.no_postprocessing,
    }
    if args.no_postprocessing:
        print(
            "apply_document_postprocessing() DISABLED — these are per-line figures. "
            "Compare them against the same A/B run without the flag; the difference is "
            "the page cascade.\n"
        )

    run_ab(df, args.const, values, base_constants, eval_kwargs, dump_discordant=args.dump_discordant)


if __name__ == "__main__":
    main()
