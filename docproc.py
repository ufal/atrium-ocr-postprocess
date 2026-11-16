"""docproc.py — the docproc reader (`atr/docproc-json`, atrium-project#84).

Since ARÚP's output contract of 12 November 2026 the record is built from docproc, the layout format the
ATR writes for scanned pages and digital-convert writes for born-digital ones (#84 §1, D20). This module
reads a docproc document into the pages and lines the record builder (`build_record.py`) writes. It
reads both the ATR's schema as delivered (#84 comment 1) and digital-convert's, the same schema with
asks 3 and 13 applied (block `lines[]`, the page verdict).

* **Pages** by `pageNumber` (the record's `page_index`); the page's size and unit become its canvas.
* **Blocks** in `readingOrder` (dense per page). A block's `lines[]`, when the producer gives them, are
  its lines, each with its own box (`bboxPrecision`, else `measured` when it has a box). Otherwise the
  block's `content` split on line breaks gives the lines, each with the block's box (`derived`); a block
  with no line break is one line marked `unit: block`, a whole block scored as one unit and kept out of
  the calibrated statistics (D18 rule 5). Until the ATR keeps a break per visual line (ask 7), a heading
  of one line cannot be told from a paragraph that lost its breaks, so both are marked.
* **Tables**: one line per `cellGrid` row (its cells joined by a tab), with the table's box (`derived`);
  without a grid, its `content` as above.
* **Image-class blocks** (`image`, `figure`, `chart`, `seal`) give no line unless they carry text.
* **Failures fail the job** (D13): a block flagged `all_engines_failed` (its text could not be read), a
  page of the original that is neither in `pages[]` nor routed `empty` (H15), and a `coordinateUnit`
  other than `pt` or `px` (D28).
* **The page's blocks** are kept too, each with its box, reading order and lines, so that the merge of a
  mixed document (`docproc_merge.py`, H6) can link digital-convert's lines to the ATR's blocks.

The page verdict, `needsAtr`, the ATR's echo of the forced pages (`forced`, `forced_reason` in routing,
D27) and the decode flags on lines are read, never copied into the record (D27, D18 rule 2): the builder
decides what to do with them. The decode flags are digital-convert's verdicts on the characters
(`decode:garbled`, `decode:inverted`), a system apart from line quality; an older docproc's spelling
(`Garbage`, `Inverted`) is read as the new one (`atrium_vocab.LEGACY_DECODE_FLAGS`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from atrium_vocab import LEGACY_DECODE_FLAGS as _LEGACY_DECODE_FLAGS
except ImportError:  # no registry vendored, or one from before 2026-11-16 (atrium-project#84)
    _LEGACY_DECODE_FLAGS = {"Garbage": "decode:garbled", "Inverted": "decode:inverted"}

#: The docproc schema major this reader reads.
SUPPORTED_MAJOR = 1

#: Block types that carry an image, not text (the schema's image class).
IMAGE_TYPES = frozenset({"image", "figure", "chart", "seal"})

#: The ATR's producer id (`pipeline`), with or without its cloud-fallback variant.
ATR_PIPELINE = "pipeline-e2e"

#: The units a page's boxes may be in (D28): `pt` (1/72 inch x userUnit), `px` only where no PDF size is known.
COORDINATE_UNITS = frozenset({"pt", "px"})

#: `sourceFormat` values of a born-digital original (digital-convert's): PDF, the office documents and
#: spreadsheets, and the text-only inputs digital-convert extracts as they are (TXT, CSV, TSV, JSON;
#: atrium-digital-convert#9, the hub's Q9).
DIGITAL_FORMATS = frozenset(
    {"pdf-digital", "docx", "pptx", "odt", "ods", "xlsx", "rtf", "doc", "xls", "txt", "csv", "tsv", "json"}
)


class DocprocError(ValueError):
    """A docproc the builder cannot build a complete record from; `reason` is a stable code."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(f"{reason}: {message}")


@dataclass
class ReadLine:
    """One line of a page, in reading order."""

    text: str
    block: str
    bbox: Optional[List[float]] = None
    bbox_precision: str = "absent"
    language: Optional[str] = None
    flags: List[str] = field(default_factory=list)
    #: `block` when the line is a whole block scored as one unit (D18 rule 5); None for a visual line.
    unit: Optional[str] = None
    #: Where this line's text came from (D25) when it differs per line (a merged mixed document,
    #: `docproc_merge.py`); None takes the document's `text_source`.
    text_source: Optional[str] = None


@dataclass
class ReadBlock:
    """One block of a page, as the merge needs it: its id, box, reading order and the lines it gave."""

    id: str
    type: Optional[str]
    bbox: Optional[List[float]]
    reading_order: int
    lines: List[ReadLine] = field(default_factory=list)


@dataclass
class ReadPage:
    page_index: int
    width: Optional[float] = None
    height: Optional[float] = None
    unit: str = "pt"
    user_unit: Optional[float] = None
    #: For an image docproc wraps into a PDF: image pixels per unit (`pixelRatio`, D28).
    pixel_ratio: Optional[float] = None
    lines: List[ReadLine] = field(default_factory=list)
    #: The page's blocks in reading order (their lines are the ones in `lines`).
    blocks: List[ReadBlock] = field(default_factory=list)
    needs_atr: bool = False
    text_layer: Optional[str] = None


@dataclass
class ReadDocument:
    """A docproc document as the builder needs it."""

    version: str
    source_format: Optional[str]
    producer: Optional[str]
    language: Optional[str]
    page_count: int
    #: `page_index` → its page, for every page in `pages[]`.
    pages: Dict[int, ReadPage]
    #: `page_index` → its route, for every page in `metadata.routing.pages[]`.
    routes: Dict[int, str]
    #: `page_index` → `forced_reason` for every routing entry with `forced: true`: the pages the ATR was
    #: forced to read, as it echoes them (D27, ask 5). Empty when the producer echoes none.
    forced: Dict[int, Optional[str]] = field(default_factory=dict)

    @property
    def text_source(self) -> str:
        """Where the lines' text came from (D25): the ATR's docproc is `atr`; digital-convert's, of a
        born-digital format, is `text-layer` — the file's own text, a text-only input's included."""
        if (self.producer or "").startswith(ATR_PIPELINE):
            return "atr"
        if self.source_format in DIGITAL_FORMATS:
            return "text-layer"
        return "atr"

    @property
    def origin(self) -> str:
        """The record's descriptive `source.origin` (#84 §1). For a merged mixed document, whose lines
        carry their own source, `digital` when any line kept the text layer, else `atr`."""
        sources = {line.text_source for page in self.pages.values() for line in page.lines if line.text_source}
        if sources:
            return "digital" if "text-layer" in sources else "atr"
        return "digital" if self.text_source == "text-layer" else "atr"


def _box(value: Any) -> Optional[List[float]]:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):
            return None
    return None


def _positive(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _language(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and len(value) == 2 and value.isalpha() else None


def _block_lines(block: Dict[str, Any]) -> List[ReadLine]:
    """The lines of one block (see the module docstring)."""
    block_id = str(block.get("id") or "")
    flags = [str(f) for f in block.get("flags") or []]
    if "all_engines_failed" in flags:
        raise DocprocError("all_engines_failed", f"block {block_id}: the producer could not read its text")
    box = _box(block.get("bbox"))
    language = _language(block.get("language"))
    precision = "derived" if box else "absent"

    own = block.get("lines")
    if isinstance(own, list) and own:
        out = []
        for row in own:
            if not isinstance(row, dict) or not str(row.get("text") or "").strip():
                continue
            line_box = _box(row.get("bbox"))
            stated = row.get("bboxPrecision")
            if line_box is None:
                line_precision = "absent"
            elif stated in ("measured", "derived"):
                line_precision = stated
            else:
                line_precision = "measured"
            out.append(
                ReadLine(
                    text=str(row["text"]),
                    block=block_id,
                    bbox=line_box,
                    bbox_precision=line_precision,
                    language=_language(row.get("language")) or language,
                    flags=[_LEGACY_DECODE_FLAGS.get(str(f), str(f)) for f in row.get("flags") or []],
                )
            )
        return out

    if block.get("type") == "table" and isinstance(block.get("cellGrid"), dict):
        texts = [
            "\t".join(str(cell.get("text") or "") for cell in row.get("cells") or [] if isinstance(cell, dict))
            for row in block["cellGrid"].get("rows") or []
            if isinstance(row, dict)
        ]
    else:
        content = block.get("content")
        if not isinstance(content, str) or not content.strip():
            return []  # an image-class block, or an empty one: no line
        texts = content.split("\n")
    rows = [text for text in texts if text.strip()]
    # Rule 5: content with no line break at all is the whole block as one unit.
    unit = "block" if len(texts) == 1 and rows else None
    return [
        ReadLine(text=text, block=block_id, bbox=box, bbox_precision=precision, language=language, unit=unit)
        for text in rows
    ]


def read_docproc(data: Dict[str, Any]) -> ReadDocument:
    """Read a docproc document (see the module docstring). Raises `DocprocError` for a docproc of
    another major, a block whose text could not be read, or a page that is neither listed nor routed
    `empty` (H15)."""
    if not isinstance(data, dict):
        raise DocprocError("invalid_docproc", "a docproc document is a JSON object")
    version = str(data.get("version") or "")
    try:
        major = int(version.split(".")[0])
    except ValueError:
        raise DocprocError("invalid_docproc", f"version {version!r} is not MAJOR.MINOR") from None
    if major != SUPPORTED_MAJOR:
        raise DocprocError("invalid_docproc", f"docproc {version}: this reader reads {SUPPORTED_MAJOR}.x")

    pages: Dict[int, ReadPage] = {}
    for raw in data.get("pages") or []:
        if not isinstance(raw, dict) or not isinstance(raw.get("pageNumber"), int):
            raise DocprocError("invalid_docproc", "a page has no pageNumber")
        index = raw["pageNumber"]
        if index in pages:
            raise DocprocError("invalid_docproc", f"page {index} is listed twice")
        unit = str(raw.get("coordinateUnit") or "pt")
        if unit not in COORDINATE_UNITS:
            raise DocprocError("invalid_docproc", f"page {index}: coordinateUnit {unit!r} is not pt or px (D28)")
        page = ReadPage(
            page_index=index,
            width=_positive(raw.get("width")),
            height=_positive(raw.get("height")),
            unit=unit,
            user_unit=_positive(raw.get("userUnit")),
            pixel_ratio=_positive(raw.get("pixelRatio")),
            needs_atr=bool(raw.get("needsAtr")),
            text_layer=raw.get("textLayer"),
        )
        blocks = [b for b in raw.get("blocks") or [] if isinstance(b, dict)]
        for block in sorted(blocks, key=lambda b: int(b.get("readingOrder", 0))):
            lines = _block_lines(block)
            page.blocks.append(
                ReadBlock(
                    id=str(block.get("id") or ""),
                    type=block.get("type"),
                    bbox=_box(block.get("bbox")),
                    reading_order=int(block.get("readingOrder", 0)),
                    lines=lines,
                )
            )
            page.lines.extend(lines)
        pages[index] = page

    routes: Dict[int, str] = {}
    forced: Dict[int, Optional[str]] = {}
    routing = ((data.get("metadata") or {}).get("routing") or {}).get("pages") or []
    for row in routing:
        if isinstance(row, dict) and isinstance(row.get("page"), int):
            routes[row["page"]] = str(row.get("route") or "")
            if row.get("forced") is True:
                reason = row.get("forced_reason")
                forced[row["page"]] = str(reason) if reason else None
    page_count = max([*pages, *routes, 0])
    missing = [n for n in range(1, page_count + 1) if n not in pages and routes.get(n) != "empty"]
    if missing:
        raise DocprocError(
            "unread_pages",
            f"page(s) {missing} are neither in pages[] nor routed `empty` (H15): the record would be partial",
        )
    return ReadDocument(
        version=version,
        source_format=data.get("sourceFormat"),
        producer=data.get("pipeline"),
        language=_language(data.get("documentLanguage")),
        page_count=page_count,
        pages=pages,
        routes=routes,
        forced=forced,
    )
