"""
document_hook.py — repo-local glue between this repo's stage scripts and the
hub-canonical `atrium_document.py` paired-hook model (atrium-llm-enrich#13).

Unlike `atrium_document.py`/`atrium_document.schema.json` themselves, this module is
NOT hub-canonical and is not copied byte-identical across the tool repos (no
para-drift enforcement here) — the grouping logic below is specific to how THIS
repo's stage scripts batch many documents' pages into a single run, which the hub
module has no opinion on.

Enablement is config-driven, not a per-script flag: a single `[DOCUMENT].JSON_DIR`
setting (or `DOCUMENT_JSON_DIR` env override) turns the hook on for every stage at
once, pointing them all at the same directory of `<doc_id>.document.json` files. Left
empty (the default), every function below is a no-op — standalone runs are
unaffected, matching rule 3 of the accretion contract.

Record schema 2.0 (atrium-project#84): this repo is the record builder (`atrium_document.RECORD_BUILDER`):
it writes the positional blocks of every record, whatever its origin, and is the only scorer of lines.
The record carries no run data and no licence; a stage that passes its ParadataLogger (`logger=`) has
the record read and written named in its paradata, by distribution name and SHA-512.

Ownership note: every write here uses PROGRAM_NAME = "ocr-postprocess" (named "alto-postprocess" until
2026-10-01; `atrium_document.PROGRAM_SUCCESSORS` treats the two as one writer), the current name
`atrium_document.BLOCK_OWNERS` lists for this repo's blocks. Every stage's
ParadataLogger stamps that same `program` (classify, aggregate and the text-lines
stages import PROGRAM_NAME from here; the other stages spell the literal) and names
the script in `config.script`. The older per-stage names (`langID-classify`,
`langID-aggregate`) survive only in the 2026-06 sample logs under `paradata/`, so
atrium-llm-enrich#13's "normalise the alto-postprocess program names" TODO is done
(the plan of atrium-alto-postprocess#31, Phase 4; it stayed with that repository).

(#31 Phase 4) `SOURCE_ORIGIN_BY_KIND` — a per-kind `source.origin` override for the
text-lines inputs — is parsed and resolved here (`parse_origin_by_kind`,
`resolve_input_origin`), so text_split.py and the service agree on it.

(atrium-project#73 R6) Both record writers, `write_document_block()` and `write_scores()`, end by
setting this repo's `quality_summary` block — `atrium_document.quality_summary()` of the record as
written — whenever the call wrote `pages` or `lines`, so `/process`, `/score_record` and every batch
stage leave the summary in step with the fields it reads, on both branches. It is numbers only: no
band, no threshold, and nothing here or elsewhere reads it to route or refuse a document.
"""

from __future__ import annotations

import logging
import os
import sys
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from atrium_document import (
    DocumentRecord,
    load_document,
    quality_summary,
    validate_baseline,
    validate_document,
)

logger = logging.getLogger(__name__)

PROGRAM_NAME = "ocr-postprocess"

#: (atrium-project#10 D4) One-shot latch for the "validation is unavailable" warning.
#: The gate below is called once per document, and a batch run holds thousands of
#: them — repeating the same line per record would bury every other diagnostic the
#: run emits. Loud once is the point; loud 5000 times is noise that gets filtered.
_VALIDATION_UNAVAILABLE_WARNED = False

#: The AMČR distribution name of the record (atrium-project#84): what the paradata name it by.
RECORD_DISTRIBUTION = "atrium/document-json"

#: How an input's origin maps onto record schema 2.0's descriptive `source.origin` (#84 §1;
#: prefix match, case-insensitive; the hub's own migration table): the born-digital spellings
#: (`digital-born-pdf`, ...), the PCSS (ABBYY) ALTO route, and OCR/VLM engines (`ocr:pero`).
RECORD_ORIGINS: Tuple[Tuple[str, str], ...] = (
    ("digital", "digital"),
    ("docx", "digital"),
    ("pdf", "digital"),
    ("abbyy", "legacy"),
    ("alto", "legacy"),
    ("ocr:", "atr"),
    ("vlm:", "atr"),
    ("atr", "atr"),
    ("legacy", "legacy"),
)

#: A line's `text_source` per record origin (D25).
TEXT_SOURCE_FOR_ORIGIN = {"digital": "text-layer", "atr": "atr", "legacy": "legacy"}


def record_origin(origin: Optional[str]) -> Optional[str]:
    """`digital`, `atr` or `legacy` for an input's origin, or None when it maps to none."""
    if not isinstance(origin, str) or not origin.strip():
        return None
    folded = origin.strip().casefold()
    return next((new for prefix, new in RECORD_ORIGINS if folded.startswith(prefix)), None)


_ORIGIN_BY_KIND_KEY = "[DOCUMENT] SOURCE_ORIGIN_BY_KIND"


def parse_origin_by_kind(raw: str, known_kinds: Iterable[str]) -> Dict[str, str]:
    """`xlsx = ocr:generic, pptx = ocr:generic` → {"xlsx": "ocr:generic", ...}.

    Entries are separated by commas or newlines. A malformed entry, a kind no reader
    registers, a kind given twice, an empty value, or an origin no ORIGIN_ORIGINATORS
    prefix recognises raises ValueError naming the key: a typo must fail the run, not
    quietly write a record without an origin for that kind.
    """
    known = {k.lower() for k in known_kinds}
    out: Dict[str, str] = {}
    for item in (part.strip() for chunk in (raw or "").splitlines() for part in chunk.split(",")):
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"{_ORIGIN_BY_KIND_KEY}: {item!r} is not <kind> = <origin>")
        kind, _, origin = (x.strip() for x in item.partition("="))
        kind = kind.lower()
        if kind not in known:
            raise ValueError(f"{_ORIGIN_BY_KIND_KEY}: unknown kind {kind!r} (known: {', '.join(sorted(known))})")
        if kind in out:
            raise ValueError(f"{_ORIGIN_BY_KIND_KEY}: kind {kind!r} is given twice")
        if not origin:
            raise ValueError(f"{_ORIGIN_BY_KIND_KEY}: kind {kind!r} has an empty origin")
        if record_origin(origin) is None:
            raise ValueError(
                f"{_ORIGIN_BY_KIND_KEY}: origin {origin!r} for {kind!r} matches no known origin prefix "
                f"(ocr:…, vlm:…, ABBYY-ALTO, digital-born-…, atr, legacy)"
            )
        out[kind] = origin
    return out


def resolve_input_origin(
    kind: str,
    default: str,
    *,
    override: str = "",
    env: str = "",
    configured: str = "",
    by_kind: Optional[Dict[str, str]] = None,
) -> str:
    """`source.origin` for one text-lines input. Precedence: the CLI flag (`override`)
    > the DOCUMENT_SOURCE_ORIGIN env var (`env`) > `[DOCUMENT].SOURCE_ORIGIN_BY_KIND` for
    this kind > `[DOCUMENT].SOURCE_ORIGIN` (`configured`) > the truthful per-kind default.

    The flag and the env var are per-run statements about every input, so they win
    over the config; within the config the per-kind entry is the more specific one.
    Pure — the caller reads the environment.
    """
    for value in (override, env, (by_kind or {}).get((kind or "").lower(), ""), configured):
        if value and value.strip():
            return value.strip()
    return default


def _warn(message: str) -> None:
    """stderr in atrium_document's own `[document]` voice, so the accretion
    diagnostics of a run read as one stream regardless of which side emitted them.
    """
    print(f"[document] WARNING – {message}", file=sys.stderr)


def _warn_validation_unavailable(reason: str) -> None:
    """(D4) The gate could not run at all. Announced ONCE, loudly, and never
    silently: `validate_document()` deliberately raises rather than passing when
    `jsonschema` is absent, because a validation gate that quietly becomes a no-op
    is indistinguishable from a passing one. Degrading loudly keeps that property
    while honouring rule 3 — a missing optional dependency must not stop a
    standalone run from producing its output.
    """
    global _VALIDATION_UNAVAILABLE_WARNED
    if _VALIDATION_UNAVAILABLE_WARNED:
        return
    _VALIDATION_UNAVAILABLE_WARNED = True
    _warn(
        f"schema validation is DISABLED for this run — {reason}. This is a DEGRADED "
        f"gate, not a pass: records are being written unchecked. Install the missing "
        f"dependency (setup/requirements.txt declares jsonschema for exactly this call)."
    )


def _baseline_is_invalid(path: str) -> bool:
    """Validate an INHERITED baseline before this stage accretes onto it (D4).

    Warns and returns True on a schema failure rather than refusing to run: the
    defect belongs to whichever upstream tool wrote it, and turning one bad record
    into a stalled pipeline is worse than passing it through (rule 6 already commits
    to carrying unknown content forward). The flag it returns downgrades the
    own-output gate below from raise to warn, so this stage is not blamed for a
    defect it inherited.

    A baseline that cannot even be READ is not this function's problem —
    `DocumentRecord.open()` reports and raises on it a few lines later, with the
    right message.

    An AMČR seed (`doc_id` and `source` only, atrium-project#71) is checked against the seed
    profile (`validate_baseline`), not the full schema it could never pass: it used to be
    reported here as an invalid baseline, which also demoted this stage's own output gate.
    """
    if not path or not os.path.exists(path):
        return False
    try:
        record = load_document(path)
    except Exception:
        return False
    try:
        validate_baseline(record)
    except (RuntimeError, FileNotFoundError) as exc:
        # RuntimeError = jsonschema missing; FileNotFoundError = the schema itself
        # was not vendored next to the module. Neither means "the record is bad".
        _warn_validation_unavailable(str(exc))
        return False
    except Exception as exc:
        _warn(
            f"inherited baseline {path} does not validate against "
            f"atrium_document.schema.json — {exc}. Accreting onto it anyway; this "
            f"stage's own output gate is downgraded to a warning as a result."
        )
        return True
    return False


def _validate_own_output(doc: DocumentRecord, baseline_was_invalid: bool) -> None:
    """The Layer D gate on THIS stage's output, called before `finalize()` (D4).

    Raises on a schema failure so the record is never emitted — `DocumentRecord`'s
    context manager only finalises when the body left without an exception, so
    raising here is what makes "no doc.json is emitted if validation fails" true.
    The one exception is an already-invalid baseline: the failure is then almost
    certainly the inherited one, and refusing to write would drop this stage's work
    as well as the upstream stage's.
    """
    try:
        validate_document(doc.to_dict())
    except (RuntimeError, FileNotFoundError) as exc:
        _warn_validation_unavailable(str(exc))
    except Exception as exc:
        if baseline_was_invalid:
            _warn(
                f"{PROGRAM_NAME} output for {doc.doc_id} does not validate — {exc}. "
                f"Emitting it anyway: the inherited baseline was already invalid, so "
                f"this is very likely not our defect to refuse."
            )
            return
        raise


def resolve_document_json_dir(configured: Optional[str] = None) -> str:
    """`DOCUMENT_JSON_DIR` env var wins, then the `[DOCUMENT].JSON_DIR` config value.

    Empty string means disabled — every helper below then does nothing.
    """
    return os.getenv("DOCUMENT_JSON_DIR") or (configured or "")


def document_path(document_json_dir: str, doc_id: str) -> str:
    return os.path.join(document_json_dir, f"{doc_id}.document.json")


def _set_quality_summary(doc: DocumentRecord) -> None:
    """(atrium-project#73 R6) The record's `quality_summary`, recomputed from its pages and lines."""
    doc.set_block(
        "quality_summary",
        quality_summary({"pages": doc.get_block("pages") or [], "lines": doc.get_block("lines") or []}),
    )


def record_source(source: Dict[str, Any]) -> Dict[str, Any]:
    """`source` as record schema 2.0 holds it: the origin mapped to `digital`/`atr`/`legacy`
    (dropped when it maps to none), no SHA-256, no empty value."""
    out = {k: v for k, v in source.items() if v not in (None, "") and k != "sha256"}
    if "origin" in out:
        mapped = record_origin(out.pop("origin"))
        if mapped:
            out["origin"] = mapped
    return out


def _page_index(row: Dict[str, Any], position: int) -> int:
    """A page row's `page_index`: its own, else its numeric label, else its position."""
    index = row.get("page_index")
    if isinstance(index, int) and not isinstance(index, bool) and index > 0:
        return index
    label = str(row.get("page", "")).strip()
    return int(label) if label.isdigit() and int(label) > 0 else position


def _page_rows(rows: List[Dict[str, Any]], known: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    """Page rows keyed by `page_index`, the 2.0 key (BLOCK_KEY_FIELDS["pages"]): a label the record
    already has (`known`, label → page_index) keeps its row, so `iv` lands on its own page."""
    known = known or {}
    out = []
    for n, row in enumerate(rows, start=1):
        label = str(row.get("page", ""))
        index = row.get("page_index") if isinstance(row.get("page_index"), int) else known.get(label)
        out.append({**row, "page_index": index or _page_index(row, n)})
    return out


def _known_pages(doc: DocumentRecord) -> Dict[str, int]:
    """label → page_index of the record's page rows that carry both."""
    return {
        str(row["page"]): row["page_index"]
        for row in doc.get_block("pages") or []
        if isinstance(row, dict) and row.get("page") is not None and isinstance(row.get("page_index"), int)
    }


def _line_rows(rows: List[Dict[str, Any]], origin: Optional[str]) -> List[Dict[str, Any]]:
    """Line rows with the builder's 2.0 fields: `text_source` from the record's origin, and
    `bbox_precision: measured` for a line with its own box (D16, D25)."""
    out = []
    for row in rows:
        row = dict(row)
        if "text" in row and "text_source" not in row and origin in TEXT_SOURCE_FOR_ORIGIN:
            row["text_source"] = TEXT_SOURCE_FOR_ORIGIN[origin]
        if row.get("bbox") and "bbox_precision" not in row:
            row["bbox_precision"] = "measured"
        out.append(row)
    return out


def _content_status(doc: DocumentRecord) -> None:
    """`pages[].content_status` (D29), when the record has lines: `text` for a page with a line
    (whatever it said before), `empty` for a row without a status and without a line; an empty
    page keeps no page score (rule 7). A row is matched by its label, else (a row page-classification
    wrote before it was labelled) by its `page_index`."""
    lines = doc.get_block("lines")
    if not lines:
        return
    with_lines = {str(row.get("page")) for row in lines if isinstance(row, dict)}
    updates = []
    for row in doc.get_block("pages") or []:
        if not isinstance(row, dict) or not isinstance(row.get("page_index"), int):
            continue
        key = str(row["page"]) if row.get("page") is not None else str(row["page_index"])
        status = "text" if key in with_lines else ("empty" if "content_status" not in row else None)
        if status and row.get("content_status") != status:
            updates.append({"page_index": row["page_index"], "content_status": status})
    if updates:
        doc.merge_block("pages", updates)


def write_document_block(
    document_json_dir: str,
    doc_id: str,
    *,
    source: Optional[Dict[str, Any]] = None,
    set_blocks: Optional[Dict[str, Any]] = None,
    merge_blocks: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    logger: Optional[Any] = None,
) -> Optional[str]:
    """Open `<doc_id>.document.json` under `document_json_dir` (if configured and if
    it already exists), apply this stage's own contribution, and write it back in
    place. A missing baseline is safe (rule 3): the record then holds just this
    stage's part. No-ops entirely when `document_json_dir` is falsy.

    Record schema 2.0 (atrium-project#84): ocr-postprocess is the record builder, so it writes
    the positional blocks of every record, whatever its origin, and the record carries no run
    data (no stamps, no licence). `source.origin` is written as `digital`/`atr`/`legacy`
    (`record_source`), page rows are keyed by `page_index`, each line gets its `text_source`
    and `bbox_precision`, and each page its `content_status` (`_content_status`). Until #84 a
    born-digital record was digital-convert's to originate, and this hook wrote only the pages
    it flagged `needs_ocr` (the OCR hand-off, now gone: the mixed merge is the builder's, H6).

    With `logger` (the stage's ParadataLogger), the record version read and the record written
    go into its paradata, by distribution name and SHA-512, the output with the blocks and fields
    written (`DocumentRecord.written()`).

    Returns the path written, or None when nothing was written. Callers read the
    record back from that path (atrium-project#68). "In place" is literal: the record goes back
    to `path`, not to finalize()'s default `<record doc_id>.document.json` (an AMČR seed carries
    the AMČR file id).

    This is the repo's single document-write chokepoint, so it is also where the Layer D
    guarantees are enforced once (atrium-project#10 D4/D8): the inherited baseline is
    validated and a failure WARNED about; every `merge_block()` is followed by
    `assert_fields_survived()`; this stage's own output is validated before `finalize()`.
    """
    if not document_json_dir:
        return None
    if not any([source, set_blocks, merge_blocks]):
        return None

    path = document_path(document_json_dir, doc_id)
    if logger is not None and os.path.exists(path):
        logger.add_input(RECORD_DISTRIBUTION, path=path, media_type="application/json")

    baseline_was_invalid = _baseline_is_invalid(path)
    with DocumentRecord.open(doc_id, PROGRAM_NAME, baseline=path, out_dir=document_json_dir) as doc:
        if source:
            doc.set_source(**record_source(source))
        origin = (doc.to_dict().get("source") or {}).get("origin")
        for block, payload in (set_blocks or {}).items():
            doc.set_block(block, payload)
        for block, records in (merge_blocks or {}).items():
            if not records:
                continue
            if block == "pages":
                records = _page_rows(records, _known_pages(doc))
            elif block == "lines":
                records = _line_rows(records, origin)
            doc.merge_block(block, records)
            # (#10 D8) merge_block()'s field filtering is silent by design: the raising form
            # fires only when THIS repo hands over a field its own grant in BLOCK_FIELD_OWNERS
            # does not cover, which is a code bug in the caller, not data variance.
            doc.assert_fields_survived(block, records)
        wrote = set(set_blocks or {}) | {block for block, rows in (merge_blocks or {}).items() if rows}
        if wrote & {"pages", "lines"}:
            _content_status(doc)
            _set_quality_summary(doc)
        _validate_own_output(doc, baseline_was_invalid)
        # Explicitly, and to `path` (#68): see the docstring. __exit__ then has nothing left to do.
        written = doc.finalize(path)
        if logger is not None:
            logger.add_output(
                RECORD_DISTRIBUTION, path=written, media_type="application/json", record_fields=doc.written()
            )
        return written


#: What `write_scores()` writes: the line-quality model's fields (`atrium_document`'s grant).
SCORE_LINE_FIELDS = ["categ", "quality_score", "lang"]
SCORE_PAGE_FIELDS = ["quality_score", "quality_band"]


def write_scores(
    document_json_dir: str,
    doc_id: str,
    *,
    pages: Optional[List[Dict[str, Any]]] = None,
    lines: Optional[List[Dict[str, Any]]] = None,
    logger: Optional[Any] = None,
) -> Optional[str]:
    """Score an EXISTING record in place: lines[] categ/quality_score/lang, pages[] quality.

    The scoring-only write path (`POST /score_record`, `score_record.py`). Since record schema
    2.0 (atrium-project#84, D18 rules 1–3) this repo scores every line of every record, whatever
    its text source, with one model and one band scheme; until #84 a line carrying
    digital-convert's decode verdict was left out. It never writes `text`, `bbox` or any other
    positional field, never `source`, and never a row:

      * a row carrying any other field (`text`, `bbox`, ...) is refused with ValueError
        before anything is opened — a caller bug, never data to drop in silence;
      * rows whose key is not in the record are left out (a score cannot invent a line);
      * page rows name the page by its label (`page`) and are written under its `page_index`;
        an empty page (`content_status: empty`) carries no page score (rule 7);
      * every merge is followed by `assert_fields_survived()`, and the output is validated
        before it is written, as in `write_document_block()`.

    With `logger`, the record read and written go into its paradata, as in
    `write_document_block()`. Returns the path written, or None when there is no record at
    `<doc_id>.document.json` or nothing to write.
    """
    for block, rows, allowed in (
        ("lines", lines, ["page", "line", *SCORE_LINE_FIELDS]),
        ("pages", pages, ["page", *SCORE_PAGE_FIELDS]),
    ):
        extra = sorted({field for row in rows or [] for field in row} - set(allowed))
        if extra:
            raise ValueError(f"write_scores writes only {allowed} into {block}[], not {extra}")
    if not document_json_dir:
        return None
    path = document_path(document_json_dir, doc_id)
    if not os.path.exists(path):
        return None
    record = load_document(path) or {}
    known_lines = {
        (str(row.get("page")), row.get("line")) for row in record.get("lines") or [] if isinstance(row, dict)
    }
    index_of = {
        str(row.get("page")): row
        for row in record.get("pages") or []
        if isinstance(row, dict) and isinstance(row.get("page_index"), int)
    }
    line_rows = [row for row in lines or [] if (str(row.get("page")), row.get("line")) in known_lines]
    page_rows = [
        {"page_index": index_of[str(row["page"])]["page_index"], **{k: v for k, v in row.items() if k != "page"}}
        for row in pages or []
        if str(row.get("page")) in index_of and index_of[str(row["page"])].get("content_status") != "empty"
    ]
    if not line_rows and not page_rows:
        return None

    if logger is not None:
        logger.add_input(RECORD_DISTRIBUTION, path=path, media_type="application/json")
    baseline_was_invalid = _baseline_is_invalid(path)
    with DocumentRecord.open(doc_id, PROGRAM_NAME, baseline=path, out_dir=document_json_dir) as doc:
        if line_rows:
            doc.merge_block("lines", line_rows, key_fields=["page", "line"], own_fields=SCORE_LINE_FIELDS)
            doc.assert_fields_survived("lines", line_rows)
        if page_rows:
            doc.merge_block("pages", page_rows, own_fields=SCORE_PAGE_FIELDS)
            doc.assert_fields_survived("pages", page_rows)
        _set_quality_summary(doc)
        _validate_own_output(doc, baseline_was_invalid)
        written = doc.finalize(path)
        if logger is not None:
            logger.add_output(
                RECORD_DISTRIBUTION, path=written, media_type="application/json", record_fields=doc.written()
            )
        return written


def group_tasks_by_doc(tasks: Iterable[Sequence[Any]]) -> "OrderedDict[str, List[Any]]":
    """Group (file_id, page_id, ...) task tuples by file_id, preserving the page
    order each task list was built in (the extraction CSV's row order).
    """
    by_doc: "OrderedDict[str, List[Any]]" = OrderedDict()
    for task in tasks:
        file_id, page_id = str(task[0]), task[1]
        by_doc.setdefault(file_id, []).append(page_id)
    return by_doc


def read_page_index(csv_path: str):
    """Read the page statistics CSV that drives the extract and classify stages.

    `file` is read as a string: with pandas' default type inference a document id such
    as `0001` became the integer 1 and `NA`/`null` became NaN, so the pages were written
    under `1/` (or `nan/`) and the next stage, looking under `0001/`, skipped the
    document in silence. Only `file` changes — `page` stays numeric (the sorts and the
    `<doc>-<page>.txt` names rely on it) and an empty cell is still NaN; ALTO ids
    (`CTX…`) are unaffected. (#31; shared by every extractor and classify_TEXT since
    Phase 5 — it lives here because classify_TEXT is too heavy to import.)
    """
    import pandas as pd

    return pd.read_csv(csv_path, dtype={"file": str}, keep_default_na=False, na_values=[""])


def output_is_current(output_path: Any, input_paths: Iterable[Any]) -> bool:
    """True when `output_path` exists and is at least as new as every input that exists.

    (#31 Phase 5) The resume check of the extract and classify stages. They used to skip
    a page or document whenever its output existed, so a re-ingested (changed) input kept
    the old text or categories. An input that does not exist is ignored — the stage
    decides what a missing input means.
    """
    try:
        out_mtime = os.stat(output_path).st_mtime_ns
    except OSError:
        return False
    for path in input_paths:
        try:
            if os.stat(path).st_mtime_ns > out_mtime:
                return False
        except OSError:
            continue
    return True


def write_bytes_if_changed(path: Any, data: bytes) -> bool:
    """Write `data` to `path` unless the file already holds exactly these bytes; True when
    written. (#31 Phase 5) The resume checks compare file times (``output_is_current``), so
    the split and extract stages leave an unchanged file untouched: rewriting it on every run
    would make each later stage redo its work."""
    try:
        with open(path, "rb") as fh:
            if fh.read() == data:
                return False
    except OSError:
        pass
    with open(path, "wb") as fh:
        fh.write(data)
    return True


def write_text_if_changed(path: Any, text: str, newline: Optional[str] = None) -> bool:
    """``write_bytes_if_changed`` for text, with the bytes `open(path, "w", encoding="utf-8",
    newline=newline)` would write (so a call site keeps its newline handling)."""
    if newline is None:
        text = text.replace("\n", os.linesep)
    elif newline not in ("", "\n"):
        text = text.replace("\n", newline)
    return write_bytes_if_changed(path, text.encode("utf-8"))


def read_page_text(output_text_dir: str, file_id: str, page_id: Any) -> Optional[str]:
    """Read back one page's extracted text, mirroring classify_TEXT.py's own lookup
    (hyphen filename first, underscore fallback for older layouts).
    """
    base = os.path.join(str(output_text_dir), str(file_id))
    for sep in ("-", "_"):
        candidate = os.path.join(base, f"{file_id}{sep}{page_id}.txt")
        if os.path.exists(candidate):
            with open(candidate, "r", encoding="utf-8") as fh:
                return fh.read()
    return None


def pages_and_content_from_text(
    output_text_dir: str,
    file_id: str,
    page_ids: Sequence[Any],
    force_single_page: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build the `pages[]` rows + the doc-level `content.text` for one document from its
    already-written page .txt files. Pages whose text could not be read back are skipped rather
    than guessed at.

    Each row is keyed by `page_index` (the page id of the OCR file: its physical position) and
    carries `content_status` (`text`, or `empty` for a page without text; D29). Since record
    schema 2.0 no row carries the engine (`pages[].ocr` is gone, D9): the caller records it as a
    paradata parameter.

    `content.text` is always the full document (every readable page concatenated,
    in source-page order, joined on a blank line) regardless of `force_single_page` —
    that flag is purely a `pages[]` assembly policy (issue #37 / D4): every source page
    for this document collapses into a SINGLE `pages[]` row (`page: "1"`).
    """
    page_records: List[Dict[str, Any]] = []
    texts: List[str] = []
    for position, page_id in enumerate(page_ids, start=1):
        text = read_page_text(output_text_dir, file_id, page_id)
        if text is None:
            continue
        texts.append(text)
        if not force_single_page:
            row = {"page": str(page_id), "content_status": "text" if text.strip() else "empty"}
            page_records.append({"page_index": _page_index(row, position), **row})
    joined = "\n\n".join(t for t in texts if t)

    if force_single_page and texts:
        page_records = [{"page_index": 1, "page": "1", "content_status": "text" if joined.strip() else "empty"}]

    return page_records, {"text": joined or None}


def quality_band(clear: int, noisy: int, trash: int) -> str:
    """Reduce a page's Clear/Noisy/Trash line counts (already computed by
    aggregate_STAT.py) to the schema's three-way `quality_band` enum. Deterministic
    plurality vote; ties favour the more optimistic band (Clear over Noisy over
    Trash), matching how a human skimming the counts would call a close page.
    """
    if clear >= noisy and clear >= trash:
        return "Clear"
    if noisy >= trash:
        return "Noisy"
    return "Trash"
