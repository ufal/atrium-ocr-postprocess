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

Ownership note: every write here uses PROGRAM_NAME = "ocr-postprocess" (named "alto-postprocess" until
2026-10-01; `atrium_document.PROGRAM_SUCCESSORS` treats the two as one writer), the current name
`atrium_document.BLOCK_OWNERS` lists for this repo's blocks. Every stage's
ParadataLogger stamps that same `program` (classify, aggregate and the text-lines
stages import PROGRAM_NAME from here; the other stages spell the literal) and names
the script in `config.script`. The older per-stage names (`langID-classify`,
`langID-aggregate`) survive only in the 2026-06 sample logs under `paradata/`, so
atrium-llm-enrich#13's "normalise the alto-postprocess program names" TODO is done
(agent_dev_logs/plans/31.plan.md, Phase 4).

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
    ocr_handoff_pages,
    quality_summary,
    resolve_originator,
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

#: (#31) The blocks whose WRITER is decided by `source.origin` (atrium_document
#: BLOCK_OWNERS lists two originators for each — this repo and llm-enrich's
#: digital-convert — and Issue #18 §1a lets the record's origin pick one).
POSITIONAL_BLOCKS = ("pages", "content", "lines", "tables")

#: (#31) doc_ids already warned about by the origin guard in this process — one line
#: per document, not one per stage call.
_FOREIGN_ORIGIN_WARNED: set = set()

#: (#31 Phase 4) The text-lines kinds atrium-digital-convert can read (its
#: api_util/digital_to_json.py sniffs content, not the extension: a PDF or a
#: WordprocessingML package since llm-enrich#18; ODT, ODS, XLSX and RTF through this
#: repository's own text_formats.py, which it vendors, since its v1.1.0-beta — #2 W2; DOC
#: and XLS too, which are no reader kind here). A `digital-born-<kind>` origin for any
#: OTHER kind (PPTX, EPUB, ...) hands the positional plane to an originator that cannot
#: produce it, so the record keeps `source` only — which is what
#: `[DOCUMENT].SOURCE_ORIGIN_BY_KIND` exists to let an operator decide.
DIGITAL_CONVERT_KINDS = frozenset({"pdf", "docx", "odt", "ods", "xlsx", "rtf"})

_ORIGIN_BY_KIND_KEY = "[DOCUMENT] SOURCE_ORIGIN_BY_KIND"


def parse_origin_by_kind(raw: str, known_kinds: Iterable[str]) -> Dict[str, str]:
    """`xlsx = ocr:generic, pptx = ocr:generic` → {"xlsx": "ocr:generic", ...}.

    Entries are separated by commas or newlines. A malformed entry, a kind no reader
    registers, a kind given twice, an empty value, or an origin no ORIGIN_ORIGINATORS
    prefix recognises raises ValueError naming the key: a typo must fail the run, not
    quietly switch the §1a ownership check off for that kind.
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
        if resolve_originator(origin) is None:
            raise ValueError(
                f"{_ORIGIN_BY_KIND_KEY}: origin {origin!r} for {kind!r} matches no known originator prefix "
                f"(ocr:…, vlm:…, ABBYY-ALTO, digital-born-…)"
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


def paradata_ref_for(logger) -> str:
    """Best-effort path to the paradata JSON this stage's ParadataLogger will emit.

    A plain function of the logger's own attributes, so callers that run inside a
    multiprocessing worker (which never sees the logger object itself — it isn't
    passed across the process boundary) can compute it once in the parent process
    and pass the resulting string down instead.
    """
    return os.path.join(logger.paradata_dir, f"{logger.run_id}_{logger.program}.json")


def _read_record(path: str) -> Dict[str, Any]:
    """The record at `path`, or {} when there is none or it cannot be read."""
    if path and os.path.exists(path):
        try:
            return load_document(path) or {}
        except Exception:
            return {}
    return {}


def foreign_origin(path: str, source: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The record's `source.origin` when it authorises ANOTHER originator, else None (#31).

    Reads the baseline record at `path` (its `source` is first-writer-wins, so it is
    the one that counts), falling back to the `source` this call is about to write.
    Returns None — "nothing to hold back" — when there is no origin, when the origin
    matches no ORIGIN_ORIGINATORS prefix (§1a abstains; so do we), and when it authorises
    this repo. A record that carries digital-convert's `pages[].needs_ocr` hand-off is still
    foreign: since W4 (atrium-digital-convert#4) `write_document_block()` writes such a record
    page by page (`handoff_pages`), never as a whole.
    """
    record = _read_record(path)
    origin = (record.get("source") or {}).get("origin") or (source or {}).get("origin")
    if not origin:
        return None
    if resolve_originator(origin) in (None, PROGRAM_NAME):
        return None
    return origin


def handoff_pages(record: Optional[Dict[str, Any]]) -> List[str]:
    """The record's page keys that digital-convert flagged `needs_ocr`: the pages this repo is
    asked to re-originate from OCR (atrium-digital-convert#4 W4; `atrium_document.OCR_HANDOFF`)."""
    return ocr_handoff_pages((record or {}).get("pages"))


def record_page_key(record: Optional[Dict[str, Any]], key: Any) -> Optional[str]:
    """The record's own key for the page an OCR file calls `key`, or None (W4).

    An ALTO page is named by its `PHYSICAL_IMG_NR` (or its 1-based position: `page_split`'s
    labels, `service/utils.parse_alto_page_labels`) — a PHYSICAL position. A born-digital record
    names its pages by their labels (`i`, `ii`, `1`, `A-1`) and carries the position in
    `pages[].page_index`. So when every page row has a `page_index`, a numeric key is looked up
    by position and never matched to a label by its number (position 3 of `i, ii, 1` is `1`, not
    a page labelled `3`) — page-classification's rule for the same records. Without page_index
    (an ALTO record), the key must be one of the record's labels.
    """
    if key is None:
        return None
    wanted = str(key).strip()
    rows = [r for r in (record or {}).get("pages") or [] if isinstance(r, dict) and r.get("page") is not None]
    if rows and all(isinstance(r.get("page_index"), int) and not isinstance(r.get("page_index"), bool) for r in rows):
        if wanted.isdigit():
            return next((str(r["page"]) for r in rows if r["page_index"] == int(wanted)), None)
    labels = [str(r["page"]) for r in rows]
    return wanted if wanted in labels else None


#: (W4) What this repo writes into a flagged page's `pages[]` row of a born-digital record: the
#: page's quality and that an engine ran. `page_index`, `canvas`, `needs_ocr` and its reason stay
#: the converter's (an ALTO's own canvas is in another unit, and `needs_ocr` is the request).
HANDOFF_PAGE_FIELDS = ("quality_score", "quality_band", "ocr")


def _handoff_writes(
    record: Dict[str, Any],
    set_blocks: Optional[Dict[str, Any]],
    merge_blocks: Optional[Dict[str, List[Dict[str, Any]]]],
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]], List[str]]:
    """This call's writes into a born-digital record that flags pages for OCR, page by page (W4).

    Returns (merge_blocks, replace_lines, notes): the `pages` rows and the `lines` rows of the
    flagged pages, keyed by the record's own page labels (`record_page_key`), and why anything
    else was held back. The `lines` of a flagged page REPLACE the converter's (they are what the
    OCR read on it); the `pages` rows merge `HANDOFF_PAGE_FIELDS` into the converter's, which
    keep `page_index`, `canvas`, `needs_ocr`, `text_layer` and page-classification's `category`.
    `content`, `tables` and every row of an unflagged page are held back: the shared module
    refuses them.
    """
    flagged = set(handoff_pages(record))
    notes: List[str] = []
    held = sorted(set(set_blocks or {}) & set(POSITIONAL_BLOCKS))
    if held:
        notes.append(f"not writing {', '.join(held)} (a whole-document block; the hand-off is per page)")
    merges: Dict[str, List[Dict[str, Any]]] = {}
    replace: Dict[str, List[Dict[str, Any]]] = {}
    for block, rows in (merge_blocks or {}).items():
        if block not in POSITIONAL_BLOCKS:
            merges[block] = rows
            continue
        if block not in ("pages", "lines"):
            notes.append(f"not writing {block}")
            continue
        kept, outside = [], set()
        for row in rows or []:
            key = record_page_key(record, row.get("page"))
            if key is None or key not in flagged:
                outside.add(str(row.get("page")))
                continue
            if block == "pages":
                row = {field: value for field, value in row.items() if field in HANDOFF_PAGE_FIELDS}
                if not row:
                    continue
            kept.append({**row, "page": key})
        if outside:
            notes.append(f"not writing {block}[] of page(s) {sorted(outside)}: no flagged page of the record")
        if kept:
            (replace if block == "lines" else merges)[block] = kept
    return merges, replace, notes


def _set_quality_summary(doc: DocumentRecord) -> None:
    """(atrium-project#73 R6) The record's `quality_summary`, recomputed from its pages and lines."""
    doc.set_block(
        "quality_summary",
        quality_summary({"pages": doc.get_block("pages") or [], "lines": doc.get_block("lines") or []}),
    )


def write_document_block(
    document_json_dir: str,
    doc_id: str,
    run_id: Optional[str],
    paradata_ref: str = "",
    *,
    source: Optional[Dict[str, Any]] = None,
    set_blocks: Optional[Dict[str, Any]] = None,
    merge_blocks: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    run_uuid: Optional[str] = None,
    ocr_pages: Optional[List[str]] = None,
) -> Optional[str]:
    """Open `<doc_id>.document.json` under `document_json_dir` (if configured and if
    it already exists), apply this stage's own contribution, and write it back in
    place. A missing baseline is safe (rule 3): the record then holds just this
    stage's part. No-ops entirely when `document_json_dir` is falsy.

    On a born-digital record that flags pages `needs_ocr` (digital-convert's OCR hand-off,
    atrium-digital-convert#4 W4) the write is made page by page: the `pages`/`lines` rows of the
    flagged pages, keyed by the record's own labels (`record_page_key`), with each flagged page's
    `lines` REPLACED (`DocumentRecord.replace_page_rows`), so the page holds what the OCR read and
    nothing of the converter's undecodable layer. Rows of other pages, `content`, `tables` and an
    OCR input's `source` are held back, with one warning. `ocr_pages` names flagged pages this
    call re-acquires even when it has no line for them (an OCR pass that found no text).

    Returns the path written, or None when nothing was written. Callers read the
    record back from that path (atrium-project#68).

    `run_uuid` is the stage's `ParadataLogger.run_uuid` (atrium-project#71): stamped with every
    block and the contributor entry, and the `@id` of the run's CreateAction.

    "In place" is literal: the record goes back to `path`, not to finalize()'s default
    `<record doc_id>.document.json`. The two differ whenever the baseline is keyed by
    another id than the one derived here (an AMČR seed carries the AMČR file id), and
    DocumentRecord keeps the baseline's id. Writing to the default name left the file
    every stage and caller looks up untouched, so the next stage re-read the seed, the
    last stage's write replaced all earlier ones, and /process returned the seed.

    This is the repo's single document-write chokepoint — every stage script routes
    through it — so it is also where the two Layer D guarantees are enforced once
    for all of them (atrium-project#10 D4/D8):

      * the inherited baseline is validated and a failure WARNED about (see
        `_baseline_is_invalid`);
      * every `merge_block()` is followed by `assert_fields_survived()`, which
        RAISES when a field the caller handed in was filtered away;
      * this stage's own output is validated before `finalize()`, and a failure
        RAISES so nothing is emitted (see `_validate_own_output`).
    """
    if not document_json_dir:
        return
    if not any([source, set_blocks, merge_blocks, ocr_pages]):
        return

    path = document_path(document_json_dir, doc_id)

    # (#31) The §1a originator check in atrium_document only WARNS when not strict
    # and then writes the block anyway — so without this guard every stage of this
    # repo (the unchanged classify/aggregate included) would put OCR-path fields and
    # categories into a record that digital-convert originates, the half-OCR/half-
    # digital plane §1a exists to refuse. `source` is still written; the CSV outputs
    # of the run are unaffected. (W4) A record that flags pages `needs_ocr` is the one
    # exception, and it is written page by page (`_handoff_writes`).
    replace_lines: List[Dict[str, Any]] = []
    replace_pages: List[str] = []
    if set_blocks or merge_blocks or ocr_pages or source:
        foreign = foreign_origin(path, source)
        record = _read_record(path) if foreign else {}
        source_note = None
        recorded = resolve_originator((record.get("source") or {}).get("origin"))
        if source and recorded and resolve_originator(source.get("origin")) != recorded:
            # The record's `source` is the original's (first writer wins). This stage read another
            # file — an OCR of the original's pages — whose sha256 and origin describe that file.
            source_note = "not writing source: the record's is the original's, and this stage read another file"
            source = None
        if foreign and handoff_pages(record):
            # (W4) The hand-off: the flagged pages only, the converter's other pages untouched.
            merge_blocks, replace, notes = _handoff_writes(record, set_blocks, merge_blocks)
            set_blocks = {k: v for k, v in (set_blocks or {}).items() if k not in POSITIONAL_BLOCKS} or None
            replace_lines = replace.get("lines", [])
            flagged = handoff_pages(record)
            replace_pages = [
                p for p in flagged if p in set(ocr_pages or []) or any(r["page"] == p for r in replace_lines)
            ]
            refused = sorted(set(ocr_pages or []) - set(flagged))
            if refused:
                raise ValueError(
                    f"{doc_id}: page(s) {refused} are not flagged needs_ocr in the record (flagged: {flagged})"
                )
            if source_note:
                notes.append(source_note)
            if notes and doc_id not in _FOREIGN_ORIGIN_WARNED:
                _FOREIGN_ORIGIN_WARNED.add(doc_id)
                _warn(
                    f"{doc_id}: the needs_ocr hand-off of a {foreign!r} record covers page(s) {flagged} "
                    f"(atrium_document OCR_HANDOFF) — " + "; ".join(notes) + ". The CSV outputs are unaffected."
                )
            merge_blocks = merge_blocks or None
        elif foreign and (set_blocks or merge_blocks or ocr_pages):
            dropped = sorted((set(set_blocks or {}) | set(merge_blocks or {})) & set(POSITIONAL_BLOCKS))
            if ocr_pages:
                raise ValueError(f"{doc_id}: the record flags no page needs_ocr, so no page of it can be re-acquired")
            if dropped and doc_id not in _FOREIGN_ORIGIN_WARNED:
                _FOREIGN_ORIGIN_WARNED.add(doc_id)
                _warn(
                    f"{doc_id}: source.origin {foreign!r} is originated by {resolve_originator(foreign)!r}, "
                    f"not {PROGRAM_NAME!r} — not writing {', '.join(dropped)} into its record "
                    f"(atrium_document §1a){'; ' + source_note if source_note else ''}. The CSV outputs are unaffected."
                )
            set_blocks = {k: v for k, v in (set_blocks or {}).items() if k not in POSITIONAL_BLOCKS} or None
            merge_blocks = {k: v for k, v in (merge_blocks or {}).items() if k not in POSITIONAL_BLOCKS} or None
        if not any([source, set_blocks, merge_blocks, replace_pages]):
            return

    baseline_was_invalid = _baseline_is_invalid(path)
    with DocumentRecord.open(
        doc_id,
        PROGRAM_NAME,
        baseline=path,
        run_id=run_id,
        run_uuid=run_uuid,
        paradata_ref=paradata_ref,
        out_dir=document_json_dir,
    ) as doc:
        if source:
            doc.set_source(**source)
        for block, payload in (set_blocks or {}).items():
            doc.set_block(block, payload)
        for block, records in (merge_blocks or {}).items():
            if records:
                doc.merge_block(block, records)
                # (#10 D8) merge_block()'s field filtering is silent by design, and
                # that silence is how a wrong grant produced rows stripped down to
                # their key that still validated (only page+line are required). This
                # is deliberately the raising form, not `warn_dropped_fields=True`:
                # it fires only when THIS repo hands over a field its own declared
                # grant in BLOCK_FIELD_OWNERS does not cover, which is a code bug in
                # the caller, not data variance — so every call site must pass only
                # fields it owns, plus the block's key fields.
                doc.assert_fields_survived(block, records)
        if replace_pages:
            # (W4) The flagged pages' lines are what the OCR read on them, and nothing else.
            doc.replace_page_rows("lines", replace_pages, replace_lines)
            doc.assert_fields_survived("lines", replace_lines)
        wrote = set(set_blocks or {}) | {block for block, rows in (merge_blocks or {}).items() if rows}
        if replace_pages or wrote & {"pages", "lines"}:
            _set_quality_summary(doc)
        _validate_own_output(doc, baseline_was_invalid)
        # Explicitly, and to `path` (#68): see the docstring. __exit__ then has nothing left to do.
        return doc.finalize(path)


#: (atrium-digital-convert#4 W3) What `write_scores()` writes: the common line-quality
#: model's fields, exactly `atrium_document.SCORING_FIELDS` — the one write this repo may make
#: into a positional plane another tool originated (a born-digital record's).
SCORE_LINE_FIELDS = ["categ", "quality_score", "lang"]
SCORE_PAGE_FIELDS = ["quality_score", "quality_band"]

#: Line categories this repo never overwrites: digital-convert's decode verdict on a born-digital
#: line (atrium_vocab.LINE_CATEGORY_ORIGINATORS). Its own Clear/Noisy/Trash/Non-text/Empty are a
#: disjoint set, so a verdict that a text layer does not decode is never re-scored as text.
DECODE_VERDICTS = frozenset({"Garbage", "Inverted"})


def write_scores(
    document_json_dir: str,
    doc_id: str,
    run_id: Optional[str],
    paradata_ref: str = "",
    *,
    pages: Optional[List[Dict[str, Any]]] = None,
    lines: Optional[List[Dict[str, Any]]] = None,
    run_uuid: Optional[str] = None,
) -> Optional[str]:
    """Score an EXISTING record in place: lines[] categ/quality_score/lang, pages[] quality.

    The scoring-only write path (atrium-digital-convert#4 W3, `POST /score_record`): unlike
    `write_document_block()`, it writes into any record — an OCR record this repo originated
    (a re-score) and a born-digital one digital-convert originated, where the shared module
    treats a merge limited to `atrium_document.SCORING_FIELDS` as a co-contribution. It never
    writes `text`, `bbox` or any other positional field, never `source`, and never a row:

      * a row carrying any other field (`text`, `bbox`, ...) is refused with ValueError
        before anything is opened — a caller bug, never data to drop in silence;
      * rows whose key is not in the record are left out (a score cannot invent a line);
      * a line carrying digital-convert's decode verdict (`DECODE_VERDICTS`) is left out;
      * every merge is followed by `assert_fields_survived()`, as in `write_document_block()`;
      * the output is validated before it is written, as in `write_document_block()`.

    Returns the path written, or None when there is no record at `<doc_id>.document.json` or
    nothing to write.
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
        (str(row.get("page")), row.get("line")): row for row in record.get("lines") or [] if isinstance(row, dict)
    }
    known_pages = {str(row.get("page")) for row in record.get("pages") or [] if isinstance(row, dict)}

    line_rows = [
        row
        for row in lines or []
        if (str(row.get("page")), row.get("line")) in known_lines
        and known_lines[(str(row.get("page")), row.get("line"))].get("categ") not in DECODE_VERDICTS
    ]
    page_rows = [row for row in pages or [] if str(row.get("page")) in known_pages]
    if not line_rows and not page_rows:
        return None

    baseline_was_invalid = _baseline_is_invalid(path)
    with DocumentRecord.open(
        doc_id,
        PROGRAM_NAME,
        baseline=path,
        run_id=run_id,
        run_uuid=run_uuid,
        paradata_ref=paradata_ref,
        out_dir=document_json_dir,
    ) as doc:
        if line_rows:
            doc.merge_block("lines", line_rows, key_fields=["page", "line"], own_fields=SCORE_LINE_FIELDS)
            doc.assert_fields_survived("lines", line_rows)
        if page_rows:
            doc.merge_block("pages", page_rows, key_fields=["page"], own_fields=SCORE_PAGE_FIELDS)
            doc.assert_fields_survived("pages", page_rows)
        _set_quality_summary(doc)
        _validate_own_output(doc, baseline_was_invalid)
        return doc.finalize(path)


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
    engine: str,
    force_single_page: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build the `pages[].ocr` records + the doc-level `content.text` for one
    document from its already-written page .txt files. Pages whose text could not
    be read back are skipped rather than guessed at.

    `content.text` is always the full document (every readable page concatenated,
    in source-page order, joined on a blank line) regardless of `force_single_page` —
    that flag is purely a `pages[]` assembly policy (issue #37 / D4): it does not
    change what text is extracted, only how many `pages[]` rows describe it.

    force_single_page=False (default): one `pages[]` row per source page, as before.
    force_single_page=True: every source page for this document collapses into a
    SINGLE `pages[]` row (`page: "1"`), whose `ocr.source_pages` lists the original
    page labels in the order they were concatenated — so the mapping back to
    individual source pages is preserved rather than lost.
    """
    page_records: List[Dict[str, Any]] = []
    texts: List[str] = []
    source_pages: List[str] = []
    for page_id in page_ids:
        text = read_page_text(output_text_dir, file_id, page_id)
        if text is None:
            continue
        source_pages.append(str(page_id))
        texts.append(text)
        if not force_single_page:
            page_records.append({"page": str(page_id), "ocr": {"engine": engine}})
    joined = "\n\n".join(t for t in texts if t)

    if force_single_page and source_pages:
        page_records = [
            {
                "page": "1",
                "ocr": {
                    "engine": engine,
                    "source_pages": source_pages,
                    "force_single_page": True,
                },
            }
        ]

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
