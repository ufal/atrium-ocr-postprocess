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

Five subcommands
----------------
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

``diff``
    Joins two re-scored trees document by document on ``(page_num, line_num)``
    and writes the changed lines, the touched documents and a summary
    (transitions overall / per collection / per decade, the ``pp_*`` flags of
    every arrival in ``Trash``, and the documents that carry the change).
    ``--verify`` recounts every transition through a second, independent reader
    (pandas, a dict keyed on the locator -- David's ``cmp_full.py`` semantics,
    where the first pass is positional -- his ``nezavisle.py``) and fails if the
    two disagree.

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

What is safe to paste into the issue: everything printed to stdout and the
``*_summary.json`` / ``*_score.json`` files (no line text). ``*_changes.csv``,
``*_groups.csv`` and packs carry line text.
"""

from __future__ import annotations

import argparse
import ast
import csv
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
    todo = [name for name in files if not (out / name).exists()]
    print(
        f"recat: {len(files):,} input document(s) in {in_dir}; {len(files) - len(todo):,} already written, "
        f"{len(todo):,} to do; {len(stray)} stray .part removed"
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
    if missing:
        print(
            f"recat: {len(missing):,} document(s) have no output (list: {work / 'missing.txt'}); "
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


def _diff_document(task: tuple[str, str, str, str]) -> dict:
    label, name, base_path, arm_path = task
    stem = Path(name).stem
    dec = decade(stem)
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
    return out


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
    for label, base_dir, arm_dir in args.pair:
        base, arm = csv_files(Path(base_dir)), csv_files(Path(arm_dir))
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
    t0 = time.time()
    with (
        open(f"{prefix}_changes.csv", "w", encoding="utf-8", newline="") as fh,
        ProcessPoolExecutor(max_workers=args.workers) as ex,
    ):
        writer = csv.writer(fh)
        writer.writerow(CHANGE_FIELDS)
        for res in ex.map(_diff_document, tasks, chunksize=64):
            agg = pairs[res["label"]]
            agg["documents_compared"] += 1
            agg["rows_compared"] += res["rows_base"]
            agg["unpaired"] += res["unpaired"]
            if res["error"]:
                errors.append(f"{res['label']}/{res['doc']}: {res['error']}")
            if res["changes"]:
                agg["changed_documents"] += 1
                agg["changed_lines"] += len(res["changes"])
                agg["transitions"].update(res["transitions"])
                by_decade[decade(res["doc"])].update(res["transitions"])
                trash_pp.update(res["trash_pp"])
                per_doc[(res["label"], res["doc"])] += len(res["changes"])
                touched.append((res["label"], f"{res['doc']}.csv"))
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
        "minutes": round((time.time() - t0) / 60, 1),
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
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
