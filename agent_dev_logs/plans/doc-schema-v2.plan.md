# Strategy — doc-schema-v2 (atrium-project, issue to be opened): ocr-postprocess's part

> _Written **2026-11-11** after the meeting of that day (ARÚP's output contract) and K4TEL's points in session, at
> this checkout's head `bb9d0c4` (11-09). The hub carries the whole change:
> [plan](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/plans/doc-schema-v2.plan.md) ·
> [digest](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/digests/doc-schema-v2.digest.md) ·
> [meeting digest](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/digests/project_state_1111.md).
> This file is the repository's slice only. Rename it to the issue number once the hub issue is opened._

**Status (2026-11-11)**: 📋 **Planned, plans only this round. ocr-postprocess runs right after ATR: it builds the
record of a scanned document from docproc, merges the route's OCR pages into a born-digital record, and is the only
scorer of lines (D14, D17, D18). Code starts after the hub's shared modules; the reader waits for the docproc schema.**

## Its fields

Only what this repository writes; the full tables are the hub plan's.

| Field                                                      | Written here?          | Note                                                                              |
|------------------------------------------------------------|------------------------|-----------------------------------------------------------------------------------|
| `pages[].page_index`, `page`                               | yes (`page_index` new) | = docproc `pageNumber` = ALTO `PHYSICAL_IMG_NR`; today read only                  |
| `pages[].canvas`                                           | yes, new               | from docproc width and height, pt; an ALTO page converted to pt                   |
| `pages[].origin`                                           | yes, new               | `atr`; replaces `pages[].ocr`, whose `engine` goes to the paradata (D9)           |
| `pages[].quality_score`, `quality_band`; `quality_summary` | yes, the only writer   | one banding rule: the plurality vote (D18)                                        |
| `lines[].page_index`, `line`, `text`                       | yes (ATR lines)        | `line` from 1, in reading order within the page                                   |
| `lines[].origin`, `bbox`, `bbox_level`                     | yes, new               | `atr`; the block's box, pt, top-left; `block`                                     |
| `lines[].block`, `handwritten`                             | yes, new               | the docproc block id; its handwritten/typed type (Q5)                             |
| `lines[].style.region`                                     | yes, new (ATR lines)   | from docproc `crossPageClusters`; the hub grants `style` to digital-convert today |
| `lines[].categ`, `quality_score`, `lang`                   | yes, the only writer   | every line on every origin (D17); `lang` ISO 639-1                                |
| `content`, `tables[]`                                      | yes                    | built for a scan, rebuilt after a merge (H8); tables from docproc `cellGrid`      |
| `source.sha512`                                            | batch path only        | in the pipeline digital-convert writes `source` first                             |

## Tasks (next rounds)

1. **The docproc reader** (none exists). Today's readers: ALTO (`alto-tools`, `layoutreader` by default, `glm`),
   generic JSON (`extract_JSON_2_TXT.py:37-60`; `text_formats.json_text_lines()`/`_json_pages()`, 2069-2120: no
   geometry, no order), `text-lines` (`text_formats.READERS`, 364+); `TextPage` (303-311) has no geometry. ATR's
   JSON is read as content blocks, in order, then processed as usual text:
   - pages by `pageNumber` → `page_index`; blocks in docproc's reading order, `readingOrderExtensions` included;
     LayoutReader is skipped (the order is given);
   - a text block split into lines on newlines; each line takes the block's box (`bbox_level: block`: ATR boxes are
     regions, no word positions), `block`, the `lang` hint and `handwritten`;
   - `cellGrid` → `tables[]`; `crossPageClusters` (running headers, footers, footnotes) → `style.region`; figure and
     image blocks give no lines;
   - a page the route sent and docproc omits (ATR skips empty pages): Q2. Field names wait for the schema (Q4).
2. **The record of a scanned document** (variant C). Today the batch path writes it: `page_split.py:615-627`
   (`source`), `extract_*_2_TXT.py` (`pages[].ocr`, `content`), `classify_TEXT._lines_records_from_df()` (1006-1025;
   lines from 1 at 1098-1103), `aggregate_STAT._page_records_from_stats()` (212), all through
   `document_hook.write_document_block()` (377-526). v2: `pages` with `page_index`, `page`, `canvas`, `origin: atr`;
   `lines`, `content`, `tables`. `pages[].ocr` (`document_hook.py:705-752`) → `pages[].origin`. For ALTO input,
   keep the TextLine boxes and convert the MeasurementUnit to pt (read and dropped today: `service/utils.py:175-277`,
   `normalize_boxes()`, 328). `_accretion_records()` refuses multi-page ALTO (`service/text_api.py:668-736`):
   revisit it for docproc, which is multi-page.
3. **The D14 merge** (variant D; H6, H8). On a page the route sent to OCR: keep the lines with `decode: ok`; replace
   each line whose `decode` is not `ok` by the ATR lines of the blocks overlapping its area (box intersection, one
   coordinate space, pt, D8); drop ATR blocks that overlap kept lines; rebuild `content`. It replaces the whole-page
   `OCR_HANDOFF` / `replace_page_rows()` (hub `atrium_document.py:449-460, 924`; called at `document_hook.py:519`).
4. **Every line scored** (D17, D18). Drop the skip of digital-convert's `Garbage`/`Inverted` lines (`DECODE_VERDICTS`,
   `document_hook.py:538`; `record_scoring.py:144`). Sole writer of `categ`, `quality_score`, `lang`,
   `pages[].quality_score`/`quality_band` (`document_hook.quality_band()`, 755-765) and `quality_summary`. A line
   that cannot be scored (`text_util.PPL_FAILED` 99999, `text_util.py:1467`; the `skipped` note,
   `service/text_inference.py:255-258`): Q7.
5. **SHA-512** (K4TEL, §5.5). `page_split._sha256_of()` (122-127, used at 622), `text_split.py:401-404`,
   `service/text_api.py:1182, 1368-1389`; the shared `file_entity` calls (`text_api.py:1042, 1354, 1412`) once hub
   task 3 lands. `cleaned_lines.json` (1412) leaves the result: it is not a stored output.
6. **Limits refuse** (D13). The `partial` batch status (`text_split.py:22-45, 450-451`) does not exist in pipeline
   mode. `trimmed` and `skipped` notes refuse instead: `ODF_REPEAT_CAP` (`tool_limits.py:69-71`; digital-convert's
   copy is re-vendored), a ZIP member over `MAX_FILE_MB` (36-37), LayoutReader's fallback (80-82). `sampled` and
   `split` stay recorded. A line scored on its first N tokens (`ppl_max_tokens()`, 136-142, `trimmed`) is the kind
   of case Q6 asks about.
7. **Run data out, no licence** (D1, D2): one `run_uuid` per block (Q1) through the re-vendored `atrium_document.py`;
   `para_licenses.py` keeps its components as facts, with no ranking (hub task 2).

## Paradata

Both actions go to `paradata/atrium/document-json`, one per step and document:
- **OCR route:** object = `atr/docproc-json` (or `atr/alto-xml`) by distribution name and SHA-512, and the record
  version; result = the record blocks `pages`, `lines`, `content` (and `tables`); parameters = the reader, and the
  OCR engine as ATR reports it (D9).
- **Scoring:** object = the record version; result = `lines`, `pages`, `quality_summary`; parameters = the rules' version.
- **Limits:** `sampled` and `split` only.
- **Components, as facts with their terms** (`setup/para_config.txt`): fastText (CC BY-NC 4.0), Qwen2.5-0.5B
  (Apache-2.0), LayoutReader's LayoutLMv3 (CC BY-NC-SA 4.0), and the rest as listed. Never merged; the document's
  licence lives only in AMČR's RO-Crate.

## Depends on

- Hub tasks 1 (the v2 record; the D14 merge in place of `OCR_HANDOFF`; the field grants), 2, 3 (`file_entity`),
  4 (`atrium_limits`), 5 (one line-category originator), re-vendored with `scripts/revendor_shared.sh`; hub task 7
  (a docproc fixture for the E2E lanes).
- Q2 (empty pages), Q3 (ALTO's paradata; docproc as the primary input), Q4 (the docproc schema), Q5 (`handwritten`;
  the decode names this merge reads), Q7 (unscorable lines); Q1 for the run map.
- digital-convert's slice: the `decode` markers, lines from 1, `page_index` on every line row.
- The proof gate (D12): a document of each variant A–D through the assembled chain.

## Superseded in this repository's logs

Marked in place in open issues' files (` _(superseded 11-11: …)_`; line numbers after this round's edits); closed
issues' files are not edited.
- `plans/10.plan.md:44, 63` — Phase A proceeds until motyc answers; the next counts → parked during the pilot phase.
- `plans/4.plan.md:335-336`, `digests/4.digest.md:337-338` — `para_licenses.py` ranks licences; a CC BY-NC floor →
  D2: components listed, nothing ranked or merged.
- Closed, not edited: `digests/1.digest.md:21` (the converter's `Garbage`/`Inverted` verdict is not re-scored →
  D17); `plans/6.plan.md:84-88` (a `needs_ocr` page merged whole, with no need of the positional plane → D14, which
  matches by boxes in pt).

## Not in scope

- The decision models of #10 (parked); fine-tuning the scorer (#4, deferred past the pilot phase).
- The ATR service and its schema (ARÚP's); the route (the pipeline's); page classification (bundled with ATR).
- Calling another ATRIUM service; the document's licence (AMČR's RO-Crate).
