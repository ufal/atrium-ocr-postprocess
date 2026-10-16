"""
tests/test_batch_integrity.py
=============================

A batch run that did not finish must not look finished.

Three ways it did, found reading the production path for the 2026-10-16 release:

* **A truncated per-document CSV read as done.** `classify_TEXT` appended rows straight to
  `<doc>.csv` and rewrote it after the document-level passes. A job that died in between (GPU worker lost,
  walltime, an exception) left a partial, never-post-processed file, which the next run's resume check read
  as finished and `aggregate_STAT.py` aggregated into page stats. Rows are now built in `<doc>.csv.part` and
  renamed once whole: a `<doc>.csv` that exists is complete.
* **Exit 0 over missing outputs.** `run_pipeline.py` printed "Pipeline aborted" on stderr and then
  "PIPELINE COMPLETE" and exited 0; `classify_TEXT.main` exited 0 when every document failed or the GPU
  worker was lost. A SLURM job, a Kubernetes Job or CI read success over stale or partial outputs.
* **`page_split.py` exited 0 on a missing input directory** and the pipeline went on with the page
  directories an earlier run left.

No model is loaded: the documents here hold only lines the CPU pre-filter settles (digits are `Non-text`).
"""

import json
import os
import queue
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import classify_TEXT as LC
import run_pipeline

_ROOT = Path(__file__).resolve().parent.parent


def _task(tmp_path, file_id="doc1", pages=(1,)):
    text_dir = tmp_path / "text"
    (text_dir / file_id).mkdir(parents=True, exist_ok=True)
    out_dir = tmp_path / "output"
    out_dir.mkdir(exist_ok=True)
    group = pd.DataFrame({"file": [file_id] * len(pages), "page": list(pages)})
    return text_dir, out_dir, (file_id, group, str(text_dir), str(out_dir), 128, queue.Queue(), {}, ["ces"], [], None)


def _page(text_dir, text, file_id="doc1", page=1):
    path = text_dir / file_id / f"{file_id}-{page}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _age(path, seconds):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns - seconds * 10**9, st.st_mtime_ns - seconds * 10**9))


# ── the per-document CSV ─────────────────────────────────────────────────────────────────────


def test_a_finished_document_leaves_its_csv_and_no_part(tmp_path):
    text_dir, out_dir, task = _task(tmp_path)
    _page(text_dir, "12345 678\n")
    result = LC.process_document(task)
    assert result["status"] == "success", result
    assert (out_dir / "doc1.csv").exists()
    assert not list(out_dir.glob("*.part"))
    assert list(pd.read_csv(out_dir / "doc1.csv", dtype={"file": str})["categ"]) == ["Non-text"]


def test_rows_are_built_under_another_name_than_the_output(tmp_path):
    """While a document is being written its rows are in `<doc>.csv.part`: nothing named `*.csv` exists
    for resume or aggregation to mistake for a finished document."""
    out_dir = tmp_path / "o"
    out_dir.mkdir()
    LC.write_rows_to_doc(out_dir, "docX", [LC._fast_track_row("docX", "1", 1, "12", "12", "", "", "Non-text")])
    assert sorted(p.name for p in out_dir.iterdir()) == ["docX.csv.part"]
    assert not list(out_dir.glob("*.csv"))
    assert LC.doc_part_path(out_dir, "docX") == out_dir / "docX.csv.part"


def test_a_leftover_part_is_never_trusted(tmp_path):
    text_dir, out_dir, task = _task(tmp_path)
    _page(text_dir, "12345 678\n")
    (out_dir / "doc1.csv.part").write_text("file,garbage\nTRUNCATED,row\n", encoding="utf-8")
    result = LC.process_document(task)
    assert result["status"] == "success", result
    assert "TRUNCATED" not in (out_dir / "doc1.csv").read_text(encoding="utf-8")
    assert not list(out_dir.glob("*.part"))


def test_a_document_with_only_a_part_is_classified_not_skipped(tmp_path):
    """The resume check looks for `<doc>.csv`; a run that died left only the `.part`."""
    text_dir, out_dir, task = _task(tmp_path)
    page = _page(text_dir, "12345 678\n")
    _age(page, 60)
    (out_dir / "doc1.csv.part").write_text("half a document", encoding="utf-8")
    assert LC.process_document(task)["status"] == "success"
    assert (out_dir / "doc1.csv").exists()


def test_a_failed_document_leaves_no_output_and_no_part(tmp_path, monkeypatch):
    text_dir, out_dir, task = _task(tmp_path)
    _page(text_dir, "12345 678\n")

    def die(df):
        raise RuntimeError("the document-level pass failed")

    monkeypatch.setattr(LC, "apply_document_postprocessing", die)
    result = LC.process_document(task)
    assert result["status"] == "error" and "document-level pass" in result["reason"]
    assert sorted(out_dir.iterdir()) == [], "nothing a later resume could mistake for a finished document"


def test_a_failed_reclassification_keeps_the_last_complete_output(tmp_path, monkeypatch):
    """The changed page is classified again; if that fails, the previous complete CSV is still there
    (and still older than the page, so the next run tries again)."""
    text_dir, out_dir, task = _task(tmp_path)
    out = out_dir / "doc1.csv"
    out.write_text("previous,complete\n", encoding="utf-8")
    _age(out, 60)
    _page(text_dir, "12345 678\n")
    monkeypatch.setattr(LC, "apply_document_postprocessing", lambda df: (_ for _ in ()).throw(RuntimeError("x")))

    assert LC.process_document(task)["status"] == "error"
    assert out.read_text(encoding="utf-8") == "previous,complete\n"
    assert not list(out_dir.glob("*.part"))
    monkeypatch.undo()
    assert LC.process_document(task)["status"] == "success", "the next run classifies it again"


def test_a_changed_document_left_without_text_loses_its_stale_output(tmp_path):
    """Before, the old CSV was removed up front; it must still not survive a document with no lines."""
    text_dir, out_dir, task = _task(tmp_path)
    out = out_dir / "doc1.csv"
    out.write_text("stale,rows\n", encoding="utf-8")
    _age(out, 60)
    _page(text_dir, "")  # newer than the output, and empty
    result = LC.process_document(task)
    assert result["status"] == "success" and result["lines"] == 0
    assert not out.exists() and not list(out_dir.glob("*.part"))


def test_neither_the_listing_nor_aggregation_sees_a_part(tmp_path):
    (tmp_path / "a.csv").write_text("x", encoding="utf-8")
    (tmp_path / "b.csv.part").write_text("x", encoding="utf-8")
    assert LC.outputs_outside_the_index(tmp_path, ["a"]) == []
    assert [p.name for p in tmp_path.glob("*.csv")] == ["a.csv"]


# ── the exit status of classify_TEXT ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "total, failed, gpu_lost, fails",
    [
        (10, 0, False, False),  # all classified (or resumed)
        (10, 3, False, False),  # some failed: the rest are done, aggregation still wanted
        (10, 10, False, True),  # nothing classified
        (1, 1, False, True),
        (10, 0, True, True),  # the GPU worker was lost, whatever else happened
        (0, 0, False, False),  # nothing to do is not a failure
    ],
)
def test_classify_fails_only_when_it_did_not_complete(total, failed, gpu_lost, fails):
    reason = LC.incomplete_reason(total, failed, gpu_lost)
    assert (reason is not None) is fails
    if reason:
        assert f"{failed} of {total}" in reason
        assert ("GPU worker was lost" in reason) is gpu_lost


# ── the exit status of run_pipeline ─────────────────────────────────────────────────────────


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """`run_pipeline.main()` with its stages replaced: stage 1 finishes and leaves paradata, stage 2 is
    given by the test. The three environment variables main() exports are restored afterwards."""
    for name in ("LANGID_CONFIG", "LANGID_TEXT_DIR", "DOCUMENT_JSON_DIR"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setattr(run_pipeline.time, "sleep", lambda s: None)
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "stage_count": 1,
                "license": "CC BY-NC 4.0",
                "license_url": "https://example.org",
                "intermediate_formats": {"csv": 1},
                "total_duration_seconds": 1,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(run_pipeline, "merge_run_paradata", lambda **kw: str(summary))
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "--paradata-dir", str(tmp_path / "para")])

    def install(second_stage):
        calls = []

        def fake_stage(name, cmd, paradata_dir):
            calls.append(name)
            if len(calls) == 1:
                return [str(tmp_path / "stage1.json")]
            return second_stage(name)

        monkeypatch.setattr(run_pipeline, "_run_stage", fake_stage)
        return calls

    return install


def test_a_pipeline_that_finished_exits_zero(pipeline, capsys):
    pipeline(lambda name: [])
    assert run_pipeline.main() == 0
    assert "PIPELINE COMPLETE" in capsys.readouterr().out


def test_a_pipeline_that_aborted_after_a_finished_stage_exits_nonzero(pipeline, capsys):
    def crash(name):
        raise RuntimeError(f"Stage '{name}' failed with exit code 1")

    calls = pipeline(crash)
    assert run_pipeline.main() == 1
    out, err = capsys.readouterr()
    assert len(calls) == 2, "it stopped at the failing stage"
    assert "PIPELINE ABORTED" in out and "PIPELINE COMPLETE" not in out
    assert "failed with exit code 1" in err
    assert "merged 1 logged stage" in out, "the paradata of the stage that finished is still merged"


def test_a_pipeline_whose_first_stage_failed_exits_nonzero(tmp_path, monkeypatch):
    for name in ("LANGID_CONFIG", "LANGID_TEXT_DIR", "DOCUMENT_JSON_DIR"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setattr(sys, "argv", ["run_pipeline.py", "--paradata-dir", str(tmp_path / "para")])

    def crash(name, cmd, paradata_dir):
        raise RuntimeError("Stage failed with exit code 2")

    monkeypatch.setattr(run_pipeline, "_run_stage", crash)
    assert run_pipeline.main() == 1


# ── page_split ───────────────────────────────────────────────────────────────────────────────


def test_page_split_exits_nonzero_on_a_missing_input_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, str(_ROOT / "page_split.py"), str(tmp_path / "nope"), str(tmp_path / "out")],
        capture_output=True,
        text=True,
        cwd=str(_ROOT),
    )
    assert result.returncode == 1
    assert "Input directory not found" in result.stdout
