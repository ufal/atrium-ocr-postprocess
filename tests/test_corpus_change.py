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
7. ``recat`` shards, resumes, and fails when a document has no output;
8. ``reach`` writes ``diff``'s changed-line format per pair from in-memory arms,
   scores every changed line against the label sidecars (fixed / broken / neither
   and the ``Clear``-loss change), refuses an unknown constant, an undefined arm
   and an unexpected unreadable document, and gives the same answer in one
   process and in a pool -- with the real engine, where ``LOWPPL_CLEAR_MAX`` 50
   against the shipped 35 moves no category of the sample documents;
9. ``reach --page-ref`` publishes per page exactly what ``aggregate_STAT`` does
   (the batch score over Clear and Noisy lines) and the band
   ``document_hook.quality_band`` does, counts band flips and score moves per
   pair, and scores the labelled pages; ``pagecheck`` applies stage 17's rule
   (GO / HOLD / REVERT, record-only arms never decide);
10. ``recat --expect-missing`` and ``diff --skip`` / ``--summary-only`` -- the
    options stage 18 needs for the one unreadable document and the
    whole-corpus release diff.
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


# ---------------------------------------------------------------------------
# 8. reach
# ---------------------------------------------------------------------------


_FAKE_QS = {"Clear": 0.9, "Noisy": 0.6, "Trash": 0.3}


def _fake_categorise(frame, constants):
    """Stand-in engine: an arm that overrides LOWPPL_CLEAR_MAX sends every `x...` line to Trash."""
    cats = [str(c) for c in frame["categ"].tolist()]
    flags = [""] * len(cats)
    if constants and "LOWPPL_CLEAR_MAX" in constants:
        for i, text in enumerate(frame["text"].tolist()):
            if text.startswith("x"):
                cats[i], flags[i] = "Trash", "pp_page_context"
    return cats, flags, [_FAKE_QS.get(c, 0.0) for c in cats]


@pytest.fixture
def reach_inputs(tmp_path: Path) -> dict[str, Path]:
    arup, arub = tmp_path / "arup", tmp_path / "arub"
    _write_doc(
        arup / "CTX200100001.csv",
        [("Clear", 1, 1, "xyzzy", 0), ("Clear", 1, 2, "Archaia Brno", 0), ("Noisy", 2, 1, "xqq", 0)],
    )
    _write_doc(arup / "CTX200100002.csv", [("Clear", 1, 1, "nothing moves", 0)])
    _write_doc(arub / "CTX199900003.csv", [("Noisy", 3, 7, "xkcd", 0)])
    (arub / "CTX199900004.csv").write_text("categ,file,text\nClear,CTX199900004,no locators\n", encoding="utf-8")
    fields = ["file", "page_num", "line_num", "gold_categ"]
    labels = _write_sheet(
        tmp_path / "labels.csv",
        fields,
        [
            {"file": "CTX200100001", "page_num": "1", "line_num": "1", "gold_categ": "Trash"},  # fixed
            {"file": "CTX200100001", "page_num": "2", "line_num": "1", "gold_categ": "Noisy"},  # broken
            {"file": "CTX199900003", "page_num": "3.0", "line_num": "7", "gold_categ": "Clear"},  # neither, loss +1
            {"file": "CTX200100002", "page_num": "1", "line_num": "1", "gold_categ": "Clear"},  # does not move
            {"file": "CTX209900009", "page_num": "1", "line_num": "1", "gold_categ": "Clear"},  # in no input
        ],
    )
    return {"arup": arup, "arub": arub, "labels": labels, "out": tmp_path / "out" / "16r"}


def _reach(inputs: dict[str, Path], *extra: str, arms: tuple = (("base",), ("arm", "LOWPPL_CLEAR_MAX=1"))) -> int:
    argv = ["reach", "--input", "arup", str(inputs["arup"]), "--input", "arub", str(inputs["arub"])]
    for arm in arms:
        argv += ["--arm", *arm]
    return cc.main([*argv, "--infer-string", "keep", "--out-prefix", str(inputs["out"]), *extra])


def test_reach_writes_the_diff_format_and_scores_every_changed_line(reach_inputs, monkeypatch):
    monkeypatch.setattr(cc, "_categorise", _fake_categorise)
    rc = _reach(
        reach_inputs,
        "--pair", "base", "arm",
        "--pair", "arm", "base",
        "--labels", "gold", str(reach_inputs["labels"]),
        "--expect-docs", "4",
        "--expect-unreadable", "CTX199900004.csv",
        "--workers", "1",
    )  # fmt: skip
    assert rc == 0
    out = reach_inputs["out"]
    rows = _read_csv(Path(f"{out}_base__arm_changes.csv"))
    assert list(rows[0]) == list(cc.CHANGE_FIELDS)
    got = {(r["collection"], r["doc"], r["decade"], r["page_num"], r["line_num"], r["from"], r["to"]) for r in rows}
    assert got == {
        ("arup", "CTX200100001", "2000", "1", "1", "Clear", "Trash"),
        ("arup", "CTX200100001", "2000", "2", "1", "Noisy", "Trash"),
        ("arub", "CTX199900003", "1990", "3", "7", "Noisy", "Trash"),
    }
    assert {r["pp_arm"] for r in rows} == {"pp_page_context"} and {r["pp_base"] for r in rows} == {""}

    summary = json.loads(Path(f"{out}_summary.json").read_text(encoding="utf-8"))
    assert (summary["documents"], summary["documents_read"], summary["lines_read"]) == (4, 3, 5)
    assert list(summary["unreadable"]) == ["CTX199900004.csv"]
    assert summary["labels"] == {"gold": {"keys": 5, "resolved": 4}}
    fwd, back = summary["pairs"]["base->arm"], summary["pairs"]["arm->base"]
    assert fwd["changed_lines"] == 3 and fwd["changed_documents"] == 2
    assert fwd["transitions"] == {"Noisy->Trash": 2, "Clear->Trash": 1}
    assert fwd["by_collection"] == {"arub": {"Noisy->Trash": 1}, "arup": {"Clear->Trash": 1, "Noisy->Trash": 1}}
    assert fwd["trash_arrivals_by_pp"] == {"pp_page_context": 3}
    assert fwd["labelled"]["gold"] == {"fixed": 1, "broken": 1, "neither": 1, "clear_loss": 1, "clear_demoted": 0}
    assert back["labelled"]["gold"] == {"fixed": 1, "broken": 1, "neither": 1, "clear_loss": -1, "clear_demoted": 0}

    per_doc = _read_csv_tsv(Path(f"{out}_labelled.tsv"))
    assert {(r["pair"], r["doc"], r["fixed"], r["broken"], r["clear_loss_delta"]) for r in per_doc} == {
        ("base->arm", "CTX200100001", "1", "1", "0"),
        ("base->arm", "CTX199900003", "0", "0", "1"),
        ("arm->base", "CTX200100001", "1", "1", "0"),
        ("arm->base", "CTX199900003", "0", "0", "-1"),
    }

    # score and pack read reach's output as they read diff's.
    changes = f"{out}_base__arm_changes.csv"
    assert cc.main(["score", "--changes", changes, "--out-prefix", str(out.parent / "s")]) == 0
    pack = ["pack", "--changes", changes, "--top", "1", "--tail", "1", "--tag", "t"]
    assert cc.main([*pack, "--out-dir", str(out.parent / "p")]) == 0
    assert (out.parent / "p" / "t_top.csv").exists() and (out.parent / "p" / "t_tail.csv").exists()


def _read_csv_tsv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def test_reach_refuses_unknown_constants_and_undefined_arms(reach_inputs, monkeypatch, capsys):
    monkeypatch.setattr(cc, "_categorise", _fake_categorise)
    bad_key = (("base",), ("arm", "NOT_A_CONSTANT_ANYWHERE=1"))
    assert _reach(reach_inputs, "--pair", "base", "arm", "--workers", "1", arms=bad_key) == 2
    assert "NOT_A_CONSTANT_ANYWHERE is not a constant" in capsys.readouterr().err
    assert _reach(reach_inputs, "--pair", "base", "nope", "--workers", "1") == 2
    assert "'nope' is not defined" in capsys.readouterr().err
    assert _reach(reach_inputs, "--pair", "base", "arm", "--workers", "1", arms=(("base",), ("base", "X=1"))) == 2


def test_reach_fails_on_unexpected_documents(reach_inputs, monkeypatch, capsys):
    monkeypatch.setattr(cc, "_categorise", _fake_categorise)
    assert _reach(reach_inputs, "--pair", "base", "arm", "--workers", "1") == 3  # the locator-less one
    assert "could not be re-scored" in capsys.readouterr().err
    unread = ("--expect-unreadable", "CTX199900004.csv", "CTX200100002.csv")
    assert _reach(reach_inputs, "--pair", "base", "arm", "--workers", "1", *unread) == 3  # one of them reads
    assert _reach(reach_inputs, "--pair", "base", "arm", "--workers", "1", "--expect-docs", "5") == 3
    dup = reach_inputs["arub"] / "CTX200100002.csv"
    _write_doc(dup, [("Clear", 1, 1, "same name, other collection", 0)])
    assert _reach(reach_inputs, "--pair", "base", "arm", "--workers", "1") == 2
    assert "is in both" in capsys.readouterr().err


def test_reach_real_engine_agrees_in_and_out_of_process(tmp_path):
    samples = _ROOT / "data_samples" / "DOC_LINE_CATEG"
    names = sorted(p.name for p in samples.glob("CTX00000000[123].csv"))
    if len(names) != 3:
        pytest.skip("the committed sample documents are not present")
    inputs = {"arup": tmp_path / "arup", "arub": tmp_path / "arub"}
    for name, coll in zip(names, ("arup", "arup", "arub"), strict=True):
        inputs[coll].mkdir(exist_ok=True)
        (inputs[coll] / name).write_bytes((samples / name).read_bytes())
    outputs = []
    for workers in ("1", "2"):
        inputs["out"] = tmp_path / f"w{workers}" / "16r"
        arms = (("p50", "LOWPPL_CLEAR_MAX=50.0"), ("ship",), ("floor", "SHORT_GARBAGE_WITNESS_FLOOR=true"))
        rc = _reach(inputs, "--pair", "p50", "ship", "--pair", "ship", "floor", "--workers", workers, arms=arms)
        assert rc == 0
        summary = json.loads(Path(f"{inputs['out']}_summary.json").read_text(encoding="utf-8"))
        assert summary["lines_read"] == 15 and not summary["unreadable"]
        # LOWPPL_CLEAR_MAX 50 -> 35 changes only the route of the four sample lines in
        # [35, 50), never their category (tests/fixtures/pipeline_characterization.json).
        assert summary["pairs"]["p50->ship"]["changed_lines"] == 0
        outputs.append(
            (
                {k: v for k, v in summary["arms"].items()},
                {k: {x: y for x, y in v.items() if x != "changes_csv"} for k, v in summary["pairs"].items()},
                Path(f"{inputs['out']}_ship__floor_changes.csv").read_text(encoding="utf-8"),
            )
        )
    assert outputs[0] == outputs[1]


# ---------------------------------------------------------------------------
# 9. reach --page-ref and pagecheck
# ---------------------------------------------------------------------------


def _read_gz(path: Path) -> list[dict]:
    import gzip

    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_quality_band_is_document_hooks():
    import document_hook

    for c in range(4):
        for n in range(4):
            for t in range(4):
                assert cc.quality_band(c, n, t) == document_hook.quality_band(c, n, t)


def test_reach_pages_publish_scores_bands_flips_and_labelled_pages(reach_inputs, monkeypatch):
    monkeypatch.setattr(cc, "_categorise", _fake_categorise)
    rc = _reach(
        reach_inputs,
        "--pair", "base", "arm",
        "--labels", "gold", str(reach_inputs["labels"]),
        "--expect-unreadable", "CTX199900004.csv",
        "--page-ref", "base", "--page-min-labelled", "1", "--page-min-coverage", "0.5",
        "--workers", "1",
    )  # fmt: skip
    assert rc == 0
    out = reach_inputs["out"]
    ref = {(r["doc"], r["page_num"]): r for r in _read_gz(Path(f"{out}_pages_base.csv.gz"))}
    assert set(ref) == {("CTX200100001", "1"), ("CTX200100001", "2"), ("CTX200100002", "1"), ("CTX199900003", "3")}
    p1 = ref[("CTX200100001", "1")]
    assert (p1["lines"], p1["clear"], p1["batch_score"], p1["service_score"], p1["band"]) == (
        "2",
        "2",
        "0.9000",
        "0.9000",
        "Clear",
    )
    assert ref[("CTX199900003", "3")]["band"] == "Noisy" and ref[("CTX199900003", "3")]["collection"] == "arub"

    changed = {(r["doc"], r["page_num"]): r for r in _read_gz(Path(f"{out}_base__arm_pages.csv.gz"))}
    assert set(changed) == {("CTX200100001", "1"), ("CTX200100001", "2"), ("CTX199900003", "3")}
    # One of two Clear lines to Trash: the batch score keeps its Clear line, the service score averages both.
    c1 = changed[("CTX200100001", "1")]
    assert (c1["band_ref"], c1["band_arm"], c1["batch_arm"], c1["service_arm"], c1["d_clear"], c1["d_trash"]) == (
        "Clear", "Clear", "0.9000", "0.6000", "-1", "1"
    )  # fmt: skip
    assert changed[("CTX200100001", "2")]["batch_arm"] == ""  # no text line left on the page

    pages = json.loads(Path(f"{out}_summary.json").read_text(encoding="utf-8"))["pages"]
    assert pages["arms"]["base"]["pages"] == 4 and pages["arms"]["base"]["bands"] == {"Clear": 2, "Noisy": 2}
    assert pages["arms"]["arm"]["bands"] == {"Clear": 2, "Trash": 2}
    assert pages["reference_by_collection"] == {"arub": {"Noisy": 1}, "arup": {"Clear": 2, "Noisy": 1}}
    pp = pages["pairs"]["base->arm"]
    assert (pp["pages_changed"], pp["band_flips"], pp["band_transitions"]) == (3, 2, {"Noisy->Trash": 2})
    assert pp["batch_moved_ge_0.05"] == 2
    gold = pages["labelled"]["gold"]
    assert (gold["base"]["pages"], gold["base"]["band_agree"], gold["base"]["clear_share_mae"]) == (4, 2, 0.5)
    assert (gold["arm"]["band_agree"], gold["arm"]["clear_share_mae"], gold["arm"]["trash_share_mae"]) == (2, 0.25, 0.5)
    rows = _read_csv(Path(f"{out}_labelled_pages.csv"))
    assert len(rows) == 4 and {"human_Trash", "base_Clear", "arm_Trash"} <= set(rows[0])


def test_labelled_page_metrics_skip_thin_pages():
    row = ("gold", "arup", "D", "1", 10, 4, 4, 0, 0, 0, 0, 4, 0, 0, 0, 0)
    assert cc.labelled_page_metrics([row], ["a"], 5, 0.8) == {}
    assert cc.labelled_page_metrics([row], ["a"], 4, 0.4)["gold"]["a"]["band_agreement"] == 1.0


def _page_summary(arm_lines: dict, flips: int, moved: int, e2: dict, revert_lines: dict | None = None) -> dict:
    base = {"pages": 10, "band_pages": 10, "band_agree": 8, "clear_abs": 1.0, "trash_abs": 1.0, "clear_signed": 0.0}
    labelled = {"gold": {"ship": dict(base), "cand": {**base, **e2}, "old": dict(base), "fl": dict(base)}}
    pairs = {
        "ship->cand": {"labelled": arm_lines},
        "ship->fl": {"labelled": {"gold": {"fixed": 9, "broken": 0, "neither": 0, "clear_loss": 0}}},
    }
    page_pairs = {
        "ship->cand": {"pages_changed": flips + moved, "band_flips": flips, "abs_dbatch_hist": {"7": moved}},
        "ship->fl": {"pages_changed": 900, "band_flips": 900, "abs_dbatch_hist": {"9": 900}},
    }
    if revert_lines is not None:
        pairs["ship->old"] = {"labelled": revert_lines}
        page_pairs["ship->old"] = {"pages_changed": 1, "band_flips": 0, "abs_dbatch_hist": {"1": 1}}
    return {
        "pairs": pairs,
        "pages": {"reference": "ship", "arms": {"ship": {"pages": 1000}}, "pairs": page_pairs, "labelled": labelled},
    }


def test_pagecheck_go_hold_revert_and_record_only():
    good = {"gold": {"fixed": 5, "broken": 1, "neither": 0, "clear_loss": 0}}
    kw = {"revert_arm": "old", "record_only": ["fl"], "page_sources": ["gold"]}
    keep = {"gold": {"fixed": 0, "broken": 3, "neither": 0, "clear_loss": 0}}
    # material (12 of 1,000 flip), better lines, labelled pages not worse -> HOLD
    res = cc.pagecheck(_page_summary(good, 12, 0, {}, keep), "ship", **kw)
    assert res["verdict"] == "HOLD" and res["qualifying"] == ["cand"]
    # the floor arm qualifies on paper but is record only
    assert next(e for e in res["pairs"] if e["arm"] == "fl")["role"] == "record only"
    # immaterial -> GO
    assert cc.pagecheck(_page_summary(good, 1, 2, {}, keep), "ship", **kw)["verdict"] == "GO"
    # labelled pages worse -> GO (the candidate does not qualify)
    res = cc.pagecheck(_page_summary(good, 12, 0, {"band_agree": 7}, keep), "ship", **kw)
    assert res["verdict"] == "GO" and "labelled pages worse" in "; ".join(res["pairs"][0]["reasons"])
    # a source with Clear-loss -> GO
    loss = {"gold": {"fixed": 5, "broken": 1, "neither": 0, "clear_loss": 1}}
    assert cc.pagecheck(_page_summary(loss, 12, 0, {}, keep), "ship", **kw)["verdict"] == "GO"
    # against the state before it the shipped change is net broken on gold -> REVERT
    broke = {"gold": {"fixed": 4, "broken": 1, "neither": 0, "clear_loss": 0}}
    res = cc.pagecheck(_page_summary(good, 1, 0, {}, broke), "ship", **kw)
    assert res["verdict"] == "REVERT"
    assert res["pairs"][-1]["reasons"] == ["the shipped change is net broken on gold"]


def test_pagecheck_cli_writes_the_decision(tmp_path):
    good = {"gold": {"fixed": 5, "broken": 1, "neither": 0, "clear_loss": 0}}
    path = tmp_path / "s.json"
    path.write_text(json.dumps(_page_summary(good, 1, 0, {})), encoding="utf-8")
    argv = ["pagecheck", "--summary", str(path), "--ref", "ship", "--page-sources", "gold"]
    assert cc.main([*argv, "--record-only", "fl", "--out-prefix", str(tmp_path / "17")]) == 0
    assert json.loads((tmp_path / "17_decision.json").read_text(encoding="utf-8"))["verdict"] == "GO"
    assert (tmp_path / "17_decision.md").read_text(encoding="utf-8").startswith("# Page-level decision: **GO**")
    assert cc.main([*argv, "--ref", "other", "--out-prefix", str(tmp_path / "x")]) == 2


def test_reach_pages_match_aggregate_stat_on_the_real_engine(tmp_path):
    pd = pytest.importorskip("pandas")
    samples = _ROOT / "data_samples" / "DOC_LINE_CATEG"
    names = sorted(p.name for p in samples.glob("CTX00000000[123].csv"))
    if len(names) != 3:
        pytest.skip("the committed sample documents are not present")
    import aggregate_STAT
    import document_hook

    inputs = {"arup": tmp_path / "arup", "arub": tmp_path / "arub", "out": tmp_path / "o" / "17"}
    for name, coll in zip(names, ("arup", "arup", "arub"), strict=True):
        inputs[coll].mkdir(exist_ok=True)
        (inputs[coll] / name).write_bytes((samples / name).read_bytes())
    arms = (("ship",), ("p50", "LOWPPL_CLEAR_MAX=50.0"))
    assert _reach(inputs, "--pair", "ship", "p50", "--page-ref", "ship", "--workers", "1", arms=arms) == 0
    table = {(r["doc"], r["page_num"]): r for r in _read_gz(Path(f"{inputs['out']}_pages_ship.csv.gz"))}

    engine = cc._engine_module()
    numeric = [
        "quality_score",
        "word_count",
        "char_count",
        "word_weird",
        "lang_score",
        "perplex",
        "vowel_ratio",
        "rot_ratio",
    ]
    checked = 0
    for name in names:
        frame = pd.read_csv(samples / name, dtype=str, keep_default_na=False)
        new = engine.recategorize_dataframe(frame)
        for col in numeric:
            new[col] = pd.to_numeric(new[col], errors="coerce")
        stats = aggregate_STAT._sum_metrics(new, ["Clear", "Noisy", "Trash", "Non-text", "Empty"])
        for _, row in stats.iterrows():
            ours = table[(str(row["file"]), str(row["page_num"]))]
            if pd.notna(row["avg_quality_score"]):
                assert abs(float(ours["batch_score"]) - row["avg_quality_score"]) < 1e-4
            else:
                assert ours["batch_score"] == ""
            c, n, t = int(row["Clear"]), int(row["Noisy"]), int(row["Trash"])
            assert (int(ours["clear"]), int(ours["noisy"]), int(ours["trash"])) == (c, n, t)
            if c + n + t:
                assert ours["band"] == document_hook.quality_band(c, n, t)
            checked += 1
    assert checked == len(table) and checked > 0


# ---------------------------------------------------------------------------
# 10. recat --expect-missing, diff --skip / --summary-only
# ---------------------------------------------------------------------------


def test_recat_expect_missing_is_not_attempted_and_not_a_failure(corpus):
    assert _recat(corpus, "--expect-missing", "CTX200100004.csv") == 0
    assert not (corpus["out"] / "CTX200100004.csv").exists()
    linked = [p.name for d in (corpus["work"] / "shards").iterdir() for p in d.iterdir()]
    assert "CTX200100004.csv" not in linked
    assert _recat(corpus, "--expect-missing", "CTX299999999.csv") == 2


def test_diff_skip_and_summary_only(trees):
    for side in ("base", "arm"):
        (trees[side] / "arub" / "CTX198503026.csv").write_text("categ,file,text\nClear,x,y\n", encoding="utf-8")
    assert _diff(trees) == 2
    assert _diff(trees, "--skip", "CTX198503026.csv", "--summary-only", "--verify") == 0
    summary = json.loads((trees["out"] / "d_summary.json").read_text(encoding="utf-8"))
    assert summary["skipped"] == ["CTX198503026.csv"] and summary["changes_written"] is False
    assert summary["overall"]["changed_lines"] == 3 and summary["verify"]["agree"] is True
    assert _read_csv(trees["out"] / "d_changes.csv") == []
