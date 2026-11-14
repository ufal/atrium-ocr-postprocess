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
* **Failures fail the job** (D13): a block flagged `all_engines_failed` (its text could not be read), and
  a page of the original that is neither in `pages[]` nor routed `empty` (H15).

The page verdict, `needsAtr` and the decode flags on lines are read, never copied into the record (D27,
D18 rule 2): the builder decides what to do with them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: The docproc schema major this reader reads.
SUPPORTED_MAJOR = 1

#: Block types that carry an image, not text (the schema's image class).
IMAGE_TYPES = frozenset({"image", "figure", "chart", "seal"})

#: The ATR's producer id (`pipeline`), with or without its cloud-fallback variant.
ATR_PIPELINE = "pipeline-e2e"

#: `sourceFormat` values of a born-digital original (digital-convert's).
DIGITAL_FORMATS = frozenset({"pdf-digital", "docx", "pptx", "odt", "ods", "xlsx", "rtf", "doc", "xls"})


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


@dataclass
class ReadPage:
    page_index: int
    width: Optional[float] = None
    height: Optional[float] = None
    unit: str = "pt"
    user_unit: Optional[float] = None
    lines: List[ReadLine] = field(default_factory=list)
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

    @property
    def text_source(self) -> str:
        """Where the lines' text came from (D25): the ATR's docproc is `atr`; digital-convert's, of a
        born-digital format, is `text-layer`."""
        if (self.producer or "").startswith(ATR_PIPELINE):
            return "atr"
        if self.source_format in DIGITAL_FORMATS:
            return "text-layer"
        return "atr"

    @property
    def origin(self) -> str:
        """The record's descriptive `source.origin` (#84 §1)."""
        return "digital" if self.text_source == "text-layer" else "atr"


def _box(value: Any) -> Optional[List[float]]:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return [float(v) for v in value]
        except (TypeError, ValueError):
            return None
    return None


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
                    flags=[str(f) for f in row.get("flags") or []],
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
        page = ReadPage(
            page_index=index,
            width=float(raw["width"]) if raw.get("width") else None,
            height=float(raw["height"]) if raw.get("height") else None,
            unit=str(raw.get("coordinateUnit") or "pt"),
            user_unit=float(raw["userUnit"]) if raw.get("userUnit") else None,
            needs_atr=bool(raw.get("needsAtr")),
            text_layer=raw.get("textLayer"),
        )
        blocks = [b for b in raw.get("blocks") or [] if isinstance(b, dict)]
        for block in sorted(blocks, key=lambda b: int(b.get("readingOrder", 0))):
            page.lines.extend(_block_lines(block))
        pages[index] = page

    routes: Dict[int, str] = {}
    routing = ((data.get("metadata") or {}).get("routing") or {}).get("pages") or []
    for row in routing:
        if isinstance(row, dict) and isinstance(row.get("page"), int):
            routes[row["page"]] = str(row.get("route") or "")
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
    )
