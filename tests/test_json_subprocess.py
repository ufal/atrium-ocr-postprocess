"""
tests/test_json_subprocess.py — E2E subprocess tests for extract_JSON_2_TXT.py
(the json-keys extraction method), issue #37 / its "Implement E2E Subprocess
Tests for JSON extraction" follow-up comment.

Unlike the rest of tests/test_extract_json.py and tests/test_document_hook.py
(which call the script's functions in-process), everything here shells out via
`subprocess.run([sys.executable, ...])`, exactly as run_pipeline.py itself does
(`build_plan()`: `[py, extract_script]`). In-process unit tests can't see
argument-parsing bugs, CLI exit codes, or file I/O that only manifests through
a real process boundary — that gap is the whole point of this file.

Every test resolves `sys.executable` dynamically and points `LANGID_CONFIG` /
`MAX_WORKERS` / `DOCUMENT_JSON_DIR` at a fresh `tmp_path`, so nothing here reads
or writes the real repo config or a real `paradata/` directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "extract_JSON_2_TXT.py"


def _run(
    args: list,
    cwd: Path,
    config_path: Optional[Path] = None,
    extra_env: Optional[Dict[str, str]] = None,
    timeout: float = 60.0,
) -> subprocess.CompletedProcess:
    """Invoke extract_JSON_2_TXT.py as a real subprocess.

    `cwd` is a scratch directory (never the repo root) so the hardcoded
    `paradata/` output lands somewhere disposable. The script's own directory
    still resolves via its absolute path, so sibling-module imports
    (document_hook, atrium_paradata, ...) work regardless of `cwd`.
    """
    env = dict(os.environ)
    env.setdefault("MAX_WORKERS", "2")
    if config_path is not None:
        env["LANGID_CONFIG"] = str(config_path)
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _write_config(
    tmp_path: Path,
    input_csv: Path,
    output_txt_dir: Path,
    document_json_dir: Optional[Path] = None,
    force_single_page: Optional[bool] = None,
) -> Path:
    lines = [
        "[EXTRACT]",
        f"INPUT_CSV = {input_csv}",
        f"OUTPUT_TXT_JSON = {output_txt_dir}",
        "WORKERS_MAX_JSON = 2",
    ]
    if force_single_page is not None:
        lines.append(f"FORCE_SINGLE_PAGE_JSON = {'true' if force_single_page else 'false'}")
    lines.append("")
    lines.append("[DOCUMENT]")
    lines.append(f"JSON_DIR = {document_json_dir or ''}")
    config_path = tmp_path / "config.txt"
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def _write_realistic_json_samples(json_dir: Path) -> None:
    """Three deliberately different JSON *shapes* for the same TARGET_KEYS
    whitelist, mirroring the heterogeneous vendor structures README.md
    documents for the split stage (Azure-like nested pages, AWS-Textract-like
    flat tagged elements, pero-ocr-like single-page-per-file)."""
    json_dir.mkdir(parents=True, exist_ok=True)

    # doc-a: two pages, each with a nested list of line dicts (docTR/Azure-like).
    (json_dir / "doc-a-1.json").write_text(
        json.dumps(
            {
                "metadata": {"engine": "AzureLike", "confidence": 0.98},
                "page": {"lines": [{"textline": "Alpha first line"}, {"textline": "Alpha second line"}]},
            }
        ),
        encoding="utf-8",
    )
    (json_dir / "doc-a-2.json").write_text(
        json.dumps({"page": {"lines": [{"textline": "Alpha page two content"}]}}),
        encoding="utf-8",
    )

    # doc-b: single-page-per-file, flat "text" field (pero-ocr-like).
    (json_dir / "doc-b-1.json").write_text(
        json.dumps({"text": "Beta only page, single flat field."}),
        encoding="utf-8",
    )


def _write_stats_csv(csv_path: Path, rows) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8") as fh:
        fh.write("file,page,path\n")
        for file_id, page_id, path in rows:
            fh.write(f"{file_id},{page_id},{path}\n")


# ── test_cli_json_keys_success ────────────────────────────────────────────────


def test_cli_json_keys_success(tmp_path: Path):
    """Realistic multi-structure JSON samples, run through the real CLI process
    end to end: exit code 0, clean stdout/stderr, correct .txt files generated,
    and (with [DOCUMENT].JSON_DIR set) a schema-shaped doc.json accreted."""
    json_dir = tmp_path / "json"
    _write_realistic_json_samples(json_dir)

    csv_path = tmp_path / "stats.csv"
    _write_stats_csv(
        csv_path,
        [
            ("doc-a", 1, json_dir / "doc-a-1.json"),
            ("doc-a", 2, json_dir / "doc-a-2.json"),
            ("doc-b", 1, json_dir / "doc-b-1.json"),
        ],
    )

    txt_dir = tmp_path / "txt"
    doc_dir = tmp_path / "doc"
    config_path = _write_config(tmp_path, csv_path, txt_dir, document_json_dir=doc_dir)

    result = _run([], cwd=tmp_path, config_path=config_path)

    assert result.returncode == 0, f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    assert "Done." in result.stdout
    assert "Success rate: 100.00%" in result.stdout

    # File generation, following the shared {file}/{file}-{page}.txt convention.
    assert (txt_dir / "doc-a" / "doc-a-1.txt").read_text(encoding="utf-8") == "Alpha first line\nAlpha second line"
    assert (txt_dir / "doc-a" / "doc-a-2.txt").read_text(encoding="utf-8") == "Alpha page two content"
    assert (txt_dir / "doc-b" / "doc-b-1.txt").read_text(encoding="utf-8") == "Beta only page, single flat field."

    # doc.json accretion produced valid, schema-shaped records for both documents.
    doc_a = json.loads((doc_dir / "doc-a.document.json").read_text(encoding="utf-8"))
    assert [p["page"] for p in doc_a["pages"]] == ["1", "2"]
    assert doc_a["content"]["text"] == "Alpha first line\nAlpha second line\n\nAlpha page two content"

    doc_b = json.loads((doc_dir / "doc-b.document.json").read_text(encoding="utf-8"))
    assert doc_b["content"]["text"] == "Beta only page, single flat field."


def test_cli_force_single_page_flag_via_subprocess(tmp_path: Path):
    """The --force-single-page CLI flag, exercised through a real process, not
    just the in-process unit tests in test_extract_json.py / test_document_hook.py."""
    json_dir = tmp_path / "json"
    _write_realistic_json_samples(json_dir)

    csv_path = tmp_path / "stats.csv"
    _write_stats_csv(
        csv_path,
        [
            ("doc-a", 1, json_dir / "doc-a-1.json"),
            ("doc-a", 2, json_dir / "doc-a-2.json"),
        ],
    )

    txt_dir = tmp_path / "txt"
    doc_dir = tmp_path / "doc"
    config_path = _write_config(tmp_path, csv_path, txt_dir, document_json_dir=doc_dir)

    result = _run(["--force-single-page"], cwd=tmp_path, config_path=config_path)

    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    doc_a = json.loads((doc_dir / "doc-a.document.json").read_text(encoding="utf-8"))
    assert len(doc_a["pages"]) == 1
    assert doc_a["pages"][0]["page"] == "1"
    assert "ocr" not in doc_a["pages"][0], "the engine is a paradata parameter since record schema 2.0"


def test_cli_force_single_page_config_default_applies_with_no_flags(tmp_path: Path):
    """run_pipeline.py invokes this script with NO extra CLI args (`[py, extract_script]`),
    so [EXTRACT].FORCE_SINGLE_PAGE_JSON must be able to opt in on its own."""
    json_dir = tmp_path / "json"
    _write_realistic_json_samples(json_dir)

    csv_path = tmp_path / "stats.csv"
    _write_stats_csv(csv_path, [("doc-a", 1, json_dir / "doc-a-1.json"), ("doc-a", 2, json_dir / "doc-a-2.json")])

    txt_dir = tmp_path / "txt"
    doc_dir = tmp_path / "doc"
    config_path = _write_config(tmp_path, csv_path, txt_dir, document_json_dir=doc_dir, force_single_page=True)

    result = _run([], cwd=tmp_path, config_path=config_path)  # no CLI flags at all

    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    doc_a = json.loads((doc_dir / "doc-a.document.json").read_text(encoding="utf-8"))
    assert len(doc_a["pages"]) == 1


# ── test_cli_malformed_arguments ──────────────────────────────────────────────


def test_cli_malformed_arguments(tmp_path: Path):
    """An unrecognised flag must fail fast via argparse: non-zero exit, a
    stderr message, and — critically — no extraction work attempted at all."""
    csv_path = tmp_path / "stats.csv"
    _write_stats_csv(csv_path, [])  # would be a no-op even if reached
    config_path = _write_config(tmp_path, csv_path, tmp_path / "txt")

    result = _run(["--not-a-real-flag"], cwd=tmp_path, config_path=config_path)

    assert result.returncode != 0
    assert result.returncode == 2  # argparse's own usage-error exit code
    assert "unrecognized arguments" in result.stderr
    assert "Loaded" not in result.stdout  # never reached the extraction loop


def test_cli_missing_input_csv_fails_with_clear_message(tmp_path: Path):
    """A CLI-adjacent malformed-configuration case: INPUT_CSV pointing nowhere
    must fail loudly (SystemExit(1)) rather than silently doing nothing."""
    missing_csv = tmp_path / "does_not_exist.csv"
    config_path = _write_config(tmp_path, missing_csv, tmp_path / "txt")

    result = _run([], cwd=tmp_path, config_path=config_path)

    assert result.returncode == 1
    assert "CRITICAL ERROR" in result.stdout
    assert str(missing_csv) in result.stdout


# ── test_cli_corrupted_input ──────────────────────────────────────────────────


def test_cli_corrupted_input(tmp_path: Path):
    """Corrupted/invalid JSON for one page must fail gracefully — the process
    still exits 0 (per-page failures are reported, not fatal, matching the
    other extraction methods' resume/skip semantics), the corrupted page's
    .txt is never written, and a good sibling page still succeeds."""
    json_dir = tmp_path / "json"
    json_dir.mkdir()
    (json_dir / "good-1.json").write_text(json.dumps({"text": "This page is fine"}), encoding="utf-8")
    (json_dir / "bad-1.json").write_text("{not valid json at all", encoding="utf-8")

    csv_path = tmp_path / "stats.csv"
    _write_stats_csv(
        csv_path,
        [
            ("good", 1, json_dir / "good-1.json"),
            ("bad", 1, json_dir / "bad-1.json"),
        ],
    )

    txt_dir = tmp_path / "txt"
    config_path = _write_config(tmp_path, csv_path, txt_dir)

    result = _run([], cwd=tmp_path, config_path=config_path)

    # Graceful, not fatal: the process completes successfully overall.
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    assert "Success rate: 50.00%" in result.stdout

    assert (txt_dir / "good" / "good-1.txt").exists()
    assert not (txt_dir / "bad" / "bad-1.txt").exists()


def test_cli_corrupted_input_all_pages_bad_still_exits_zero(tmp_path: Path):
    """Even a 0% success rate must not crash the process — every failure is
    caught per-page and logged, matching extract_single_page's contract."""
    json_dir = tmp_path / "json"
    json_dir.mkdir()
    (json_dir / "bad-1.json").write_text("not json", encoding="utf-8")

    csv_path = tmp_path / "stats.csv"
    _write_stats_csv(csv_path, [("bad", 1, json_dir / "bad-1.json")])

    txt_dir = tmp_path / "txt"
    config_path = _write_config(tmp_path, csv_path, txt_dir)

    result = _run([], cwd=tmp_path, config_path=config_path)

    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    assert "Success rate: 0.00%" in result.stdout
    assert not any((txt_dir).rglob("*.txt"))


@pytest.mark.parametrize("bad_payload", ["[1, 2, 3]", "null", '"just a string"', ""])
def test_cli_corrupted_input_handles_valid_json_wrong_shape(tmp_path: Path, bad_payload: str):
    """Structurally valid JSON that isn't an object (or is empty/unparseable)
    must not crash the subprocess — it should be treated as an extraction
    failure for that page like any other corrupted input."""
    json_dir = tmp_path / "json"
    json_dir.mkdir()
    (json_dir / "odd-1.json").write_text(bad_payload, encoding="utf-8")

    csv_path = tmp_path / "stats.csv"
    _write_stats_csv(csv_path, [("odd", 1, json_dir / "odd-1.json")])

    txt_dir = tmp_path / "txt"
    config_path = _write_config(tmp_path, csv_path, txt_dir)

    result = _run([], cwd=tmp_path, config_path=config_path)

    assert result.returncode == 0, f"stderr:\n{result.stderr}"
