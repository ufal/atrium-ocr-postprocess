"""
tests/test_corpus_change.py
===========================
``tools/corpus_change.py`` -- @david-spacil's corpus-change method (issue #3, stage 16).

What must not fail silently:

1. the group key is his: two spellings that differ only in case and punctuation
   are one group;
2. ``diff`` counts transitions, names the ``pp_*`` flags of every arrival in
   ``Trash``, lists the touched documents, and REFUSES a pair whose documents or
   line locators do not match -- a half-written arm must not read as "no change";
3. ``--verify`` recounts through an independent reader and agrees;
4. ``score`` counts top groups line by line, scales the tail sample to the tail
   population with his normal-approximation interval, keeps unlabelled groups out
   of both, and restricts the tail to the arm's own changes when the arm is a
   subset of the reference run;
5. ``--expect-results`` passes on matching counts and fails (exit 4) otherwise;
6. ``pack`` is blind (no category in a sheet), deterministic for a seed, and
   leaves already-labelled groups out;
7. ``recat`` shards, resumes, and fails when a document has no output.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import corpus_change as cc  # noqa: E402

HEADER = ["categ", "file", "page_num", "line_num", "text", "pp_dedup", "pp_page_context"]


def _write_doc(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for categ, page, line, text, pp_page in rows:
            w.writerow([categ, path.stem, page, line, text, "False", "True" if pp_page else "False"])


def _write_sheet(path: Path, fields: list[str], rows: list[dict]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return path


def _read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# 1. key
# ---------------------------------------------------------------------------


def test_group_key_is_casefolded_and_punctuation_free():
    assert cc.group_key("ARCHAIA Brno o.p.s.") == cc.group_key("Archaia Brno, o.p.s.") == "archaiabrnoops"
    assert cc.group_key("[ ] (Liter.)") == "liter"
    assert cc.group_key("•  —") == "•  —"  # nothing left: the raw text is the key
    assert cc.decade("CTX201703691") == "2010"
    assert cc.decade("P027_00002_034") == "?"


# ---------------------------------------------------------------------------
# 2-3. diff
# ---------------------------------------------------------------------------


@pytest.fixture
def trees(tmp_path: Path) -> dict[str, Path]:
    base, arm = tmp_path / "base", tmp_path / "arm"
    _write_doc(base / "arup" / "CTX200100001.csv", [("Clear", 1, 1, "ARCHAIA", 0), ("Noisy", 1, 2, "IV-2", 0)])
    _write_doc(arm / "arup" / "CTX200100001.csv", [("Noisy", 1, 1, "ARCHAIA", 0), ("Trash", 1, 2, "IV-2", 1)])
    _write_doc(base / "arup" / "CTX195000002.csv", [("Clear", 1, 1, "vrstva", 0)])
    _write_doc(arm / "arup" / "CTX195000002.csv", [("Clear", 1, 1, "vrstva", 0)])
    _write_doc(base / "arub" / "MTX199900003.csv", [("Clear", 2, 5, "Literatuia", 0)])
    _write_doc(arm / "arub" / "MTX199900003.csv", [("Noisy", 2, 5, "Literatuia", 0)])
    return {"base": base, "arm": arm, "out": tmp_path / "out"}


def _diff(trees: dict[str, Path], *extra: str) -> int:
    return cc.main(
        [
            "diff",
            "--pair",
            "arup",
            str(trees["base"] / "arup"),
            str(trees["arm"] / "arup"),
            "--pair",
            "arub",
            str(trees["base"] / "arub"),
            str(trees["arm"] / "arub"),
            "--workers",
            "1",
            "--out-prefix",
            str(trees["out"] / "d"),
            *extra,
        ]
    )


def test_diff_counts_transitions_flags_and_touched_documents(trees):
    assert _diff(trees, "--verify") == 0
    summary = json.loads((trees["out"] / "d_summary.json").read_text(encoding="utf-8"))
    ov = summary["overall"]
    assert ov["transitions"] == {"Clear->Noisy": 2, "Noisy->Trash": 1}
    assert ov["changed_lines"] == 3 and ov["changed_documents"] == 2
    assert ov["rows_compared"] == 4 and ov["unpaired"] == 0
    assert ov["trash_arrivals_by_pp"] == {"pp_page_context": 1}
    assert summary["verify"]["agree"] is True
    assert ov["by_decade"]["2000"] == {"Clear->Noisy": 1, "Noisy->Trash": 1}
    changes = _read_csv(trees["out"] / "d_changes.csv")
    assert {(c["collection"], c["doc"], c["line_num"], c["from"], c["to"]) for c in changes} == {
        ("arup", "CTX200100001", "1", "Clear", "Noisy"),
        ("arup", "CTX200100001", "2", "Noisy", "Trash"),
        ("arub", "MTX199900003", "5", "Clear", "Noisy"),
    }
    touched = (trees["out"] / "d_touched.tsv").read_text(encoding="utf-8").splitlines()
    assert touched == ["arub\tMTX199900003.csv", "arup\tCTX200100001.csv"]


def test_diff_refuses_a_missing_arm_document_unless_subset(trees):
    (trees["arm"] / "arup" / "CTX195000002.csv").unlink()
    assert _diff(trees) == 2
    assert _diff(trees, "--subset") == 0


def test_diff_refuses_an_arm_document_the_base_lacks(trees):
    _write_doc(trees["arm"] / "arub" / "MTX200000099.csv", [("Clear", 1, 1, "x", 0)])
    assert _diff(trees, "--subset") == 2


def test_diff_expect_list_must_match_the_arm(trees, tmp_path):
    names = tmp_path / "arup.txt"
    names.write_text("CTX200100001.csv\n", encoding="utf-8")
    assert _diff(trees, "--subset", "--expect-list", "arup", str(names)) == 2
    names.write_text("CTX200100001.csv\nCTX195000002.csv\n", encoding="utf-8")
    assert _diff(trees, "--subset", "--expect-list", "arup", str(names)) == 0


def test_diff_refuses_lines_that_do_not_pair(trees):
    _write_doc(trees["arm"] / "arub" / "MTX199900003.csv", [("Noisy", 2, 6, "Literatuia", 0)])
    assert _diff(trees) == 2


# ---------------------------------------------------------------------------
# 4-5. score
# ---------------------------------------------------------------------------


def _changes(path: Path, rows: list[tuple]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cc.CHANGE_FIELDS)
        for doc, line, frm, to, text in rows:
            w.writerow(["arup", doc, "2000", "1", line, frm, to, text, "", ""])
    return path


@pytest.fixture
def scored(tmp_path: Path) -> dict[str, Path]:
    # Reference run: 6 lines in two labelled groups, 2 in an unlabelled one, 4 in the tail.
    rows = [
        ("D1", 1, "Clear", "Noisy", "ARCHAIA"),
        ("D1", 2, "Clear", "Noisy", "Archaia."),
        ("D2", 1, "Clear", "Noisy", "ARCHAIA"),
        ("D3", 1, "Clear", "Noisy", "Literatuia"),
        ("D3", 2, "Clear", "Noisy", "Literatuia"),
        ("D4", 1, "Noisy", "Trash", "IV-2"),
        ("D5", 1, "Clear", "Noisy", "[ ] (Liter.)"),
        ("D5", 2, "Clear", "Noisy", "[ ] (Liter.)"),
        ("T1", 1, "Clear", "Noisy", "foo one"),
        ("T2", 1, "Clear", "Noisy", "bar two"),
        ("T3", 1, "Clear", "Noisy", "baz three"),
        ("T4", 1, "Clear", "Noisy", "qux four"),
    ]
    ref = _changes(tmp_path / "ref.csv", rows)
    top = _write_sheet(
        tmp_path / "top.csv",
        ["text", "annotation"],
        [
            {"text": "ARCHAIA", "annotation": "Clear"},
            {"text": "Literatuia", "annotation": "Noisy"},
            {"text": "IV-2", "annotation": "Clear"},
            {"text": "[ ] (Liter.)", "annotation": ""},
        ],
    )
    tail = _write_sheet(
        tmp_path / "tail.csv",
        ["example", "annotation"],
        [{"example": "T1:1:1", "annotation": "Trash"}, {"example": "T2:1:1", "annotation": "Clear"}],
    )
    return {"ref": ref, "top": top, "tail": tail, "tmp": tmp_path}


def _score(scored: dict[str, Path], changes: Path, *extra: str) -> tuple[int, dict]:
    out = scored["tmp"] / "s"
    rc = cc.main(
        [
            "score",
            "--changes",
            str(changes),
            "--reference-changes",
            str(scored["ref"]),
            "--top",
            str(scored["top"]),
            "--tail",
            str(scored["tail"]),
            "--out-prefix",
            str(out),
            *extra,
        ]
    )
    data = json.loads((scored["tmp"] / "s_score.json").read_text(encoding="utf-8")) if rc in (0, 4) else {}
    return rc, data


def test_score_counts_top_lines_and_scales_the_tail(scored):
    rc, res = _score(scored, scored["ref"])
    assert rc == 0
    # ARCHAIA x3 Clear->Noisy against Clear: broken; Literatuia x2 against Noisy: fixed;
    # IV-2 Noisy->Trash against Clear: neither, a Clear-loss.
    assert res["top_counts"] == {"fixed": 2, "broken": 3, "neither": 1}
    assert res["coverage"] == {
        "top_labelled": 6,
        "tail_population": 4,
        "excluded_unlabelled_groups": 2,
        "new_uncovered": 0,
        "covered_share": round(10 / 12, 4),
    }
    # Tail: 2 of 4 sampled; T1 (Trash) moved Clear->Noisy = neither/closer, T2 (Clear) = broken.
    assert res["tail_counts"] == {"fixed": 0, "broken": 1, "neither": 1}
    broken = res["totals"]["broken"]
    p, n, pop = 0.5, 2, 4
    se = math.sqrt(p * (1 - p) / n)
    assert broken["tail"]["estimate"] == pytest.approx(p * pop)
    assert broken["tail"]["low"] == pytest.approx((p - 1.96 * se) * pop, abs=1e-3)
    assert broken["total_estimate"] == pytest.approx(3 + 2)
    assert broken["share"] == pytest.approx(5 / 10)
    assert res["totals"]["clear_loss"]["top"] == 1
    assert res["totals"]["closer"]["top"] == 2 + 0  # Literatuia; IV-2 moved away from Clear
    assert res["totals"]["farther"]["top"] == 3 + 1


def test_score_restricts_the_tail_to_the_arm_when_the_arm_is_a_subset(scored):
    arm = _changes(
        scored["tmp"] / "arm.csv",
        [
            ("D3", 1, "Clear", "Noisy", "Literatuia"),
            ("T1", 1, "Clear", "Noisy", "foo one"),
            ("T3", 1, "Clear", "Noisy", "baz three"),
            ("N1", 1, "Clear", "Noisy", "brand new"),
        ],
    )
    rc, res = _score(scored, arm)
    assert rc == 0
    assert res["coverage"]["tail_population"] == 2 and res["coverage"]["new_uncovered"] == 1
    assert res["tail"] == {"population": 2, "sampled_labelled": 1, "sampled_unlabelled": 0}
    assert res["totals"]["neither"]["tail"]["estimate"] == pytest.approx(2.0)


def test_expect_results_reproduces_or_fails(scored):
    good = scored["tmp"] / "results_good.txt"
    good.write_text(
        "top {'broken': 3, 'neither': 1, 'fixed': 2} 6 | tail {'broken': 1, 'neither': 1} 2\n", encoding="utf-8"
    )
    assert _score(scored, scored["ref"], "--expect-results", str(good))[0] == 0
    bad = scored["tmp"] / "results_bad.txt"
    bad.write_text("top {'broken': 4, 'neither': 1, 'fixed': 2} 7 | tail {'broken': 1, 'neither': 1} 2\n", "utf-8")
    assert _score(scored, scored["ref"], "--expect-results", str(bad))[0] == 4


def test_a_group_with_two_labels_is_refused(scored):
    _write_sheet(
        scored["top"],
        ["text", "annotation"],
        [{"text": "ARCHAIA", "annotation": "Clear"}, {"text": "Archaia!", "annotation": "Noisy"}],
    )
    assert _score(scored, scored["ref"])[0] == 2


# ---------------------------------------------------------------------------
# 6. pack
# ---------------------------------------------------------------------------


def test_pack_is_blind_deterministic_and_skips_labelled_groups(scored):
    out = scored["tmp"] / "pack"
    argv = ["pack", "--changes", str(scored["ref"]), "--top", "2", "--tail", "2", "--seed", "7", "--tag", "t"]
    assert cc.main([*argv, "--out-dir", str(out), "--exclude-top", str(scored["top"])]) == 0
    top = _read_csv(out / "t_top.csv")
    assert all(r["annotation"] == "" for r in top)
    assert not {"from", "to", "categ", "base", "floor"} & set(top[0])
    assert cc.group_key(top[0]["text"]) not in {"archaia", "literatuia", "iv2", "liter"}
    tail_a = _read_csv(out / "t_tail.csv")
    assert cc.main([*argv, "--out-dir", str(scored["tmp"] / "pack2"), "--exclude-top", str(scored["top"])]) == 0
    assert _read_csv(scored["tmp"] / "pack2" / "t_tail.csv") == tail_a
    keys = _read_csv(out / "t_top_key.csv")
    assert keys[0]["transitions"] == "Clear->Noisy 1"
    frame = json.loads((out / "t_frame.json").read_text(encoding="utf-8"))
    assert frame["excluded_lines"] == 8 and frame["considered_lines"] == 4


def test_returned_pack_scores_directly(scored):
    out = scored["tmp"] / "pack"
    assert (
        cc.main(
            ["pack", "--changes", str(scored["ref"]), "--top", "1", "--tail", "0", "--tag", "t", "--out-dir", str(out)]
        )
        == 0
    )
    rows = _read_csv(out / "t_top.csv")
    rows[0]["annotation"] = "Clear"
    _write_sheet(out / "t_top.csv", list(rows[0]), rows)
    labels, unlabelled = cc.load_top_labels([str(out / "t_top.csv")])
    assert labels == {"archaia": "Clear"} and not unlabelled


# ---------------------------------------------------------------------------
# 7. recat and sample
# ---------------------------------------------------------------------------

_FAKE_RUNNER = """
import argparse, shutil, sys
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument('--input-dir'); ap.add_argument('--out'); ap.add_argument('--override', nargs='*', default=[])
a = ap.parse_args()
skip = {s.split('=')[1] for s in a.override if s.startswith('SKIP=')}
for p in sorted(Path(a.input_dir).glob('*.csv')):
    if p.name in skip:
        print('skipping', p.name, file=sys.stderr); continue
    shutil.copy(p, Path(a.out) / p.name)
"""


@pytest.fixture
def corpus(tmp_path: Path) -> dict[str, Path]:
    src = tmp_path / "in"
    for i in range(7):
        _write_doc(src / f"CTX20010000{i}.csv", [("Clear", 1, 1, f"line {i}", 0)])
    runner = tmp_path / "fake_runner.py"
    runner.write_text(_FAKE_RUNNER, encoding="utf-8")
    return {"in": src, "out": tmp_path / "out", "work": tmp_path / "work", "runner": runner}


def _recat(corpus: dict[str, Path], *extra: str) -> int:
    return cc.main(
        [
            "recat",
            "--input-dir",
            str(corpus["in"]),
            "--out",
            str(corpus["out"]),
            "--work",
            str(corpus["work"]),
            "--shards",
            "3",
            "--runner",
            str(corpus["runner"]),
            "--infer-string",
            "keep",
            *extra,
        ]
    )


def test_recat_shards_with_file_symlinks_and_completes(corpus):
    assert _recat(corpus) == 0
    assert sorted(p.name for p in corpus["out"].glob("*.csv")) == sorted(p.name for p in corpus["in"].glob("*.csv"))
    shards = sorted((corpus["work"] / "shards").iterdir())
    assert len(shards) == 3
    linked = [p for d in shards for p in d.iterdir()]
    assert len(linked) == 7 and all(p.is_symlink() for p in linked)


def test_recat_resumes_and_clears_stray_parts(corpus):
    corpus["out"].mkdir(parents=True)
    (corpus["out"] / "CTX200100000.csv").write_text("already here\n", encoding="utf-8")
    (corpus["out"] / "CTX200100001.csv.part").write_text("half", encoding="utf-8")
    assert _recat(corpus) == 0
    assert (corpus["out"] / "CTX200100000.csv").read_text(encoding="utf-8") == "already here\n"
    assert not list(corpus["out"].glob("*.part"))
    linked = [p.name for d in (corpus["work"] / "shards").iterdir() for p in d.iterdir()]
    assert "CTX200100000.csv" not in linked and len(linked) == 6


def test_recat_check_lines_redoes_a_short_output(corpus):
    corpus["out"].mkdir(parents=True)
    (corpus["out"] / "CTX200100000.csv").write_text("", encoding="utf-8")
    assert _recat(corpus, "--check-lines") == 0
    assert (corpus["out"] / "CTX200100000.csv").read_text(encoding="utf-8").count("\n") == 2


def test_recat_fails_when_a_document_has_no_output(corpus):
    assert _recat(corpus, "--override", "SKIP=CTX200100004.csv") == 1
    assert (corpus["work"] / "missing.txt").read_text(encoding="utf-8") == "CTX200100004.csv\n"


def test_recat_only_restricts_and_refuses_unknown_names(corpus, tmp_path):
    names = tmp_path / "only.txt"
    names.write_text("CTX200100002.csv\n", encoding="utf-8")
    assert _recat(corpus, "--only", str(names)) == 0
    assert [p.name for p in corpus["out"].glob("*.csv")] == ["CTX200100002.csv"]
    names.write_text("CTX299999999.csv\n", encoding="utf-8")
    assert _recat(corpus, "--only", str(names)) == 2


def test_sample_is_seeded_and_respects_the_exclusion(corpus, tmp_path):
    excl = tmp_path / "touched.txt"
    excl.write_text("CTX200100000.csv\nCTX200100001.csv\n", encoding="utf-8")
    outs = []
    for i in range(2):
        out = tmp_path / f"s{i}.txt"
        argv = ["sample", "--input-dir", str(corpus["in"]), "--exclude", str(excl)]
        assert cc.main([*argv, "--fraction", "0.5", "--min", "1", "--seed", "3", "--out", str(out)]) == 0
        outs.append(out.read_text(encoding="utf-8").split())
    assert outs[0] == outs[1] and len(outs[0]) == 2
    assert not {"CTX200100000.csv", "CTX200100001.csv"} & set(outs[0])
