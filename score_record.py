#!/usr/bin/env python3
"""
score_record.py — score the lines of existing ATRIUM records: `POST /score_record` on the command line.

    python3 score_record.py RECORD.document.json [RECORD.document.json ...]
    python3 score_record.py DIR                      # every *.document.json in DIR
    python3 score_record.py DIR --pages i,ii --out-dir scored/ --scores

Each record's lines (`lines[].text`, in record order) are scored with the common line-quality
model exactly as they are, and the record gets `lines[].categ/quality_score/lang` and
`pages[].quality_score/quality_band` on the rows it already has, through the scoring-only write
path (`document_hook.write_scores`): never `text`, never a new row, never `source`. A line with
digital-convert's decode verdict (`Garbage`, `Inverted`) is left as it is. The selection and the
write are `record_scoring.py`'s, the same the service runs (atrium-project#72).

Where the outputs go:
  * the record: in place, as every stage of the batch pipeline updates it, or into `--out-dir`
    under its own file name;
  * with `--scores`: `<doc>.scores.json` beside it, the endpoint's answer without the record
    (`cleaned_lines`, `pages`, `limits_applied`);
  * the run's paradata: a file in `--paradata-dir` (the service returns it as `paradata`).

The service's MAX_PAGES and MAX_LINES_PER_PAGE do not apply here: the command line is the batch
path. The models are the service's (`service/text_inference.py`; GPT2_MODEL_NAME picks the
perplexity model), without LayoutReader.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from atrium_limits import LimitNotes
from atrium_paradata import ParadataLogger
from document_hook import PROGRAM_NAME
from record_scoring import UnknownPages, parse_pages, score_record, scored_by_model, select_lines

RECORD_SUFFIX = ".document.json"
SCORES_SUFFIX = ".scores.json"
SETUP_DIR = Path(__file__).resolve().parent / "setup"

Classifier = Callable[[List[str], Optional[LimitNotes]], List[Dict[str, Any]]]


def collect(paths: List[str]) -> List[Path]:
    """The record files named, and the `*.document.json` files of the folders named, sorted."""
    found: List[Path] = []
    for name in paths:
        path = Path(name)
        if path.is_dir():
            found.extend(sorted(p for p in path.iterdir() if p.is_file() and p.name.endswith(RECORD_SUFFIX)))
        elif path.is_file():
            found.append(path)
        else:
            raise FileNotFoundError(f"{name}: no such file or folder")
    return found


def score_file(
    path: Path,
    classify: Classifier,
    run: ParadataLogger,
    *,
    out_dir: Optional[Path] = None,
    pages: Optional[str] = None,
    scores: bool = False,
) -> Dict[str, Any]:
    """Score one record file; write the record (and with `scores`, its scores). Returns the answer
    `/score_record` gives for it, without `paradata`. Raises ValueError for a file that is not a
    record or names pages it does not have."""
    raw = path.read_bytes()
    try:
        record = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: not a record ({exc})") from exc
    if not isinstance(record, dict) or not str(record.get("doc_id") or "").strip():
        raise ValueError(f"{path}: the record has no doc_id")
    try:
        selection = select_lines(record, parse_pages(pages))
    except UnknownPages as exc:
        raise ValueError(f"{path}: {exc}") from exc

    notes = LimitNotes()
    scored = score_record(
        record,
        raw,
        selection,
        lambda texts: classify(texts, notes),
        run_id=run.run_id,
        paradata_ref=run.paradata_ref,
        run_uuid=run.run_uuid,
    )
    run.note_limits(notes)
    body = {
        "type": scored["type"],
        "doc_id": scored["doc_id"],
        "cleaned_lines": scored["cleaned_lines"],
        "pages": scored["pages"],
        "limits_applied": notes.as_list(),
        "document_json": scored["document_json"],
    }

    target_dir = out_dir or path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    target.write_text(json.dumps(body["document_json"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if scores:
        stem = path.name[: -len(RECORD_SUFFIX)] if path.name.endswith(RECORD_SUFFIX) else path.stem
        answer = {key: value for key, value in body.items() if key != "document_json"}
        (target_dir / f"{stem}{SCORES_SUFFIX}").write_text(
            json.dumps(answer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return body


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score the lines of ATRIUM records with the common line-quality model (POST /score_record).",
    )
    parser.add_argument("records", nargs="+", help="*.document.json files, or folders of them")
    parser.add_argument(
        "--pages",
        default=None,
        help="score only these pages, by the record's own page keys, comma-separated (e.g. i,ii,3); default: all",
    )
    parser.add_argument(
        "--out-dir", default=None, help="write the scored records here (default: in place, as the batch stages do)"
    )
    parser.add_argument(
        "--scores", action="store_true", help="also write <doc>.scores.json: the scored lines and the page summary"
    )
    parser.add_argument("--paradata-dir", default="paradata", help="where the run's paradata goes (default: paradata)")
    return parser


def _model_classifier() -> tuple[Classifier, Callable[[], str]]:
    """The service's quality model, loaded without LayoutReader."""
    from service.text_inference import text_manager

    text_manager.load_models(layout=False)
    return text_manager.classify_line_texts, text_manager.ppl_component


def main(argv: Optional[List[str]] = None, classify: Optional[Classifier] = None) -> int:
    """Exit 0 when every record was scored, 1 when one failed, 2 for a usage error."""
    args = build_parser().parse_args(argv)
    try:
        files = collect(args.records)
    except FileNotFoundError as exc:
        print(f"score_record: {exc}", file=sys.stderr)
        return 2
    if not files:
        print("score_record: no *.document.json files found", file=sys.stderr)
        return 2

    ppl_component: Optional[Callable[[], str]] = None
    if classify is None:
        classify, ppl_component = _model_classifier()

    run = ParadataLogger(
        program=PROGRAM_NAME,
        config={
            "command": "score_record",
            "records": len(files),
            "pages": args.pages,
            "out_dir": args.out_dir,
            "scores": args.scores,
        },
        paradata_dir=args.paradata_dir,
        config_dir=str(SETUP_DIR),
    )
    out_dir = Path(args.out_dir) if args.out_dir else None
    failed = 0
    model_used = False
    for path in files:
        try:
            body = score_file(path, classify, run, out_dir=out_dir, pages=args.pages, scores=args.scores)
        except ValueError as exc:
            failed += 1
            run.log_skip(str(path), str(exc))
            print(f"score_record: {exc}", file=sys.stderr)
            continue
        model_used = model_used or scored_by_model(body["cleaned_lines"])
        run.log_document_success()
        print(f"{path}: {len(body['cleaned_lines'])} lines scored on {len(body['pages'])} pages")
    if model_used and ppl_component is not None:
        run.log_component(ppl_component())
    run.finalize(input_total=len(files))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
