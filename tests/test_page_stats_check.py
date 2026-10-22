"""
tests/test_page_stats_check.py
==============================
``tools/page_stats_check.py`` -- the #8 rerun of the page statistics, checked against the run it repeats.

The collection is built from the shipped line tables: the "old" run is today's aggregation without the
five ratio columns, the "new" run is today's aggregation. What must not pass silently: a changed old
column, a ratio that is not its counts' quotient, a page or a per-document file that went missing, and
an error the run logged (a warning, not a failure).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aggregate_STAT import EMPTY_RATIO, RATIO_COLUMNS, process_csv_file, sort_page_stats  # noqa: E402
from tools import page_stats_check as psc  # noqa: E402

STANDARD = frozenset(["Clear", "Noisy", "Trash", "Non-text", "Empty"])
SAMPLES = _ROOT / "data_samples" / "DOC_LINE_CATEG"


def _aggregate(input_dir: Path, doc_dir: Path, table: Path, drop=()):
    doc_dir.mkdir()
    frames = []
    for path in sorted(input_dir.glob("*.csv")):
        result = process_csv_file(path, STANDARD).drop(columns=list(drop))
        result.to_csv(doc_dir / f"stats_{path.stem}.csv", index=False, encoding="utf-8")
        frames.append(result)
    sort_page_stats(pd.concat(frames, ignore_index=True)).to_csv(table, index=False, encoding="utf-8")


@pytest.fixture
def collection(tmp_path):
    coll = tmp_path / "ARUP"
    shutil.copytree(SAMPLES, coll / "DOC_LINE_CATEG_1210")
    inputs = coll / "DOC_LINE_CATEG_1210"
    _aggregate(inputs, coll / "DOC_LINE_STATS_1210", coll / "page_stats_1210.csv", drop=[*RATIO_COLUMNS, EMPTY_RATIO])
    _aggregate(inputs, coll / "DOC_LINE_STATS_1210r", coll / "page_stats_1210r.csv")
    paradata = tmp_path / "run.json"
    paradata.write_text(
        json.dumps(
            {
                "program": "ocr-postprocess",
                "tool_version": "1.12.1-beta",
                "run_id": "261022-101500",
                "duration_seconds": 1.5,
                "statistics": {"input_files_total": 3, "successfully_processed": 3, "skipped_files": 0},
                "skipped_files_detail": [],
            }
        ),
        encoding="utf-8",
    )
    return coll, paradata


def _edit(path: Path, edit):
    df = pd.read_csv(path, dtype={"file": str, "main_lang": str})
    edit(df)
    df.to_csv(path, index=False, encoding="utf-8")


def test_a_rerun_that_only_adds_the_ratios_passes_and_says_what_it_checked(collection):
    coll, paradata = collection
    assert psc.main([str(coll), "--paradata", str(paradata), "--config", "cfg.txt"]) == 0
    report = (coll / "page_stats_1210r_report.md").read_text(encoding="utf-8")
    assert report.splitlines()[2].startswith("**PASS**") and "0 failed" in report
    assert "| Input | " in report and "3 line tables" in report and "| Config | `cfg.txt` |" in report
    assert "| Documents | 3 | 3 |" in report and "| Pages | 7 | 7 |" in report
    assert "added: clear_rat, noisy_rat, trash_ratio, non-text_rat, empty_rat" in report
    assert "The run skipped no document." in report


def test_a_changed_old_column_fails_and_is_named_with_its_rows(collection):
    coll, paradata = collection

    def change(df):
        df.loc[0, "avg_quality_score"] = 0.1

    _edit(coll / "page_stats_1210r.csv", change)
    text, failed = psc.run(coll, "1210", "1210r", paradata)
    assert failed == 1
    assert "differ: avg_quality_score (1)" in text
    assert "## Columns that changed" in text and "→ 0.1 " in text


def test_a_ratio_that_is_not_its_counts_quotient_fails(collection):
    coll, paradata = collection
    _edit(coll / "page_stats_1210r.csv", lambda df: df.__setitem__("clear_rat", df["clear_rat"] * 0 + 0.5))
    text, failed = psc.run(coll, "1210", "1210r", paradata)
    assert failed >= 1 and "| every ratio equal to its counts' quotient" in text
    assert "clear_rat: " in text


def test_a_ratio_outside_zero_one_fails(collection):
    coll, paradata = collection
    _edit(coll / "page_stats_1210r.csv", lambda df: df.__setitem__("empty_rat", df["empty_rat"] + 2))
    text, _ = psc.run(coll, "1210", "1210r", paradata)
    assert "| every ratio in [0, 1] or empty | FAIL | 7 outside |" in text


def test_a_missing_per_document_file_fails_unless_the_run_logged_its_skip(collection):
    coll, paradata = collection
    (coll / "DOC_LINE_STATS_1210r" / "stats_CTX000000003.csv").unlink()
    _edit(coll / "page_stats_1210r.csv", lambda df: df.drop(df.index[df["file"] == "CTX000000003"], inplace=True))
    text, failed = psc.run(coll, "1210", "1210r", paradata)
    assert "1 missing (CTX000000003)" in text and failed >= 2  # the file, and its page in the table

    record = json.loads(paradata.read_text(encoding="utf-8"))
    record["skipped_files_detail"] = [{"file": "CTX000000003.csv", "reason": "Hard Crash: boom"}]
    paradata.write_text(json.dumps(record), encoding="utf-8")
    text, _ = psc.run(coll, "1210", "1210r", paradata)
    assert "0 missing" in text
    assert "| no document failed with an error | WARN | 1 error(s) |" in text
    assert "| `CTX000000003.csv` | Hard Crash: boom |" in text
    # the page the old run had is still reported missing
    assert "1 only in the old (CTX000000003 p.1)" in text


def test_an_error_alone_is_a_warning_not_a_failure(collection):
    coll, paradata = collection
    record = json.loads(paradata.read_text(encoding="utf-8"))
    record["skipped_files_detail"] = [{"file": "CTX000000099.csv", "reason": "Processing Error: bad"}]
    paradata.write_text(json.dumps(record), encoding="utf-8")
    assert psc.main([str(coll), "--paradata", str(paradata)]) == 0
    assert "1 warning(s)" in (coll / "page_stats_1210r_report.md").read_text(encoding="utf-8")


def test_a_missing_rerun_is_exit_2(collection, capsys):
    coll, _ = collection
    (coll / "page_stats_1210r.csv").unlink()
    assert psc.main([str(coll)]) == 2
    assert "not found" in capsys.readouterr().err
