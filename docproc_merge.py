"""docproc_merge.py — the record's lines of a mixed document, from its two docprocs (atrium-project#84).

A document with any page digital-convert cannot use goes through the ATR whole, with those pages forced
(H4). Its stored docproc is then the ATR's (H14, D22), and digital-convert's line-level docproc is not
stored but named by its SHA-512 in the builder's paradata (D23). The record keeps the exact lines of
the text layer where digital-convert could use it (H6, D21), with the layout of the stored docproc (D26):

* **a page digital-convert routed to the ATR** (`needsAtr`) takes the ATR's lines (`text_source: atr`),
  unless the document is an office document (`OFFICE_FORMATS`, judged on digital-convert's
  `sourceFormat`) and the page has lines of its own: such a page went to the ATR for what its objects
  show, not for its text, which is exact (atrium-project#84 decision 3). It keeps its lines as any
  other page does, and an ATR block's own lines are dropped only when the native lines linked to it
  hold its text: at least `KEEP_NATIVE_MIN_RECALL` of its words (casefolded alphanumeric tokens).
  Otherwise both are kept, the native lines first (a caption in a text box over a scan keeps the
  scan's OCR). A table's own lines go whenever native cells were linked to it: its OCR repeats them.
  Nothing is lost; at worst a block's text is there twice;
* **any other page** keeps digital-convert's lines (`text_source: text-layer`, their own box, box
  precision and flags). Each is linked to the ATR block holding the largest share of its box; with no
  overlap, to the nearest block. A nested block wins a tie with its parent (a figure's `.t0` OCR child
  over the figure). A line without a box takes the block most of its digital-convert block's lines went
  to, else the block of the line before it (else after it);
* **an ATR block that no kept line went to** — figure OCR, a scanned insert, text the layer lacks — adds
  its own lines (`text_source: atr`);
* **the order** is the stored docproc's (H8): the ATR's blocks in `readingOrder`, and digital-convert's
  order within a block.

What fails the job (D13, H9), raised as `docproc.DocprocError`:

* `not_mixed`: digital-convert's docproc has no page that needs the ATR, so it is the stored one (H14);
* `docproc_roles`: the stored docproc has pages that need the ATR (the two were swapped);
* `page_mismatch`: the two do not list the same pages of the original;
* `forced_mismatch`: the ATR echoes forced pages (D27) other than digital-convert's `needsAtr` pages;
* `coordinate_mismatch`: a page both describe (forced or not) differs in unit, or in size by more than
  `SIZE_TOLERANCE_PT` or `SIZE_TOLERANCE_SHARE` (D28: both producers measure the displayed page);
* `atr_page_missing`: a page with text-layer lines has no ATR block to link them to (the ATR failed it).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from docproc import OFFICE_FORMATS, DocprocError, ReadBlock, ReadDocument, ReadLine, ReadPage

#: A page's size may differ between the two producers by this many units (rendering and rounding) ...
SIZE_TOLERANCE_PT = 2.0
#: ... or by this share of the size, whichever is larger.
SIZE_TOLERANCE_SHARE = 0.01
#: On a forced page of an office document, an ATR block's own lines are dropped only when the native
#: lines linked to it hold at least this share of its words (atrium-project#84 decision 3, R4).
KEEP_NATIVE_MIN_RECALL = 0.5

_WORD = re.compile(r"[^\W_]+")

Box = Sequence[float]


def _area(box: Box) -> float:
    return max(0.0, float(box[2]) - float(box[0])) * max(0.0, float(box[3]) - float(box[1]))


def _overlap(a: Box, b: Box) -> float:
    width = min(float(a[2]), float(b[2])) - max(float(a[0]), float(b[0]))
    height = min(float(a[3]), float(b[3])) - max(float(a[1]), float(b[1]))
    return max(0.0, width) * max(0.0, height)


def _coverage(line: Box, block: Box) -> float:
    """The share of `line`'s box inside `block`; a box without area counts by its centre."""
    area = _area(line)
    if area > 0:
        return _overlap(line, block) / area
    cx, cy = (float(line[0]) + float(line[2])) / 2, (float(line[1]) + float(line[3])) / 2
    inside = float(block[0]) <= cx <= float(block[2]) and float(block[1]) <= cy <= float(block[3])
    return 1.0 if inside else 0.0


def _distance(a: Box, b: Box) -> float:
    dx = max(0.0, float(b[0]) - float(a[2]), float(a[0]) - float(b[2]))
    dy = max(0.0, float(b[1]) - float(a[3]), float(a[1]) - float(b[3]))
    return (dx * dx + dy * dy) ** 0.5


def _link(line_box: Box, blocks: List[ReadBlock]) -> ReadBlock:
    """The ATR block a text-layer line belongs to (see the module docstring)."""
    boxed = [b for b in blocks if b.bbox is not None]
    best = max(
        boxed,
        key=lambda b: (round(_coverage(line_box, b.bbox), 6), -_area(b.bbox), -b.reading_order),
    )
    if _coverage(line_box, best.bbox) > 0:
        return best
    return min(boxed, key=lambda b: (_distance(line_box, b.bbox), _area(b.bbox), b.reading_order))


def _tolerance(size: float) -> float:
    return max(SIZE_TOLERANCE_PT, SIZE_TOLERANCE_SHARE * size)


def _check_geometry(index: int, atr: Optional[ReadPage], layer: Optional[ReadPage]) -> None:
    if atr is None or layer is None or not (layer.width and layer.height):
        return
    if not (atr.width and atr.height):
        return
    if atr.unit != layer.unit:
        raise DocprocError(
            "coordinate_mismatch",
            f"page {index}: the ATR measures in {atr.unit}, digital-convert in {layer.unit} (D28)",
        )
    for name, a, b in (("width", atr.width, layer.width), ("height", atr.height, layer.height)):
        if abs(a - b) > _tolerance(max(a, b)):
            raise DocprocError(
                "coordinate_mismatch",
                f"page {index}: {name} {a:g} (ATR) and {b:g} (digital-convert) {atr.unit} differ; the two "
                "producers do not measure the same displayed page (D28), so lines cannot be linked to blocks",
            )


def _relabel(lines: Sequence[ReadLine], source: str, block: Optional[str] = None) -> List[ReadLine]:
    return [
        ReadLine(
            text=line.text,
            block=block or line.block,
            bbox=line.bbox,
            bbox_precision=line.bbox_precision,
            language=line.language,
            flags=list(line.flags),
            unit=line.unit,
            text_source=source,
        )
        for line in lines
    ]


def _words(texts: Iterable[str]) -> List[str]:
    """Casefolded alphanumeric tokens."""
    return [word for text in texts for word in _WORD.findall(str(text or "").casefold())]


def _recall(block: ReadBlock, native: Sequence[ReadLine]) -> float:
    """The share of `block`'s own words that the native lines linked to it hold (1.0 for a block
    with no words of its own: there is nothing to lose)."""
    words = _words(line.text for line in block.lines)
    if not words:
        return 1.0
    have = Counter(_words(line.text for line in native))
    held = 0
    for word in words:
        if have[word] > 0:
            have[word] -= 1
            held += 1
    return held / len(words)


def _text_layer_page(index: int, atr: Optional[ReadPage], layer: ReadPage, by_text: bool = False) -> List[ReadLine]:
    """The lines of a page whose text layer is kept (see the module docstring). `by_text`: a forced
    page of an office document, whose ATR blocks keep their own lines unless the native lines linked
    to them hold their text (`KEEP_NATIVE_MIN_RECALL`)."""
    blocks = list(atr.blocks) if atr is not None else []
    if not any(block.bbox is not None for block in blocks):
        raise DocprocError(
            "atr_page_missing",
            f"page {index} has text-layer lines, but the ATR's docproc has no block on it to link them to (H6, H9)",
        )
    # Each text-layer line with its digital-convert order and the ATR block it links to.
    links: List[Optional[ReadBlock]] = []
    for line in layer.lines:
        links.append(_link(line.bbox, blocks) if line.bbox is not None else None)
    for i, line in enumerate(layer.lines):
        if links[i] is not None:
            continue
        # A line without a box: its digital-convert block's majority, else its neighbours'.
        votes: Dict[str, int] = {}
        for j, other in enumerate(layer.lines):
            if links[j] is not None and other.block == line.block:
                votes[links[j].id] = votes.get(links[j].id, 0) + 1
        if votes:
            target = max(votes.items(), key=lambda kv: (kv[1], -_order(blocks, kv[0])))[0]
            links[i] = next(b for b in blocks if b.id == target)
            continue
        before = next((links[j] for j in range(i - 1, -1, -1) if links[j] is not None), None)
        after = next((links[j] for j in range(i + 1, len(links)) if links[j] is not None), None)
        links[i] = before or after or next(b for b in blocks if b.bbox is not None)

    kept: Dict[str, List[ReadLine]] = {}
    for line, block in zip(layer.lines, links, strict=True):
        kept.setdefault(block.id, []).extend(_relabel([line], "text-layer", block=block.id))
    out: List[ReadLine] = []
    for block in sorted(blocks, key=lambda b: b.reading_order):
        if block.id in kept:
            out.extend(kept[block.id])
            if by_text and block.type != "table" and _recall(block, kept[block.id]) < KEEP_NATIVE_MIN_RECALL:
                out.extend(_relabel(block.lines, "atr"))
        else:
            out.extend(_relabel(block.lines, "atr"))
    return out


def _order(blocks: Sequence[ReadBlock], block_id: str) -> int:
    return next((b.reading_order for b in blocks if b.id == block_id), 0)


def merge(stored: ReadDocument, text_layer: ReadDocument) -> Tuple[ReadDocument, List[int]]:
    """Merge the stored (ATR) docproc and digital-convert's docproc of a mixed document. Returns the
    merged document, whose lines carry their own `text_source`, and the pages taken from the ATR.
    Raises `DocprocError` (see the module docstring)."""
    atr_pages = sorted(index for index, page in text_layer.pages.items() if page.needs_atr)
    if not atr_pages:
        raise DocprocError(
            "not_mixed",
            "no page of digital-convert's docproc needs the ATR: that docproc is the stored one (H14); "
            "build the record from it alone",
        )
    swapped = sorted(index for index, page in stored.pages.items() if page.needs_atr)
    if swapped:
        raise DocprocError(
            "docproc_roles",
            f"the stored docproc has pages that need the ATR ({swapped}): send the ATR's docproc as the "
            "docproc and digital-convert's as the text-layer docproc",
        )
    if stored.page_count != text_layer.page_count:
        raise DocprocError(
            "page_mismatch",
            f"the ATR's docproc lists {stored.page_count} page(s), digital-convert's {text_layer.page_count}: "
            "they are not of the same original",
        )
    if stored.forced and sorted(stored.forced) != atr_pages:
        raise DocprocError(
            "forced_mismatch",
            f"the ATR echoes forced pages {sorted(stored.forced)}, digital-convert routed {atr_pages} to it (D27)",
        )

    # An office document's forced pages keep their own text (atrium-project#84 decision 3, R4).
    office = (text_layer.source_format or "") in OFFICE_FORMATS
    pages: Dict[int, ReadPage] = {}
    for index in range(1, stored.page_count + 1):
        atr, layer = stored.pages.get(index), text_layer.pages.get(index)
        _check_geometry(index, atr, layer)
        forced = index in atr_pages
        if layer is not None and layer.lines and (office or not forced):
            lines = _text_layer_page(index, atr, layer, by_text=forced)
        else:
            lines = _relabel(atr.lines, "atr") if atr is not None else []
        frame = atr if atr is not None else layer
        if frame is None:
            continue  # routed empty by both
        pages[index] = ReadPage(
            page_index=index,
            width=frame.width,
            height=frame.height,
            unit=frame.unit,
            user_unit=frame.user_unit,
            pixel_ratio=frame.pixel_ratio,
            lines=lines,
        )
    merged = ReadDocument(
        version=stored.version,
        source_format=stored.source_format,
        producer=stored.producer,
        language=stored.language or text_layer.language,
        page_count=stored.page_count,
        pages=pages,
        routes=dict(stored.routes),
        forced=dict(stored.forced),
    )
    return merged, atr_pages
