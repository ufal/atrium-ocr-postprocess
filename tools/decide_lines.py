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

**The decision run (``--decide``, offline, shadow mode).** The model reads the text only: ``line``,
the two lines before and after it on the same page, and FastText's raw label; no rule signal is
sent. Every scored line (``--gold``: every gold line; ``--tags-only``: the S1–S5 lines;
``--sample-rate``: a reproducible share) is asked ``decisions/line_quality.v1.json``:

* ten yes/no *degradation factors* (``FACTORS``), each the reader's view of what a rule detector
  measures (``RULE_VIEW``): stray symbols, digits in words, repeated characters, too few vowels,
  invented letter strings, misplaced capitals, misread letters, split or merged words, a mirrored
  scan, handwriting read as print;
* ``f_foreign`` (another language than Czech or Slovak), context for S4, not a degradation;
* ``quality``, a ``score`` from 0 (garbage) to 4 (clean); ``quality`` in the table is its
  expectation / 4. ``damage_index`` is the mean probability of the ten factors, computed here as a
  check on the score.

A line tagged S1–S5 is also asked ``decisions/line_category.v1.json``, the category choice
(``Clear``, ``Noisy``, ``Trash``, ``Non-text``), in the same request. Answers go to a sidecar only::

    <out-dir>/DOC_LINE_DECIDE.csv          one row per line asked: key, tags, categories, rule view,
                                           the model's probabilities, scores, status, latency, digest
    <out-dir>/DOC_LINE_DECIDE.summary.json agreement with the rule view per factor (not gold), the
                                           quality score against gold_categ beside the rules' raw
                                           score, the category's fixed and broken lines per tag

    python3 tools/decide_lines.py --write-question-sets
    python3 tools/decide_lines.py --input data_samples/DOC_LINE_CATEG --out-dir /tmp/decide \\
        --decide --model clef-oQ8e --served-on ufal-endpoint      # URL from $DECISION_MODEL_URL

``categ``, the record and every other output stay untouched. Which archive text may go to which
server is settled on ufal/atrium-project#83; the three sample documents are public.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
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
import decision_client as dc  # noqa: E402 — vendored from atrium-page-classification (tests/test_vendored_decision_parity.py)
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
    """``gold_categ`` of a gold sidecar by ``(file, page_num, line_num)``; rows without one are left out."""
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


def scored_lines(
    rows: Sequence[Mapping[str, Any]],
    expected_langs,
    known_bases,
    gold: Optional[Mapping[Tuple[str, int, int], str]] = None,
) -> Iterator[Dict[str, Any]]:
    """Re-score the rows of one document, in order, as the production scorer does.

    Yields ``row`` (as stored), ``rescored`` (``None`` for a fast-track line the pre-filter answered),
    ``fired`` (the rules that fired), ``raw_qs`` (the score before the clamp), ``reason``, ``gold_categ``
    and ``index`` (the row's position). With *gold*, only the gold lines are yielded."""
    with capture_raw_quality() as raw:
        for index, row in enumerate(rows):
            gold_categ = gold.get(_gold_key(row)) if gold is not None else None
            if gold is not None and gold_categ is None:
                continue
            line: Dict[str, Any] = {"index": index, "row": row, "gold_categ": gold_categ}
            if _is_fast_track(row):
                line.update(rescored=None, fired=frozenset(), raw_qs=None, reason=None)
                yield line
                continue
            raw["qs"], raw["reason"] = None, None
            with _tu.rule_fire_capture() as fired:
                rescored = _rescore_row(row, expected_langs, known_bases)
            line.update(rescored=rescored, fired=frozenset(fired), raw_qs=raw["qs"], reason=raw["reason"])
            yield line


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
    for line in scored_lines(rows, expected_langs, known_bases, gold):
        row, rescored, fired, gold_categ = line["row"], line["rescored"], line["fired"], line["gold_categ"]
        raw = {"qs": line["raw_qs"], "reason": line["reason"]}
        counts["lines"] += 1
        if rescored is None:
            counts["fast_track"] += 1
            continue
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
    """*path* itself if it is a file, else the ``*.csv`` under it (with *recursive*, in subfolders too), sorted."""
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


# ── the decision questions (#10 Phase B) ──────────────────────────────────────────────────────

QUESTION_DIR = _ROOT / "decisions"
QUALITY_SET_FILE = QUESTION_DIR / "line_quality.v1.json"
CATEGORY_SET_FILE = QUESTION_DIR / "line_category.v1.json"

#: What the state says about every line. The rules' signals are never sent: the model judges the text.
LINE_TASK = (
    "Judge the OCR text of `line`, one line recognised from a scanned page of an archaeological report "
    "or related material from a Czech archive (1920–2020). `before` and `after` are its neighbours on the page."
)

#: The degradations of a line, as a reader would name what the rule detectors measure (RULE_VIEW).
FACTORS: Dict[str, str] = {
    "f_symbols": "Does `line` contain stray symbols or punctuation that are not part of any word (e.g. ¦ § ~ ^ |)?",
    "f_digits": "Are digits mixed into words, or is `line` mostly numbers and fragments where words are expected?",
    "f_repeated": "Does `line` contain runs of one repeated character or letter pair (e.g. `iiii`, `ididid`)?",
    "f_few_vowels": "Does `line` contain letter strings with too few vowels to be words (e.g. `HRPSTK`, `vfrbn`)?",
    "f_gibberish": "Does `line` contain letter strings that are not words in any language?",
    "f_case": "Are capital letters out of place inside words (e.g. `dalSÍ`)?",
    "f_misread_letters": "Are some letters misread in otherwise recognisable words "
    "(e.g. `fraament okraie` for `fragment okraje`, or lost diacritics)?",
    "f_split_merged": "Are words broken into fragments, or run together without spaces?",
    "f_mirrored": "Does `line` look like a mirrored or upside-down scan read as text "
    "(many w, x, q or unlikely letter shapes)?",
    "f_handwriting": "Does `line` look like handwriting that was read as print, so most letters are wrong?",
}
#: Context, not a degradation: a clean foreign line is not damaged (S4).
CONTEXT: Dict[str, str] = {"f_foreign": "Is `line` written in a language other than Czech or Slovak?"}
QUALITY_INSTRUCTIONS = "How good is the OCR text of `line`?"
QUALITY_LEVELS = (
    "garbage: no recognisable words",
    "mostly garbage: a few words can be recognised",
    "damaged: readable with effort, many errors",
    "minor errors: readable, with a few wrong letters or symbols",
    "clean: the text is correct",
)
QUALITY_MAX = len(QUALITY_LEVELS) - 1
CATEGORY_INSTRUCTIONS = "Which category describes `line`?"
#: The categories asked; ``Empty`` stays deterministic (the pre-filter answers it).
ASKED_CATEGORIES = ("Clear", "Noisy", "Trash", "Non-text")

#: Which fired rules and detector columns make a factor "seen by the rules". Not gold: the rules'
#: view, to measure agreement against. ``f_handwriting`` has none.
RULE_VIEW: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    # factor: (rules that fired, detector columns > 0 or true)
    "f_symbols": (("rule_garbage_density",), ("_strange_symbols",)),
    "f_digits": (("rule_ledger_fragmentation",), ("ldl_fuses",)),
    "f_repeated": (("rule_bigram_run",), ("repeated",)),
    "f_few_vowels": (("rule_vowelless", "rule_allcaps"), ("allcaps_novowel",)),
    "f_gibberish": ((), ("gibberish",)),
    "f_case": (("rule_mid_uppercase",), ("upper",)),
    "f_misread_letters": (("rule_damaged_token",), ("damaged",)),
    "f_split_merged": (("rule_fragment_tokens",), ("fused_words",)),
    "f_mirrored": (("rule_inverted", "rule_wqx_rot"), ("weird_wx",)),
}


def line_category_definitions() -> Dict[str, str]:
    """The asked categories with their definitions from the vendored vocabulary."""
    import atrium_vocab  # noqa: PLC0415

    concepts = atrium_vocab.CONCEPTS["line-category"]
    return {name: concepts[name]["definition"]["en"] for name in ASKED_CATEGORIES}


def quality_questions() -> Dict[str, Dict[str, Any]]:
    """The quality set asked of every line: the ten factors and ``f_foreign`` as yes/no, then the 0–4 score."""
    items: Dict[str, Dict[str, Any]] = {qid: dc.noul(text) for qid, text in {**FACTORS, **CONTEXT}.items()}
    items["quality"] = dc.score(QUALITY_INSTRUCTIONS, QUALITY_LEVELS)
    return dc.questions(items)


def category_questions() -> Dict[str, Dict[str, Any]]:
    """The category set, asked of the S1–S5 lines only: one choice among ``ASKED_CATEGORIES``."""
    return dc.questions({"line_category": dc.choice(CATEGORY_INSTRUCTIONS, line_category_definitions())})


def line_questions(tagged: bool) -> Dict[str, Dict[str, Any]]:
    """One line's request: the quality set, plus the category choice for an S1–S5 line."""
    return dc.questions({**quality_questions(), **category_questions()}) if tagged else quality_questions()


def question_sets() -> Dict[Path, Dict[str, Any]]:
    """The committed question-set files, as they should read."""
    common = {"generated_by": "tools/decide_lines.py --write-question-sets", "state": {"task": LINE_TASK}}
    quality, category = quality_questions(), category_questions()
    return {
        QUALITY_SET_FILE: {
            "name": "line_quality",
            "version": 1,
            **common,
            "source": "#10 plan, Phase B (2026-11-08): the factors mirror the rule detectors of "
            "docs/reference/categorization_rules.md",
            "questions": quality,
            "degradation_factors": list(FACTORS),
            "context": list(CONTEXT),
            "rule_view": {f: {"rules": list(r), "columns": list(c)} for f, (r, c) in RULE_VIEW.items()},
            "schema_digest": dc.schema_digest(quality),
        },
        CATEGORY_SET_FILE: {
            "name": "line_category",
            "version": 1,
            **common,
            "source": "categories and definitions: atrium_vocab.CONCEPTS['line-category']; asked on S1–S5 lines",
            "questions": category,
            "schema_digest": dc.schema_digest(category),
        },
    }


def render_question_set(content: Mapping[str, Any]) -> str:
    """One question-set file's text, as committed: indented JSON, non-ASCII kept, a final newline."""
    return json.dumps(content, indent=2, ensure_ascii=False) + "\n"


def write_question_sets() -> List[Path]:
    """Write ``decisions/line_quality.v1.json`` and ``decisions/line_category.v1.json``; return their paths."""
    QUESTION_DIR.mkdir(exist_ok=True)
    written = []
    for path, content in question_sets().items():
        path.write_text(render_question_set(content), encoding="utf-8")
        written.append(path)
    return written


# ── the decision run (#10 Phase C) ────────────────────────────────────────────────────────────


def rule_view(rescored: Mapping[str, Any], fired: Iterable[str], foreign_min: float = 0.85) -> Dict[str, str]:
    """``"1"``/``"0"`` per factor as the rules see the line; ``""`` where they have no view."""
    fired = set(fired)
    text = str(rescored.get("original_text") or rescored.get("text") or "")
    signals = dict(rescored)
    signals["_strange_symbols"] = _tu.detect_strange_symbols(text)
    out: Dict[str, str] = {}
    for factor in FACTORS:
        if factor not in RULE_VIEW:
            out[factor] = ""
            continue
        rules, columns = RULE_VIEW[factor]
        seen = bool(fired & set(rules)) or any(_flag(signals.get(c)) or _float(signals.get(c)) > 0 for c in columns)
        out[factor] = "1" if seen else "0"
    base = _tu._lang_base(str(rescored.get("original_lang", "") or ""))
    foreign = bool(base) and base not in CZECH_OR_SLOVAK and _float(rescored.get("orig_lang_score")) >= foreign_min
    out["f_foreign"] = "1" if foreign else "0"
    return out


def line_state(rows: Sequence[Mapping[str, Any]], index: int, rescored: Mapping[str, Any], window: int = 2):
    """The state of one line: its text, its neighbours on the same page, and FastText's raw label."""
    row = rows[index]
    page = str(row.get("page_num"))

    def neighbour_texts(indices: Iterable[int]) -> List[str]:
        out: List[str] = []
        for i in indices:
            if not 0 <= i < len(rows) or str(rows[i].get("page_num")) != page:
                break
            text = str(rows[i].get("text") or "").strip()
            if text:
                out.append(text)
            if len(out) == window:
                break
        return out

    text = str(rescored.get("text", row.get("text")) or "")
    state: Dict[str, Any] = {"task": LINE_TASK, "line": text}
    original = str(row.get("original_text") or "")
    if original and original != text:
        state["original_text"] = original
    state["before"] = list(reversed(neighbour_texts(range(index - 1, -1, -1))))
    state["after"] = neighbour_texts(range(index + 1, len(rows)))
    state["detected_language"] = str(row.get("original_lang") or "")
    return state


def _sampled(key: Tuple[str, int, int], rate: Optional[float], seed: int) -> bool:
    if rate is None or rate >= 1:
        return True
    digest = hashlib.sha256(f"{seed}:{key[0]}:{key[1]}:{key[2]}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < rate


DECIDE_COLUMNS = (
    ["file", "page_num", "line_num", "tags", "categ", "line_categ", "gold_categ", "raw_quality_score"]
    + [f"R_{q}" for q in (*FACTORS, *CONTEXT)]
    + [f"P_{q}" for q in (*FACTORS, *CONTEXT)]
    + ["E_quality", "quality", "damage_index", "category"]
    + [f"P_{c}" for c in ASKED_CATEGORIES]
    + ["confidence", "lane", "status", "error", "latency_ms", "input_tokens", "attempts", "schema_digest", "text"]
)


def decide_line(
    client: "dc.DecisionClient",
    rows: Sequence[Mapping[str, Any]],
    line: Mapping[str, Any],
    tags: Sequence[str],
    foreign_min: float = 0.85,
    accept_at: Optional[float] = None,
    review_below: Optional[float] = None,
) -> Dict[str, Any]:
    """One sidecar row. Never raises for a server problem: the row carries the status."""
    row, rescored = line["row"], line["rescored"]
    out: Dict[str, Any] = {
        "file": row.get("file"),
        "page_num": row.get("page_num"),
        "line_num": row.get("line_num"),
        "tags": "|".join(tags),
        "categ": row.get("categ"),
        "line_categ": rescored.get("categ"),
        "gold_categ": line.get("gold_categ") or "",
        "raw_quality_score": f"{line['raw_qs']:.4f}" if line.get("raw_qs") is not None else "",
        "text": rescored.get("text", row.get("text")),
    }
    out.update({f"R_{q}": v for q, v in rule_view({**row, **rescored}, line["fired"], foreign_min).items()})
    d = client.decide(line_state(rows, line["index"], rescored), line_questions(bool(tags)))
    p_factors: Dict[str, float] = {}
    for qid in (*FACTORS, *CONTEXT):
        answer = d.answers.get(qid)
        if answer is not None and answer.p_true is not None:
            p_factors[qid] = answer.p_true
        out[f"P_{qid}"] = p_factors.get(qid, "")
    quality = d.answers.get("quality")
    out["E_quality"] = quality.score if quality is not None and quality.score is not None else ""
    out["quality"] = round(float(out["E_quality"]) / QUALITY_MAX, 4) if out["E_quality"] != "" else ""
    factor_ps = [p_factors[f] for f in FACTORS if f in p_factors]
    out["damage_index"] = round(statistics.fmean(factor_ps), 4) if len(factor_ps) == len(FACTORS) else ""
    category = d.answers.get("line_category")
    out["category"] = category.choice if category else ""
    for name in ASKED_CATEGORIES:
        out[f"P_{name}"] = category.probabilities.get(name, "") if category else ""
    out["confidence"] = category.confidence if category else ""
    out["lane"] = (
        dc.lane(category.confidence, accept_at, review_below)
        if category and accept_at is not None and review_below is not None
        else ""
    )
    out.update(
        {
            "status": d.status,
            "error": d.error or "",
            "latency_ms": d.latency_ms if d.latency_ms is not None else "",
            "input_tokens": d.input_tokens if d.input_tokens is not None else "",
            "attempts": d.attempts,
            "schema_digest": d.schema_digest,
        }
    )
    return out


def decide_document(
    client: "dc.DecisionClient",
    path: Path,
    expected_langs,
    known_bases,
    gold: Optional[Mapping[Tuple[str, int, int], str]] = None,
    tags_only: bool = False,
    sample_rate: Optional[float] = None,
    seed: int = 420,
    margin: float = 0.05,
    foreign_min: float = 0.85,
    accept_at: Optional[float] = None,
    review_below: Optional[float] = None,
    done: Optional[set] = None,
) -> Tuple[List[Dict[str, Any]], Counter]:
    """Ask the model about the selected lines of one ``DOC_LINE_CATEG`` CSV; returns (rows, counts)."""
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    counts: Counter = Counter()
    out: List[Dict[str, Any]] = []
    for line in scored_lines(rows, expected_langs, known_bases, gold):
        counts["lines"] += 1
        if line["rescored"] is None:
            counts["fast_track_skipped"] += 1
            continue
        key = _gold_key(line["row"])
        tags = tag_line(line["row"], line["rescored"], line["fired"], line["raw_qs"], margin, foreign_min)
        if (tags_only and not tags) or not _sampled(key, sample_rate, seed):
            counts["not_selected"] += 1
            continue
        if done is not None and key in done:
            counts["resumed"] += 1
            continue
        out.append(decide_line(client, rows, line, tags, foreign_min, accept_at, review_below))
        counts["asked"] += 1
    return out, counts


# ── the decision summary ──────────────────────────────────────────────────────────────────────


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _ranks(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Spearman's rank correlation (average ranks for ties); None below 3 pairs or without spread."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    sx = sum((a - mx) ** 2 for a in rx) ** 0.5
    sy = sum((b - my) ** 2 for b in ry) ** 0.5
    if not sx or not sy:
        return None
    return round(sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True)) / (sx * sy), 4)


def auc(scores: Sequence[float], positive: Sequence[bool]) -> Optional[float]:
    """The probability that a positive scores above a negative (ties count half): Mann–Whitney."""
    pos = [s for s, p in zip(scores, positive, strict=True) if p]
    neg = [s for s, p in zip(scores, positive, strict=True) if not p]
    if not pos or not neg:
        return None
    ranks = _ranks(list(scores))
    rank_sum = sum(r for r, p in zip(ranks, positive, strict=True) if p)
    return round((rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)), 4)


def kappa(a: Sequence[bool], b: Sequence[bool]) -> Optional[float]:
    """Cohen's kappa of two yes/no raters."""
    n = len(a)
    if not n:
        return None
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    return round((observed - expected) / (1 - expected), 4) if expected < 1 else None


GOLD_ORDER = {"Trash": 0, "Noisy": 1, "Clear": 2}


def summarise_decisions(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The figures of one sidecar: operations, the factors against the rule view, the quality score
    against gold beside the rules' raw score, and the category's fixed and broken lines per tag."""
    ok = [r for r in rows if r.get("status") == "ok"]
    latencies = sorted(float(r["latency_ms"]) for r in ok if _num(r.get("latency_ms")) is not None)
    out: Dict[str, Any] = {
        "lines": len(rows),
        "statuses": dict(Counter(str(r.get("status")) for r in rows)),
        "latency_ms_p50": latencies[len(latencies) // 2] if latencies else None,
        "latency_ms_p95": latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else None,
        "factors": {},
    }
    for qid in (*FACTORS, *CONTEXT):
        values = [(float(r[f"P_{qid}"]), str(r.get(f"R_{qid}", ""))) for r in ok if _num(r.get(f"P_{qid}")) is not None]
        entry: Dict[str, Any] = {
            "model_rate": round(sum(p >= 0.5 for p, _ in values) / len(values), 4) if values else None
        }
        known = [(p >= 0.5, rv == "1") for p, rv in values if rv in ("0", "1")]
        if known:
            model, rule = [m for m, _ in known], [r for _, r in known]
            both = sum(m and r for m, r in known)
            entry.update(
                {
                    "rule_rate": round(sum(rule) / len(rule), 4),
                    "agreement": round(sum(m == r for m, r in known) / len(known), 4),
                    "kappa": kappa(model, rule),
                    "model_given_rule": round(both / sum(rule), 4) if sum(rule) else None,
                    "rule_given_model": round(both / sum(model), 4) if sum(model) else None,
                }
            )
        out["factors"][qid] = entry
    graded = [r for r in ok if r.get("gold_categ") in GOLD_ORDER and _num(r.get("E_quality")) is not None]
    if graded:
        gold = [GOLD_ORDER[r["gold_categ"]] for r in graded]
        model = [float(r["E_quality"]) for r in graded]
        quality: Dict[str, Any] = {
            "lines": len(graded),
            "spearman_model_gold": spearman(model, gold),
            "auc_clear_model": auc(model, [g == 2 for g in gold]),
            "auc_trash_model": auc([-m for m in model], [g == 0 for g in gold]),
        }
        rule_rows = [
            (float(r["raw_quality_score"]), GOLD_ORDER[r["gold_categ"]])
            for r in graded
            if _num(r.get("raw_quality_score")) is not None
        ]
        if rule_rows:
            rule, rule_gold = [s for s, _ in rule_rows], [g for _, g in rule_rows]
            quality.update(
                {
                    "spearman_rules_gold": spearman(rule, rule_gold),
                    "auc_clear_rules": auc(rule, [g == 2 for g in rule_gold]),
                    "auc_trash_rules": auc([-s for s in rule], [g == 0 for g in rule_gold]),
                }
            )
        out["quality_vs_gold"] = quality
    paired = [
        (float(r["E_quality"]), float(r["raw_quality_score"]))
        for r in ok
        if _num(r.get("E_quality")) is not None and _num(r.get("raw_quality_score")) is not None
    ]
    out["spearman_quality_rules"] = spearman([a for a, _ in paired], [b for _, b in paired])
    indexed = [
        (float(r["damage_index"]), float(r["E_quality"]))
        for r in ok
        if _num(r.get("damage_index")) is not None and _num(r.get("E_quality")) is not None
    ]
    out["spearman_damage_index_quality"] = spearman([a for a, _ in indexed], [b for _, b in indexed])
    categories: Dict[str, Any] = {}
    for tag in TAGS:
        asked = [r for r in ok if r.get("category") and tag in str(r.get("tags", "")).split("|")]
        if not asked:
            continue
        entry = {
            "lines": len(asked),
            "agrees_with_stored": round(sum(r["category"] == r["categ"] for r in asked) / len(asked), 4),
        }
        with_gold = [r for r in asked if r.get("gold_categ")]
        if with_gold:
            fixed = sum(r["categ"] != r["gold_categ"] and r["category"] == r["gold_categ"] for r in with_gold)
            broken = sum(r["categ"] == r["gold_categ"] and r["category"] != r["gold_categ"] for r in with_gold)
            entry.update(
                {
                    "gold_lines": len(with_gold),
                    "rule_wrong": sum(r["categ"] != r["gold_categ"] for r in with_gold),
                    "model_wrong": sum(r["category"] != r["gold_categ"] for r in with_gold),
                    "fixed": fixed,
                    "broken": broken,
                    "mcnemar_p": round(dc.mcnemar_exact(fixed, broken), 6),
                }
            )
        categories[tag] = entry
    out["category_per_tag"] = categories
    return out


SERVED_ON = ("cluster-job", "ufal-endpoint", "local", "other")


def served_on(url: Optional[str], given: Optional[str] = None) -> str:
    """Where the lines went, for the paradata, never the URL: the internal endpoint is not published."""
    if given:
        return given
    from urllib.parse import urlsplit  # noqa: PLC0415

    host = urlsplit(url or "").hostname or ""
    return "local" if host in ("127.0.0.1", "localhost", "::1") else "other"


def _done_keys(path: Path) -> set:
    if not path.is_file():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {_gold_key(r) for r in csv.DictReader(handle)}


def run_decide(
    inputs: Sequence[Path],
    out_dir: Path,
    config_path: Path,
    client: "dc.DecisionClient",
    gold_path: Optional[Path] = None,
    tags_only: bool = False,
    sample_rate: Optional[float] = None,
    seed: int = 420,
    margin: float = 0.05,
    foreign_min: float = 0.85,
    accept_at: Optional[float] = None,
    review_below: Optional[float] = None,
    resume: bool = False,
    progress: bool = False,
) -> Tuple[Dict[str, Any], Counter]:
    """Ask the model about the selected lines of *inputs*; write the sidecar; return (summary, counts)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    table = out_dir / "DOC_LINE_DECIDE.csv"
    if table.exists() and not resume:
        raise SystemExit(f"{table} exists; pass --resume to continue it or choose another --out-dir")
    done = _done_keys(table) if resume else None
    if gold_path:
        gold_docs = {key[0] for key in read_gold(gold_path)}
        inputs = [p for p in inputs if p.stem in gold_docs]
    _init_worker(str(config_path), str(gold_path) if gold_path else None, margin, foreign_min)
    w = _WORKER
    counts: Counter = Counter()
    new_file = not table.exists()
    with table.open("a", newline="", encoding="utf-8") as handle, _tu.override_constants(w["constants"]):
        writer = csv.DictWriter(handle, fieldnames=DECIDE_COLUMNS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        for index, path in enumerate(inputs, start=1):
            rows, part = decide_document(
                client,
                path,
                w["expected_langs"],
                w["known_bases"],
                w["gold"],
                tags_only,
                sample_rate,
                seed,
                margin,
                foreign_min,
                accept_at,
                review_below,
                done,
            )
            writer.writerows(rows)
            handle.flush()
            counts.update(part)
            counts["documents"] += 1
            if progress:
                print(f"[{index}/{len(inputs)}] {path.name}: {part.get('asked', 0)} lines asked", file=sys.stderr)
    with table.open(newline="", encoding="utf-8") as handle:
        summary = summarise_decisions(list(csv.DictReader(handle)))
    return summary, counts


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def default_config_path() -> Path:
    """``LANGID_CONFIG``, else ``setup/config.txt``: the config file the service and the batch stages read."""
    # The rule of tool_limits.config_path(), written out rather than imported: importing tool_limits
    # checks every limit variable in the environment, and a malformed one this tool never reads would
    # stop it. tests/test_decide_lines.py keeps the two in step.
    return Path(os.getenv("LANGID_CONFIG", str(_ROOT / "setup" / "config.txt")))


def build_parser() -> argparse.ArgumentParser:
    """The command line; ``--config`` defaults to ``LANGID_CONFIG`` as read when the parser is built."""
    parser = argparse.ArgumentParser(
        description="Tag the lines a decision model would be asked about, and ask it (#10)."
    )
    parser.add_argument("--write-question-sets", action="store_true", help="regenerate decisions/line_*.json and exit")
    parser.add_argument("--input", type=Path, help="a DOC_LINE_CATEG directory or one CSV")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="the constants and languages to score with ($LANGID_CONFIG, else setup/config.txt)",
    )
    parser.add_argument("--gold", type=Path, help="a gold sidecar (file,page_num,line_num,gold_categ): only its lines")
    parser.add_argument("--workers", type=int, default=1, help="documents scored in parallel (selection only)")
    parser.add_argument("--margin", type=float, default=0.05, help="S3: distance to the band edges")
    parser.add_argument("--foreign-min", type=float, default=0.85, help="S4: FastText confidence")
    parser.add_argument("--limit-docs", type=int, help="only the first N documents")
    decide = parser.add_argument_group("the decision run (--decide)")
    decide.add_argument("--decide", action="store_true", help="ask the decision model; write DOC_LINE_DECIDE.csv")
    decide.add_argument("--tags-only", action="store_true", help="only the lines tagged S1–S5")
    decide.add_argument("--sample-rate", type=float, help="a reproducible share of the selected lines (0–1)")
    decide.add_argument("--seed", type=int, default=420)
    decide.add_argument("--accept-at", type=float, help="category lane threshold (set on dev)")
    decide.add_argument("--review-below", type=float, help="category lane threshold (set on dev)")
    decide.add_argument("--resume", action="store_true", help="continue DOC_LINE_DECIDE.csv in --out-dir")
    decide.add_argument("--url", default=os.environ.get("DECISION_MODEL_URL"), help="($DECISION_MODEL_URL)")
    decide.add_argument("--model", default=os.environ.get("DECISION_MODEL_NAME"), help="($DECISION_MODEL_NAME)")
    decide.add_argument("--model-revision", default="", help="the served build or HF revision, for the paradata")
    decide.add_argument("--served-on", choices=SERVED_ON, help="where the lines go (default: local for a loopback URL)")
    decide.add_argument("--timeout", type=float, default=_env_float("DECISION_TIMEOUT_S", 60.0))
    decide.add_argument("--retries", type=int, default=2)
    decide.add_argument("--no-proxy", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Write the question sets, tag the lines, or (``--decide``) ask the model; returns the exit status."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.write_question_sets:
        for path in write_question_sets():
            print(f"wrote {path.relative_to(_ROOT)}")
        return 0
    if not args.input or not args.out_dir:
        parser.error("--input and --out-dir are required (or --write-question-sets)")
    if (args.accept_at is None) != (args.review_below is None):
        parser.error("--accept-at and --review-below go together")
    # Checked before any request: decision_client.lane() refuses a --review-below above --accept-at only
    # when it meets the first answer, after the model has been called, and takes a threshold outside
    # [0, 1], where no probability lies, without a word.
    if args.accept_at is not None:
        if not (0 <= args.accept_at <= 1 and 0 <= args.review_below <= 1):
            parser.error("--accept-at and --review-below are probabilities in [0, 1]")
        if args.review_below > args.accept_at:
            parser.error("--review-below must not exceed --accept-at")
    if args.sample_rate is not None and not 0 < args.sample_rate <= 1:
        parser.error("--sample-rate is a share in (0, 1]")
    inputs = input_files(args.input, args.recursive)
    if args.limit_docs:
        inputs = inputs[: args.limit_docs]
    if not inputs:
        print(f"no CSV under {args.input}", file=sys.stderr)
        return 2
    if args.decide:
        return main_decide(args, parser, inputs)
    report = run(
        inputs, args.out_dir, args.config, args.gold, args.workers, args.margin, args.foreign_min, progress=True
    )
    print(json.dumps({k: report[k] for k in ("documents", "scored", "drift", "tagged", "per_tag")}, indent=2))
    return 0


def main_decide(args: argparse.Namespace, parser: argparse.ArgumentParser, inputs: Sequence[Path]) -> int:
    """The ``--decide`` run: ask the model, write the sidecar, its summary and the paradata; returns 0."""
    client = dc.DecisionClient(
        args.url, args.model, timeout_s=args.timeout, retries=args.retries, use_env_proxy=not args.no_proxy
    )
    if not client.endpoint or not client.model:
        parser.error("set --url/--model (or DECISION_MODEL_URL/DECISION_MODEL_NAME)")
    where = served_on(args.url, args.served_on)
    digests = {
        "line_quality": dc.schema_digest(quality_questions()),
        "line_category": dc.schema_digest(category_questions()),
        "with_category": dc.schema_digest(line_questions(True)),
    }
    from atrium_paradata import ParadataLogger  # noqa: PLC0415
    from document_hook import PROGRAM_NAME  # noqa: PLC0415

    paradata = ParadataLogger(
        program=PROGRAM_NAME,
        config={
            "mode": "decide_lines",
            "decision_model": args.model,
            "decision_revision": args.model_revision,
            "served_on": where,
            "decision_schema_digests": digests,
            "gold": str(args.gold) if args.gold else None,
            "tags_only": args.tags_only,
            "sample_rate": args.sample_rate,
            "seed": args.seed,
            "accept_at": args.accept_at,
            "review_below": args.review_below,
        },
        paradata_dir=str(args.out_dir / "paradata"),
        output_types=["csv"],
        config_dir=str(_ROOT / "setup"),
    )
    paradata.log_component("decision_model")
    summary, counts = run_decide(
        inputs,
        args.out_dir,
        args.config,
        client,
        args.gold,
        args.tags_only,
        args.sample_rate,
        args.seed,
        args.margin,
        args.foreign_min,
        args.accept_at,
        args.review_below,
        args.resume,
        progress=True,
    )
    paradata.log_success("csv", counts.get("asked", 0))
    summary.update(
        {
            "tool": "tools/decide_lines.py --decide",
            "model": args.model,
            "model_revision": args.model_revision,
            "served_on": where,
            "schema_digests": digests,
            "config": {"path": str(args.config), "sha256": _sha256(args.config)},
            "gold": {"sidecar": str(args.gold), "sha256": _sha256(args.gold)} if args.gold else None,
            "counts": dict(counts),
        }
    )
    (args.out_dir / "DOC_LINE_DECIDE.summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    paradata.finalize(input_total=counts.get("lines", 0), processed_total=summary["statuses"].get("ok", 0))
    print(json.dumps({k: summary[k] for k in ("lines", "statuses", "counts")}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
