#!/usr/bin/env python3
"""
build_record.py — build the ATRIUM record from docproc: `POST /build_record` on the command line.

    python3 build_record.py DOC.docproc.json --seed SEED.document.json --out-dir records/
    python3 build_record.py DOC.docproc.json --doc-id C-1 --out-dir records/ --no-score

Since ARÚP's output contract of 12 November 2026 (atrium-project#84 §1, D20) ocr-postprocess is the
record builder: it builds every record from the stored docproc (`atr/docproc-json`), whichever
producer wrote it — digital-convert for a born-digital document, the ATR for a scanned one — and is
the only scorer of its lines. One run, one action:

  * `source`: the seed's identity (`doc_id`, `sha512`, `filename`, `media_type`; first writer wins),
    with `origin` (`digital` or `atr`), `page_count` and `language`;
  * `pages[]`: a row for every page of the original (`page_index`, `page`, `content_status`, the
    canvas when docproc gives a size), `empty` for a page routed empty (D29);
  * `lines[]`: from 1 per page in docproc's reading order, each with its docproc `block`, its
    `text_source` (`text-layer`, `atr`) and `bbox_precision`, and `unit: block` for a block scored as
    one unit (`docproc.py`; D18 rule 5);
  * `content.text`: the lines in reading order (H8);
  * the scores (unless `--no-score`): every line's `categ`, `quality_score`, `lang`, each page's
    `quality_score`, `quality_band`, and `quality_summary`, with the one model and band scheme
    `/score_record` uses, then the batch's document passes (D18 rules 1–4; an empty page has no score,
    rule 7; a block unit stays out of the page score, rule 5).

What fails the job (D13): a docproc that cannot be read (`docproc.DocprocError`), a page neither in
`pages[]` nor routed empty (H15), a block whose text could not be read, and a born-digital docproc with
pages that need the ATR: its ATR pages come in the ATR's docproc, and the merge of the two (H6) is not
built yet. The paradata name the docproc and the seed read, and the record written with its blocks and
fields, by SHA-512.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from atrium_document import DocumentRecord, validate_document
from atrium_limits import LimitNotes
from atrium_paradata import ParadataLogger
from docproc import DocprocError, ReadDocument, read_docproc
from document_hook import PROGRAM_NAME, RECORD_DISTRIBUTION, _set_quality_summary, document_path
from record_scoring import document_passes, page_records_from_lines, record_lang, scored_by_model

#: The AMČR distribution name of docproc (#84 §4).
DOCPROC_DISTRIBUTION = "atr/docproc-json"
SETUP_DIR = Path(__file__).resolve().parent / "setup"

#: `classify(texts)`: one scoring entry per text (`category`, `quality_score`, `lang`, ...).
Classifier = Callable[[List[str]], List[Dict[str, Any]]]


@dataclass
class Built:
    """What one build wrote: the record's path and bytes, the fields written, the scored entries."""

    path: str
    data: bytes
    written: Dict[str, List[str]]
    entries: List[Dict[str, Any]]
    document: ReadDocument


def _line_rows(document: ReadDocument) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index in sorted(document.pages):
        for number, line in enumerate(document.pages[index].lines, start=1):
            row: Dict[str, Any] = {
                "page": str(index),
                "line": number,
                "text": line.text,
                "block": line.block,
                "text_source": document.text_source,
                "bbox_precision": line.bbox_precision,
            }
            if line.bbox is not None:
                row["bbox"] = line.bbox
            if line.unit:
                row["unit"] = line.unit
            rows.append(row)
    return rows


def _page_rows(document: ReadDocument, lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    with_lines = {row["page"] for row in lines}
    rows: List[Dict[str, Any]] = []
    for index in range(1, document.page_count + 1):
        row: Dict[str, Any] = {
            "page_index": index,
            "page": str(index),
            "content_status": "text" if str(index) in with_lines else "empty",
        }
        page = document.pages.get(index)
        if page is not None and page.width and page.height:
            canvas: Dict[str, Any] = {"width": page.width, "height": page.height, "unit": page.unit}
            if page.user_unit:
                canvas["user_unit"] = page.user_unit
            row["canvas"] = canvas
        rows.append(row)
    return rows


def build(
    docproc_data: bytes,
    out_dir: str,
    *,
    seed: Optional[Dict[str, Any]] = None,
    doc_id: Optional[str] = None,
    classify: Optional[Classifier] = None,
    logger: Optional[ParadataLogger] = None,
    seed_data: Optional[bytes] = None,
) -> Built:
    """Build (and, with `classify`, score) the record from one docproc and an optional seed; write it
    to `<out_dir>/<doc_id>.document.json`. See the module docstring. Raises `DocprocError` (the
    job fails) or ValueError for a missing doc_id."""
    try:
        data = json.loads(docproc_data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DocprocError("invalid_docproc", f"not JSON ({exc})") from exc
    document = read_docproc(data)
    # A page flagged `needsAtr` is one whose text is not in this docproc (digital-convert's, of a page
    # the ATR has to read): it comes in the ATR's docproc, the stored one (H14).
    atr_pages = sorted(i for i, page in document.pages.items() if page.needs_atr)
    if atr_pages:
        raise DocprocError(
            "needs_atr",
            f"page(s) {atr_pages} need the ATR: their text comes in the ATR's docproc, and the merge of a "
            "mixed document (H6) is not built yet",
        )
    doc_id = (str((seed or {}).get("doc_id") or "").strip() or (doc_id or "").strip()) or None
    if not doc_id:
        raise ValueError("the record needs a doc_id: send the seed, or name one")

    lines = _line_rows(document)
    entries: List[Dict[str, Any]] = []
    if classify is not None and lines:
        entries = list(classify([row["text"] for row in lines]))
        if len(entries) != len(lines):
            raise RuntimeError(f"the classifier answered {len(entries)} lines for {len(lines)}")
        entries = document_passes(entries, [row["page"] for row in lines])  # D18 rule 4
        for row, entry in zip(lines, entries, strict=True):
            for name, value in (
                ("categ", entry.get("category")),
                ("quality_score", entry.get("quality_score")),
                ("lang", record_lang(entry)),
            ):
                if value is not None:
                    row[name] = value
    pages = _page_rows(document, lines)
    if entries:
        scored = {row["page"]: row for row in page_records_from_lines(lines) if len(row) > 1}
        for row in pages:
            if row["content_status"] == "text" and row["page"] in scored:
                row.update({k: v for k, v in scored[row["page"]].items() if k != "page"})

    os.makedirs(out_dir, exist_ok=True)
    baseline = None
    if seed is not None:
        baseline = document_path(out_dir, doc_id)
        Path(baseline).write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
    with DocumentRecord.open(doc_id, PROGRAM_NAME, baseline=baseline, out_dir=out_dir) as record:
        described: Dict[str, Any] = {"origin": document.origin, "page_count": document.page_count}
        if document.language:
            described["language"] = [document.language]
        record.set_source(**described)
        record.merge_block("pages", pages)
        record.assert_fields_survived("pages", pages)
        if lines:
            record.merge_block("lines", lines)
            record.assert_fields_survived("lines", lines)
        record.set_block("content", {"text": "\n".join(row["text"] for row in lines) or None})
        _set_quality_summary(record)
        validate_document(record.to_dict())
        path = record.finalize(document_path(out_dir, doc_id))
        written = record.written()
    data_written = Path(path).read_bytes()

    if logger is not None:
        logger.add_input(DOCPROC_DISTRIBUTION, docproc_data, media_type="application/json")
        if seed is not None:
            seed_bytes = seed_data if seed_data is not None else json.dumps(seed, ensure_ascii=False).encode("utf-8")
            logger.add_input(RECORD_DISTRIBUTION, seed_bytes, media_type="application/json")
        logger.add_output(RECORD_DISTRIBUTION, data_written, media_type="application/json", record_fields=written)
        logger.set_parameter("docproc_version", document.version)
        logger.set_parameter("scored", bool(entries))
    return Built(path=path, data=data_written, written=written, entries=entries, document=document)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_record.py",
        description="Build the ATRIUM record from docproc and score its lines (POST /build_record).",
    )
    parser.add_argument("docproc", help="the docproc document (atr/docproc-json)")
    parser.add_argument("--seed", default=None, help="the AMČR seed (doc_id, source.sha512, filename, media_type)")
    parser.add_argument("--doc-id", default=None, help="the record's doc_id when no seed is sent")
    parser.add_argument("--out-dir", default=".", help="where <doc_id>.document.json goes (default: .)")
    parser.add_argument("--no-score", action="store_true", help="build the record without scoring its lines")
    parser.add_argument("--paradata-dir", default="paradata", help="where the run's paradata goes (default: paradata)")
    return parser


def main(argv: Optional[List[str]] = None, classify: Optional[Callable[..., List[Dict[str, Any]]]] = None) -> int:
    """Exit 0 when the record was written, 1 when the build failed (nothing is written), 2 for a usage error."""
    args = build_parser().parse_args(argv)
    try:
        docproc_data = Path(args.docproc).read_bytes()
        seed_data = Path(args.seed).read_bytes() if args.seed else None
        seed = json.loads(seed_data.decode("utf-8-sig")) if seed_data else None
    except (OSError, ValueError) as exc:
        print(f"build_record: {exc}", file=sys.stderr)
        return 2

    ppl_component: Optional[Callable[[], str]] = None
    notes = LimitNotes()
    if not args.no_score and classify is None:
        from score_record import _model_classifier  # noqa: PLC0415  (loads the models)

        classify, ppl_component = _model_classifier()
    run = ParadataLogger(
        program=PROGRAM_NAME,
        config={"command": "build_record", "score": not args.no_score},
        paradata_dir=args.paradata_dir,
        config_dir=str(SETUP_DIR),
    )
    scorer = None if args.no_score else (lambda texts: classify(texts, notes))
    # Built in a scratch directory and moved into place only when complete: a failed build leaves nothing.
    with tempfile.TemporaryDirectory() as scratch:
        try:
            built = build(
                docproc_data,
                scratch,
                seed=seed,
                doc_id=args.doc_id,
                classify=scorer,
                logger=run,
                seed_data=seed_data,
            )
        except (DocprocError, ValueError, RuntimeError) as exc:
            run.fail(str(exc))
            run.finalize(input_total=1, processed_total=0)
            print(f"build_record: {exc}", file=sys.stderr)
            return 1
        os.makedirs(args.out_dir, exist_ok=True)
        target = Path(args.out_dir) / Path(built.path).name
        target.write_bytes(built.data)
    run.note_limits(notes)
    if ppl_component is not None and scored_by_model(built.entries):
        run.log_component(ppl_component())
    run.log_document_success()
    run.finalize(input_total=1)
    print(f"build_record: wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
