#!/usr/bin/env python3
"""Corpus-scale change measurement for one categorisation arm (issue #3, stage 16).

Why this exists
---------------
On 2026-10-05 @david-spacil re-categorised both collections twice -- the shipped
config, and the same with ``SHORT_GARBAGE_WITNESS_FLOOR = true`` -- diffed the two
runs line by line, grouped the changed lines by text, and annotated the 200
largest groups plus 100 random tail lines blind. Both sidecars (Dana's 826 lines,
the 2,064 gold) had passed the floor; his corpus diff found ~3,920 fixed against
~13,509 broken, because the risk sat in strings repeated across documents that
neither sidecar contains (digest AG1-AG3). His scripts lived on his machine. This
tool is that method inside the repository, so every arm stage 16 runs is measured
the same way, and his labels can score any arm whose changes they cover.

Seven subcommands
-----------------
``recat``
    Sharded, resumable runner around ``tools/recategorize_from_csv.py``. The
    re-scorer is one sequential process; a collection of 113k documents wants N.
    Each shard is a directory of FILE symlinks (a symlinked directory globs to
    nothing before Python 3.13), outputs land flat in ``--out`` (the re-scorer
    mirrors names), a resume skips documents already written (the re-scorer
    writes ``.part`` and renames, so a present output is complete), and the run
    fails unless every input has an output. The arm's constants come from the
    environment (``ATRIUM_TEXT_UTILS_*``, inherited) and, for tunables,
    ``--override``. ``--infer-string false`` sets pandas'
    ``future.infer_string = False`` in every shard, as David's wrapper did.
    ``--expect-missing`` names documents known not to re-score (stage 18's
    ``CTX198503026.csv``): they are not attempted and not a failure; the caller
    decides what goes in their place.

``diff``
    Joins two re-scored trees document by document on ``(page_num, line_num)``
    and writes the changed lines, the touched documents and a summary
    (transitions overall / per collection / per decade, the ``pp_*`` flags of
    every arrival in ``Trash``, and the documents that carry the change).
    ``--verify`` recounts every transition through a second, independent reader
    (pandas, a dict keyed on the locator -- David's ``cmp_full.py`` semantics,
    where the first pass is positional -- his ``nezavisle.py``) and fails if the
    two disagree. ``--skip`` leaves named documents out; ``--summary-only``
    counts every change but writes no changed line, for whole-corpus diffs such
    as stage 18's 307 -> 1210 release record. ``--pages`` adds what end users
    read: every page's published outputs in both trees (the batch score, the
    service score, the band -- ``reach --page-ref``'s definitions), the band
    transitions and score moves between them, and the changed pages (keys and
    numbers, no text).

``score``
    Scores a changed-line list against annotated sheets in David's format: TOP
    sheets label a whole text group (``text`` + ``annotation``; the group key is
    his, the text casefolded with every non-word character removed), TAIL sheets
    label single lines (``example`` = ``document:page:line`` + ``annotation``).
    Top groups count line by line. The tail is a uniform sample of the
    REFERENCE run's changed lines outside every labelled group; the arm's tail is
    that population intersected with the arm's own changes, and the labelled tail
    lines still changed in the arm are a uniform sample of it -- scaled with a
    normal-approximation 95 % interval, as his ``vyhodnot.py``. Reports exact
    fixed / broken / neither, the ordinal reading (closer / farther on
    Trash < Noisy < Clear), ``Clear``-loss (annotated ``Clear`` sent to ``Trash``
    / ``Non-text``), ``Clear``-demoted, coverage, and an indicative per-class
    table. ``--expect-results`` compares the counts with a ``results.txt``: on
    David's own diff the tool must reproduce his figures exactly.

``pack``
    Builds a BLIND annotation pack from a changed-line list by his ``vzorky.py``
    method: the N largest groups plus a seeded uniform tail, no category in the
    sheets, the transitions in separate key files, and a ``frame.json``. The
    sheets carry his column names, so a returned pack feeds ``score`` unchanged.

``sample``
    A seeded fraction of the documents NOT in a list -- the untouched-document
    guard of stage 16's floor arms.

``reach``
    ``recat`` + ``diff`` without the trees. Every document is read once and
    re-scored IN MEMORY under each ``--arm`` (a name and the constants it
    overrides; none = the shipped config) through the re-scorer's own
    ``recategorize_dataframe`` -- the per-``file`` path ``ab_constant_eval`` uses
    too -- and for each ``--pair REF ARM`` the lines whose category differs are
    written in ``diff``'s changed-line format, so ``score`` and ``pack`` read them
    unchanged. No re-scored document is written anywhere (stage 16's constraint
    of 2026-10-08). ``--labels`` joins key-only sidecars (``file``, ``page_num``,
    ``line_num``, ``gold_categ``) onto every changed line: fixed / broken /
    neither and the ``Clear``-loss change per source, per pair and per document
    -- the per-document table is what pins this path to the per-part A/Bs of
    stage 16 (same rows, same engine, so the counts must be equal). A document
    that cannot be re-scored is reported by name; ``--expect-unreadable`` names
    the ones that are known (``CTX198503026.csv`` in ``DOC_LINE_CATEG_307``: no
    columns at all, pandas' ``EmptyDataError``), and any other exits 3.

    ``--page-ref ARM`` adds the pages (issue #3 stage 17): per arm and page,
    what the pipeline publishes -- the batch score (mean ``quality_score`` over
    Clear and Noisy lines, ``aggregate_STAT``), the service score (mean over
    every scored line, ``service/text_api``) and the band (``document_hook.
    quality_band``). ARM's full page table is written (keys and numbers, no
    text), each pair's changed pages, and every labelled page with the human
    and each arm's counts over its labelled lines; the summary carries band
    distributions, score quantiles, band transitions, score-change histograms
    and the labelled-page agreement (``labelled_page_metrics``).

``pagecheck``
    Stage 17's written rule over a ``reach --page-ref`` summary: a candidate is
    an area for improvement only if it improves the labelled lines with no
    source net broken and no ``Clear``-loss, does not worsen the labelled pages,
    and matters to enough pages. The shipped change is checked against the
    state before it under a revert condition. The verdict -- GO, HOLD or
    REVERT -- is what stage 18, the final re-scoring, is gated on.

What is safe to paste into the issue: everything printed to stdout and the
``*_summary.json`` / ``*_score.json`` / ``*_labelled.tsv`` / ``*_decision.*`` files and the page tables (no
line text).
``*_changes.csv``, ``*_groups.csv`` and packs carry line text.
"""

from __future__ import annotations

import argparse
import ast
import csv
import gzip
import json
import math
import random
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from functools import partial
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

try:
    csv.field_size_limit(sys.maxsize)
except OverflowError:  # 32-bit C long
    csv.field_size_limit(2**31 - 1)

CATEGORIES = ("Clear", "Noisy", "Trash", "Non-text", "Empty")
RANK = {"Trash": 0, "Noisy": 1, "Clear": 2}
LOSS_TARGETS = {"Trash", "Non-text"}
VERDICTS = ("fixed", "broken", "neither")
ORDINAL = ("closer", "farther", "same", "n/a")
CHANGE_FIELDS = (
    "collection",
    "doc",
    "decade",
    "page_num",
    "line_num",
    "from",
    "to",
    "text",
    "pp_arm",
    "pp_base",
)
_RE_YEAR = re.compile(r"[A-Z]{3}(\d{4})")
_RE_NONWORD = re.compile(r"[\W_]+")

#: Indicative classes, keyed on the strings @david-spacil's 2026-10-05 pack names.
#: First match wins; a class row is a reading aid, not a measurement of the class.
CLASS_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("archaia", re.compile(r"archai|achaia", re.I)),
    ("vodovod", re.compile(r"vodovod", re.I)),
    ("iv_cell", re.compile(r"^\s*[IVX]{1,4}-\d{1,2}\s*$")),
    ("roman_letter", re.compile(r"^\W*[A-Z]{1,2}[IVXLCDM]{2,7}[a-z]?\W*$")),
    (
        "latin_term",
        re.compile(
            r"\b(mammalia|rubus|praeauricularis|hyoid\w*|urtica|euonymus|radioulna|stachys|papaver|"
            r"aethusa|capra|cervus|lepus\w*|fragaria|ilium|llium|anser\w*|triticum|equus)\b",
            re.I,
        ),
    ),
)

# Runs inside every shard: optionally pins pandas' string inference the way
# @david-spacil's 2026-10-05 wrapper did, then hands over to the re-scorer as
# its own __main__ (argv[1] is the mode, argv[2] the runner script).
_SHARD_WRAPPER = (
    "import runpy, sys\n"
    "mode = sys.argv[1]\n"
    "if mode == 'false':\n"
    "    try:\n"
    "        import pandas as pd\n"
    "        pd.set_option('future.infer_string', False)\n"
    "    except Exception as exc:  # noqa: BLE001\n"
    "        print(f'note: future.infer_string not set: {exc}', file=sys.stderr)\n"
    "sys.argv = sys.argv[2:]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def group_key(text: str) -> str:
    """@david-spacil's grouping key: casefolded, every non-word character removed."""
    norm = _RE_NONWORD.sub("", text.casefold())
    return norm or text


def decade(stem: str) -> str:
    m = _RE_YEAR.search(stem)
    if not m:
        return "?"
    year = int(m.group(1))
    return f"{year // 10 * 10}" if 1900 <= year <= 2030 else "?"


def line_id(doc: str, page: str, line: str) -> str:
    return f"{doc}:{page}:{line}"


def classify(text: str) -> str:
    for name, rx in CLASS_RULES:
        if rx.search(text):
            return name
    return "other"


def read_names(path: Path) -> list[str]:
    """One document file name per line; blank lines and ``#`` comments skipped."""
    names = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        name = raw.strip()
        if name and not name.startswith("#"):
            names.append(Path(name).name)
    return names


def csv_files(directory: Path) -> dict[str, Path]:
    """Flat ``*.csv`` of a directory, keyed by file name (symlinks followed)."""
    return {p.name: p for p in sorted(directory.glob("*.csv")) if p.is_file()}


def verdict(label: str, frm: str, to: str) -> str:
    if to == label and frm != label:
        return "fixed"
    if frm == label and to != label:
        return "broken"
    return "neither"


def ordinal(label: str, frm: str, to: str) -> str:
    if label not in RANK or frm not in RANK or to not in RANK:
        return "n/a"
    before, after = abs(RANK[frm] - RANK[label]), abs(RANK[to] - RANK[label])
    return "closer" if after < before else "farther" if after > before else "same"


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


# ---------------------------------------------------------------------------
# recat
# ---------------------------------------------------------------------------


def _count_lines(path: Path) -> int:
    n = 0
    with open(path, "rb") as fh:
        for _ in fh:
            n += 1
    return n


def cmd_recat(args: argparse.Namespace) -> int:
    in_dir = Path(args.input_dir)
    out = Path(args.out)
    work = Path(args.work)
    files = csv_files(in_dir)
    if not files:
        print(f"error: no *.csv in {in_dir}", file=sys.stderr)
        return 2
    if args.only:
        wanted = read_names(Path(args.only))
        missing_inputs = [n for n in wanted if n not in files]
        if missing_inputs:
            print(
                f"error: {len(missing_inputs)} name(s) in {args.only} are not in {in_dir}, e.g. {missing_inputs[:3]}",
                file=sys.stderr,
            )
            return 2
        files = {n: files[n] for n in wanted}
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    stray = list(out.glob("*.part"))
    for part in stray:
        part.unlink()
    removed = 0
    if args.check_lines:
        for name, src in files.items():
            dst = out / name
            if dst.exists() and _count_lines(dst) != _count_lines(src):
                dst.unlink()
                removed += 1
    expected_missing = set(args.expect_missing or [])
    unknown = sorted(expected_missing - set(files))
    if unknown:
        print(f"error: --expect-missing names no input document: {unknown[:3]}", file=sys.stderr)
        return 2
    todo = [name for name in files if not (out / name).exists() and name not in expected_missing]
    done = sum(1 for name in files if (out / name).exists())
    print(
        f"recat: {len(files):,} input document(s) in {in_dir}; {done:,} already written, {len(todo):,} to do"
        + (f", {len(expected_missing)} expected missing" if expected_missing else "")
        + f"; {len(stray)} stray .part removed"
        + (f"; {removed} short output(s) removed" if args.check_lines else ""),
        flush=True,
    )

    rcs: list[int] = []
    t0 = time.time()
    if todo:
        shards = max(1, min(args.shards, len(todo)))
        shard_root = work / "shards"
        shutil.rmtree(shard_root, ignore_errors=True)
        dirs = [shard_root / f"s{i:02d}" for i in range(shards)]
        for d in dirs:
            d.mkdir(parents=True)
        for k, name in enumerate(todo):
            (dirs[k % shards] / name).symlink_to(files[name].resolve())
        runner = str(Path(args.runner).resolve())
        procs = []
        for i, d in enumerate(dirs):
            cmd = [
                args.python,
                "-c",
                _SHARD_WRAPPER,
                args.infer_string,
                runner,
                "--input-dir",
                str(d),
                "--out",
                str(out),
            ]
            if args.override:
                cmd += ["--override", *args.override]
            stdout = open(work / f"s{i:02d}.log", "w", encoding="utf-8")  # noqa: SIM115 - closed below
            stderr = open(work / f"s{i:02d}.err", "w", encoding="utf-8")  # noqa: SIM115
            procs.append((subprocess.Popen(cmd, stdout=stdout, stderr=stderr, cwd=args.cwd), stdout, stderr))
        print(f"recat: {shards} shard(s) started; logs in {work}", flush=True)
        for proc, stdout, stderr in procs:
            rcs.append(proc.wait())
            stdout.close()
            stderr.close()

    missing = [name for name in files if not (out / name).exists()]
    (work / "missing.txt").write_text("".join(f"{n}\n" for n in missing), encoding="utf-8")
    minutes = (time.time() - t0) / 60
    bad = [i for i, rc in enumerate(rcs) if rc != 0]
    print(
        f"recat: {len(files) - len(missing):,} / {len(files):,} written in {minutes:.1f} min; "
        f"shards with a non-zero exit: {len(bad)}" + (f" ({', '.join(f's{i:02d}' for i in bad)})" if bad else ""),
        flush=True,
    )
    known = [n for n in missing if n in expected_missing]
    if known:
        print(f"recat: {len(known)} known-unreadable document(s) not re-scored, left to the caller: {known[:5]}")
    unexpected = [n for n in missing if n not in expected_missing]
    if unexpected:
        print(
            f"recat: {len(unexpected):,} document(s) have no output (list: {work / 'missing.txt'}); "
            "re-run the same command to resume",
            file=sys.stderr,
        )
        return 1
    return 0


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def _header_index(header: Sequence[str], path: Path) -> dict[str, int]:
    idx = {name: i for i, name in enumerate(header)}
    for col in ("categ", "page_num", "line_num", "text"):
        if col not in idx:
            raise ValueError(f"{path}: no {col!r} column")
    return idx


def _diff_document(task: tuple[str, str, str, str], pages: bool = False) -> dict:
    label, name, base_path, arm_path = task
    stem = Path(name).stem
    dec = decade(stem)
    by_page: dict[str, list[tuple[str, str, str, str]]] = {}
    out = {
        "label": label,
        "doc": stem,
        "rows_base": 0,
        "rows_arm": 0,
        "unpaired": 0,
        "transitions": Counter(),
        "trash_pp": Counter(),
        "changes": [],
        "error": "",
    }
    try:
        with open(base_path, encoding="utf-8", newline="") as fb, open(arm_path, encoding="utf-8", newline="") as fa:
            rb, ra = csv.reader(fb), csv.reader(fa)
            hb, ha = next(rb, None), next(ra, None)
            if hb is None or ha is None:
                if (hb is None) != (ha is None):
                    out["error"] = "one side is empty"
                return out
            ib, ia = _header_index(hb, Path(base_path)), _header_index(ha, Path(arm_path))
            qb, qa = ib.get("quality_score"), ia.get("quality_score")
            pp_b = [(h, i) for i, h in enumerate(hb) if h.startswith("pp_")]
            pp_a = [(h, i) for i, h in enumerate(ha) if h.startswith("pp_")]
            for row_b in rb:
                out["rows_base"] += 1
                row_a = next(ra, None)
                if row_a is None:
                    out["unpaired"] += 1
                    continue
                out["rows_arm"] += 1
                key_b = (row_b[ib["page_num"]], row_b[ib["line_num"]])
                key_a = (row_a[ia["page_num"]], row_a[ia["line_num"]])
                if key_b != key_a:
                    out["unpaired"] += 1
                    continue
                cb, ca = row_b[ib["categ"]], row_a[ia["categ"]]
                if pages:
                    by_page.setdefault(key_b[0], []).append(
                        (
                            cb,
                            row_b[qb] if qb is not None and qb < len(row_b) else "",
                            ca,
                            row_a[qa] if qa is not None and qa < len(row_a) else "",
                        )
                    )
                if cb == ca:
                    continue
                out["transitions"][f"{cb}->{ca}"] += 1
                on_a = ";".join(h for h, i in pp_a if i < len(row_a) and row_a[i] == "True")
                on_b = ";".join(h for h, i in pp_b if i < len(row_b) and row_b[i] == "True")
                if ca == "Trash" and cb != "Trash":
                    out["trash_pp"][on_a or "(none)"] += 1
                out["changes"].append((label, stem, dec, key_b[0], key_b[1], cb, ca, row_b[ib["text"]], on_a, on_b))
            for _ in ra:
                out["rows_arm"] += 1
                out["unpaired"] += 1
    except (OSError, ValueError, IndexError, csv.Error) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    if pages and not out["error"]:
        out.update(_diff_pages(label, stem, dec, by_page))
    return out


def _diff_pages(label: str, stem: str, dec: str, by_page: dict[str, list[tuple[str, str, str, str]]]) -> dict:
    """``diff --pages``, one document: both trees' page profiles and the pages whose outputs differ."""
    base, arm, change = _page_side(), _page_side(), _page_change()
    for page in sorted(by_page, key=_page_sort):
        rows = by_page[page]
        idx = range(len(rows))
        x = page_stats([r[0] for r in rows], [_score(r[1]) for r in rows], idx)
        y = page_stats([r[2] for r in rows], [_score(r[3]) for r in rows], idx)
        _page_side_add(base, x)
        _page_side_add(arm, y)
        _page_change_add(change, x, y, (label, stem, dec, page, len(rows)))
    return {"page_base": base, "page_arm": arm, "page_change": change}


def _verify_document(task: tuple[str, str, str, str]) -> tuple[str, Counter]:
    """Independent recount: pandas, a dict keyed on the locator (last row wins)."""
    import pandas as pd

    label, _name, base_path, arm_path = task
    cols = ["categ", "page_num", "line_num"]

    def read(path: str) -> dict:
        try:
            frame = pd.read_csv(path, usecols=cols, dtype=str, keep_default_na=False)
        except pd.errors.EmptyDataError:
            return {}
        locators = zip(frame["page_num"], frame["line_num"], strict=True)
        return dict(zip(locators, frame["categ"], strict=True))

    old, new = read(base_path), read(arm_path)
    counts: Counter = Counter()
    for key, cb in old.items():
        ca = new.get(key)
        if ca is not None and ca != cb:
            counts[f"{cb}->{ca}"] += 1
    return label, counts


def cmd_diff(args: argparse.Namespace) -> int:
    expect = {label: Path(path) for label, path in (args.expect_list or [])}
    tasks: list[tuple[str, str, str, str]] = []
    problems: list[str] = []
    per_pair_files: dict[str, dict[str, int]] = {}
    skip = set(args.skip or [])
    for label, base_dir, arm_dir in args.pair:
        base, arm = csv_files(Path(base_dir)), csv_files(Path(arm_dir))
        for name in skip:
            base.pop(name, None)
            arm.pop(name, None)
        extra = sorted(set(arm) - set(base))
        lacking = sorted(set(base) - set(arm))
        if extra:
            problems.append(f"{label}: {len(extra)} arm document(s) absent from the base, e.g. {extra[:3]}")
        if lacking and not args.subset:
            problems.append(f"{label}: {len(lacking)} base document(s) absent from the arm, e.g. {lacking[:3]}")
        if label in expect:
            wanted = set(read_names(expect[label]))
            if set(arm) != wanted:
                problems.append(
                    f"{label}: the arm holds {len(arm):,} document(s), the expected list {len(wanted):,} "
                    f"({len(wanted - set(arm)):,} missing, {len(set(arm) - wanted):,} unexpected)"
                )
        per_pair_files[label] = {"base_documents": len(base), "arm_documents": len(arm)}
        tasks += [(label, name, str(base[name]), str(arm[name])) for name in sorted(set(arm) & set(base))]
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return 2

    prefix = Path(args.out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    pairs: dict[str, dict] = defaultdict(
        lambda: {
            "documents_compared": 0,
            "rows_compared": 0,
            "unpaired": 0,
            "changed_lines": 0,
            "changed_documents": 0,
            "transitions": Counter(),
        }
    )
    by_decade: dict[str, Counter] = defaultdict(Counter)
    trash_pp: Counter = Counter()
    per_doc: Counter = Counter()
    errors: list[str] = []
    touched: list[tuple[str, str]] = []
    page_path = Path(f"{prefix}_pages.csv.gz") if args.pages else None
    page_acc: dict[str, dict] = defaultdict(
        lambda: {"base": _page_side(), "arm": _page_side(), "change": _page_change()}
    )
    t0 = time.time()
    with (
        open(f"{prefix}_changes.csv", "w", encoding="utf-8", newline="") as fh,
        gzip.open(page_path, "wt", encoding="utf-8", newline="") if page_path else nullcontext() as fp,
        ProcessPoolExecutor(max_workers=args.workers) as ex,
    ):
        writer = csv.writer(fh)
        writer.writerow(CHANGE_FIELDS)
        page_writer = csv.writer(fp) if fp is not None else None
        if page_writer is not None:
            page_writer.writerow(PAGE_DIFF_FIELDS)
        for res in ex.map(partial(_diff_document, pages=args.pages), tasks, chunksize=64):
            agg = pairs[res["label"]]
            agg["documents_compared"] += 1
            agg["rows_compared"] += res["rows_base"]
            agg["unpaired"] += res["unpaired"]
            if res["error"]:
                errors.append(f"{res['label']}/{res['doc']}: {res['error']}")
            if page_writer is not None and "page_base" in res:
                acc = page_acc[res["label"]]
                _page_side_merge(acc["base"], res["page_base"])
                _page_side_merge(acc["arm"], res["page_arm"])
                _page_change_merge(acc["change"], res["page_change"])
                page_writer.writerows(res["page_change"]["rows"])
            if res["changes"]:
                agg["changed_documents"] += 1
                agg["changed_lines"] += len(res["changes"])
                agg["transitions"].update(res["transitions"])
                by_decade[decade(res["doc"])].update(res["transitions"])
                trash_pp.update(res["trash_pp"])
                per_doc[(res["label"], res["doc"])] += len(res["changes"])
                touched.append((res["label"], f"{res['doc']}.csv"))
                if not args.summary_only:
                    writer.writerows(res["changes"])

    with open(f"{prefix}_touched.tsv", "w", encoding="utf-8") as fh:
        for label, name in sorted(touched):
            fh.write(f"{label}\t{name}\n")

    total_changed = sum(p["changed_lines"] for p in pairs.values())
    transitions: Counter = Counter()
    for p in pairs.values():
        transitions.update(p["transitions"])
    concentration = [
        {"collection": label, "doc": doc, "changes": n, "share": round(n / total_changed, 4)}
        for (label, doc), n in per_doc.most_common()
        if total_changed and n / total_changed > args.concentration
    ]
    top_docs = [
        {"collection": label, "doc": doc, "changes": n, "share": round(n / total_changed, 4) if total_changed else 0.0}
        for (label, doc), n in per_doc.most_common(10)
    ]
    summary = {
        "pairs": {
            label: {
                **per_pair_files.get(label, {}),
                **{k: (dict(v) if isinstance(v, Counter) else v) for k, v in agg.items()},
            }
            for label, agg in pairs.items()
        },
        "overall": {
            "documents_compared": sum(p["documents_compared"] for p in pairs.values()),
            "rows_compared": sum(p["rows_compared"] for p in pairs.values()),
            "unpaired": sum(p["unpaired"] for p in pairs.values()),
            "changed_lines": total_changed,
            "changed_documents": len(per_doc),
            "transitions": dict(transitions.most_common()),
            "trash_arrivals_by_pp": dict(trash_pp.most_common()),
            "by_decade": {d: dict(c.most_common()) for d, c in sorted(by_decade.items())},
            "documents_over_threshold": concentration,
            "concentration_threshold": args.concentration,
            "top_documents": top_docs,
        },
        "errors": errors[:50],
        "error_count": len(errors),
        "skipped": sorted(skip),
        "changes_written": not args.summary_only,
        "minutes": round((time.time() - t0) / 60, 1),
    }
    if args.pages:
        everything = {"base": _page_side(), "arm": _page_side(), "change": _page_change()}
        for acc in page_acc.values():
            _page_side_merge(everything["base"], acc["base"])
            _page_side_merge(everything["arm"], acc["arm"])
            _page_change_merge(everything["change"], acc["change"])

        def _block(acc: dict) -> dict:
            return {
                "base": _page_side_summary(acc["base"]),
                "arm": _page_side_summary(acc["arm"]),
                **_page_change_summary(acc["change"]),
            }

        summary["pages"] = {
            "file": str(page_path),
            "pairs": {label: _block(acc) for label, acc in sorted(page_acc.items())},
            "overall": _block(everything),
        }

    rc = 0
    if args.verify:
        recount: dict[str, Counter] = defaultdict(Counter)
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for label, counts in ex.map(_verify_document, tasks, chunksize=64):
                recount[label].update(counts)
        agree = all(dict(recount[label]) == dict(pairs[label]["transitions"]) for label in set(pairs) | set(recount))
        summary["verify"] = {
            "agree": agree,
            "transitions": {label: dict(c.most_common()) for label, c in recount.items()},
        }
        if not agree:
            rc = 3

    Path(f"{prefix}_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    ov = summary["overall"]
    print(
        f"diff: {ov['documents_compared']:,} document(s), {ov['rows_compared']:,} line(s) compared "
        f"in {summary['minutes']} min"
    )
    for label, p in summary["pairs"].items():
        print(
            f"  {label}: {p['documents_compared']:,} docs, {p['rows_compared']:,} lines, "
            f"{p['changed_lines']:,} changed in {p['changed_documents']:,} docs, unpaired {p['unpaired']:,}"
        )
    print(f"  changed lines: {total_changed:,} in {ov['changed_documents']:,} document(s)")
    for t, n in ov["transitions"].items():
        print(f"    {t:<20} {n:>10,}")
    if ov["trash_arrivals_by_pp"]:
        print("  arrivals in Trash, by the arm's pp_* flags:")
        for flags, n in ov["trash_arrivals_by_pp"].items():
            print(f"    {flags:<40} {n:>8,}")
    if concentration:
        print(f"  documents holding > {args.concentration:.0%} of the change:")
        for c in concentration:
            print(f"    {c['collection']}/{c['doc']}: {c['changes']:,} ({c['share']:.1%})")
    if args.verify:
        print(f"  independent recount agrees: {summary['verify']['agree']}")
    if args.pages:
        pg = summary["pages"]["overall"]
        print(
            f"  pages: {pg['base']['pages']:,}; {pg['pages_changed']:,} changed, {pg['band_flips']:,} band flip(s), "
            f"{pg['batch_moved_ge_0.05']:,} batch score(s) moved >= 0.05"
        )
        print(f"    bands, base: {pg['base']['bands']}")
        print(f"    bands, arm:  {pg['arm']['bands']}")
        for t, n in list(pg["band_transitions"].items())[:6]:
            print(f"    {t:<20} {n:>10,}")
    if errors:
        print(f"error: {len(errors)} document(s) could not be read; first: {errors[0]}", file=sys.stderr)
        rc = rc or 2
    if ov["unpaired"]:
        print(f"error: {ov['unpaired']:,} line(s) could not be paired on (page_num, line_num)", file=sys.stderr)
        rc = rc or 2
    if rc == 3:
        print("error: the independent recount disagrees with the positional pass", file=sys.stderr)
    return rc


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


def load_changes(paths: Iterable[str]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        with open(path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                row["id"] = line_id(row["doc"], row["page_num"], row["line_num"])
                row["key"] = group_key(row["text"])
                rows.append(row)
    return rows


def _label(raw: str | None) -> str:
    value = (raw or "").strip()
    return value if value in CATEGORIES else ""


def load_top_labels(paths: Iterable[str]) -> tuple[dict[str, str], set[str]]:
    """Group key -> label, and the keys a sheet lists without a usable label."""
    labels: dict[str, str] = {}
    unlabelled: set[str] = set()
    for path in paths:
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if not {"text", "annotation"} <= set(reader.fieldnames or ()):
                raise ValueError(f"{path}: a top sheet needs 'text' and 'annotation' columns")
            for row in reader:
                key, lab = group_key(row["text"]), _label(row["annotation"])
                if not lab:
                    unlabelled.add(key)
                    continue
                if labels.get(key, lab) != lab:
                    raise ValueError(f"{path}: group {row['text']!r} carries two labels ({labels[key]} and {lab})")
                labels[key] = lab
    return labels, unlabelled - set(labels)


def load_tail_labels(paths: Iterable[str]) -> tuple[dict[str, str], set[str]]:
    labels: dict[str, str] = {}
    unlabelled: set[str] = set()
    for path in paths:
        with open(path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if not {"example", "annotation"} <= set(reader.fieldnames or ()):
                raise ValueError(f"{path}: a tail sheet needs 'example' and 'annotation' columns")
            for row in reader:
                lid, lab = row["example"].strip(), _label(row["annotation"])
                if lab:
                    labels[lid] = lab
                else:
                    unlabelled.add(lid)
    return labels, unlabelled - set(labels)


def parse_results(path: Path) -> tuple[dict[str, int], dict[str, int]]:
    """``top {...} n | tail {...} m`` -- the first line of David's results.txt."""
    first = path.read_text(encoding="utf-8").splitlines()[0]
    m = re.match(r"\s*top\s+(\{.*?\})\s+\d+\s*\|\s*tail\s+(\{.*?\})\s+\d+", first)
    if not m:
        raise ValueError(f"{path}: first line is not 'top {{...}} n | tail {{...}} m'")
    return ast.literal_eval(m.group(1)), ast.literal_eval(m.group(2))


def _scaled(count: int, n: int, population: int) -> dict:
    if not n:
        return {"sample": count, "estimate": None, "low": None, "high": None}
    p = count / n
    se = math.sqrt(p * (1 - p) / n)
    return {
        "sample": count,
        "estimate": round(p * population, 3),
        "low": round((p - 1.96 * se) * population, 3),
        "high": round((p + 1.96 * se) * population, 3),
    }


def score_changes(
    arm: list[dict],
    reference: list[dict],
    top_labels: dict[str, str],
    top_unlabelled: set[str],
    tail_labels: dict[str, str],
    tail_unlabelled: set[str],
) -> dict:
    labelled_or_listed = set(top_labels) | top_unlabelled
    ref_tail_ids = {r["id"] for r in reference if r["key"] not in labelled_or_listed}

    buckets: Counter = Counter()
    top = {"verdict": Counter(), "ordinal": Counter(), "clear_loss": 0, "clear_demoted": 0}
    tail = {"verdict": Counter(), "ordinal": Counter(), "clear_loss": 0, "clear_demoted": 0}
    per_class: dict[str, Counter] = defaultdict(Counter)
    groups: dict[str, dict] = {}
    tail_population = 0
    tail_sampled = 0
    tail_sample_unlabelled = 0
    for row in arm:
        frm, to, key = row["from"], row["to"], row["key"]
        if key in top_labels:
            lab = top_labels[key]
            bucket, acc = "top", top
            v = verdict(lab, frm, to)
            per_class[classify(row["text"])][v] += 1
        elif key in top_unlabelled:
            buckets["excluded"] += 1
            continue
        elif row["id"] in ref_tail_ids:
            tail_population += 1
            buckets["tail_population"] += 1
            if row["id"] in tail_labels:
                lab, acc = tail_labels[row["id"]], tail
                tail_sampled += 1
            else:
                if row["id"] in tail_unlabelled:
                    tail_sample_unlabelled += 1
                continue
            bucket = None
        else:
            buckets["new"] += 1
            g = groups.setdefault(key, {"text": Counter(), "lines": 0, "label": "", "transitions": Counter()})
            g["text"][row["text"]] += 1
            g["lines"] += 1
            g["transitions"][f"{frm}->{to}"] += 1
            continue
        if bucket:
            buckets[bucket] += 1
            g = groups.setdefault(key, {"text": Counter(), "lines": 0, "label": lab, "transitions": Counter()})
            g["text"][row["text"]] += 1
            g["lines"] += 1
            g["transitions"][f"{frm}->{to}"] += 1
        acc["verdict"][verdict(lab, frm, to)] += 1
        acc["ordinal"][ordinal(lab, frm, to)] += 1
        if lab == "Clear" and to in LOSS_TARGETS and frm not in LOSS_TARGETS:
            acc["clear_loss"] += 1
        if lab == "Clear" and frm == "Clear" and to != "Clear":
            acc["clear_demoted"] += 1

    top_lines = sum(top["verdict"].values())
    totals: dict[str, dict] = {}
    for name in (*VERDICTS, "closer", "farther", "clear_loss", "clear_demoted"):
        if name in VERDICTS:
            t, s = top["verdict"][name], tail["verdict"][name]
        elif name in ORDINAL:
            t, s = top["ordinal"][name], tail["ordinal"][name]
        else:
            t, s = top[name], tail[name]
        scaled = _scaled(s, tail_sampled, tail_population)
        est = t + (s / tail_sampled * tail_population if tail_sampled else 0.0)
        totals[name] = {
            "top": t,
            "tail": scaled,
            "total_estimate": round(est, 3),
            "share": round(est / (top_lines + tail_population), 4) if (top_lines + tail_population) else None,
        }
    return {
        "arm_changed_lines": len(arm),
        "reference_changed_lines": len(reference),
        "coverage": {
            "top_labelled": buckets["top"],
            "tail_population": buckets["tail_population"],
            "excluded_unlabelled_groups": buckets["excluded"],
            "new_uncovered": buckets["new"],
            "covered_share": round((buckets["top"] + buckets["tail_population"]) / len(arm), 4) if arm else None,
        },
        "tail": {
            "population": tail_population,
            "sampled_labelled": tail_sampled,
            "sampled_unlabelled": tail_sample_unlabelled,
        },
        "top_counts": {k: top["verdict"][k] for k in VERDICTS},
        "tail_counts": {k: tail["verdict"][k] for k in VERDICTS},
        "totals": totals,
        "per_class_top": {c: {k: v[k] for k in VERDICTS} for c, v in sorted(per_class.items())},
        "_groups": groups,
    }


def cmd_score(args: argparse.Namespace) -> int:
    arm = load_changes(args.changes)
    reference = load_changes(args.reference_changes) if args.reference_changes else arm
    try:
        top_labels, top_unlabelled = load_top_labels(args.top or [])
        tail_labels, tail_unlabelled = load_tail_labels(args.tail or [])
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    res = score_changes(arm, reference, top_labels, top_unlabelled, tail_labels, tail_unlabelled)
    groups = res.pop("_groups")

    per_doc = Counter((r["collection"], r["doc"]) for r in arm)
    res["documents_over_threshold"] = [
        {"collection": c, "doc": d, "changes": n, "share": round(n / len(arm), 4)}
        for (c, d), n in per_doc.most_common()
        if arm and n / len(arm) > args.concentration
    ]
    prefix = Path(args.out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{prefix}_score.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    with open(f"{prefix}_groups.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["key", "text", "lines", "label", "transitions"])
        for key, g in sorted(groups.items(), key=lambda kv: (-kv[1]["lines"], kv[0])):
            w.writerow(
                [
                    key,
                    g["text"].most_common(1)[0][0],
                    g["lines"],
                    g["label"] or "(uncovered)",
                    "; ".join(f"{t} {n}" for t, n in g["transitions"].most_common()),
                ]
            )

    cov, tl = res["coverage"], res["tail"]
    print(
        f"score: {res['arm_changed_lines']:,} changed line(s) in the arm; reference {res['reference_changed_lines']:,}"
    )
    print(
        f"  coverage: top-labelled {cov['top_labelled']:,} | tail population {cov['tail_population']:,} "
        f"(sample {tl['sampled_labelled']} labelled) | unlabelled groups {cov['excluded_unlabelled_groups']:,} | "
        f"new, uncovered {cov['new_uncovered']:,}  -> covered {cov['covered_share']}"
    )
    print(f"  top {res['top_counts']}  |  tail sample {res['tail_counts']}")
    print(f"  {'':<14} {'top':>8} {'tail ~':>9} {'tail 95% CI':>21} {'total ~':>9} {'share':>7}")
    for name, t in res["totals"].items():
        tail_est = t["tail"]["estimate"]
        ci = f"[{_fmt(t['tail']['low'])}; {_fmt(t['tail']['high'])}]" if tail_est is not None else "-"
        share = f"{t['share']:.1%}" if t["share"] is not None else "-"
        print(
            f"  {name:<14} {t['top']:>8,} {(_fmt(tail_est) if tail_est is not None else '-'):>9} {ci:>21} "
            f"{_fmt(t['total_estimate']):>9} {share:>7}"
        )
    if res["per_class_top"]:
        print("  top groups by indicative class (fixed / broken / neither):")
        for c, v in res["per_class_top"].items():
            print(f"    {c:<14} {v['fixed']:>7,} {v['broken']:>7,} {v['neither']:>7,}")
    if res["documents_over_threshold"]:
        print(f"  documents holding > {args.concentration:.0%} of the arm's change:")
        for c in res["documents_over_threshold"]:
            print(f"    {c['collection']}/{c['doc']}: {c['changes']:,} ({c['share']:.1%})")

    if args.expect_results:
        want_top, want_tail = parse_results(Path(args.expect_results))
        got_top = {k: v for k, v in res["top_counts"].items() if v or k in want_top}
        got_tail = {k: v for k, v in res["tail_counts"].items() if v or k in want_tail}
        ok = got_top == want_top and got_tail == want_tail
        print(f"  expected top {want_top} | tail {want_tail} -> {'REPRODUCED' if ok else 'DIFFERENT'}")
        if not ok:
            return 4
    return 0


# ---------------------------------------------------------------------------
# pack
# ---------------------------------------------------------------------------


def cmd_pack(args: argparse.Namespace) -> int:
    rows = load_changes(args.changes)
    excluded: set[str] = set()
    for sheet in args.exclude_top or []:
        labels, unlabelled = load_top_labels([sheet])
        excluded |= set(labels) | unlabelled
    considered = [r for r in rows if r["key"] not in excluded]
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in considered:
        groups[r["key"]].append(r)
    top = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))[: args.top]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tag = args.tag

    total = len(considered)
    cum = 0
    sheet, key_rows = [], []
    for rank, (_key, members) in enumerate(top, 1):
        cum += len(members)
        variants = Counter(m["text"] for m in members)
        first = members[0]
        sheet.append(
            {
                "rank": rank,
                "occurrences": len(members),
                "cumulative_share": round(cum / total, 3) if total else 0,
                "text": variants.most_common(1)[0][0],
                "variants": len(variants),
                "other_variants": " | ".join(t for t, _ in variants.most_common(4)[1:]),
                "documents": len({(m["collection"], m["doc"]) for m in members}),
                "example": first["id"],
                "annotation": "",
                "note": "",
            }
        )
        key_rows.append(
            {
                "rank": rank,
                "text": sheet[-1]["text"],
                "transitions": "; ".join(
                    f"{a} {n}" for a, n in Counter(f"{m['from']}->{m['to']}" for m in members).most_common()
                ),
            }
        )

    top_keys = {k for k, _ in top}
    population = [r for r in considered if r["key"] not in top_keys]
    rng = random.Random(args.seed)
    sample = rng.sample(population, min(args.tail, len(population)))
    rng.shuffle(sample)

    def write(name: str, fields: Sequence[str], data: list[dict]) -> None:
        with open(out / name, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(data)

    write(f"{tag}_top.csv", list(sheet[0]) if sheet else ["rank", "text", "example", "annotation", "note"], sheet)
    write(f"{tag}_top_key.csv", ["rank", "text", "transitions"], key_rows)
    if args.tail:
        write(
            f"{tag}_tail.csv",
            ["id", "text", "example", "annotation", "note"],
            [
                {"id": i, "text": r["text"], "example": r["id"], "annotation": "", "note": ""}
                for i, r in enumerate(sample, 1)
            ],
        )
        write(
            f"{tag}_tail_key.csv",
            ["id", "text", "group_occurrences", "from", "to"],
            [
                {
                    "id": i,
                    "text": r["text"],
                    "group_occurrences": len(groups[r["key"]]),
                    "from": r["from"],
                    "to": r["to"],
                }
                for i, r in enumerate(sample, 1)
            ],
        )
    frame = {
        "tag": tag,
        "seed": args.seed,
        "sources": [str(p) for p in args.changes],
        "excluded_by": [str(p) for p in args.exclude_top or []],
        "changed_lines": len(rows),
        "excluded_lines": len(rows) - total,
        "considered_lines": total,
        "groups": len(groups),
        "top_groups": len(top),
        "top_lines": cum,
        "tail_population": len(population),
        "tail_sampled": len(sample),
        "how_to_label": "Fill `annotation` with Clear / Noisy / Trash (Non-text / Empty if needed); "
        "a top-sheet label applies to every spelling of the group. Blank = not annotated.",
    }
    (out / f"{tag}_frame.json").write_text(json.dumps(frame, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"pack {tag}: {total:,} line(s) considered ({len(rows) - total:,} in excluded groups), {len(groups):,} groups; "
        f"top {len(top)} = {cum:,} lines ({cum / total:.1%} of considered)"
        if total
        else f"pack {tag}: nothing to pack"
    )
    if total:
        print(f"  tail: {len(sample)} of {len(population):,} lines, seed {args.seed}; files in {out}")
    return 0


# ---------------------------------------------------------------------------
# sample
# ---------------------------------------------------------------------------


def cmd_sample(args: argparse.Namespace) -> int:
    names = sorted(csv_files(Path(args.input_dir)))
    excluded: set[str] = set()
    for path in args.exclude or []:
        excluded |= set(read_names(Path(path)))
    pool = [n for n in names if n not in excluded]
    k = min(len(pool), max(args.min, round(args.fraction * len(pool))))
    picked = sorted(random.Random(args.seed).sample(pool, k))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text("".join(f"{n}\n" for n in picked), encoding="utf-8")
    print(f"sample: {k:,} of {len(pool):,} document(s) outside the exclusion list, seed {args.seed} -> {args.out}")
    return 0


# ---------------------------------------------------------------------------
# reach
# ---------------------------------------------------------------------------

#: Worker state, set by ``_reach_init``: the engine module, the arms with their
#: parsed constants, the pairs, and the label sources. Read-only once set.
_REACH: dict = {}


def _locator(value: object) -> str:
    """``'149'``, ``' 149'``, ``'149.0'`` -> ``'149'``; anything else stripped as it is."""
    raw = str(value).strip()
    try:
        num = float(raw)
    except ValueError:
        return raw
    return str(int(num)) if num.is_integer() else raw


def _flagged(value: object) -> bool:
    return value is True or str(value) == "True"


def parse_arms(specs: Sequence[Sequence[str]]) -> dict[str, dict[str, str]]:
    """``[[NAME, KEY=VALUE, ...], ...]`` -> ``{NAME: {KEY: VALUE}}`` (values still text)."""
    arms: dict[str, dict[str, str]] = {}
    for spec in specs:
        name, *items = spec
        if "=" in name or not name:
            raise ValueError(f"an arm starts with its name, not {name!r}")
        if name in arms:
            raise ValueError(f"arm {name!r} is defined twice")
        overrides: dict[str, str] = {}
        for item in items:
            key, sep, value = item.partition("=")
            if not sep or not key.strip():
                raise ValueError(f"arm {name}: {item!r} is not KEY=VALUE")
            overrides[key.strip()] = value.strip()
        arms[name] = overrides
    return arms


def load_label_sources(specs: Sequence[Sequence[str]]) -> dict[str, dict[tuple[str, str, str], str]]:
    """``--labels NAME SIDECAR``: key-only sidecars (``file,page_num,line_num,gold_categ``)."""
    sources: dict[str, dict[tuple[str, str, str], str]] = {}
    for name, path in specs:
        if name in sources:
            raise ValueError(f"label source {name!r} is given twice")
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if not {"file", "page_num", "line_num", "gold_categ"} <= set(reader.fieldnames or ()):
                raise ValueError(f"{path}: a label sidecar needs file, page_num, line_num and gold_categ columns")
            labels: dict[tuple[str, str, str], str] = {}
            for row in reader:
                lab = _label(row["gold_categ"])
                if lab:
                    labels[(row["file"].strip(), _locator(row["page_num"]), _locator(row["line_num"]))] = lab
        sources[name] = labels
    return sources


# Page aggregates (``reach --pages``): what the pipeline publishes per page (digest AJ8).
PAGE_COUNTS = ("Clear", "Noisy", "Trash", "Non-text", "Empty")
PAGE_TEXT = frozenset({"Clear", "Noisy"})
PAGE_ROW_FIELDS = (
    "collection",
    "doc",
    "decade",
    "page_num",
    "lines",
    "clear",
    "noisy",
    "trash",
    "nontext",
    "empty",
    "batch_score",
    "service_score",
    "band",
)
PAGE_DIFF_FIELDS = (
    "collection",
    "doc",
    "decade",
    "page_num",
    "lines",
    "band_ref",
    "band_arm",
    "batch_ref",
    "batch_arm",
    "service_ref",
    "service_arm",
    "d_clear",
    "d_noisy",
    "d_trash",
    "d_nontext",
    "d_empty",
)
_E2_SUMS = ("pages", "band_pages", "band_agree", "clear_abs", "trash_abs", "clear_signed")


def quality_band(clear: int, noisy: int, trash: int) -> str:
    """``document_hook.quality_band``: the plurality, ties to the more optimistic band.

    Restated so reach's workers need not import the record layer; a test pins the two equal.
    """
    if clear >= noisy and clear >= trash:
        return "Clear"
    if noisy >= trash:
        return "Noisy"
    return "Trash"


def _score(value: object) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(num) else num


def page_stats(cats: Sequence[str], scores: Sequence[float | None], rows: Sequence[int]) -> dict:
    """One page under one arm: the counts, both published page scores and the band.

    ``batch`` is ``aggregate_STAT._sum_metrics``'s ``avg_quality_score``: the mean over the page's Clear and Noisy
    lines only, which is what the collections get. ``service`` is ``service/text_api._page_records_from_lines``'s:
    the mean over every scored line. ``band`` is empty when the page has no Clear, Noisy or Trash line (the service
    omits it there; the batch path's plurality would say Clear).
    """
    counts = Counter(cats[i] for i in rows)
    text = [scores[i] for i in rows if cats[i] in PAGE_TEXT and scores[i] is not None]
    every = [scores[i] for i in rows if scores[i] is not None]
    c, n, t = counts["Clear"], counts["Noisy"], counts["Trash"]
    return {
        "counts": tuple(counts[k] for k in PAGE_COUNTS),
        "batch": sum(text) / len(text) if text else None,
        "service": sum(every) / len(every) if every else None,
        "band": quality_band(c, n, t) if c + n + t else "",
    }


def _bin(score: float) -> int:
    return min(99, max(0, int(score * 100 + 1e-9)))


def _bucket(score: float | None) -> str:
    if score is None:
        return "no text line"
    return "<0.55" if score < 0.55 else "0.55-0.80" if score < 0.80 else ">=0.80"


def _num(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"


def _page_sort(page: str) -> tuple:
    return (0, int(page), page) if page.isdigit() else (1, 0, page)


def _quantiles(hist: Counter, points: Sequence[float] = (0.05, 0.25, 0.5, 0.75, 0.95)) -> dict:
    """Quantiles of a 0.01-bin histogram, at bin centres."""
    total = sum(hist.values())
    out: dict[str, float] = {}
    if not total:
        return out
    cum, i = 0, 0
    for b in sorted(hist):
        cum += hist[b]
        while i < len(points) and cum >= points[i] * total:
            out[f"p{round(points[i] * 100):02d}"] = round((int(b) + 0.5) / 100, 3)
            i += 1
    return out


def _page_side() -> dict:
    """One arm's (or tree's) page profile, summed page by page and document by document."""
    return {
        "pages": 0,
        "bands": Counter(),
        "band_by_score": Counter(),
        "batch_hist": Counter(),
        "service_hist": Counter(),
    }


def _page_side_add(side: dict, st: dict) -> None:
    side["pages"] += 1
    side["bands"][st["band"] or "none"] += 1
    side["band_by_score"][f"{st['band'] or 'none'} | {_bucket(st['batch'])}"] += 1
    if st["batch"] is not None:
        side["batch_hist"][_bin(st["batch"])] += 1
    if st["service"] is not None:
        side["service_hist"][_bin(st["service"])] += 1


def _page_side_merge(dst: dict, src: dict) -> None:
    dst["pages"] += src["pages"]
    for key in ("bands", "band_by_score", "batch_hist", "service_hist"):
        dst[key].update(src[key])


def _page_side_summary(side: dict) -> dict:
    return {
        "pages": side["pages"],
        "bands": dict(side["bands"].most_common()),
        "band_by_score": dict(sorted(side["band_by_score"].items())),
        "batch_quantiles": _quantiles(side["batch_hist"]),
        "service_quantiles": _quantiles(side["service_hist"]),
        "batch_hist": {str(b): n for b, n in sorted(side["batch_hist"].items())},
    }


def _page_change() -> dict:
    """One pair's page changes: the pages whose outputs differ, their band transitions, |Δ batch score| in 0.01
    bins (bin 100: the page gains or loses its last text line), and their rows (``PAGE_DIFF_FIELDS``)."""
    return {"pages_changed": 0, "band_transitions": Counter(), "abs_dbatch_hist": Counter(), "rows": []}


def _page_change_add(change: dict, x: dict, y: dict, head: tuple) -> None:
    """One page, reference ``x`` against arm ``y`` (``page_stats``); ``head`` is collection, doc, decade, page,
    lines."""
    if x == y:
        return
    change["pages_changed"] += 1
    if x["band"] != y["band"]:
        change["band_transitions"][f"{x['band'] or 'none'}->{y['band'] or 'none'}"] += 1
    if x["batch"] is None or y["batch"] is None:
        if x["batch"] != y["batch"]:
            change["abs_dbatch_hist"][100] += 1
    elif abs(y["batch"] - x["batch"]) > 1e-12:
        change["abs_dbatch_hist"][min(100, int(abs(y["batch"] - x["batch"]) * 100 + 1e-9))] += 1
    change["rows"].append(
        (*head, x["band"], y["band"], _num(x["batch"]), _num(y["batch"]), _num(x["service"]), _num(y["service"]),
         *(b - c for b, c in zip(y["counts"], x["counts"], strict=True)))
    )  # fmt: skip


def _page_change_merge(dst: dict, src: dict) -> None:
    """Sums only: the rows are written as they come, never kept."""
    dst["pages_changed"] += src["pages_changed"]
    dst["band_transitions"].update(src["band_transitions"])
    dst["abs_dbatch_hist"].update(src["abs_dbatch_hist"])


def _page_change_summary(change: dict) -> dict:
    hist = change["abs_dbatch_hist"]
    return {
        "pages_changed": change["pages_changed"],
        "band_flips": sum(change["band_transitions"].values()),
        "band_transitions": dict(change["band_transitions"].most_common()),
        "abs_dbatch_hist": {str(b): n for b, n in sorted(hist.items())},
        "batch_moved_ge_0.01": sum(n for b, n in hist.items() if b >= 1),
        "batch_moved_ge_0.05": sum(n for b, n in hist.items() if b >= 5),
    }


def _e2_derive(m: dict) -> dict:
    m["band_agreement"] = round(m["band_agree"] / m["band_pages"], 4) if m["band_pages"] else None
    m["clear_share_mae"] = round(m["clear_abs"] / m["pages"], 4) if m["pages"] else None
    m["trash_share_mae"] = round(m["trash_abs"] / m["pages"], 4) if m["pages"] else None
    m["clear_share_bias"] = round(m["clear_signed"] / m["pages"], 4) if m["pages"] else None
    return m


def labelled_page_metrics(
    rows: Iterable[Sequence], arms: Sequence[str], min_labelled: int, min_coverage: float
) -> dict[str, dict[str, dict]]:
    """E2 per label source and arm, over pages with enough of their lines labelled.

    A row is ``reach``'s labelled-page row: source, collection, doc, page, the page's lines, its labelled lines, the
    human counts (``PAGE_COUNTS`` order), then each arm's predicted counts over the same labelled lines. Band
    agreement compares the plurality of the labels with the plurality of the predictions on those lines; the share
    errors are per page, over its labelled lines. Sums are kept so that sources pool by addition.
    """
    k = len(PAGE_COUNTS)
    out: dict[str, dict[str, dict]] = {}
    for row in rows:
        src, lines, n = row[0], int(row[4]), int(row[5])
        if n < min_labelled or not lines or n / lines < min_coverage:
            continue
        human = [int(x) for x in row[6 : 6 + k]]
        h_band = quality_band(*human[:3]) if sum(human[:3]) else ""
        for j, arm in enumerate(arms):
            pred = [int(x) for x in row[6 + k * (j + 1) : 6 + k * (j + 2)]]
            m = out.setdefault(src, {}).setdefault(arm, dict.fromkeys(_E2_SUMS, 0))
            m["pages"] += 1
            if h_band:
                m["band_pages"] += 1
                m["band_agree"] += int((quality_band(*pred[:3]) if sum(pred[:3]) else "") == h_band)
            m["clear_abs"] += abs(pred[0] - human[0]) / n
            m["trash_abs"] += abs(pred[2] - human[2]) / n
            m["clear_signed"] += (pred[0] - human[0]) / n
    for per_arm in out.values():
        for m in per_arm.values():
            _e2_derive(m)
    return out


def _page_results(coll: str, dec: str, docs: list, pages: list, results: dict, row_labels: dict) -> dict:
    """``reach --pages``, one document: every arm's pages, each pair's page changes, the labelled pages."""
    groups: dict[tuple[str, str], list[int]] = {}
    for i, key in enumerate(zip(docs, pages, strict=True)):
        groups.setdefault(key, []).append(i)
    order = sorted(groups, key=lambda key: (key[0], _page_sort(key[1])))
    stats = {
        arm: {key: page_stats(cats, scores, groups[key]) for key in order}
        for arm, (cats, _flags, scores) in results.items()
    }
    arms_out: dict[str, dict] = {}
    for arm, per_page in stats.items():
        side = _page_side()
        for st in per_page.values():
            _page_side_add(side, st)
        arms_out[arm] = side
    ref = stats[_REACH["page_ref"]]
    page_rows = [
        (coll, key[0], dec, key[1], len(groups[key]), *ref[key]["counts"], _num(ref[key]["batch"]),
         _num(ref[key]["service"]), ref[key]["band"])
        for key in order
    ]  # fmt: skip
    pairs_out: dict[str, dict] = {}
    for r, a in _REACH["pairs"]:
        change = _page_change()
        for key in order:
            _page_change_add(change, stats[r][key], stats[a][key], (coll, key[0], dec, key[1], len(groups[key])))
        pairs_out[f"{r}->{a}"] = change
    labelled = []
    arm_order = list(results)
    for src, hits in row_labels.items():
        by_page: dict[tuple[str, str], list[int]] = {}
        for i in sorted(hits):
            by_page.setdefault((docs[i], pages[i]), []).append(i)
        for key, rows in by_page.items():
            human = Counter(hits[i] for i in rows)
            pred = [Counter(results[arm][0][i] for i in rows) for arm in arm_order]
            labelled.append(
                (src, coll, key[0], key[1], len(groups[key]), len(rows), *(human[c] for c in PAGE_COUNTS),
                 *(pc[c] for pc in pred for c in PAGE_COUNTS))
            )  # fmt: skip
    return {"page_arms": arms_out, "page_rows": page_rows, "page_pairs": pairs_out, "labelled_pages": labelled}


def _engine_module():
    """``tools/recategorize_from_csv.py``: the re-scorer the CLI runs, imported as the CLI imports it."""
    tools = str(_ROOT / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import recategorize_from_csv

    return recategorize_from_csv


def _reach_init(state: dict) -> None:
    """Pool initializer (and the in-process path): import the engine, pin pandas."""
    engine = _engine_module()
    if state.get("infer_string") == "false":
        try:
            import pandas as pd

            pd.set_option("future.infer_string", False)
        except Exception as exc:  # noqa: BLE001 -- an older pandas has no such option
            print(f"note: future.infer_string not set: {exc}", file=sys.stderr)
    _REACH.clear()
    _REACH.update(state)
    _REACH["engine"] = engine
    _REACH["arm_constants"] = {
        name: {k: engine._parse_scalar(v) for k, v in over.items()} for name, over in state["arms"].items()
    }
    _REACH["label_docs"] = {src: {k[0] for k in labels} for src, labels in state["labels"].items()}
    _REACH["page_ref"] = state.get("page_ref") or ""


def _categorise(frame, constants: dict):
    """One arm over one document: normalised categories, ``pp_*`` flags and ``quality_score`` (clamped), by row."""
    engine = _REACH["engine"]
    new = engine.recategorize_dataframe(frame, constants or None)
    cats = [engine.normalize_category(v) for v in new["categ"].tolist()]
    pp_cols = [c for c in new.columns if c.startswith("pp_")]
    columns = [new[c].tolist() for c in pp_cols]
    flags = [";".join(c for c, col in zip(pp_cols, columns, strict=True) if _flagged(col[i])) for i in range(len(new))]
    scores = [_score(v) for v in new["quality_score"].tolist()] if "quality_score" in new.columns else [None] * len(new)
    return cats, flags, scores


def _reach_document(task: tuple[str, str, str]) -> dict:
    coll, name, path = task
    out: dict = {"collection": coll, "name": name, "doc": Path(name).stem, "rows": 0, "error": ""}
    try:
        import pandas as pd

        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        renames = {}
        if "page" in frame.columns and "page_num" not in frame.columns:
            renames["page"] = "page_num"
        if "line" in frame.columns and "line_num" not in frame.columns:
            renames["line"] = "line_num"
        elif "line_order" in frame.columns and "line_num" not in frame.columns:
            renames["line_order"] = "line_num"
        frame = frame.rename(columns=renames)
        missing = [c for c in ("categ", "page_num", "line_num", "text") if c not in frame.columns]
        if missing:
            out["error"] = f"unreadable: no {'/'.join(missing)} column"
            return out
        out["rows"] = len(frame)
        if frame.empty:
            out.update(arms={}, pairs={}, resolved={})
            return out
        stem = Path(name).stem
        docs = [(f.strip() or stem) for f in frame["file"].tolist()] if "file" in frame.columns else [stem] * len(frame)
        pages = [_locator(v) for v in frame["page_num"].tolist()]
        lines = [_locator(v) for v in frame["line_num"].tolist()]
        texts = frame["text"].tolist()
        stored = [_REACH["engine"].normalize_category(v) for v in frame["categ"].tolist()]

        results = {arm: _categorise(frame, consts) for arm, consts in _REACH["arm_constants"].items()}
        out["arms"] = {
            arm: sum(a != b for a, b in zip(cats, stored, strict=True)) for arm, (cats, _, _) in results.items()
        }

        # Label sources that have a key in this document, row index -> label.
        keys = None
        row_labels: dict[str, dict[int, str]] = {}
        out["resolved"] = {}
        for src, labels in _REACH["labels"].items():
            if not _REACH["label_docs"][src] & set(docs):
                continue
            if keys is None:
                keys = list(zip(docs, pages, lines, strict=True))
            hits = {i: labels[k] for i, k in enumerate(keys) if k in labels}
            if hits:
                row_labels[src] = hits
                out["resolved"][src] = len({keys[i] for i in hits})

        dec = decade(stem)
        out["pairs"] = {}
        for ref, arm in _REACH["pairs"]:
            (c_ref, f_ref, _), (c_arm, f_arm, _) = results[ref], results[arm]
            changed = [i for i, (a, b) in enumerate(zip(c_ref, c_arm, strict=True)) if a != b]
            if not changed:
                continue
            transitions: Counter = Counter()
            trash_pp: Counter = Counter()
            rows = []
            for i in changed:
                frm, to = c_ref[i], c_arm[i]
                transitions[f"{frm}->{to}"] += 1
                if to == "Trash" and frm != "Trash":
                    trash_pp[f_arm[i] or "(none)"] += 1
                rows.append((coll, docs[i], dec, pages[i], lines[i], frm, to, texts[i], f_arm[i], f_ref[i]))
            labelled: dict[str, Counter] = {}
            for src, hits in row_labels.items():
                tally: Counter = Counter()
                for i in changed:
                    lab = hits.get(i)
                    if lab is None:
                        continue
                    frm, to = c_ref[i], c_arm[i]
                    tally[verdict(lab, frm, to)] += 1
                    if lab == "Clear":
                        tally["clear_loss"] += (to in LOSS_TARGETS) - (frm in LOSS_TARGETS)
                        tally["clear_demoted"] += (to != "Clear") - (frm != "Clear")
                if tally:
                    labelled[src] = tally
            out["pairs"][f"{ref}->{arm}"] = {
                "transitions": transitions,
                "trash_pp": trash_pp,
                "changes": rows,
                "labelled": labelled,
            }
        if _REACH["page_ref"]:
            out.update(_page_results(coll, dec, docs, pages, results, row_labels))
    except Exception as exc:  # noqa: BLE001 -- one bad document is reported, not fatal
        out["error"] = f"failed: {type(exc).__name__}: {exc}"
    return out


def cmd_reach(args: argparse.Namespace) -> int:
    try:
        arms = parse_arms(args.arm)
        labels = load_label_sources(args.labels or [])
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    pairs = [tuple(p) for p in args.pair]
    problems = [f"pair {r} -> {a}: arm {x!r} is not defined" for r, a in pairs for x in (r, a) if x not in arms]
    if len(set(pairs)) != len(pairs):
        problems.append("a pair is given twice")
    page_ref = args.page_ref or ""
    if page_ref and page_ref not in arms:
        problems.append(f"--page-ref {page_ref!r} is not an arm")

    engine = _engine_module()
    for name, over in arms.items():
        for key in over:
            if not any(hasattr(mod, key) for mod in engine._CONST_MODULES):
                problems.append(f"arm {name}: {key} is not a constant of the production modules")
    for name, over in arms.items():
        try:
            engine.validate_constants(engine.coerce_constants({k: engine._parse_scalar(v) for k, v in over.items()}))
        except (TypeError, ValueError) as exc:
            problems.append(f"arm {name}: {exc}")

    tasks: list[tuple[str, str, str]] = []
    seen: dict[str, str] = {}
    only = set(read_names(Path(args.only))) if args.only else None
    for coll, directory in args.input:
        if not Path(directory).is_dir():
            problems.append(f"{coll}: not a directory: {directory}")
            continue
        for name, path in csv_files(Path(directory)).items():
            if only is not None and name not in only:
                continue
            if name in seen:
                problems.append(f"{name} is in both {seen[name]} and {coll}")
                continue
            seen[name] = coll
            tasks.append((coll, name, str(path)))
    if only is not None and set(only) - set(seen):
        problems.append(
            f"{len(set(only) - set(seen))} --only document(s) are in no input, e.g. {sorted(set(only) - set(seen))[:3]}"
        )
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return 2
    if args.expect_docs is not None and len(tasks) != args.expect_docs:
        print(
            f"error: the inputs hold {len(tasks):,} document(s), --expect-docs says {args.expect_docs:,}",
            file=sys.stderr,
        )
        return 3

    tasks.sort(key=lambda t: (-Path(t[2]).stat().st_size, t[1]))
    state = {"arms": arms, "pairs": pairs, "labels": labels, "infer_string": args.infer_string, "page_ref": page_ref}
    prefix = Path(args.out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    pair_labels = [f"{r}->{a}" for r, a in pairs]
    paths = {pl: Path(f"{prefix}_{pl.replace('->', '__')}_changes.csv") for pl in pair_labels}

    lines_read = 0
    docs_read = 0
    differs: Counter = Counter()
    resolved: Counter = Counter()
    errors: dict[str, str] = {}
    agg = {
        pl: {
            "changed_lines": 0,
            "changed_documents": 0,
            "transitions": Counter(),
            "by_collection": defaultdict(Counter),
            "by_decade": defaultdict(Counter),
            "trash_arrivals_by_pp": Counter(),
            "per_doc": Counter(),
            "labelled": defaultdict(Counter),
        }
        for pl in pair_labels
    }
    labelled_rows: list[tuple] = []
    t0 = time.time()
    handles = {pl: open(p, "w", encoding="utf-8", newline="") for pl, p in paths.items()}  # noqa: SIM115
    page_paths: dict[str, Path] = {}
    page_agg: dict[str, dict] = {}
    ref_by_coll: dict[str, Counter] = defaultdict(Counter)
    ref_by_dec: dict[str, Counter] = defaultdict(Counter)
    page_pairs: dict[str, dict] = {}
    labelled_pages: list[tuple] = []
    if page_ref:
        page_paths = {
            "reference": Path(f"{prefix}_pages_{page_ref}.csv.gz"),
            "labelled": Path(f"{prefix}_labelled_pages.csv"),
            **{pl: Path(f"{prefix}_{pl.replace('->', '__')}_pages.csv.gz") for pl in pair_labels},
        }
        for key, path in page_paths.items():
            handles[f"page:{key}"] = (gzip.open if path.suffix == ".gz" else open)(
                path, "wt", encoding="utf-8", newline=""
            )
        page_agg = {arm: _page_side() for arm in arms}
        page_pairs = {pl: _page_change() for pl in pair_labels}
    try:
        writers = {pl: csv.writer(fh) for pl, fh in handles.items()}
        for key, w in writers.items():
            if not key.startswith("page:"):
                w.writerow(CHANGE_FIELDS)
        if page_ref:
            writers["page:reference"].writerow(PAGE_ROW_FIELDS)
            writers["page:labelled"].writerow(
                ["source", "collection", "doc", "page_num", "lines", "labelled"]
                + [f"human_{c}" for c in PAGE_COUNTS]
                + [f"{arm}_{c}" for arm in arms for c in PAGE_COUNTS]
            )
            for pl in pair_labels:
                writers[f"page:{pl}"].writerow(PAGE_DIFF_FIELDS)
        if args.workers <= 1:
            _reach_init(state)
            results: Iterable[dict] = map(_reach_document, tasks)
            executor = None
        else:
            executor = ProcessPoolExecutor(max_workers=args.workers, initializer=_reach_init, initargs=(state,))
            results = executor.map(_reach_document, tasks, chunksize=4)
        try:
            for done, res in enumerate(results, 1):
                if res["error"]:
                    errors[res["name"]] = res["error"]
                    continue
                docs_read += 1
                lines_read += res["rows"]
                differs.update(res.get("arms", {}))
                resolved.update(res.get("resolved", {}))
                for pl, p in res.get("pairs", {}).items():
                    a = agg[pl]
                    n = len(p["changes"])
                    a["changed_lines"] += n
                    a["changed_documents"] += 1
                    a["transitions"].update(p["transitions"])
                    a["by_collection"][res["collection"]].update(p["transitions"])
                    a["by_decade"][decade(res["doc"])].update(p["transitions"])
                    a["trash_arrivals_by_pp"].update(p["trash_pp"])
                    a["per_doc"][(res["collection"], res["doc"])] += n
                    writers[pl].writerows(p["changes"])
                    for src, tally in p["labelled"].items():
                        a["labelled"][src].update(tally)
                        labelled_rows.append(
                            (
                                pl,
                                src,
                                res["collection"],
                                res["doc"],
                                res["name"],
                                tally["fixed"],
                                tally["broken"],
                                tally["neither"],
                                tally["clear_loss"],
                                tally["clear_demoted"],
                            )
                        )
                if page_ref:
                    for arm, side in res.get("page_arms", {}).items():
                        _page_side_merge(page_agg[arm], side)
                    for row in res.get("page_rows", []):
                        ref_by_coll[row[0]][row[-1] or "none"] += 1
                        ref_by_dec[row[2]][row[-1] or "none"] += 1
                    writers["page:reference"].writerows(res.get("page_rows", []))
                    for pl, pp in res.get("page_pairs", {}).items():
                        _page_change_merge(page_pairs[pl], pp)
                        writers[f"page:{pl}"].writerows(pp["rows"])
                    labelled_pages.extend(res.get("labelled_pages", []))
                    writers["page:labelled"].writerows(res.get("labelled_pages", []))
                if args.progress and done % args.progress == 0:
                    mins = (time.time() - t0) / 60
                    print(
                        f"  ... {done:,} / {len(tasks):,} document(s), {lines_read:,} line(s), {mins:.1f} min",
                        flush=True,
                    )
        finally:
            if executor is not None:
                executor.shutdown(cancel_futures=True)
    finally:
        for fh in handles.values():
            fh.close()

    with open(f"{prefix}_labelled.tsv", "w", encoding="utf-8") as fh:
        fh.write("pair\tsource\tcollection\tdoc\tname\tfixed\tbroken\tneither\tclear_loss_delta\tclear_demoted_delta\n")
        for row in sorted(labelled_rows):
            fh.write("\t".join(str(x) for x in row) + "\n")

    unexpected = sorted(set(errors) - set(args.expect_unreadable))
    absent = sorted(set(args.expect_unreadable) - set(errors))
    summary: dict = {
        "documents": len(tasks),
        "documents_read": docs_read,
        "lines_read": lines_read,
        "unreadable": errors,
        "expected_unreadable": sorted(args.expect_unreadable),
        "arms": {name: {"overrides": over, "differs_from_stored": differs[name]} for name, over in arms.items()},
        "labels": {src: {"keys": len(lab), "resolved": resolved[src]} for src, lab in labels.items()},
        "pairs": {},
        "minutes": round((time.time() - t0) / 60, 1),
    }
    for pl in pair_labels:
        a = agg[pl]
        total = a["changed_lines"]
        summary["pairs"][pl] = {
            "changes_csv": str(paths[pl]),
            "changed_lines": total,
            "changed_documents": a["changed_documents"],
            "transitions": dict(a["transitions"].most_common()),
            "by_collection": {c: dict(v.most_common()) for c, v in sorted(a["by_collection"].items())},
            "by_decade": {d: dict(v.most_common()) for d, v in sorted(a["by_decade"].items())},
            "trash_arrivals_by_pp": dict(a["trash_arrivals_by_pp"].most_common()),
            "top_documents": [
                {"collection": c, "doc": d, "changes": n, "share": round(n / total, 4) if total else 0.0}
                for (c, d), n in a["per_doc"].most_common(10)
            ],
            "labelled": {
                src: {k: t[k] for k in (*VERDICTS, "clear_loss", "clear_demoted")}
                for src, t in sorted(a["labelled"].items())
            },
        }
    if page_ref:
        summary["pages"] = {
            "reference": page_ref,
            "min_labelled": args.page_min_labelled,
            "min_coverage": args.page_min_coverage,
            "files": {key: str(path) for key, path in page_paths.items()},
            "arms": {arm: _page_side_summary(side) for arm, side in page_agg.items()},
            "reference_by_collection": {c: dict(v.most_common()) for c, v in sorted(ref_by_coll.items())},
            "reference_by_decade": {d: dict(v.most_common()) for d, v in sorted(ref_by_dec.items())},
            "pairs": {pl: _page_change_summary(pp) for pl, pp in page_pairs.items()},
            "labelled": labelled_page_metrics(
                labelled_pages, list(arms), args.page_min_labelled, args.page_min_coverage
            ),
        }
    Path(f"{prefix}_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        f"reach: {docs_read:,} of {len(tasks):,} document(s), {lines_read:,} line(s), {len(arms)} arm(s), "
        f"{len(pairs)} pair(s) in {summary['minutes']} min"
    )
    if errors:
        print(f"  unreadable: {', '.join(f'{n} ({e})' for n, e in sorted(errors.items())[:5])}")
    for src, s in summary["labels"].items():
        print(f"  labels {src}: {s['resolved']:,} of {s['keys']:,} key(s) found in the inputs")
    print(f"  {'arm':<12} {'differs from stored':>20}  overrides")
    for name, s in summary["arms"].items():
        over = " ".join(f"{k}={v}" for k, v in s["overrides"].items()) or "(shipped)"
        print(f"  {name:<12} {s['differs_from_stored']:>20,}  {over}")
    for pl, s in summary["pairs"].items():
        trans = ", ".join(f"{t} {n:,}" for t, n in list(s["transitions"].items())[:6]) or "no change"
        print(f"  {pl}: {s['changed_lines']:,} line(s) in {s['changed_documents']:,} document(s): {trans}")
        for src, t in s["labelled"].items():
            print(
                f"      {src:<12} fixed {t['fixed']:,} / broken {t['broken']:,} / neither {t['neither']:,}; "
                f"Clear-loss {t['clear_loss']:+,}, Clear-demoted {t['clear_demoted']:+,}"
            )
    if page_ref:
        pg = summary["pages"]
        ref_pages = pg["arms"][page_ref]["pages"]
        print(f"  pages ({ref_pages:,} under {page_ref}): bands {pg['arms'][page_ref]['bands']}")
        for pl, pp in pg["pairs"].items():
            print(
                f"  {pl} pages: {pp['pages_changed']:,} changed, {pp['band_flips']:,} band flip(s), "
                f"{pp['batch_moved_ge_0.05']:,} batch score(s) moved >= 0.05"
            )
        for src, per_arm in pg["labelled"].items():
            m = per_arm.get(page_ref, {})
            print(
                f"  labelled pages {src}: {m.get('pages', 0)} page(s) with >= {args.page_min_labelled} labelled lines "
                f"and coverage >= {args.page_min_coverage}; band agreement under {page_ref} {m.get('band_agreement')}"
            )
    rc = 0
    if unexpected or absent:
        if unexpected:
            print(f"error: {len(unexpected)} document(s) could not be re-scored: {unexpected[:5]}", file=sys.stderr)
        if absent:
            print(f"error: expected unreadable but read: {absent}", file=sys.stderr)
        rc = 3
    return rc


# ---------------------------------------------------------------------------
# pagecheck
# ---------------------------------------------------------------------------


def _pool(labelled: dict, sources: Sequence[str], arm: str) -> dict:
    m = dict.fromkeys(_E2_SUMS, 0)
    for src in sources:
        got = labelled.get(src, {}).get(arm)
        if got:
            for key in _E2_SUMS:
                m[key] += got[key]
    return _e2_derive(m)


def _e2_worse(arm: dict, ref: dict) -> list[str]:
    worse = []
    if ref["band_pages"] and arm["band_agree"] < ref["band_agree"]:
        worse.append(f"band agreement {arm['band_agree']} < {ref['band_agree']} of {ref['band_pages']}")
    if ref["pages"]:
        for key, name in (("clear_abs", "Clear-share"), ("trash_abs", "Trash-share")):
            if arm[key] > ref[key] + 1e-9:
                worse.append(f"{name} error {arm[key] / arm['pages']:.4f} > {ref[key] / ref['pages']:.4f}")
    return worse


def pagecheck(
    summary: dict,
    ref: str,
    *,
    revert_arm: str = "",
    record_only: Sequence[str] = (),
    page_sources: Sequence[str] = ("gold", "dana"),
    flip_share: float = 0.005,
    move_share: float = 0.01,
    move_by: float = 0.05,
) -> dict:
    """Stage 17's written rule over a ``reach --pages`` summary (plan, 2026-10-11).

    A candidate (a pair ``ref -> arm``) is an area for improvement only if all three hold:
    (1) on the labelled lines it improves the total and leaves no source net broken and no source with ``Clear``-loss;
    (2) on the labelled pages (``page_sources``, pooled) it worsens neither band agreement nor the Clear- or
    Trash-share error; (3) it matters: at least ``flip_share`` of the pages flip band, or ``move_share`` move their
    batch score by ``move_by`` or more. ``record_only`` arms are reported and never decide. ``revert_arm`` is the
    state before the shipped change: the shipped change is reverted if, against it, the change is net broken on any
    source, adds ``Clear``-loss on any source, or worsens the labelled pages.

    Verdict: REVERT (the shipped change fails), HOLD (a candidate qualifies: ship it, then measure again), or GO (no
    area for improvement is left: the final re-scoring may run with the shipped settings).
    """
    pages = summary.get("pages")
    if not pages:
        raise ValueError("the summary has no page section: run reach with --page-ref")
    if pages["reference"] != ref:
        raise ValueError(f"the page tables were written for {pages['reference']!r}, not {ref!r}")
    total = pages["arms"][ref]["pages"]
    e_ref = _pool(pages["labelled"], page_sources, ref)
    moved_bin = round(move_by * 100)
    rows = []
    revert = None
    for pl, p in summary["pairs"].items():
        r, arm = pl.split("->")
        if r != ref:
            continue
        lab = p["labelled"]
        pp = pages["pairs"][pl]
        flips = pp["band_flips"]
        moved = sum(n for b, n in pp["abs_dbatch_hist"].items() if int(b) >= moved_bin)
        e_arm = _pool(pages["labelled"], page_sources, arm)
        entry = {
            "pair": pl,
            "arm": arm,
            "role": "record only" if arm in record_only else "revert" if arm == revert_arm else "candidate",
            "lines": lab,
            "pages_changed": pp["pages_changed"],
            "band_flips": flips,
            "batch_moved": moved,
            "band_flip_share": round(flips / total, 6) if total else 0.0,
            "batch_moved_share": round(moved / total, 6) if total else 0.0,
            "labelled_pages": {"ref": e_ref, "arm": e_arm},
        }
        if arm == revert_arm:
            # Read the other way round: in ref -> revert_arm a "fixed" line is one the shipped change broke.
            reasons = [
                f"the shipped change is net broken on {s}" for s, t in sorted(lab.items()) if t["fixed"] > t["broken"]
            ]
            reasons += [
                f"the shipped change adds Clear-loss on {s}" for s, t in sorted(lab.items()) if t["clear_loss"] < 0
            ]
            reasons += [f"the shipped change worsens the labelled pages: {w}" for w in _e2_worse(e_ref, e_arm)]
            entry["revert"] = bool(reasons)
            entry["reasons"] = reasons or ["no source net broken, no Clear-loss, labelled pages not worse"]
            revert = entry
        else:
            why = []
            fixed = sum(t["fixed"] for t in lab.values())
            broken = sum(t["broken"] for t in lab.values())
            why += [f"net broken on {s}" for s, t in sorted(lab.items()) if t["broken"] > t["fixed"]]
            why += [f"Clear-loss on {s}" for s, t in sorted(lab.items()) if t["clear_loss"] > 0]
            if fixed <= broken:
                why.append(f"labelled lines not improved ({fixed} fixed / {broken} broken)")
            why += [f"labelled pages worse: {w}" for w in _e2_worse(e_arm, e_ref)]
            if not total or (flips / total < flip_share and moved / total < move_share):
                why.append(
                    f"immaterial: {flips:,} band flip(s), {moved:,} batch score(s) moved >= {move_by} of {total:,} pages"
                )
            entry["qualifies"] = not why
            entry["reasons"] = why or ["improves the labelled lines, not worse on labelled pages, material"]
        rows.append(entry)
    candidates = [e["arm"] for e in rows if e["role"] == "candidate" and e["qualifies"]]
    verdict = "REVERT" if revert and revert["revert"] else "HOLD" if candidates else "GO"
    return {
        "verdict": verdict,
        "reference": ref,
        "qualifying": candidates,
        "thresholds": {"flip_share": flip_share, "move_share": move_share, "move_by": move_by},
        "page_sources": list(page_sources),
        "pages_total": total,
        "labelled_pages_ref": e_ref,
        "arms": summary.get("arms", {}),
        "documents": summary.get("documents"),
        "lines": summary.get("lines_read"),
        "pairs": rows,
    }


def cmd_pagecheck(args: argparse.Namespace) -> int:
    try:
        summary = json.loads(Path(args.summary).read_text(encoding="utf-8"))
        res = pagecheck(
            summary,
            args.ref,
            revert_arm=args.revert_arm or "",
            record_only=args.record_only,
            page_sources=args.page_sources,
            flip_share=args.flip_share,
            move_share=args.move_share,
            move_by=args.move_by,
        )
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    res["summary"] = str(args.summary)
    prefix = Path(args.out_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    Path(f"{prefix}_decision.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    md = [
        f"# Page-level decision: **{res['verdict']}**",
        "",
        f"Reference `{res['reference']}`, {res['pages_total']:,} pages. Material: >= {args.flip_share:.1%} band flips "
        f"or >= {args.move_share:.1%} of pages moving their batch score by >= {args.move_by}. Labelled pages: "
        f"{', '.join(res['page_sources'])} pooled ({res['labelled_pages_ref']['pages']} page(s)).",
        "",
        "| pair | role | labelled lines (fixed / broken; Clear-loss) | band flips | moved | labelled pages, ref -> arm: "
        "band agreement, Clear / Trash share error | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in res["pairs"]:
        lab = "; ".join(f"{s} {t['fixed']}/{t['broken']} ({t['clear_loss']:+d})" for s, t in sorted(e["lines"].items()))
        lr, la = e["labelled_pages"]["ref"], e["labelled_pages"]["arm"]
        pages = (
            f"{la['pages']} page(s): {lr['band_agreement']} -> {la['band_agreement']}, "
            f"{lr['clear_share_mae']} -> {la['clear_share_mae']} / {lr['trash_share_mae']} -> {la['trash_share_mae']}"
            if la["pages"]
            else "no labelled page"
        )
        verdict = (
            ("REVERT" if e["revert"] else "keep")
            if e["role"] == "revert"
            else ("qualifies" if e["qualifies"] else "no") + (" (record only)" if e["role"] == "record only" else "")
        )
        md.append(
            f"| {e['pair']} | {e['role']} | {lab or 'no labelled change'} | {e['band_flips']:,} "
            f"({e['band_flip_share']:.2%}) | {e['batch_moved']:,} ({e['batch_moved_share']:.2%}) | "
            f"{pages} | {verdict}: "
            f"{'; '.join(e['reasons'])} |"
        )
    Path(f"{prefix}_decision.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("recat", help="sharded, resumable recategorize_from_csv over one directory")
    r.add_argument("--input-dir", required=True)
    r.add_argument("--only", help="file of document names (one per line) to restrict the run to")
    r.add_argument("--out", required=True)
    r.add_argument("--work", required=True, help="shard directories and per-shard logs")
    r.add_argument("--shards", type=int, default=16)
    r.add_argument(
        "--override",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="tunable override passed to the re-scorer (repeatable)",
    )
    r.add_argument(
        "--infer-string",
        choices=("false", "keep"),
        default="false",
        help="'false' sets pandas future.infer_string=False in every shard (David's 2026-10-05 run)",
    )
    r.add_argument(
        "--check-lines",
        action="store_true",
        help="before resuming, delete outputs whose line count differs from the input",
    )
    r.add_argument(
        "--expect-missing",
        nargs="*",
        default=[],
        metavar="NAME",
        help="documents known not to re-score: not attempted, and their missing output is not a failure",
    )
    r.add_argument("--runner", default=str(_ROOT / "tools" / "recategorize_from_csv.py"))
    r.add_argument("--python", default=sys.executable)
    r.add_argument("--cwd", default=None, help="working directory of the shards (default: the current one)")
    r.set_defaults(func=cmd_recat)

    d = sub.add_parser("diff", help="changed lines between a base and an arm tree")
    d.add_argument("--pair", nargs=3, action="append", required=True, metavar=("LABEL", "BASE_DIR", "ARM_DIR"))
    d.add_argument("--subset", action="store_true", help="the arm may hold a subset of the base's documents")
    d.add_argument(
        "--expect-list",
        nargs=2,
        action="append",
        metavar=("LABEL", "NAMES"),
        help="the arm of LABEL must hold exactly these documents",
    )
    d.add_argument("--workers", type=int, default=8)
    d.add_argument("--verify", action="store_true", help="recount with an independent reader and require agreement")
    d.add_argument("--concentration", type=float, default=0.10)
    d.add_argument("--skip", nargs="*", default=[], metavar="NAME", help="documents left out of the comparison")
    d.add_argument(
        "--summary-only",
        action="store_true",
        help="count every change but write no changed line (the CSV keeps its header): for whole-corpus diffs",
    )
    d.add_argument(
        "--pages",
        action="store_true",
        help="also compare what each page publishes (batch score, service score, band) between the two trees",
    )
    d.add_argument("--out-prefix", required=True)
    d.set_defaults(func=cmd_diff)

    s = sub.add_parser("score", help="score changed lines against annotated top / tail sheets")
    s.add_argument("--changes", nargs="+", required=True)
    s.add_argument(
        "--reference-changes", nargs="+", help="the run the tail sheets were drawn from (default: --changes)"
    )
    s.add_argument("--top", nargs="+", default=[])
    s.add_argument("--tail", nargs="+", default=[])
    s.add_argument("--expect-results", help="results.txt whose first line the counts must reproduce")
    s.add_argument("--concentration", type=float, default=0.10)
    s.add_argument("--out-prefix", required=True)
    s.set_defaults(func=cmd_score)

    p = sub.add_parser("pack", help="blind annotation pack: top groups + seeded tail")
    p.add_argument("--changes", nargs="+", required=True)
    p.add_argument("--exclude-top", nargs="+", default=[], help="groups already labelled in these top sheets")
    p.add_argument("--top", type=int, default=200)
    p.add_argument("--tail", type=int, default=100)
    p.add_argument("--seed", type=int, default=20261006)
    p.add_argument("--tag", required=True)
    p.add_argument("--out-dir", required=True)
    p.set_defaults(func=cmd_pack)

    m = sub.add_parser("sample", help="seeded fraction of the documents outside a list")
    m.add_argument("--input-dir", required=True)
    m.add_argument("--exclude", nargs="+", default=[])
    m.add_argument("--fraction", type=float, default=0.01)
    m.add_argument("--min", type=int, default=20)
    m.add_argument("--seed", type=int, default=20261006)
    m.add_argument("--out", required=True)
    m.set_defaults(func=cmd_sample)

    h = sub.add_parser("reach", help="in-memory re-score of whole collections under several arms; changed lines only")
    h.add_argument("--input", nargs=2, action="append", required=True, metavar=("COLLECTION", "DIR"))
    h.add_argument(
        "--arm",
        nargs="+",
        action="append",
        required=True,
        metavar="NAME [KEY=VALUE ...]",
        help="an arm: its name, then the constants it overrides (none = the shipped config); repeatable",
    )
    h.add_argument("--pair", nargs=2, action="append", required=True, metavar=("REF", "ARM"))
    h.add_argument(
        "--labels",
        nargs=2,
        action="append",
        metavar=("NAME", "SIDECAR"),
        help="key-only label sidecar (file, page_num, line_num, gold_categ) scored on every changed line",
    )
    h.add_argument("--only", help="file of document names (one per line) to restrict the run to")
    h.add_argument("--expect-docs", type=int, help="refuse (exit 3) unless the inputs hold exactly this many documents")
    h.add_argument(
        "--expect-unreadable",
        nargs="*",
        default=[],
        metavar="NAME",
        help="documents known not to re-score; any other failure, or one of these reading, exits 3",
    )
    h.add_argument("--workers", type=int, default=8, help="processes; 1 runs in this process")
    h.add_argument(
        "--infer-string",
        choices=("false", "keep"),
        default="false",
        help="'false' sets pandas future.infer_string=False, as David's 2026-10-05 run",
    )
    h.add_argument("--progress", type=int, default=0, help="print a progress line every N documents")
    h.add_argument(
        "--page-ref",
        metavar="ARM",
        help="also aggregate pages (batch score, service score, band) under every arm; ARM's full page table is written",
    )
    h.add_argument("--page-min-labelled", type=int, default=5, help="labelled pages: at least this many labelled lines")
    h.add_argument("--page-min-coverage", type=float, default=0.8, help="labelled pages: at least this share labelled")
    h.add_argument("--out-prefix", required=True)
    h.set_defaults(func=cmd_reach)

    c = sub.add_parser("pagecheck", help="stage 17's written rule over a reach --page-ref summary")
    c.add_argument("--summary", required=True)
    c.add_argument("--ref", required=True, help="the shipped arm the page tables were written for")
    c.add_argument("--revert-arm", help="the state before the shipped change (checked under the revert condition)")
    c.add_argument("--record-only", nargs="*", default=[], metavar="ARM", help="arms reported but never deciding")
    c.add_argument("--page-sources", nargs="+", default=["gold", "dana"], help="label sources pooled for the pages")
    c.add_argument("--flip-share", type=float, default=0.005)
    c.add_argument("--move-share", type=float, default=0.01)
    c.add_argument("--move-by", type=float, default=0.05)
    c.add_argument("--out-prefix", required=True)
    c.set_defaults(func=cmd_pagecheck)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
