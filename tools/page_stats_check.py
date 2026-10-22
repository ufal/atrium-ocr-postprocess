#!/usr/bin/env python3
"""tools/page_stats_check.py -- a rerun of the page statistics, checked against the run it repeats (#8).

Why this exists
---------------
Issue #8 adds five category ratios to the page statistics (``RATIO_COLUMNS`` and
``EMPTY_RATIO`` in ``aggregate_STAT.py``). The corpus tables of 2026-10-12
(``page_stats_1210.csv`` and ``DOC_LINE_STATS_1210/`` in each collection folder) are
aggregated again from the same line tables (``DOC_LINE_CATEG_1210/``) by
``tools/page_stats_rerun.sh``. Only the five columns may differ. This tool checks that
and writes the report the issue asks for:

* the scope: the collection, the inputs, the output locations, the run and its config;
* documents and pages in the old and the new table, and the rows only one of them holds;
* every column of the old table, compared by name on the rows both hold (numbers to
  1e-9, text exactly, two empty cells are equal);
* the new table's columns, in ``PAGE_STATS_COLUMNS`` order;
* the ratios: in [0, 1] or empty, equal to the counts' quotients rounded to 4 places,
  and empty exactly where the denominator is 0;
* every input line table has its ``stats_<doc>.csv`` unless the run skipped it, nothing
  else is there, and the per-document files hold exactly the table's pages;
* the run's skips and errors, from its paradata.

Each check is PASS, WARN (reported, not a failure: an error the run logged) or FAIL.
Exit status: 0 without a FAIL, 1 with one (the report says which), 2 when an input is
missing. Run from the repository root, on one collection folder::

    python3 tools/page_stats_check.py /lnet/work/projects/atrium/ARUP --old 1210 --new 1210r \\
        --paradata paradata/261022-101500_ocr-postprocess.json \\
        --config /lnet/work/projects/atrium/ARUP/page_stats_1210r.config.txt

The report goes to ``<collection>/page_stats_<new>_report.md`` unless ``--report`` says
otherwise.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aggregate_STAT import EMPTY_RATIO, PAGE_STATS_COLUMNS, RATIO_COLUMNS  # noqa: E402

KEY = ["file", "page_num"]
NEW_COLUMNS = [*RATIO_COLUMNS, EMPTY_RATIO]
#: How many differing rows or documents the report lists for each finding.
EXAMPLES = 5
#: Skip reasons that mean the document failed, not that it was empty (aggregate_STAT.main).
ERROR_REASONS = ("Processing Error", "Hard Crash")


class Checks:
    def __init__(self):
        self.rows = []

    def add(self, name: str, status: str, detail: str = ""):
        self.rows.append((name, status, detail))

    def check(self, name: str, ok: bool, detail: str = "", fail: str = "FAIL"):
        self.add(name, "PASS" if ok else fail, detail)

    @property
    def failed(self) -> int:
        return sum(1 for _, status, _ in self.rows if status == "FAIL")

    @property
    def warned(self) -> int:
        return sum(1 for _, status, _ in self.rows if status == "WARN")


def read_table(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"file": str, "main_lang": str}, keep_default_na=True)


def _keys(df: pd.DataFrame) -> set:
    return set(zip(df["file"], df["page_num"].astype(int), strict=True))


def _example(keys) -> str:
    keys = sorted(keys)[:EXAMPLES]
    return ", ".join(f"{file} p.{page}" for file, page in keys)


def compare_columns(old: pd.DataFrame, new: pd.DataFrame) -> dict:
    """Per column of ``old`` (keys aside): how many shared rows differ, and a few of them."""
    joined = old.merge(new, on=KEY, how="inner", suffixes=("_old", "_new"))
    found = {}
    for column in old.columns:
        if column in KEY or column not in new.columns:
            continue
        a, b = joined[f"{column}_old"], joined[f"{column}_new"]
        if pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b):
            same = np.isclose(a.astype(float), b.astype(float), rtol=1e-9, atol=1e-12, equal_nan=True)
        else:
            same = (a.isna() & b.isna()) | (a.astype(str) == b.astype(str))
        differ = joined.loc[~np.asarray(same), [*KEY, f"{column}_old", f"{column}_new"]]
        found[column] = {
            "differ": len(differ),
            "examples": [tuple(row) for row in differ.head(EXAMPLES).itertuples(index=False)],
        }
    return {"shared_rows": len(joined), "columns": found}


def expected_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """The ratios ``aggregate_STAT._add_category_ratios`` defines, from the table's own counts."""
    total = df["num_lines"].astype(float)
    text = total - df["Empty"].astype(float)
    out = pd.DataFrame(index=df.index)
    for column, category in RATIO_COLUMNS.items():
        out[column] = (df[category].astype(float) / text.where(text > 0)).round(4)
    out[EMPTY_RATIO] = (df["Empty"].astype(float) / total.where(total > 0)).round(4)
    return out


def check_ratios(new: pd.DataFrame) -> dict:
    expected = expected_ratios(new)
    result = {}
    for column in NEW_COLUMNS:
        values = new[column].astype(float)
        outside = values.notna() & ((values < 0) | (values > 1))
        wrong = ~np.isclose(values, expected[column], rtol=0, atol=1e-9, equal_nan=True)
        result[column] = {
            "outside": int(outside.sum()),
            "wrong": int(wrong.sum()),
            "wrong_examples": _example(_keys(new.loc[wrong])),
            "empty": int(values.isna().sum()),
            "mean": float(values.mean()) if values.notna().any() else float("nan"),
        }
    text = new["num_lines"].astype(float) - new["Empty"].astype(float)
    shares = new[list(RATIO_COLUMNS)].astype(float).sum(axis=1)
    result["_off_one"] = int(((text > 0) & ((shares - 1).abs() > 2e-4)).sum())
    return result


def per_document(input_dir: Path, doc_dir: Path, skipped: set) -> dict:
    """The run's per-document files against its inputs, and the pages they hold."""
    inputs = {p.stem for p in input_dir.glob("*.csv")}
    outputs = {p.stem[len("stats_") :]: p for p in doc_dir.glob("stats_*.csv")}
    keys, rows = set(), 0
    for path in outputs.values():
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                rows += 1
                keys.add((row["file"], int(float(row["page_num"]))))
    return {
        "inputs": len(inputs),
        "outputs": len(outputs),
        "missing": sorted(inputs - set(outputs) - skipped),
        "unexpected": sorted(set(outputs) - inputs),
        "skipped_with_output": sorted(skipped & set(outputs)),
        "rows": rows,
        "keys": keys,
    }


def read_paradata(path) -> dict:
    if path is None:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run(collection: Path, old_tag: str, new_tag: str, paradata=None, config=None, input_dir=None) -> tuple:
    """Check one collection; return the report text and the number of failed checks."""
    old_stats = collection / f"page_stats_{old_tag}.csv"
    new_stats = collection / f"page_stats_{new_tag}.csv"
    old_docs = collection / f"DOC_LINE_STATS_{old_tag}"
    new_docs = collection / f"DOC_LINE_STATS_{new_tag}"
    input_dir = Path(input_dir) if input_dir else collection / f"DOC_LINE_CATEG_{old_tag}"
    for required in (old_stats, new_stats, new_docs, input_dir):
        if not required.exists():
            raise FileNotFoundError(f"{required}: not found")

    old, new = read_table(old_stats), read_table(new_stats)
    record = read_paradata(paradata)
    skips = record.get("skipped_files_detail", []) or []
    skipped = {Path(item.get("file", "")).stem for item in skips}
    errors = [item for item in skips if str(item.get("reason", "")).startswith(ERROR_REASONS)]
    checks = Checks()

    # rows
    old_keys, new_keys = _keys(old), _keys(new)
    duplicates = len(new) - len(new_keys)
    checks.check("one row per page in the new table", duplicates == 0, f"{duplicates} repeated page(s)")
    only_old, only_new = old_keys - new_keys, new_keys - old_keys
    checks.check(
        "the same pages in both tables",
        not only_old and not only_new,
        f"{len(only_old)} only in the old ({_example(only_old) or 'none'}); "
        f"{len(only_new)} only in the new ({_example(only_new) or 'none'})",
    )

    # columns
    dropped = [c for c in old.columns if c not in new.columns]
    added = [c for c in new.columns if c not in old.columns]
    checks.check("no column of the old table dropped", not dropped, ", ".join(dropped) or "none")
    unexpected = [c for c in added if c not in NEW_COLUMNS]
    checks.check(
        "only the five ratio columns added",
        not unexpected,
        f"added: {', '.join(added) or 'none'}" + (f"; unexpected: {', '.join(unexpected)}" if unexpected else ""),
    )
    checks.check(
        "the new table's columns in `PAGE_STATS_COLUMNS` order",
        list(new.columns) == PAGE_STATS_COLUMNS,
        "" if list(new.columns) == PAGE_STATS_COLUMNS else f"got {', '.join(new.columns)}",
    )
    compared = compare_columns(old, new)
    changed = {c: v["differ"] for c, v in compared["columns"].items() if v["differ"]}
    listed = ", ".join(f"{c} ({n})" for c, n in changed.items())
    checks.check(
        "every old column unchanged on the shared rows",
        not changed,
        f"{len(compared['columns'])} columns on {compared['shared_rows']} rows"
        + (f"; differ: {listed}" if changed else ""),
    )

    # ratios
    ratios = check_ratios(new) if all(c in new.columns for c in NEW_COLUMNS) else None
    if ratios is None:
        checks.add("the ratios", "FAIL", "the new table lacks them")
    else:
        outside = sum(ratios[c]["outside"] for c in NEW_COLUMNS)
        wrong = {c: ratios[c] for c in NEW_COLUMNS if ratios[c]["wrong"]}
        checks.check("every ratio in [0, 1] or empty", outside == 0, f"{outside} outside")
        checks.check(
            "every ratio equal to its counts' quotient, empty where the denominator is 0",
            not wrong,
            "; ".join(f"{c}: {v['wrong']} ({v['wrong_examples']})" for c, v in wrong.items()) or "all",
        )

    # per-document files
    docs = per_document(input_dir, new_docs, skipped)
    checks.check(
        "every input line table has its `stats_<doc>.csv` or a logged skip",
        not docs["missing"],
        f"{docs['outputs']} files for {docs['inputs']} inputs, {len(skipped)} skipped; "
        f"{len(docs['missing'])} missing ({', '.join(docs['missing'][:EXAMPLES]) or 'none'})",
    )
    checks.check(
        "no per-document file without an input, or for a skipped document",
        not docs["unexpected"] and not docs["skipped_with_output"],
        f"{len(docs['unexpected'])} without an input, {len(docs['skipped_with_output'])} for a skipped document",
    )
    checks.check(
        "the per-document files hold exactly the table's pages",
        docs["rows"] == len(new) and docs["keys"] == new_keys,
        f"{docs['rows']} rows in the files, {len(new)} in the table",
    )
    old_files = {p.stem[len("stats_") :] for p in old_docs.glob("stats_*.csv")} if old_docs.is_dir() else set()
    new_files = {p.stem[len("stats_") :] for p in new_docs.glob("stats_*.csv")}
    if old_docs.is_dir():
        checks.check(
            "the same per-document files as the old run",
            old_files == new_files,
            f"{len(old_files - new_files)} only in the old, {len(new_files - old_files)} only in the new",
        )

    # the run
    if record:
        stats = record.get("statistics", {})
        checks.check(
            "the run read every input line table",
            stats.get("input_files_total") == docs["inputs"],
            f"paradata: {stats.get('input_files_total')} inputs, {stats.get('successfully_processed')} processed, "
            f"{stats.get('skipped_files')} skipped",
        )
        checks.check("no document failed with an error", not errors, f"{len(errors)} error(s)", fail="WARN")
    else:
        checks.add("the run's paradata", "WARN", "not given: skips and errors are not checked")

    found = {
        "collection": collection,
        "old_tag": old_tag,
        "new_tag": new_tag,
        "input_dir": input_dir,
        "paradata": paradata,
        "config": config,
        "record": record,
        "old": old,
        "new": new,
        "docs": docs,
        "ratios": ratios,
        "compared": compared,
        "skips": skips,
        "errors": errors,
    }
    return report(found, checks), checks.failed


def _fmt(value, spec: str = ".10g") -> str:
    """A cell for the report: an empty value as a dash, a number to ``spec`` (enough digits to show a change)."""
    if isinstance(value, float):
        return "–" if np.isnan(value) else format(value, spec)
    return str(value)


def report(found: dict, checks: Checks) -> str:
    collection, old_tag, new_tag = found["collection"], found["old_tag"], found["new_tag"]
    old, new, docs, ratios, record = found["old"], found["new"], found["docs"], found["ratios"], found["record"]
    skips, errors = found["skips"], found["errors"]
    verdict = "FAIL" if checks.failed else "PASS"
    stamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Page statistics of `{collection.name}`: the #8 rerun checked against `{old_tag}`",
        "",
        f"**{verdict}**: {len(checks.rows)} checks, {checks.failed} failed, {checks.warned} warning(s). "
        f"Written by `tools/page_stats_check.py` on {stamp}.",
        "",
        "## Scope",
        "",
        "| | |",
        "|---|---|",
        f"| Collection | `{collection}` |",
        f"| Input | `{found['input_dir']}`: {docs['inputs']} line tables |",
        f"| Old output | `page_stats_{old_tag}.csv`, `DOC_LINE_STATS_{old_tag}/` |",
        f"| New output | `page_stats_{new_tag}.csv`, `DOC_LINE_STATS_{new_tag}/` ({docs['outputs']} files) |",
    ]
    if record:
        lines.append(
            f"| Run | `{found['paradata']}`: {record.get('program')} {record.get('tool_version')}, run {record.get('run_id')}, "
            f"{record.get('duration_seconds')} s |"
        )
    if found["config"]:
        lines.append(f"| Config | `{found['config']}` |")
    lines += ["", "## Checks", "", "| Check | Result | Detail |", "|---|---|---|"]
    lines += [f"| {name} | {status} | {detail} |" for name, status, detail in checks.rows]

    with_text = lambda df: int(((df["num_lines"] - df["Empty"]) > 0).sum())  # noqa: E731
    lines += [
        "",
        "## Counts",
        "",
        f"| | `{old_tag}` | `{new_tag}` |",
        "|---|---:|---:|",
        f"| Documents | {old['file'].nunique()} | {new['file'].nunique()} |",
        f"| Pages | {len(old)} | {len(new)} |",
        f"| Pages with a line that is not `Empty` | {with_text(old)} | {with_text(new)} |",
        f"| Pages without lines (`num_lines` 0) | {int((old['num_lines'] == 0).sum())} | "
        f"{int((new['num_lines'] == 0).sum())} |",
        f"| Lines | {int(old['num_lines'].sum())} | {int(new['num_lines'].sum())} |",
    ]
    if ratios is not None:
        lines += [
            "",
            "## The ratios in the new table",
            "",
            "| Column | Mean | Empty cells | Outside [0, 1] | Not the counts' quotient |",
            "|---|---:|---:|---:|---:|",
        ]
        lines += [
            f"| `{c}` | {_fmt(ratios[c]['mean'], '.4f')} | {ratios[c]['empty']} | {ratios[c]['outside']} | {ratios[c]['wrong']} |"
            for c in NEW_COLUMNS
        ]
        lines += [
            "",
            f"Pages whose four text shares do not sum to 1 (within 2e-4): {ratios['_off_one']}. A line whose",
            "category is outside `STANDARD_COLS` counts in `num_lines` but in none of the four.",
        ]
    changed = {c: v for c, v in found["compared"]["columns"].items() if v["differ"]}
    if changed:
        lines += [
            "",
            "## Columns that changed",
            "",
            "| Column | Rows | Examples (file, page, old, new) |",
            "|---|---:|---|",
        ]
        for column, found in changed.items():
            shown = "; ".join(f"{f} p.{p}: {_fmt(a)} → {_fmt(b)}" for f, p, a, b in found["examples"])
            lines.append(f"| `{column}` | {found['differ']} | {shown} |")
    lines += ["", "## Skips and errors", ""]
    if not record:
        lines.append("No paradata given.")
    elif not skips:
        lines.append("The run skipped no document.")
    else:
        reasons = Counter(str(item.get("reason", "")).split(":")[0] for item in skips)
        lines += [f"{len(skips)} skipped, {len(errors)} of them with an error:", ""]
        lines += [f"- {reason}: {count}" for reason, count in reasons.most_common()]
        lines += ["", "| Document | Reason |", "|---|---|"]
        lines += [f"| `{item.get('file')}` | {item.get('reason')} |" for item in skips[:20]]
        if len(skips) > 20:
            lines.append(f"| … | {len(skips) - 20} more in the paradata |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("collection", type=Path, help="the collection folder, e.g. /lnet/work/projects/atrium/ARUP")
    parser.add_argument("--old", default="1210", help="the run checked against (default 1210)")
    parser.add_argument("--new", default=None, help="the rerun (default <old>r)")
    parser.add_argument("--input-dir", type=Path, default=None, help="default <collection>/DOC_LINE_CATEG_<old>")
    parser.add_argument("--paradata", type=Path, default=None, help="the rerun's paradata JSON")
    parser.add_argument("--config", type=Path, default=None, help="the rerun's config, named in the report")
    parser.add_argument("--report", type=Path, default=None, help="default <collection>/page_stats_<new>_report.md")
    args = parser.parse_args(argv)
    new_tag = args.new or f"{args.old}r"
    try:
        text, failed = run(args.collection, args.old, new_tag, args.paradata, args.config, args.input_dir)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    out = args.report or args.collection / f"page_stats_{new_tag}_report.md"
    out.write_text(text, encoding="utf-8")
    print(f"{'FAIL' if failed else 'PASS'}: {failed} check(s) failed; report {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
