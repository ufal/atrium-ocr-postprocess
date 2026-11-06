#!/usr/bin/env python3
"""
tools/decide_lines.py — the ambiguity selector for decision models (#10; ufal/atrium-project#83)
================================================================================================

Which lines would a decision model be asked about? This tool answers that without any model. It
reads ``DOC_LINE_CATEG`` CSVs, re-scores every line with the production scorer (the offline
re-scorer's ``_rescore_row``, i.e. ``classify_TEXT.score_line``) inside ``text_util.rule_fire_capture()``,
and tags the lines the rule cascade is least sure of:

====  =================================================================================================
Tag   Population
====  =================================================================================================
S1    at most 3 words (``ISOLATED_CHAR_MIN_TOKENS``), no Czech diacritics, stored ``Clear`` or ``Noisy``
S2    a late-gate red flag fired (9a–9f) but the per-line verdict is not ``Trash``: no conviction
S3    the score ``categorize_line`` received (before it is clamped into its band) within ``--margin``
      of ``CATEG_TRASH_SCORE_MAX`` (0.55), or ``valid_word_ratio`` within ``--margin`` of
      ``MOSTLY_READABLE_VALID_MIN`` (0.85)
S4    FastText's raw label is neither Czech nor Slovak, at ``orig_lang_score`` ≥ ``--foreign-min``
      (0.85), and the line is stored ``Noisy``
S5    a document pass changed the line (``pp_dedup``, ``pp_surrounded_trash``, ``pp_inverted_run``,
      ``pp_page_context``)
====  =================================================================================================

"Stored" is the category in the CSV, the pipeline's final answer. The per-line verdict is the
re-score's, before any document pass. A line whose per-line verdict differs from its stored
category with no document pass to explain it is counted as ``drift``: the CSV was written by other
code or constants than the ones this checkout runs.

With ``--gold`` (``tools/gold/sidecars/issue30_gold_2067.csv``) only the gold lines are read, and each
tag reports how the stored category compares with ``gold_categ``. That is the room a decision model
has to improve on the rules, measured before any model is called.

Nothing is changed: no rule, threshold, column, record or default (the pilot freeze). Output::

    <out-dir>/DOC_LINE_DECIDE_SELECT.csv   one row per tagged line (key, tags, categories, signals, text)
    <out-dir>/decide_lines_counts.json     the population of each tag, overlaps, drift, the gold view

Run it from the repository root::

    python3 tools/decide_lines.py --input data_samples/DOC_LINE_CATEG --out-dir /tmp/select
    python3 tools/decide_lines.py --input <DOC_LINE_CATEG dir on the cluster> \\
        --gold tools/gold/sidecars/issue30_gold_2067.csv --out-dir <dir> --workers 8
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import classify_TEXT as _lc  # noqa: E402
import text_util as _tu  # noqa: E402
from tools.recategorize_from_csv import (  # noqa: E402
    _is_fast_track,
    _load_lang_config,
    _rescore_row,
    coerce_constants,
    read_config_constants,
    validate_constants,
)

TAGS = ("S1", "S2", "S3", "S4", "S5")

#: The six late gates (docs/reference/categorization_rules.md, "Late gates (9a–9f)").
LATE_GATE_RULES = (
    "rule_wqx_rot",  # 9a
    "rule_vowelless",  # 9b
    "rule_ledger_fragmentation",  # 9c
    "rule_mid_uppercase",  # 9d (unreachable today: gate 7 returns first)
    "rule_bigram_run",  # 9e
    "rule_fragment_tokens",  # 9f
)
PP_COLUMNS = ("pp_dedup", "pp_surrounded_trash", "pp_inverted_run", "pp_page_context")
CZECH_OR_SLOVAK = frozenset({"ces", "slk"})

SELECT_COLUMNS = [
    "file",
    "page_num",
    "line_num",
    "tags",
    "categ",
    "line_categ",
    "reason",
    "raw_quality_score",
    "quality_score",
    "valid_word_ratio",
    "word_count",
    "original_lang",
    "orig_lang_score",
    "fired_late_gates",
    "pp_flags",
    "gold_categ",
    "text",
]


# ── the raw score ─────────────────────────────────────────────────────────────────────────────


@contextmanager
def capture_raw_quality() -> Iterator[Dict[str, Any]]:
    """Record the score ``categorize_line`` receives, before it clamps it into its band, and its reason.

    ``score_line`` returns only the clamped score, so 0.80 can mean any raw value above the Noisy
    band. This wraps the one function and delegates to it unchanged: the scoring is production's."""
    seen: Dict[str, Any] = {"qs": None, "reason": None}
    original = _lc.categorize_line

    def recording(qs, *args, **kwargs):
        seen["qs"] = float(qs)
        result = original(qs, *args, **kwargs)
        if isinstance(result, tuple) and len(result) == 3:
            seen["reason"] = result[2]  # score_line asks for the cascade's reason; the row does not keep it
        return result

    _lc.categorize_line = recording
    try:
        yield seen
    finally:
        _lc.categorize_line = original


# ── tagging (pure) ────────────────────────────────────────────────────────────────────────────


def _flag(value: Any) -> bool:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) else number


def tag_line(
    stored: Mapping[str, Any],
    rescored: Mapping[str, Any],
    fired: Iterable[str],
    raw_qs: Optional[float],
    margin: float = 0.05,
    foreign_min: float = 0.85,
) -> List[str]:
    """The tags of one line: *stored* is the CSV row, *rescored* its per-line re-score."""
    tags: List[str] = []
    stored_categ = str(stored.get("categ", ""))
    line_categ = str(rescored.get("categ", ""))
    text = str(rescored.get("text", stored.get("text", "")) or "")
    words = int(_float(rescored.get("word_count", len(text.split()))))

    if words <= _tu.ISOLATED_CHAR_MIN_TOKENS and not _tu.has_cz_diacs(text) and stored_categ in ("Clear", "Noisy"):
        tags.append("S1")
    if set(fired) & set(LATE_GATE_RULES) and line_categ != "Trash":
        tags.append("S2")
    valid = _float(rescored.get("valid_word_ratio"), default=float("nan"))
    near_band = raw_qs is not None and abs(raw_qs - _tu.CATEG_TRASH_SCORE_MAX) <= margin
    near_valid = not math.isnan(valid) and abs(valid - _tu.MOSTLY_READABLE_VALID_MIN) <= margin
    if near_band or near_valid:
        tags.append("S3")
    base = _tu._lang_base(str(stored.get("original_lang", "") or ""))
    if (
        base
        and base not in CZECH_OR_SLOVAK
        and _float(stored.get("orig_lang_score")) >= foreign_min
        and stored_categ == "Noisy"
    ):
        tags.append("S4")
    if any(_flag(stored.get(col)) for col in PP_COLUMNS):
        tags.append("S5")
    return tags


# ── one document ──────────────────────────────────────────────────────────────────────────────


def _gold_key(row: Mapping[str, Any]) -> Tuple[str, int, int]:
    return str(row.get("file", "")), int(_float(row.get("page_num"))), int(_float(row.get("line_num")))


def read_gold(path: Path) -> Dict[Tuple[str, int, int], str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {_gold_key(r): r["gold_categ"] for r in csv.DictReader(handle) if r.get("gold_categ")}


def _new_counts() -> Dict[str, Any]:
    return {
        "documents": 0,
        "lines": 0,
        "fast_track": 0,
        "scored": 0,
        "tagged": 0,
        "drift": 0,
        "per_tag": Counter(),
        "per_tag_stored": {tag: Counter() for tag in TAGS},
        "tag_count": Counter(),
        "pairs": Counter(),
        "gold_lines": 0,
        "gold_per_tag": {tag: Counter() for tag in TAGS},
        "gold_untagged": Counter(),
    }


def process_document(
    path: Path,
    expected_langs,
    known_bases,
    gold: Optional[Mapping[Tuple[str, int, int], str]] = None,
    margin: float = 0.05,
    foreign_min: float = 0.85,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Tag every line of one ``DOC_LINE_CATEG`` CSV; returns (selected rows, counts)."""
    counts = _new_counts()
    selected: List[Dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    counts["documents"] = 1
    with capture_raw_quality() as raw:
        for row in rows:
            key = _gold_key(row)
            gold_categ = gold.get(key) if gold is not None else None
            if gold is not None and gold_categ is None:
                continue
            counts["lines"] += 1
            if _is_fast_track(row):
                counts["fast_track"] += 1
                continue
            raw["qs"], raw["reason"] = None, None
            with _tu.rule_fire_capture() as fired:
                rescored = _rescore_row(row, expected_langs, known_bases)
            counts["scored"] += 1
            tags = tag_line(row, rescored, fired, raw["qs"], margin, foreign_min)
            pp = [col for col in PP_COLUMNS if _flag(row.get(col))]
            if rescored.get("categ") != row.get("categ") and not pp:
                counts["drift"] += 1
            if gold_categ is not None:
                counts["gold_lines"] += 1
                target = counts["gold_untagged"] if not tags else None
                if target is not None:
                    target[f"{row.get('categ')}→{gold_categ}"] += 1
                for tag in tags:
                    counts["gold_per_tag"][tag][f"{row.get('categ')}→{gold_categ}"] += 1
            if not tags:
                continue
            counts["tagged"] += 1
            counts["tag_count"][len(tags)] += 1
            for tag in tags:
                counts["per_tag"][tag] += 1
                counts["per_tag_stored"][tag][str(row.get("categ"))] += 1
            for i, a in enumerate(tags):
                for b in tags[i + 1 :]:
                    counts["pairs"][f"{a}+{b}"] += 1
            selected.append(
                {
                    "file": row.get("file"),
                    "page_num": row.get("page_num"),
                    "line_num": row.get("line_num"),
                    "tags": "|".join(tags),
                    "categ": row.get("categ"),
                    "line_categ": rescored.get("categ"),
                    "reason": raw["reason"] or "",
                    "raw_quality_score": f"{raw['qs']:.4f}" if raw["qs"] is not None else "",
                    "quality_score": rescored.get("quality_score"),
                    "valid_word_ratio": rescored.get("valid_word_ratio"),
                    "word_count": rescored.get("word_count"),
                    "original_lang": row.get("original_lang"),
                    "orig_lang_score": row.get("orig_lang_score"),
                    "fired_late_gates": "|".join(r for r in LATE_GATE_RULES if r in fired),
                    "pp_flags": "|".join(pp),
                    "gold_categ": gold_categ or "",
                    "text": rescored.get("text", row.get("text")),
                }
            )
    return selected, counts


def _merge(total: Dict[str, Any], part: Mapping[str, Any]) -> None:
    for key, value in part.items():
        if isinstance(value, Counter):
            total[key].update(value)
        elif isinstance(value, dict):
            for tag, counter in value.items():
                total[key][tag].update(counter)
        else:
            total[key] += value


# ── the run ───────────────────────────────────────────────────────────────────────────────────

_WORKER: Dict[str, Any] = {}


def _init_worker(config_path: str, gold_path: Optional[str], margin: float, foreign_min: float) -> None:
    expected_langs, known_bases = _load_lang_config(config_path)
    constants = coerce_constants(read_config_constants(config_path))
    validate_constants(constants)
    _WORKER.update(
        expected_langs=expected_langs,
        known_bases=known_bases,
        constants=constants,
        gold=read_gold(Path(gold_path)) if gold_path else None,
        margin=margin,
        foreign_min=foreign_min,
    )


def _work(path: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    w = _WORKER
    with _tu.override_constants(w["constants"]):
        return process_document(
            Path(path), w["expected_langs"], w["known_bases"], w["gold"], w["margin"], w["foreign_min"]
        )


def input_files(path: Path, recursive: bool = False) -> List[Path]:
    if path.is_file():
        return [path]
    return sorted(path.rglob("*.csv") if recursive else path.glob("*.csv"))


def run(
    inputs: Sequence[Path],
    out_dir: Path,
    config_path: Path,
    gold_path: Optional[Path] = None,
    workers: int = 1,
    margin: float = 0.05,
    foreign_min: float = 0.85,
    progress: bool = False,
) -> Dict[str, Any]:
    """Tag the lines of *inputs*; write the selection CSV and the counts JSON; return the counts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    total = _new_counts()
    gold_docs = None
    if gold_path:
        gold_docs = {key[0] for key in read_gold(gold_path)}
        inputs = [p for p in inputs if p.stem in gold_docs]
    args = (str(config_path), str(gold_path) if gold_path else None, margin, foreign_min)
    select_path = out_dir / "DOC_LINE_DECIDE_SELECT.csv"
    with select_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SELECT_COLUMNS)
        writer.writeheader()
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=args) as pool:
                results: Iterable = pool.map(_work, [str(p) for p in inputs], chunksize=4)
                for index, (selected, counts) in enumerate(results, start=1):
                    writer.writerows(selected)
                    _merge(total, counts)
                    if progress and index % 100 == 0:
                        print(f"{index}/{len(inputs)} documents", file=sys.stderr)
        else:
            _init_worker(*args)
            for index, path in enumerate(inputs, start=1):
                selected, counts = _work(str(path))
                writer.writerows(selected)
                _merge(total, counts)
                if progress and index % 100 == 0:
                    print(f"{index}/{len(inputs)} documents", file=sys.stderr)
    report = _report(total, inputs, config_path, gold_path, margin, foreign_min)
    (out_dir / "decide_lines_counts.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _report(total, inputs, config_path, gold_path, margin, foreign_min) -> Dict[str, Any]:
    scored = total["scored"] or 1
    report: Dict[str, Any] = {
        "tool": "tools/decide_lines.py",
        "inputs": {"files": len(inputs), "first": str(inputs[0]) if inputs else None},
        "config": {"path": str(config_path), "sha256": _sha256(config_path)},
        "parameters": {
            "margin": margin,
            "foreign_min": foreign_min,
            "CATEG_TRASH_SCORE_MAX": _tu.CATEG_TRASH_SCORE_MAX,
            "MOSTLY_READABLE_VALID_MIN": _tu.MOSTLY_READABLE_VALID_MIN,
            "ISOLATED_CHAR_MIN_TOKENS": _tu.ISOLATED_CHAR_MIN_TOKENS,
        },
        "documents": total["documents"],
        "lines": total["lines"],
        "fast_track": total["fast_track"],
        "scored": total["scored"],
        "drift": total["drift"],
        "tagged": total["tagged"],
        "tagged_share": round(total["tagged"] / scored, 4),
        "per_tag": {tag: total["per_tag"].get(tag, 0) for tag in TAGS},
        "per_tag_share": {tag: round(total["per_tag"].get(tag, 0) / scored, 4) for tag in TAGS},
        "per_tag_stored_categ": {tag: dict(total["per_tag_stored"][tag]) for tag in TAGS},
        "lines_by_tag_count": {str(k): v for k, v in sorted(total["tag_count"].items())},
        "pairs": dict(total["pairs"]),
    }
    if gold_path:
        gold: Dict[str, Any] = {"sidecar": str(gold_path), "sha256": _sha256(gold_path), "lines": total["gold_lines"]}
        for tag in TAGS:
            cells = total["gold_per_tag"][tag]
            n = sum(cells.values())
            wrong = sum(v for k, v in cells.items() if k.split("→")[0] != k.split("→")[1])
            gold[tag] = {
                "lines": n,
                "rule_wrong": wrong,
                "rule_error": round(wrong / n, 4) if n else None,
                "stored→gold": dict(cells),
            }
        cells = total["gold_untagged"]
        n = sum(cells.values())
        wrong = sum(v for k, v in cells.items() if k.split("→")[0] != k.split("→")[1])
        gold["untagged"] = {"lines": n, "rule_wrong": wrong, "rule_error": round(wrong / n, 4) if n else None}
        report["gold"] = gold
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tag the lines a decision model would be asked about (#10).")
    parser.add_argument("--input", type=Path, required=True, help="a DOC_LINE_CATEG directory or one CSV")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=_ROOT / "setup" / "config.txt")
    parser.add_argument("--gold", type=Path, help="a gold sidecar (file,page_num,line_num,gold_categ): only its lines")
    parser.add_argument("--workers", type=int, default=1, help="documents scored in parallel")
    parser.add_argument("--margin", type=float, default=0.05, help="S3: distance to the band edges")
    parser.add_argument("--foreign-min", type=float, default=0.85, help="S4: FastText confidence")
    parser.add_argument("--limit-docs", type=int, help="only the first N documents")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    inputs = input_files(args.input, args.recursive)
    if args.limit_docs:
        inputs = inputs[: args.limit_docs]
    if not inputs:
        print(f"no CSV under {args.input}", file=sys.stderr)
        return 2
    report = run(
        inputs, args.out_dir, args.config, args.gold, args.workers, args.margin, args.foreign_min, progress=True
    )
    print(json.dumps({k: report[k] for k in ("documents", "scored", "drift", "tagged", "per_tag")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
