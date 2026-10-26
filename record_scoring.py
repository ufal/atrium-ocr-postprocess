"""
record_scoring.py — score the lines of an existing ATRIUM record, the same on the command line and in the service.

`POST /score_record` (atrium-digital-convert#4 W3) reads a record's `lines[].text`, scores each
line with the common line-quality model, and writes `categ`, `quality_score` and `lang` back onto
the rows it read, with `pages[].quality_score/quality_band`, through the scoring-only write path
(`document_hook.write_scores`). The batch pipeline had no way to do the same. The selection, the
mapping of the answers back onto the rows and the write now live here, and both entry points call
them (atrium-project#72, the service–CLI alignment):

* `service/text_api.py` — `POST /score_record`, one record per request, with the service limits;
* `score_record.py` — the command line, over `*.document.json` files or folders of them.

Model-free and FastAPI-free: the caller passes the classifier
(`TextModelManager.classify_line_texts`) and checks its own limits on the selection.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from document_hook import DECODE_VERDICTS, quality_band, write_scores

#: The `lang` of a line the CPU pre-filter settled (`Empty` / `Non-text`): no language was
#: identified for it. The batch pipeline writes the same marker in its CSV (classify_TEXT._fast_track_row)
#: and reads it back as missing, so a record never carries it as a language.
NO_LANG = "N/A"

#: The categories the CPU pre-filter assigns on its own: no language, no model, quality score 0.0.
PREFILTER_CATEGORIES = ("Empty", "Non-text")

#: Characters a record's doc_id may keep in the temporary file name it is written under; the
#: record itself keeps its doc_id (DocumentRecord inherits the baseline's).
_UNSAFE_FILE_KEY = re.compile(r"[^A-Za-z0-9._-]")


class UnknownPages(ValueError):
    """`pages` names keys the record does not have."""

    def __init__(self, keys: List[str]) -> None:
        self.keys = keys
        super().__init__(f"pages: the record has no page {', '.join(keys)}.")


def record_lang(entry: Dict[str, Any]) -> Optional[str]:
    """The `lang` a line contributes to the record: none for a line no language was identified for
    (`NO_LANG`), as the batch path omits it (its CSV's "N/A" reads back as missing)."""
    lang = entry.get("lang")
    return None if lang in (None, NO_LANG) else lang


def scored_by_model(cleaned_lines: Iterable[Any]) -> bool:
    """Whether a quality model scored at least one line (one the CPU pre-filter answered used none)."""
    return any(isinstance(e, dict) and e.get("category") not in PREFILTER_CATEGORIES for e in cleaned_lines or [])


def page_records_from_lines(line_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Real `pages[]` rows for the lines just classified.

    One row per page label actually present in `line_records`, with
    `quality_score` as the mean of that page's own line scores and `quality_band`
    reduced from its own Clear/Noisy/Trash counts by the same plurality vote
    aggregate_STAT.py applies to the batch path (document_hook.quality_band).

    `quality_band` is omitted when none of the three bands is represented (an
    all-`Empty`/all-`Non-text` page): the vote's tie-break favours "Clear", so
    counting zeros would report a pristine page for one with no text on it.
    """
    by_page: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for record in line_records:
        by_page.setdefault(record["page"], []).append(record)

    pages: List[Dict[str, Any]] = []
    for page, rows in by_page.items():
        record: Dict[str, Any] = {"page": page}
        # The mean of the lines a quality model judged. A line the CPU pre-filter answered
        # (`Empty` / `Non-text`) carries a 0.0 placeholder, as in the batch CSV, and is no evidence
        # of low quality: counting it would pull a page of clean prose with a few page numbers down.
        scores = [
            r["quality_score"]
            for r in rows
            if isinstance(r.get("quality_score"), (int, float)) and r.get("categ") not in PREFILTER_CATEGORIES
        ]
        if scores:
            record["quality_score"] = round(sum(scores) / len(scores), 4)
        categs = [r.get("categ") for r in rows]
        clear, noisy, trash = categs.count("Clear"), categs.count("Noisy"), categs.count("Trash")
        if clear or noisy or trash:
            record["quality_band"] = quality_band(clear, noisy, trash)
        pages.append(record)
    return pages


def parse_pages(spec: Optional[str]) -> Optional[Set[str]]:
    """`"i, ii,3"` -> `{"i", "ii", "3"}`; empty -> None (every page)."""
    return {key.strip() for key in (spec or "").split(",") if key.strip()} or None


def file_key(doc_id: str) -> str:
    """The name a record is written under while it is scored: its doc_id, made file-safe."""
    return _UNSAFE_FILE_KEY.sub("_", doc_id)[:120] or "record"


@dataclass
class Selection:
    """The lines of a record to score, page by page, and those left as they were."""

    by_page: "OrderedDict[str, List[Tuple[int, str]]]" = field(default_factory=OrderedDict)
    skipped_verdict: Dict[str, int] = field(default_factory=dict)
    skipped_empty: Dict[str, int] = field(default_factory=dict)

    @property
    def texts(self) -> List[str]:
        return [text for rows in self.by_page.values() for _line, text in rows]


def select_lines(record: Dict[str, Any], wanted: Optional[Set[str]] = None) -> Selection:
    """The record's lines to score (`lines[].text`, in record order), on the `wanted` pages or all.

    A line carrying digital-convert's decode verdict (`Garbage`, `Inverted`) is left as it is, and a
    line without text is not scored; both are counted. Raises `UnknownPages` for a wanted key the
    record has neither in `lines[]` nor in `pages[]`.
    """
    selection = Selection()
    record_pages = [str(row.get("page")) for row in record.get("pages") or [] if isinstance(row, dict)]
    for row in record.get("lines") or []:
        if not isinstance(row, dict) or row.get("page") is None or not isinstance(row.get("line"), int):
            continue
        page = str(row["page"])
        if wanted is not None and page not in wanted:
            continue
        selection.by_page.setdefault(page, [])
        if row.get("categ") in DECODE_VERDICTS:
            selection.skipped_verdict[page] = selection.skipped_verdict.get(page, 0) + 1
            continue
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            selection.skipped_empty[page] = selection.skipped_empty.get(page, 0) + 1
            continue
        selection.by_page[page].append((row["line"], text.strip()))
    if wanted is not None:
        unknown = sorted(wanted - set(selection.by_page) - set(record_pages))
        if unknown:
            raise UnknownPages(unknown)
    return selection


def score_record(
    record: Dict[str, Any],
    raw: bytes,
    selection: Selection,
    classify: Callable[[List[str]], List[Dict[str, Any]]],
    *,
    run_id: Optional[str],
    paradata_ref: str,
    run_uuid: Optional[str],
) -> Dict[str, Any]:
    """Score the selected lines and write the answers into the record.

    `raw` is the record as it was read (the write starts from those bytes). Returns the scored
    lines (`cleaned_lines`, keyed by the record's `page` and `line`), the per-page summary
    (`pages`) and the record (`document_json`), as sent when nothing was scored.
    """
    doc_id = str(record.get("doc_id") or "")
    texts = selection.texts
    entries = classify(texts) if texts else []
    if len(entries) != len(texts):
        raise RuntimeError(f"the classifier answered {len(entries)} lines for {len(texts)}")

    cleaned: List[Dict[str, Any]] = []
    answers = iter(entries)
    for page, rows in selection.by_page.items():
        for position, (line_key, _text) in enumerate(rows, start=1):
            entry = dict(next(answers))
            entry.update(line_num=position, page=page, line=line_key)
            cleaned.append(entry)

    line_records = [
        {"page": e["page"], "line": e["line"]}
        | {
            name: value
            for name, value in (
                ("categ", e.get("category")),
                ("quality_score", e.get("quality_score")),
                ("lang", record_lang(e)),
            )
            if value is not None
        }
        for e in cleaned
    ]
    page_records = [row for row in page_records_from_lines(line_records) if len(row) > 1]
    page_summary = {row["page"]: row for row in page_records}
    counts: Dict[str, int] = {}
    for e in cleaned:
        counts[e["page"]] = counts.get(e["page"], 0) + 1

    returned = record
    with tempfile.TemporaryDirectory() as work:
        key = file_key(doc_id)
        Path(work, f"{key}.document.json").write_bytes(raw)
        written = write_scores(
            work, key, run_id, paradata_ref, pages=page_records, lines=line_records, run_uuid=run_uuid
        )
        if written:
            with open(written, "r", encoding="utf-8-sig") as fh:
                returned = json.load(fh)

    return {
        "type": "record",
        "doc_id": doc_id,
        "cleaned_lines": cleaned,
        "pages": [
            {
                "page": page,
                "lines_scored": counts.get(page, 0),
                "skipped_decode_verdict": selection.skipped_verdict.get(page, 0),
                "skipped_empty": selection.skipped_empty.get(page, 0),
                "quality_score": (page_summary.get(page) or {}).get("quality_score"),
                "quality_band": (page_summary.get(page) or {}).get("quality_band"),
            }
            for page in selection.by_page
        ],
        "document_json": returned,
    }
