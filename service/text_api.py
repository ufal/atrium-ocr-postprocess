"""
service/text_api.py
FastAPI wrapper for the ATRIUM text processing service.

The typed contract (atrium-project#32 round 2). Every route declares its response model
and its error statuses, so the committed ``service/openapi.json`` — attached to every
release, and what the AMČR pipeline generates its clients from — types every field. The
models below DOCUMENT the responses (``response_model=None``): the bytes sent are what the
handlers build, and ``tests/test_api_contract.py`` validates real responses against the
published schema. Refusals carry registered reasons: a file of a kind this service does not
read is 415 ``unsupported_media_type`` (the reader's own code in ``cause``), a record that
cannot be opened is 422 ``invalid_record``. Regenerate the spec after an API change::

    python atrium_openapi.py export --app service.text_api:app --out service/openapi.json
"""

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set, Tuple, Union

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

# Put BOTH the repo root and this file's own directory on sys.path BEFORE any
# first-party import, so every launch context resolves:
#
#   * `python service/text_api.py`   -- the Dockerfile `api` stage ENTRYPOINT (#55)
#     and the documented production start path. Python sets sys.path[0] to the
#     SCRIPT's directory (service/), so the repo root is ABSENT here.
#   * `uvicorn service.text_api:app` -- CWD is the repo root, service/ is absent.
#   * pytest importing this module as `service.text_api` from the repo root.
#
# The repo-root half is what makes `atrium_document` / `atrium_paradata` /
# `document_hook` below resolve; the service/ half is what makes the bare sibling
# imports (`atrium_service`, `text_inference`, `utils`) resolve. Only the first
# launch context lacks the repo root, and it is the one nothing exercised until the
# `api` image was first started -- `docker-build-smoke` is gated to pull_request
# events, so no push to `test` ever ran it, and the image died at import with
# `ModuleNotFoundError: No module named 'atrium_document'`.
#
# This bootstrap MUST stay above every import below it; the E402 suppressions keep
# Ruff's import sorter (I001) from hoisting them back over it and re-breaking this
# (the sibling-import form of the same regression is tracked in atrium-project#18).
_current_dir = Path(__file__).resolve().parent
_repo_root = _current_dir.parent
for _bootstrap_path in (_repo_root, _current_dir):
    if str(_bootstrap_path) not in sys.path:
        sys.path.insert(0, str(_bootstrap_path))

# Both imports below are bare (service/ is on sys.path from the bootstrap above) and
# carry noqa: E402 so Ruff's import sorter does not hoist them above that bootstrap.
# `atrium_service` is the shared ATRIUM meta-contract helper (§4), byte-identical
# across every service and enforced by para-drift.reusable.yml.
from atrium_service import (  # noqa: E402
    AtriumDocument,
    AtriumHTTPError,
    CreateAction,
    InfoBase,
    LimitNote,
    ServiceState,
    add_cors,
    attach_error_handlers,
    attach_health,
    attach_inflight_middleware,
    attach_openapi_contract,
    build_info,
    error_responses,
    operation_id,
    parse_record_part,
    read_tool_version,
    read_upload_bounded,
    serve_lifecycle,
)
from text_inference import ingest_settings, text_manager, upload_page_count  # noqa: E402

# Bare like its siblings above, for the same reason: `utils` is service/utils.py,
# reached through the sys.path bootstrap, so the import resolves under
# `python service/text_api.py` (the Docker entrypoint) as well as under
# `uvicorn service.text_api:app`.
from utils import parse_alto_page_labels, parse_alto_software  # noqa: E402

# These three live at the REPO ROOT, not in service/, so they resolve only because the
# bootstrap above put the repo root on sys.path. They used to sit above it and worked
# everywhere except the one launch context that matters in production
# (`python service/text_api.py`, the `api` stage ENTRYPOINT), where the image died at
# import. Keep them below the bootstrap; `tests/test_service_entrypoint.py` enforces it.
import atrium_rocrate  # noqa: E402
from atrium_document import canonical_doc_id, resolve_originator  # noqa: E402
from atrium_limits import LimitExceeded, LimitNotes  # noqa: E402
from atrium_paradata import ParadataLogger  # noqa: E402
from document_hook import (  # noqa: E402
    PROGRAM_NAME,
    handoff_pages,
    record_page_key,
    resolve_input_origin,
    write_document_block,
)

# The record scoring of /score_record, shared with the score_record.py command (atrium-project#72);
# the line and page projections of /process come from it too.
from record_scoring import (  # noqa: E402
    UnknownPages,
    file_key,
    parse_pages,
    scored_by_model,
    select_lines,
)
from record_scoring import page_records_from_lines as _page_records_from_lines  # noqa: E402
from record_scoring import record_lang as _record_lang  # noqa: E402
from record_scoring import score_record as score_record_lines  # noqa: E402

# `service.utils`, not the bare `utils` above, on purpose: text_inference.py imports its
# parser from the package path, and an exception is matched by class object, so this must be
# the module that raises it.
from service.utils import AltoUnreadable  # noqa: E402
from text_formats import (  # noqa: E402
    COMPRESSION_SUFFIXES,
    READERS,
    IngestError,
    compression_of,
    producer_origin,
    sniff_kind,
)
from text_util import constant_invariant_errors  # noqa: E402
from tool_limits import (  # noqa: E402
    LIMITS,
    MAX_FILE_MB,
    MAX_LINES_PER_PAGE,
    MAX_PAGES,
    MAX_UPLOAD,
    READER_TIMEOUT_S,
    ZIP_MAX_MEMBER_MB,
    ZIP_MAX_MEMBERS,
    ZIP_MAX_RATIO,
    ZIP_MAX_TOTAL_MB,
)

logger = logging.getLogger(__name__)

#: The tool id (/info `service`, the spec's `x-atrium-service`): the repository name.
SERVICE = "atrium-ocr-postprocess"

#: The id this service published until 2026-10-01 (atrium-project#72). The release gate accepts the changed
#: `x-atrium-service` only when the spec declares the previous one (`atrium_openapi.py compare`).
PREVIOUS_SERVICE = "atrium-alto-postprocess"

# Every limit this service has is declared in tool_limits.py (atrium-project#53, factor III)
# and read per request. These are the import-time values, kept for the callers and tests
# that import them.
MAX_UPLOAD_MB = MAX_UPLOAD.get()
MAX_UPLOAD_BYTES = int(MAX_UPLOAD_MB * 1024 * 1024)


#: Readiness/draining/in-flight state for the §4.6 disposability contract (issue #55).
_state = ServiceState()

#: (#31 Phase 4) Reader codes that mean "this service does not read this kind of
#: file", and a retry will not help. Since atrium-project#32 round 2 they answer 415 with
#: the registered `reason: "unsupported_media_type"` (a 400 with `reason: null` before, which
#: no other ATRIUM service sent for a media type); the reader's code stays at the start of
#: `detail` and is also the body's `cause`. The limit codes below are refusals of a
#: supported file that is over a [TEXT_INGEST] limit. `dependency_missing` is this
#: deployment's shortcoming, not the file's: a 501 (see DEPENDENCY_REASONS). Every other
#: code (corrupt, encrypted, malformed, no_text, ...) means the file is of a supported kind
#: but unreadable — a 422. One mapping for every path, sniffed or explicit.
UNSUPPORTED_REASONS = frozenset(
    {"binary_content", "legacy_office_unsupported", "image_needs_ocr", "archive_unsupported"}
)

#: (atrium-project#32 round 2) A reader's optional dependency (pypdfium2, lxml) is not
#: installed in this image: HTTP 501, `reason: null`, `cause: "dependency_missing"`. It was
#: one of the 400s above, which told the caller its file was the problem.
DEPENDENCY_REASONS = frozenset({"dependency_missing"})

#: What a 415 lists as `accepted` (§4.4): every file extension a reader is registered for,
#: and the single-file compression wrappers they may come in. The kind is decided from the
#: bytes, so this is the list of names a client can expect to work, not a filter.
ACCEPTED_EXTENSIONS = sorted({ext for spec in READERS.values() for ext in spec.extensions} | set(COMPRESSION_SUFFIXES))

#: (atrium-project#53) The reader codes that mean "over a limit". They answer with the
#: harmonised `reason: "limit_exceeded"` and the status the limit's cause calls for: 413
#: for a size or count (too_large, zip_limits_exceeded — they used to be 422), 422 for the
#: reader's time budget (timeout). alto's own code stays at the start of `detail`.
LIMIT_REASONS = frozenset({"too_large", "zip_limits_exceeded", "timeout"})

#: The [TEXT_INGEST] limits by their config key, which is how a reader message names one
#: ("… > MAX_PAGES=20000").
_INGEST_LIMITS = {
    spec.key.upper(): spec
    for spec in (
        MAX_FILE_MB,
        ZIP_MAX_MEMBERS,
        ZIP_MAX_TOTAL_MB,
        ZIP_MAX_MEMBER_MB,
        ZIP_MAX_RATIO,
        MAX_PAGES,
        MAX_LINES_PER_PAGE,
        READER_TIMEOUT_S,
    )
}
_INGEST_LIMIT_NAME = re.compile(r"\b(" + "|".join(sorted(_INGEST_LIMITS, key=len, reverse=True)) + r")\b")


def _ingest_http_error(exc: IngestError, *, sniffing: bool = False) -> Exception:
    """The HTTP error for a reader failure (§4.4).

    415 ``unsupported_media_type`` for a kind this service does not read, 501 for a missing
    reader dependency, 413/422 ``limit_exceeded`` for a limit, else 422. Every one but a
    named limit (an ``atrium_limits.LimitExceeded``, whose body names the limit in ``limit``)
    carries the reader's code as ``cause`` (unregistered: informational, it may change), and
    ``detail`` starts with it as it always did.
    """
    detail = f"{exc.code}: {exc.message}"
    if exc.code in LIMIT_REASONS:
        match = _INGEST_LIMIT_NAME.search(exc.message)
        if match:
            spec = _INGEST_LIMITS[match.group(1)]
            return LimitExceeded(
                spec.key,
                LIMITS.get(spec.key),
                None,
                unit=spec.unit,
                env=spec.env,
                http_status=spec.http_status,
                detail=f"{detail} (setting: {spec.env}, or [TEXT_INGEST] {match.group(1)} in the config)",
            )
        # A limit the reader did not name (an RTF over any cap, out of memory while reading).
        return AtriumHTTPError(422 if exc.code == "timeout" else 413, detail, reason="limit_exceeded", cause=exc.code)
    if exc.code in DEPENDENCY_REASONS:
        return AtriumHTTPError(501, detail, cause=exc.code)
    if exc.code in UNSUPPORTED_REASONS:
        if sniffing:
            detail = (
                f"Cannot auto-detect a supported file type ({detail}). "
                "Set task_type='alto', 'text', 'json' or 'document'."
            )
        return AtriumHTTPError(
            415, detail, reason="unsupported_media_type", cause=exc.code, accepted=ACCEPTED_EXTENSIONS
        )
    return AtriumHTTPError(422, detail, cause=exc.code)


#: At most this many UTF-8 bytes of the client's file name go into the temporary file's
#: name, which keeps its extension for sniffing and stays far below the OS's 255-byte limit.
_MAX_SUFFIX_BYTES = 100


def _temp_suffix(filename: str) -> str:
    """``_<name>`` for the upload's temporary file: the client's base name only (a name with
    a path, or one over the OS name limit, used to fail the request with a bare 500), its
    tail kept so the extension survives."""
    base = Path(filename.replace("\\", "/")).name
    tail = base.encode("utf-8")[-_MAX_SUFFIX_BYTES:].decode("utf-8", errors="ignore")
    return f"_{tail}" if tail not in ("", ".", "..") else ""


# ── the typed contract (atrium-project#32 round 2) ──────────────────────────────────────────
# These models document the responses the handlers build; they do not filter them. A field
# the handlers always send has no default (required); one they send only sometimes defaults
# to None. Descriptions are published in service/openapi.json, so they are written for the
# client. Values such as `type` and `category` are open strings: a new value must never break
# a client generated from an older spec.

#: `task_type` of /process. `auto` decides by the name (`.txt` → text, `.json` → json) and
#: otherwise by the bytes. Any other value is refused (422); it used to be read as `text`.
TaskType = Literal["auto", "alto", "text", "json", "document"]

#: `reading_order` of /process, for an ALTO upload: `layoutreader` (LayoutLMv3 reorders the lines,
#: the batch `--method layoutreader`) or `alto` (the ALTO's own order, `--method alto-tools`).
ReadingOrder = Literal["layoutreader", "alto"]


class AltoLine(BaseModel):
    """One classified line (`cleaned_lines[]`)."""

    model_config = ConfigDict(extra="allow")

    line_num: int = Field(description="1-based position after layout reordering; per page for a document.")
    text: str = Field(
        description="The cleaned text: split-word merges and the pre-filter's OCR repairs applied (the line as the CSV's `text`)."
    )
    lang: str = Field(
        description="The language code after the expected-language remap (FastText, then setup/config.txt)."
    )
    lang_score: float = Field(description="Its confidence, from 0 to 1.")
    original_lang: str = Field(description="FastText's own prediction, before the remap.")
    orig_lang_score: float = Field(description="FastText's own confidence, from 0 to 1.")
    perplexity: float = Field(description="Qwen2.5-0.5B perplexity; 99999 when the line's batch could not be scored.")
    garbage_density: float = Field(description="The share of non-alphanumeric noise characters.")
    sym_count: int = Field(description="Tokens with strange or unexpected symbols.")
    upper_count: int = Field(description="Tokens with mid-word uppercase artefacts.")
    repeated_count: int = Field(description="Tokens with a non-standard character repetition.")
    ldl_fuses: int = Field(description="Tokens with letter-digit-letter fusions.")
    gibberish: int = Field(description="Tokens lacking vowels or with highly irregular ratios.")
    word_weird: float = Field(description="The mean per-word weirdness score, from 0 to 1.")
    quality_score: float = Field(description="The composite quality score, from 0 to 1.")
    category: str = Field(
        description="`Clear`, `Noisy`, `Trash`, `Non-text` or `Empty`; written to the record as `lines[].categ`."
    )
    page: Optional[str] = Field(
        None,
        description=(
            "Document uploads, and text or JSON uploads of more than one page: the page (a 1-based index; a "
            "paged JSON's own page number)."
        ),
    )
    page_label: Optional[str] = Field(None, description="Document uploads only: the source's own page label.")


class AltoPage(BaseModel):
    """One page of a document upload, or of a text or JSON upload of more than one page (`pages[]`)."""

    model_config = ConfigDict(extra="allow")

    page: str = Field(description="The page, as its 1-based index (the `page` of its lines).")
    page_label: Optional[str] = Field(description="The source's own page label; empty when it has none.")
    lines: int = Field(description="How many classified lines the page has.")
    text_layer: Optional[str] = Field(description="PDF only: `none`, `garbled`, `ocr` or `digital`; else null.")
    needs_ocr_reason: Optional[str] = Field(description="Why the page needs OCR; null when it does not.")


class OcrHandoff(BaseModel):
    """What an upload re-acquired in a born-digital record (atrium-digital-convert#4 W4)."""

    page: str = Field(description="The record's page key (a `needs_ocr` page) the upload's lines were written under.")
    lines_replaced: int = Field(description="How many of the converter's lines that page held before.")
    lines_written: int = Field(description="How many classified lines it holds now.")


class ProcessResponse(BaseModel):
    """The classified lines of one upload, and its record when one was sent."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(description="What the upload was read as: `alto_xml`, `plain_text`, `json` or `document`.")
    filename: str = Field(description="The upload's name, as sent.")
    cleaned_lines: List[AltoLine] = Field(description="The classified lines, in reading order.")
    limits_applied: List[LimitNote] = Field(description="Every limit that shaped the result without refusing it.")
    format: Optional[str] = Field(None, description="Document uploads only: the reader's kind (`pdf`, `docx`, ...).")
    media_type: Optional[str] = Field(None, description="Document uploads only: the media type of that kind.")
    origin: Optional[str] = Field(None, description="Document uploads only: the `source.origin` the record gets.")
    pages: Optional[List[AltoPage]] = Field(
        None,
        description=(
            "One entry per page: document uploads, and text uploads with form feeds or JSON uploads of several "
            "pages (paged as the batch pipeline pages them)."
        ),
    )
    document_json: Optional[AtriumDocument] = Field(
        None,
        description=(
            "When the record was sent as `document_json`: the record with ocr-postprocess's `pages` and "
            "`lines` fields merged in. A born-digital record (atrium_document §1a) comes back as it was sent, "
            "except a page it flags `needs_ocr`, which an ALTO upload of that page re-acquires (see `ocr_handoff`). "
            "With `include_record=true` and no record sent: a new record holding this upload's `source`, `pages` "
            "and `lines`, as the batch pipeline writes one; absent for a born-digital upload, which is "
            "digital-convert's to originate."
        ),
    )
    ocr_handoff: Optional[OcrHandoff] = Field(
        None,
        description=(
            "Only when the upload re-acquired a page a born-digital record flags `needs_ocr` (the OCR hand-off, "
            "atrium-digital-convert#4 W4): that page's lines were replaced by the upload's."
        ),
    )
    document_json_out: Optional[AtriumDocument] = Field(
        None,
        deprecated=True,
        description=(
            "Deprecated: the same record, returned under this name when it was sent as `document_record`. "
            "Send `document_json` and read `document_json`, as every ATRIUM service does."
        ),
    )
    paradata: Optional[CreateAction] = Field(
        description=(
            "The call's provenance: its Process Run Crate `CreateAction` (atrium-project#71), whose `@id` is the "
            "`run_uuid` stamped into the returned record."
        ),
    )


class ScoredLine(AltoLine):
    """One scored record line (`/score_record` `cleaned_lines[]`): the classifier's fields, keyed
    by the record's own `page` and `line`."""

    page: str = Field(description="The record's page key (`lines[].page`, e.g. a PDF page label like `iv`).")
    line: int = Field(description="The record's line key (`lines[].line`).")


class ScoredPage(BaseModel):
    """One page of a `/score_record` call (`pages[]`)."""

    page: str = Field(description="The record's page key.")
    lines_scored: int = Field(
        description="Lines classified on this page, those the CPU pre-filter answered `Non-text` or `Empty` included."
    )
    skipped_decode_verdict: int = Field(
        description="Lines left as they were: digital-convert's decode verdict (`Garbage`, `Inverted`) stands."
    )
    skipped_empty: int = Field(description="Lines with no text, not scored.")
    quality_score: Optional[float] = Field(description="The mean score of the scored lines; null when none was.")
    quality_band: Optional[str] = Field(
        description="`Clear`, `Noisy` or `Trash` by plurality of the scored lines; null when none of the three occurs."
    )


class ScoreRecordResponse(BaseModel):
    """`/score_record`: the record's lines scored by the common quality model, and the record."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(description="`record`.")
    doc_id: str = Field(description="The record's `doc_id`.")
    cleaned_lines: List[ScoredLine] = Field(description="The scored lines, in record order.")
    pages: List[ScoredPage] = Field(description="One entry per page considered, in record order.")
    limits_applied: List[LimitNote] = Field(description="Every limit that shaped the result without refusing it.")
    document_json: AtriumDocument = Field(
        description=(
            "The record with ocr-postprocess's scoring fields merged in: `lines[].categ/quality_score/lang` and "
            "`pages[].quality_score/quality_band`, on existing rows only; returned as sent when nothing was scored."
        )
    )
    paradata: Optional[CreateAction] = Field(
        description="The call's provenance: its `CreateAction`, whose `@id` is the `run_uuid` stamped into the record."
    )


class AltoInfo(InfoBase):
    """`/info` of atrium-ocr-postprocess."""

    status: str = Field(description="`active`.")
    device: str = Field(description="Where the models run: `cpu` or `cuda`.")
    supported_formats: List[str] = Field(description="The kinds of file `/process` reads.")
    quality_categories: List[str] = Field(description="The `category` values a line can get.")
    line_fields: List[str] = Field(description="The fields of a classified line.")


#: What the record parts' description says they are.
_RECORD_PART_HELP = (
    "Optional baseline ATRIUM Document JSON (accretion model, docs/document_schema.md), or an AMČR seed "
    "(`doc_id`, `source`). When given, the response's `document_json` carries the record back with "
    "ocr-postprocess's `pages` and `lines` fields merged in; every other tool's block and field passes "
    "through. A record that does not validate against atrium_document.schema.json is still accepted "
    "(rule 6); one that cannot be opened is refused (422 `invalid_record`). An empty part counts as none."
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle context manager — loads models before traffic, then drains on SIGTERM.

    The RuntimeError below is deliberate and unchanged: a service that cannot load its
    models should fail loudly rather than sit "not ready" forever. Under Kubernetes that
    surfaces as a crash-loop on the startupProbe, which is the correct signal for a
    misconfigured deployment — see docs/k8s_deployment.md's "Known limits" in the hub.
    """
    try:
        # (#31 Phase 4) A malformed [TEXT_INGEST]/[DOCUMENT] key fails the start, like a
        # missing model, rather than the first upload that needs it.
        await asyncio.to_thread(ingest_settings)
    except ValueError as exc:
        raise RuntimeError(f"invalid [TEXT_INGEST]/[DOCUMENT] configuration: {exc}") from exc
    # A categoriser whose boundaries are inverted (CATEG_TRASH_SCORE_MAX >= CATEG_NOISY_SCORE_MAX,
    # SHORT_PPL_CAP >= PERPLEXITY_THRESHOLD_MAX, no positive QS weights) answers every line
    # confidently and wrongly, so it fails the start rather than serve: the same check
    # classify_TEXT.main and tools/recategorize_from_csv.py apply.
    invalid = constant_invariant_errors()
    if invalid:
        raise RuntimeError("invalid [TEXT_UTILS] constants: " + "; ".join(invalid))
    try:
        # Off the event loop (issue #55): load_models() pulls several torch models, and
        # the loop should be free to answer the /ready probe a startupProbe is polling.
        await asyncio.to_thread(text_manager.load_models)
    except Exception as exc:
        raise RuntimeError(f"Failed to initialise models on startup: {exc}") from exc
    _state.warm = True
    # issue #55: composes with the model load above rather than replacing it. Flips
    # /ready to 503 on SIGTERM and waits for in-flight processing before exit.
    async with serve_lifecycle(_state):
        yield


app = FastAPI(
    title="ATRIUM Text Processor",
    version=read_tool_version(Path(__file__).resolve().parent),
    lifespan=lifespan,
    # The typed contract (atrium-project#32 round 2): every route documents the §4.4 error
    # body for 422 and 500 (and FastAPI's own 422 body, which is not what is sent, goes);
    # operationIds are the handler names; the spec never depends on a root_path.
    responses=error_responses(422, 500),
    generate_unique_id_function=operation_id,
    root_path_in_servers=False,
)
attach_inflight_middleware(app, _state)
# §4.4 error body {status, reason, detail} for every error (atrium-project#32 item 2, #53).
attach_error_handlers(app)
# The published spec: reason registry, record schema, service id (atrium-project#32 item 3).
attach_openapi_contract(app, SERVICE, previous=PREVIOUS_SERVICE)

# CORS — standard §4.5 configuration; default "*" for parity with sibling services.
add_cors(app, methods=["GET", "POST"])


def _deep_health() -> str | None:
    """Deep readiness (§4.1): quality/language models are loaded."""
    # `device` is set in the constructor ("cpu"), so it says nothing: ask whether the load finished.
    if (
        not getattr(text_manager, "_models_loaded", False)
        or text_manager.ppl_model is None
        or text_manager.ft_model is None
    ):
        return "text models not loaded"
    return None


attach_health(app, deep_check=_deep_health, state=_state)


def _refuse_if_draining() -> None:
    """Reject NEW work once a shutdown signal has arrived (issue #55).

    /ready has already flipped to 503 by this point, but a request accepted before the
    orchestrator noticed can still reach a handler. Answering 503 here bounds the set of
    requests the drain must wait for — and matters more than usual in this service,
    whose handler writes a `delete=False` temp file that only its own `finally` removes:
    a request killed mid-flight by a SIGKILL leaves that file behind.
    """
    if _state.draining:
        raise HTTPException(status_code=503, detail="Service is shutting down; retry against a live replica.")


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

# Resolve the absolute path to setup/para_config.txt
PARA_CONFIG_PATH = str(Path(__file__).resolve().parent.parent / "setup" / "para_config.txt")

# ---------------------------------------------------------------------------
# Accretion mapping (atrium-project#10 J1)
# ---------------------------------------------------------------------------
# One request describes exactly ONE page, and that is a property of the inference
# layer rather than a simplification made here:
#   * alto  — service/utils.parse_alto_xml_lines() takes its geometry from
#             `root.find(".//Page")`, i.e. the FIRST <Page> only, and
#             post_process_text() then reads the whole upload's lines back in that
#             one page's coordinate space;
#   * json  — process_json() is "one JSON file = one page" by the batch pipeline's
#             own convention (see its docstring);
#   * text  — a .txt upload carries no page concept at all.
#
# `page_metrics` used to be a hardcoded
# `[{"page": "1", "quality_score": result.get("doc_quality", 1.0)}]`, and
# `doc_quality` is a key `text_inference` returns on no path whatsoever — so every
# accreted record claimed a perfect 1.0 for exactly one page named "1", regardless
# of what was uploaded, appending a page row to any document whose own labels
# (PHYSICAL_IMG_NR) were anything else. Below, the page SET, the LABEL and every
# metric are all derived from the request: the label from the ALTO's own <Page>, the
# rows from the lines actually classified, the metrics from those lines' scores.
#
# Fallback label for the two formats that have no page identity of their own. "1" is
# not a guess there: it is the label page_split.split_json_document gives the single
# page of a Family-C JSON document, and a plain-text upload has exactly one page by
# definition.
SERVICE_PAGE_LABEL = "1"


def _lines_records_from_result(result: Dict[str, Any], page: str = SERVICE_PAGE_LABEL) -> List[Dict[str, Any]]:
    """Project `text_inference`'s classified lines onto atrium_document's `lines[]`
    shape: key fields `page`/`line`, plus this repo's owned fields (categ,
    quality_score, lang, text) — the same projection classify_TEXT.py's
    `_lines_records_from_df()` performs for the batch path.

    Reads **`cleaned_lines`**, which is the key `text_inference` returns on every
    path (`process_text_file`/`process_json`/`process_alto`, including its empty-
    document early return). The endpoint used to read `result["lines"]`, a key
    nothing ever writes, so the list was always empty and `write_document_block`'s
    `if records:` guard skipped the lines merge on every single call (J1).

    Note the two spellings: the inference layer calls the field `category`, the
    schema calls it `categ`. Values pass through VERBATIM, and the values THIS repo
    passes through are the ocr-postprocess half of `lines[].categ` — `"Clear"`,
    `"Empty"`, `"Noisy"`, `"Non-text"`, `"Trash"`, i.e. whatever
    `text_util.determine_category()` returned, unaltered. `"Garbage"` and
    `"Inverted"` belong to the OTHER authorised originator of that block
    (`digital-convert`, atrium-digital-convert); the two sets are deliberately
    DISJOINT and nothing on this path can produce them.
    `atrium_vocab.LINE_CATEGORY_ORIGINATORS` is the declaration of which tool emits
    which, and `text_util.CATEGORIES_EMITTED` is this repo's side of it.

    Downstream filters key off these exact strings. atrium-digital-convert's
    api_util/json_to_md.py drops `atrium_vocab.UNTRUSTWORTHY_LINE_CATEGORIES`, which
    covers `"Trash"` from this half and `"Garbage"`/`"Inverted"` from the other (V-1 in
    the hub's `docs/skos_strategy.md` §6, fixed 2026-09-25). So never re-spell a
    category on the way out: a re-spelling would not fail validation, it would only
    put this repo's output out of step with the registry and its consumers, in
    silence.
    """
    records: List[Dict[str, Any]] = []
    for entry in result.get("cleaned_lines") or []:
        if not isinstance(entry, dict):
            continue
        line_num = entry.get("line_num")
        if line_num is None:
            # `line` is a required key field; a row without one cannot be merged
            # (merge_block would align every such row onto the same null key).
            continue
        # (#31) document uploads carry their own per-line page; the others use `page`.
        record: Dict[str, Any] = {"page": str(entry.get("page") or page), "line": int(line_num)}
        for source_key, schema_field in (
            ("text", "text"),
            ("lang", "lang"),
            ("quality_score", "quality_score"),
            ("category", "categ"),
        ):
            value = _record_lang(entry) if source_key == "lang" else entry.get(source_key)
            if value is not None:
                record[schema_field] = value
        records.append(record)
    return records


def _model_components(cleaned_lines: Any) -> Set[str]:
    """The licensed model component a request exercised: the perplexity model, when at least one
    line was scored by it (a line the CPU pre-filter answered on its own used no model). Logged
    like `classify_TEXT.py` logs it, so an API run's paradata names what a batch run's does."""
    if scored_by_model(cleaned_lines):
        return {text_manager.ppl_component()}
    return set()


def _record_page_count(record: Optional[Dict[str, Any]]) -> int:
    """How many distinct pages the record already carries, in `pages[]` or `lines[]`."""
    keys = set()
    for block in ("pages", "lines"):
        for row in (record or {}).get(block) or []:
            if isinstance(row, dict) and row.get("page") is not None:
                keys.add(str(row["page"]))
    return len(keys)


def _accretion_records(
    task_type: str,
    upload_path: str,
    result: Dict[str, Any],
    page: Optional[str] = None,
    record: Optional[Dict[str, Any]] = None,
):
    """The (pages, lines) contribution for one /process request, or ([], []) when it
    cannot be attributed to a page truthfully. `page`, when given, is the record's key the
    one-page upload is written under (the `page` form field); otherwise the upload's own label.

    A multi-page ALTO upload is the "cannot" case, and it is refused rather than
    guessed at: `parse_alto_xml_lines` flattens every page's `<TextLine>` into one
    list scaled by the FIRST page's dimensions, so `result` genuinely does not say
    which page a given line came from. Writing them all under one label would put
    misattributed rows into a record other tools then align their own fields onto —
    the silent-wrong-data failure this whole issue is about. The classified lines are
    still returned in the HTTP response; only the accretion is skipped, and loudly.
    Multi-page documents belong to the batch pipeline (page_split.py splits first).
    """
    if task_type == "document":
        # (#31) Pages come from the reader, one label per line; but a born-digital
        # upload (DOCX, visible-text PDF, ...) is digital-convert's to originate
        # (atrium_document §1a) — this repo contributes nothing to its record.
        origin = result.get("origin") or ""
        if resolve_originator(origin) not in (None, PROGRAM_NAME):
            logger.warning(
                "born-digital upload (origin %r): no pages[]/lines[] contribution to the document "
                "record — it is originated by %r (atrium_document §1a). The classified lines are "
                "still returned.",
                origin,
                resolve_originator(origin),
            )
            return [], []
        lines = _lines_records_from_result(result)
        return _page_records_from_lines(lines), lines

    page_labels = parse_alto_page_labels(upload_path) if task_type == "alto" else []
    if len(page_labels) > 1:
        logger.warning(
            "upload has %d <Page> elements: /process classifies them as one "
            "flattened page, so no pages[]/lines[] contribution can be attributed "
            "per page. Skipping the accretion for this request — split the "
            "document first (page_split.py) and post one page per request, or "
            "use the batch pipeline.",
            len(page_labels),
        )
        return [], []

    if task_type in ("json", "text") and len(result.get("pages") or []) > 1:
        # A text upload with form feeds or a paged JSON (atrium-project#72): every line carries its
        # page, as on the batch path, whose split writes the same keys. (`page` was refused for it.)
        lines = _lines_records_from_result(result)
        return _page_records_from_lines(lines), lines

    if page is None and task_type in ("json", "text") and _record_page_count(record) > 1:
        # A JSON or text upload says nothing about which page it is, and the record has several:
        # writing it under the default page "1" would silently replace that page's lines.
        logger.warning(
            "a %s upload without `page` was sent with a record of %d pages: not writing it into the record "
            "(send `page`, the record's key for it). The classified lines are still returned.",
            task_type,
            _record_page_count(record),
        )
        return [], []

    page = page or (page_labels[0] if page_labels else SERVICE_PAGE_LABEL)
    lines = _lines_records_from_result(result, page)
    return _page_records_from_lines(lines), lines


def _ocr_handoff_target(
    record: Optional[Dict[str, Any]], page: Optional[str], task_type: str, upload_path: str
) -> Tuple[Optional[str], bool]:
    """`(page key, hand-off)` for this upload's accretion, checked before any model runs.

    The OCR hand-off (atrium-digital-convert#4 W4): digital-convert flags a born-digital page
    `needs_ocr`, the pipeline OCRs it, and the ATR ALTO of that page comes here with the record.
    Its key is `page` (the record's own label), or else the ALTO page's `PHYSICAL_IMG_NR` mapped
    through the record's `page_index` (`document_hook.record_page_key`). It must be a page the
    record flags; the write then replaces that page's lines and leaves every other page alone.

    `(None, True)` means "a hand-off record, but nothing attributable": a JSON or text upload
    without `page`, or a multi-page ALTO without it. Nothing is written then, as for any upload
    that cannot be attributed to one page. On a record of this repo's own (or of no known) origin,
    `page` only names the key the rows are written under.
    """
    page = (page or "").strip() or None
    if page is not None:
        if task_type == "document":
            raise HTTPException(
                status_code=422,
                detail="page: applies to a one-page ALTO, JSON or text upload; a document upload has pages of its own.",
            )
        if record is None:
            raise HTTPException(status_code=422, detail="page: names a page of the record; send it as `document_json`.")
        if task_type == "alto":
            count = len(parse_alto_page_labels(upload_path))
            if count > 1:
                raise HTTPException(
                    status_code=422, detail=f"page: one ALTO page per request; this upload has {count} pages."
                )
        if task_type in ("json", "text"):
            count = upload_page_count(task_type, upload_path)
            if count > 1:
                raise HTTPException(
                    status_code=422,
                    detail=f"page: names one page; this {task_type} upload has {count} (form feeds or a paged JSON).",
                )
    if record is None or task_type == "document":
        return page, False
    origin = (record.get("source") or {}).get("origin")
    if resolve_originator(origin) in (None, PROGRAM_NAME):
        return page, False
    flagged = handoff_pages(record)
    if not flagged:
        if page is not None:
            raise HTTPException(
                status_code=422,
                detail=f"page: the record ({origin!r}) flags no page needs_ocr, so none of its pages can be re-acquired.",
            )
        return None, False
    if page is None:
        labels = parse_alto_page_labels(upload_path) if task_type == "alto" else []
        if len(labels) != 1:
            return None, True
        target = record_page_key(record, labels[0])
        if target is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"the ALTO page {labels[0]!r} (PHYSICAL_IMG_NR, a physical position) is no page of the record; "
                    f"send `page`, the record's key for it."
                ),
            )
    else:
        known = [str(row.get("page")) for row in record.get("pages") or [] if isinstance(row, dict)]
        if page not in known:
            raise HTTPException(status_code=422, detail=f"page: the record has no page {page!r}.")
        target = page
    if target not in flagged:
        raise HTTPException(
            status_code=422,
            detail=f"page {target!r} does not ask for OCR (pages[].needs_ocr); the record flags {', '.join(flagged)}.",
        )
    return target, True


def _ocr_engine(task_type: str, upload_path: str) -> str:
    """`pages[].ocr.engine` for a re-acquired page: the engine an ALTO names in its Description
    (`ocr:pero`, `ocr:tesseract`, ...), else the configured origin of the upload's kind."""
    if task_type == "alto":
        named = producer_origin(parse_alto_software(upload_path))
        if named:
            return named
    _limits, _options, configured, by_kind = ingest_settings()
    kind = _TASK_KINDS[task_type]
    return resolve_input_origin(kind, READERS[kind].default_origin, configured=configured, by_kind=by_kind)


@app.get("/", response_model=None)
async def root() -> Union[HTMLResponse, Dict[str, str]]:
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return {"message": "Service running. Frontend not found."}


@app.get(
    "/info",
    response_model=None,
    responses={200: {"model": AltoInfo, "description": "Identity, limits, capabilities."}},
)
async def info() -> Dict[str, Any]:
    return build_info(
        app,
        service=SERVICE,
        limits=LIMITS,
        status="active",
        device=text_manager.device,
        supported_formats=["ALTO XML (.xml)", "Plain Text (.txt)", "Generic JSON (.json)"]
        + [
            f"{spec.label} ({', '.join(spec.extensions)})"
            for kind, spec in READERS.items()
            if kind not in ("alto", "txt", "json")
        ],
        quality_categories=["Clear", "Noisy", "Trash", "Non-text", "Empty"],
        line_fields=[
            "line_num",
            "text",
            "lang",
            "lang_score",
            "perplexity",
            "garbage_density",
            "sym_count",
            "upper_count",
            "repeated_count",
            "ldl_fuses",
            "gibberish",
            "word_weird",
            "quality_score",
            "category",
        ],
    )


@app.post(
    "/process",
    response_model=None,
    responses={
        200: {"model": ProcessResponse, "description": "The classified lines, and the record when one was sent."},
        **error_responses(413, 415, 501, 503),
    },
)
async def process_document(
    file: UploadFile = File(
        ...,
        description=(
            "The page or document: ALTO XML, plain text, generic JSON, or any other text-bearing file "
            "(PDF, DOCX, ODT, XLSX, PPTX, EPUB, RTF, HTML/hOCR, PAGE XML, TEI, Markdown, CSV/TSV, ...)."
        ),
    ),
    task_type: TaskType = Form(
        "auto",
        description=(
            "How to read the upload: `alto`, `text`, `json`, `document` (any other supported kind), or `auto` "
            "(`.txt` → text, `.json` → json, anything else decided from the bytes)."
        ),
    ),
    document_json: UploadFile = File(
        None, description=_RECORD_PART_HELP, json_schema_extra={"contentMediaType": "application/json"}
    ),
    document_record: UploadFile = File(
        None,
        deprecated=True,
        description=(
            "Deprecated name of `document_json`; its record comes back as `document_json_out`. Send one of "
            "the two, not both."
        ),
        json_schema_extra={"contentMediaType": "application/json"},
    ),
    page: Optional[str] = Form(
        None,
        description=(
            "Optional: the record's page key this one-page upload is written under. With a born-digital record "
            "(digital-convert's), it names the page flagged `needs_ocr` that this ALTO re-acquires (the OCR "
            "hand-off): that page's lines are replaced, every other page is left as it is. Without it, an ALTO "
            "page's `PHYSICAL_IMG_NR` is mapped through the record's `page_index`. A page the record does not "
            "have, or does not flag, is refused (422), as is a multi-page ALTO, text or JSON upload, or a document "
            "upload, with `page`."
        ),
    ),
    reading_order: ReadingOrder = Form(
        "layoutreader",
        description=(
            "ALTO uploads only: `layoutreader` reorders the lines with LayoutReader (LayoutLMv3, whose outputs "
            "carry CC BY-NC-SA 4.0), as the batch `--method layoutreader`; `alto` keeps the ALTO's own order and "
            "runs no LayoutReader, as `--method alto-tools`."
        ),
    ),
    include_record: bool = Form(
        False,
        description=(
            "Return a record even when none was sent: `document_json` then holds a new record with this upload's "
            "`source`, `pages` and `lines`, as the batch pipeline writes one into `[DOCUMENT] JSON_DIR` without a "
            "baseline. Ignored when a record is sent (it always comes back)."
        ),
    ),
) -> JSONResponse:
    """
    Upload an ALTO XML, plain-text or generic JSON file — or (#31) any other text-bearing
    document: PDF, DOCX, ODT, XLSX/ODS, PPTX/ODP, EPUB, RTF, HTML/hOCR, PAGE XML, TEI,
    Markdown, CSV/TSV, JSON Lines, ABBYY/DjVu XML, Tesseract TSV, SRT/VTT, EML/MBOX,
    gzip/bz2/xz-compressed files and ZIP bundles of page files. `task_type="auto"`
    keeps `.txt` → text and `.json` → json; `.xml` and every other extension are
    decided from the bytes (an uncompressed ALTO root → alto, anything readable →
    document). Document results carry `page`/`page_label` per line and a `pages`
    summary; so do text uploads with form feeds and JSON uploads of several pages, paged as
    the batch pipeline pages them. The readers use the config's [TEXT_INGEST] settings.

    `reading_order=alto` keeps an ALTO's own line order (no LayoutReader), and
    `include_record=true` returns a new record when none was sent (atrium-project#72, the
    service–CLI alignment).

    Errors name the reader's code at the start of `detail` and, but for a named limit, as
    `cause`: 415 `unsupported_media_type` for a file of a kind this service does not read
    (binary_content, legacy_office_unsupported, image_needs_ocr, archive_unsupported), with
    the readable extensions as `accepted`; 413 `limit_exceeded` for a file over a size or
    count limit (too_large, zip_limits_exceeded; 422 for the reader's time limit, timeout);
    422 for a supported kind that cannot be read (corrupt, encrypted, malformed, no_text,
    ...); 501 when this deployment lacks the reader's optional dependency
    (dependency_missing). A record part that cannot be opened is 422 `invalid_record`.
    Every response carries `limits_applied` (atrium-project#53).

    The record goes in `document_json` and comes back in `document_json`, as with every
    other ATRIUM service (atrium-project#32 round 2). The earlier names — `document_record`
    in, `document_json_out` out — still work and are deprecated in the spec.

    The OCR hand-off (atrium-digital-convert#4 W4). With digital-convert's record of a
    born-digital PDF and the ATR ALTO of one page it flagged `needs_ocr`, the classified lines
    replace that page's lines in the record, its `pages[]` row gets the page's quality and
    `ocr.engine`, and every other page stays as the converter wrote it (`ocr_handoff` in the
    response). The page is `page`, or the ALTO's `PHYSICAL_IMG_NR` mapped through the record's
    `page_index`; one that the record lacks or does not flag is a 422, checked before any model
    runs. The record's `source` stays the original's.

    Returns a list of classified lines.  Each entry carries:

      line_num        (int)   – 1-based position after layout reordering
      text            (str)   – cleaned text with split-word merges applied
      lang            (str)   – ISO language code predicted by FastText
      lang_score      (float) – FastText confidence [0, 1]
      perplexity      (float) – Qwen2.5-0.5B perplexity; 0 for pre-filtered lines
      garbage_density (float) – ratio of non-alphanumeric noise characters
      sym_count       (int)   – tokens with strange/unexpected symbols
      upper_count     (int)   – tokens with mid-word uppercase artefacts
      repeated_count  (int)   – tokens with non-standard char repetition (>=40%)
      ldl_fuses       (int)   – tokens with letter-digit-letter fusions
      gibberish       (int)   – tokens lacking vowels or highly irregular ratios
      word_weird      (float) – mean per-word weirdness score [0, 1]
      quality_score   (float) – composite continuous quality score [0, 1]
      category        (str)   – Clear | Noisy | Trash | Non-text | Empty
                                Assigned dynamically using the unified penalty system.
    """
    if not file.filename:
        raise HTTPException(status_code=422, detail="Filename is missing from the upload.")
    if not file.content_type:
        raise HTTPException(status_code=422, detail="Content-Type is missing from the upload.")

    filename = file.filename.lower()
    # (atrium-project#10 D2) The record's key comes from the hub's one derivation,
    # on the ORIGINAL-CASE filename. `Path(filename).stem` on the lower-cased name
    # yielded `ctx000000001.alto` for this repo's own documented convention
    # `CTX000000001.alto.xml` — down-cased AND still carrying `.alto`, because
    # `stem` strips only the LAST extension — while page_split.py keys the same
    # document `CTX000000001`. Since DocumentRecord.__init__ sets `_data["doc_id"]`
    # unconditionally, uploading a real baseline re-keyed the accreted output.
    # Nothing else in the pipeline lower-cases, so neither does this.
    doc_id = canonical_doc_id(file.filename)
    if not doc_id:
        raise HTTPException(status_code=422, detail="Filename has no usable document id.")

    if task_type == "auto":
        if filename.endswith(".txt"):
            task_type = "text"
        elif filename.endswith(".json"):
            task_type = "json"
        else:
            # (#31) .xml and every other extension: decided from the uploaded bytes
            # below (an ALTO root stays on the ALTO path; anything else readable is a
            # "document"); an unsupported file is a 415 (a 400 before atrium-project#32
            # round 2). `sniff` is internal: the TaskType a client may send excludes it.
            task_type = "sniff"

    _refuse_if_draining()

    # The call's paradata. config_dir: this repo keeps para_config.txt under setup/, and the
    # logger looks for it in config_dir — at the default "." it found nothing, so every API
    # record said tool_version "unknown" with no components (atrium-project#53 D9).
    # paradata_dir=None (atrium-project#71): nothing is written to the container's working
    # directory, which used to collect one paradata/ file per request; the run goes back as the
    # response's `paradata`, and its run_id / run_uuid stamp the record.
    para_logger = ParadataLogger(
        config={
            "endpoint": "/process",
            "task_type": task_type,
            "reading_order": reading_order,
            "include_record": include_record,
        },
        program=PROGRAM_NAME,
        paradata_dir=None,
        config_dir=str(Path(PARA_CONFIG_PATH).parent),
    )

    # Read in bounded chunks and refused once over MAX_UPLOAD_MB (413 limit_exceeded), rather
    # than copied to disk whole and measured afterwards (atrium-project#53).
    upload_mb = MAX_UPLOAD.get()
    content = await read_upload_bounded(file, upload_mb, "File")
    # What the call read, by content (the action's `object`), taken before `content` is dropped.
    upload_entity = atrium_rocrate.file_entity(file.filename, content, media_type=file.content_type)

    # The record, read before any model runs so a record that cannot be opened is refused
    # up front (422 `invalid_record`, atrium-project#32 round 2); it used to reach the
    # catch-all below as a 500 "Processing failed" after the whole upload was classified.
    # Bounded like the file (atrium-project#53). An empty part counts as none. It is
    # accepted under two names: `document_json` (every ATRIUM service's) comes back as
    # `document_json`; the deprecated `document_record` comes back as `document_json_out`,
    # as it always did.
    record_bytes: Optional[bytes] = None
    record: Optional[Dict[str, Any]] = None
    record_key = "document_json"
    for part, label, key in (
        (document_json, "document_json", "document_json"),
        (document_record, "document_record", "document_json_out"),
    ):
        if part is None:
            continue
        raw = await read_upload_bounded(part, upload_mb, label)
        parsed = parse_record_part(raw, label)
        if parsed is None:
            continue
        if record_bytes is not None:
            raise HTTPException(
                status_code=422,
                detail="Send the record as `document_json` or as `document_record` (deprecated), not both.",
            )
        record_bytes, record_key, record = raw, key, parsed

    with tempfile.NamedTemporaryFile(delete=False, suffix=_temp_suffix(file.filename)) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    del content

    # Every limit that shapes this result without refusing it (atrium-project#53): passed
    # down explicitly, returned as `limits_applied` and recorded in the paradata.
    notes = LimitNotes()
    # The licensed reader components this upload needed (pypdfium2 for a PDF,
    # charset_normalizer for a detected encoding), logged like the CLI logs them, so an API
    # run records the same licences (atrium-project#6, #72).
    components: Set[str] = set()

    try:
        if task_type == "sniff":
            try:
                kind = await asyncio.to_thread(sniff_kind, tmp_path, ingest_settings()[0])
            except IngestError as exc:
                raise _ingest_http_error(exc, sniffing=True) from exc
            # A compressed ALTO (.alto.xml.gz) is decompressed by the document reader;
            # the ALTO path parses the uploaded bytes directly.
            task_type = "alto" if kind == "alto" and not compression_of(tmp_path) else "document"

        # (W4) Which page of the record this upload is written under, and whether it re-acquires a
        # page a born-digital record flags for OCR: decided here, so a wrong page costs no inference.
        target_page, handoff = _ocr_handoff_target(record, page, task_type, tmp_path)

        # Execute text inference, off the event loop (issue #55). These are synchronous
        # torch calls (LayoutReader + Qwen perplexity + fastText); run inline in an
        # `async def` they blocked the ONLY event loop, so uvicorn's SIGTERM handler —
        # an event-loop callback — could not run until the whole document finished, which
        # made --timeout-graceful-shutdown meaningless here.
        if task_type == "alto":
            # An upload that is not ALTO (broken XML, no <Page>) is the caller's input: 422, as a
            # JSON upload that does not parse. It used to come back as 200 with no lines, which
            # reads as a blank page.
            try:
                result = await asyncio.to_thread(
                    text_manager.process_alto,
                    tmp_path,
                    notes=notes,
                    components=components,
                    reading_order=reading_order,
                )
            except AltoUnreadable as exc:
                raise AtriumHTTPError(
                    422, f"malformed: the ALTO upload cannot be read: {exc}.", cause="malformed"
                ) from exc
        elif task_type == "json":
            # A JSON upload that does not decode or parse is the caller's input, not our
            # failure: a 422 with the reader codes the other paths use (it was the catch-all
            # 500 before atrium-project#32 round 2).
            try:
                result = await asyncio.to_thread(text_manager.process_json, tmp_path, notes=notes)
            except UnicodeDecodeError as exc:
                raise AtriumHTTPError(
                    422, f"decode_failed: the JSON upload is not UTF-8 ({exc}).", cause="decode_failed"
                ) from exc
            except json.JSONDecodeError as exc:
                raise AtriumHTTPError(
                    422, f"malformed: the JSON upload does not parse ({exc}).", cause="malformed"
                ) from exc
        elif task_type == "document":
            try:
                result = await asyncio.to_thread(
                    text_manager.process_document, tmp_path, notes=notes, components=components
                )
            except IngestError as exc:
                raise _ingest_http_error(exc) from exc
        else:
            try:
                result = await asyncio.to_thread(
                    text_manager.process_text_file, tmp_path, notes=notes, components=components
                )
            except IngestError as exc:
                raise _ingest_http_error(exc) from exc

        result["filename"] = file.filename
        result["limits_applied"] = notes.as_list()
        components |= _model_components(result.get("cleaned_lines"))

        # --- Paradata Pair Accretion Hook ---
        # With no record sent, `include_record` asks for a new one (atrium-project#72): the hook
        # writes it from nothing (rule 3), as the batch path does without a baseline.
        if record_bytes is not None or include_record:
            with tempfile.TemporaryDirectory() as doc_tmp_dir:
                baseline_path = os.path.join(doc_tmp_dir, f"{doc_id}.document.json")

                # Save the uploaded baseline JSON as it was sent (read and opened above).
                if record_bytes is not None:
                    with open(baseline_path, "wb") as bf:
                        bf.write(record_bytes)

                # (#10 J1) Real lines + real per-page rows, both derived from this
                # request — see _accretion_records for what was fabricated before.
                if handoff and target_page is None:
                    # A born-digital record that flags pages, and an upload no page can be named
                    # for: nothing is written (the record comes back as it was sent).
                    logger.warning(
                        "%s: the record flags pages %s for OCR, but this %s upload names none of them; send "
                        "`page`. Not writing it into the record.",
                        doc_id,
                        handoff_pages(record),
                        task_type,
                    )
                    page_metrics, lines_metrics = [], []
                else:
                    page_metrics, lines_metrics = _accretion_records(
                        task_type, tmp_path, result, page=target_page, record=record
                    )
                ocr_pages = None
                upload_source = _upload_source(task_type, result, file.filename, upload_entity["sha256"])
                if handoff and target_page is not None:
                    # The OCR hand-off: the page's quality and engine, its lines replaced; the
                    # record's `source` is the original's, not this ALTO's.
                    page_metrics = [row | {"ocr": {"engine": _ocr_engine(task_type, tmp_path)}} for row in page_metrics]
                    page_metrics = page_metrics or [
                        {"page": target_page, "ocr": {"engine": _ocr_engine(task_type, tmp_path)}}
                    ]
                    ocr_pages, upload_source = [target_page], None
                    result["ocr_handoff"] = {
                        "page": target_page,
                        "lines_replaced": sum(
                            1
                            for row in (record or {}).get("lines") or []
                            if isinstance(row, dict) and str(row.get("page")) == target_page
                        ),
                        "lines_written": len(lines_metrics),
                    }

                # Write the block using the repo-local hook. `pages`/`lines` are
                # field-split with page-classification/nlp-enrich (BLOCK_FIELD_OWNERS),
                # so this must merge — set_blocks would erase their co-owned fields
                # (category/category_confidence, teitok_surface, lemma/upos/feats, ...)
                # on any document that already carries a baseline record.
                record_path = write_document_block(
                    document_json_dir=doc_tmp_dir,
                    doc_id=doc_id,
                    run_id=para_logger.run_id,
                    paradata_ref=para_logger.paradata_ref,  # the run_uuid: the service writes no file
                    source=upload_source,
                    merge_blocks={"pages": page_metrics, "lines": lines_metrics},
                    run_uuid=para_logger.run_uuid,
                    ocr_pages=ocr_pages,
                )

                # Read back the record from the path the hook wrote (atrium-project#68), not
                # from a name re-derived here. With nothing written (a foreign-origin record,
                # say) the uploaded baseline goes back unchanged; with no baseline either (a
                # born-digital upload and `include_record`), there is no record to return.
                # utf-8-sig: a baseline returned unchanged may start with the BOM
                # parse_record_part accepted.
                if record_path or record_bytes is not None:
                    with open(record_path or baseline_path, "r", encoding="utf-8-sig") as bf:
                        result[record_key] = json.load(bf)
        # ------------------------------------

        for name in sorted(components):
            para_logger.log_component(name)
        para_logger.note_limits(notes)
        para_logger.log_document_success()
        para_logger.finalize()
        result["paradata"] = _run_action(
            para_logger, upload_entity, result, record_key, doc_id, record_sent=record_bytes is not None
        )
        return JSONResponse(content=result)

    except (HTTPException, LimitExceeded):
        raise
    except Exception as exc:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Processing failed: {exc}") from exc

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


_SCORE_RECORD_HELP = (
    "The ATRIUM Document JSON whose lines to score (required). Any origin: a born-digital record "
    "(digital-convert's) gets the scoring fields only, an OCR record is re-scored. One that cannot be "
    "opened, or has no `doc_id`, is refused (422 `invalid_record`)."
)


@app.post(
    "/score_record",
    response_model=None,
    responses={
        200: {"model": ScoreRecordResponse, "description": "The scored lines and the record."},
        **error_responses(413, 503),
    },
)
async def score_record(
    document_json: UploadFile = File(
        ..., description=_SCORE_RECORD_HELP, json_schema_extra={"contentMediaType": "application/json"}
    ),
    pages: Optional[str] = Form(
        None,
        description=(
            "Optional: score only these pages, by the record's own page keys, comma-separated (e.g. `i,ii,3`). "
            "Empty (the default): every page. A key the record does not have is refused (422)."
        ),
    ),
) -> JSONResponse:
    """Score a record's lines with the common line-quality model (atrium-digital-convert#4 W3).

    The lines are read from the record (`lines[].text`, in record order) and scored exactly as
    they are — not re-read, split or reordered — so each answer maps back onto its row. A line
    carrying digital-convert's decode verdict (`Garbage`, `Inverted`) is left as it is, and a
    line without text is not scored. The record gets `lines[].categ/quality_score/lang` and
    `pages[].quality_score/quality_band` on the rows it already has, through the scoring-only
    write path (document_hook.write_scores): never `text`, never a new row, never `source`. On a
    born-digital record that is the shared module's scoring co-contribution (SCORING_FIELDS), so
    the record stays digital-convert's.

    `MAX_PAGES` bounds the pages scored and `MAX_LINES_PER_PAGE` the lines of one page (413
    `limit_exceeded`); `MAX_UPLOAD_MB` the record part.
    """
    _refuse_if_draining()
    para_logger = ParadataLogger(
        config={"endpoint": "/score_record"},
        program=PROGRAM_NAME,
        paradata_dir=None,
        config_dir=str(Path(PARA_CONFIG_PATH).parent),
    )
    raw = await read_upload_bounded(document_json, MAX_UPLOAD.get(), "document_json")
    record = parse_record_part(raw, "document_json")
    if record is None:
        raise AtriumHTTPError(
            422, "The document_json part is empty: /score_record scores a record.", reason="invalid_record"
        )
    doc_id = str(record.get("doc_id") or "").strip()
    if not doc_id:
        raise AtriumHTTPError(422, "The record has no doc_id.", reason="invalid_record")

    try:
        selection = select_lines(record, parse_pages(pages))
    except UnknownPages as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    by_page = selection.by_page

    # The limits as /info reports them: the environment and the config file, not the default alone.
    max_pages, max_lines = LIMITS.get("max_pages"), LIMITS.get("max_lines_per_page")
    MAX_PAGES.check(
        len(by_page), value=max_pages, detail=f"{len(by_page)} pages to score; the limit is {max_pages} (MAX_PAGES)."
    )
    for page, rows in by_page.items():
        MAX_LINES_PER_PAGE.check(
            len(rows),
            value=max_lines,
            detail=f"page {page!r} has {len(rows)} lines to score; the limit is {max_lines} (MAX_LINES_PER_PAGE).",
        )

    notes = LimitNotes()
    try:
        # Off the event loop (issue #55): synchronous perplexity + fastText.
        scored = await asyncio.to_thread(
            score_record_lines,
            record,
            raw,
            selection,
            lambda texts: text_manager.classify_line_texts(texts, notes),
            run_id=para_logger.run_id,
            paradata_ref=para_logger.paradata_ref,
            run_uuid=para_logger.run_uuid,
        )
        cleaned = scored["cleaned_lines"]
        result: Dict[str, Any] = {
            "type": scored["type"],
            "doc_id": doc_id,
            "cleaned_lines": cleaned,
            "pages": scored["pages"],
            "limits_applied": notes.as_list(),
            "document_json": scored["document_json"],
        }
        for name in sorted(_model_components(cleaned)):
            para_logger.log_component(name)
        para_logger.note_limits(notes)
        para_logger.log_document_success()
        para_logger.finalize()
        upload = atrium_rocrate.file_entity(f"{file_key(doc_id)}.document.json", raw, media_type="application/json")
        result["paradata"] = _run_action(para_logger, upload, result, "document_json", doc_id)
        return JSONResponse(content=result)
    except (HTTPException, LimitExceeded):
        raise
    except Exception as exc:
        logger.exception("score_record failed")
        raise HTTPException(status_code=500, detail=f"Scoring failed: {exc}") from exc


#: The reader kind of each non-document task type, for its default origin and media type.
_TASK_KINDS = {"alto": "alto", "json": "json", "text": "txt"}


def _upload_source(task_type: str, result: Dict[str, Any], filename: str, sha256: str) -> Optional[Dict[str, Any]]:
    """`source` as this call read it, for the record (atrium-project#71), or None.

    This service reads the source, so it records the origin, like page_split.py on the batch
    path; a record that already has a `source` (an AMČR seed, an earlier run) keeps every value
    it has, since the first writer wins, and only gains what it lacks. The origin is resolved
    from the same [DOCUMENT] keys as the batch path (the DOCUMENT_SOURCE_ORIGIN env var is a
    batch-run knob, not read here). An origin another tool originates (a born-digital upload)
    is not this service's to write, and neither is the rest of `source` then: None.
    """
    if task_type == "document":
        origin, media_type = result.get("origin") or "", result.get("media_type") or ""
    else:
        _limits, _options, configured, by_kind = ingest_settings()
        reader = READERS[_TASK_KINDS[task_type]]
        origin = resolve_input_origin(
            _TASK_KINDS[task_type], reader.default_origin, configured=configured, by_kind=by_kind
        )
        media_type = reader.media_type
    if resolve_originator(origin) not in (None, PROGRAM_NAME):
        return None
    return {"sha256": sha256, "filename": filename, "media_type": media_type or None, "origin": origin or None}


def _run_action(
    run: ParadataLogger,
    upload: Dict[str, Any],
    result: Dict[str, Any],
    record_key: str,
    doc_id: str,
    record_sent: bool = True,
) -> Dict[str, Any]:
    """The call's CreateAction (atrium-project#71): what it read and what it wrote.

    `object` is the upload and, when one was sent, the record; `result` is the record's blocks
    this call stamped and the classified lines it answers with. A record made from nothing
    (`include_record`) is an output only.
    """
    record = result.get(record_key)
    inputs = [upload]
    if record is not None and record_sent:
        inputs.append(atrium_rocrate.record_entity(str(record.get("doc_id") or doc_id)))
    outputs = atrium_rocrate.block_entities(atrium_rocrate.blocks_written(record, run.run_uuid))
    lines = json.dumps(result.get("cleaned_lines") or [], ensure_ascii=False, sort_keys=True).encode("utf-8")
    outputs.append(atrium_rocrate.file_entity("cleaned_lines.json", lines, media_type="application/json"))
    return atrium_rocrate.create_action(run.record, inputs=inputs, outputs=outputs)


# @app.post("/process")
# async def process_document(
#     file: UploadFile = File(...),
#     task_type: str = Form("auto"),
# ) -> JSONResponse:
#
#     # §4.4: missing upload metadata is a client error (422), not a server 500.
#     if not file.filename:
#         raise HTTPException(status_code=422, detail="Filename is missing from the upload.")
#     if not file.content_type:
#         raise HTTPException(status_code=422, detail="Content-Type is missing from the upload.")
#
#     filename = file.filename.lower()
#
#     if task_type == "auto":
#         if filename.endswith(".xml"):
#             task_type = "alto"
#         elif filename.endswith(".txt"):
#             task_type = "text"
#         elif filename.endswith(".json"):
#             task_type = "json"
#         else:
#             raise HTTPException(
#                 status_code=400,
#                 detail="Cannot auto-detect file type. Set task_type='alto', 'text', or 'json'.",
#             )
#
#     with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as tmp:
#         shutil.copyfileobj(file.file, tmp)
#         tmp_path = tmp.name
#
#     try:
#         # §4.3/§4.4: enforce the canonical upload limit (413).
#         if os.path.getsize(tmp_path) > MAX_UPLOAD_BYTES:
#             raise HTTPException(status_code=413, detail=f"File too large. Maximum size is {MAX_UPLOAD_MB} MB.")
#
#         if task_type == "alto":
#             result = text_manager.process_alto(tmp_path)
#         elif task_type == "json":
#             result = text_manager.process_json(tmp_path)
#         else:
#             result = text_manager.process_text_file(tmp_path)
#
#         result["filename"] = file.filename
#         return JSONResponse(content=result)
#
#     except HTTPException:
#         # Never re-wrap an intentional 4xx (413/422) as a 500.
#         raise
#     except Exception as exc:
#         import traceback
#
#         traceback.print_exc()
#         raise HTTPException(status_code=500, detail=f"Processing failed: {exc}") from exc
#
#     finally:
#         if os.path.exists(tmp_path):
#             os.remove(tmp_path)


if __name__ == "__main__":
    import logging

    import uvicorn

    # (12-factor XI) Logs are an event stream: emit to stdout and let the
    # supervisor route them. The library modules only getLogger(); this is the
    # one place allowed to configure handlers.
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    # (12-factor VII) The service exports itself by binding a port, and which
    # port is configuration. These were hardcoded, which also meant `reload=True`
    # — a development convenience that watches the filesystem and respawns —
    # was what docker-compose ran as the `api` profile entrypoint.
    uvicorn.run(
        "text_api:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("RELOAD", "false").strip().lower() in ("true", "1", "yes", "on"),
        # (12-factor IX, issue #55) Disposability: bound how long uvicorn waits for
        # in-flight requests before closing their connections, so a SIGTERM leads to a
        # prompt, predictable exit instead of an open-ended wait. serve_lifecycle()
        # adds its own drain on top of this; docs/k8s_deployment.md in the hub carries
        # the full budget these two have to fit inside.
        timeout_graceful_shutdown=int(os.getenv("GRACEFUL_SHUTDOWN_S", "20")),
    )
