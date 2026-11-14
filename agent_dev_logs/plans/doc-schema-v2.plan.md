# Strategy — [atrium-project#84](https://github.com/ufal/atrium-project/issues/84) (doc-schema-v2): ocr-postprocess's part

> _**Refreshed 2026-11-14 (afternoon)**: "Status — round 6 (11-14)": the hub merged, the pins moved, round 6._
> _**Refreshed 2026-11-14**: "Status — PR (11-14)": the PR's checks and round 5's fixes._
> _**Refreshed 2026-11-13**: "Status — round 2" (the code, pushed 11-13 to `test`) and round 4's notes. The file keeps
> the name `doc-schema-v2.plan.md` (it was `84.plan.md` on 11-12 and 11-13)._
> _**Refreshed 2026-11-12** on ARÚP's output contract of 12 November and atrium-project#84 (motyc, 11-12), at this
> checkout's head `ff3a467` (= `test`). The hub carries the whole change:
> [plan](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/plans/doc-schema-v2.plan.md) ·
> [digest](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/digests/doc-schema-v2.digest.md) (the docproc schema
> of #84's comment 1) · [contract digest](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/digests/project_state_1211.md).
> This file is the repository's slice only; its 11-11 version (written before the
> issue existed), whose reader for "ATR's JSON or ALTO", D14 merge by decode verdict, `pages[].origin`,
> `lines[].style.region` and `tables[]` are superseded below._

**Status (2026-11-14, afternoon)**: 🟡 **The hub's PR is merged and this repository is pinned to it: drift is green.
Round 6 completes this repository's part where no answer is pending; see "Status — round 6". The release (v2.0.0-beta)
is cut at K4TEL's cadence.**

**Status (2026-11-14)**: 🟡 **[atrium-ocr-postprocess#11](https://github.com/ufal/atrium-ocr-postprocess/pull/11) is open
(`doc-schema-v2` into `test`); see "Status — PR". Drift clears once the hub's PR merges and the pins move; the
record-major check clears at this tool's next release, which raises its major version.** _(superseded 11-14 afternoon)_

**Status (2026-11-13)**: 🟡 **Implemented, pushed 11-13 to `test`; see "Status — round 2". Deferred items are listed there.** _(superseded 11-14: the PR is open)_

**Status (2026-11-12)**: 📋 **Planned, plans only. ocr-postprocess becomes the record builder: it builds every
record from docproc, whichever producer wrote it (digital-convert, the ATR, the PCSS converter), merges the two
outputs of a mixed document, and is the only scorer of lines, under rules 1–8 (D17, D18, D20, D21). Code starts
after Kate's and Ronald's answers on the two-level contract (16–20 November) and the hub's shared modules.**

**Status (2026-11-11)**: 📋 Planned: a docproc/ALTO reader for scans and the D14 merge into a born-digital record.
_(superseded 11-12)_

## Its fields

Only what this repository writes; the full table is the hub plan's.

| Field                                                               | Written here?        | Note                                                                                                                       |
|---------------------------------------------------------------------|----------------------|----------------------------------------------------------------------------------------------------------------------------|
| `source.language`, `page_count`, `origin`                           | yes (builder)        | `origin` descriptive: `digital`, `atr`, `legacy` (PCSS)                                                                    |
| `pages[].page_index`, `page`, `canvas`, `content_status`            | yes (builder)        | a row for every page of the original, from docproc's routing (H5, H13, D29); `canvas` in pt with the UserUnit factor (D28) |
| `lines[].page`, `line`, `text`, `bbox`                              | yes (builder)        | `line` from 1 in docproc's reading order; `bbox` in the one coordinate space                                               |
| `lines[].block`, `text_source`, `bbox_precision`                    | yes, new (builder)   | the docproc block id; `text-layer`/`atr`/`legacy`; `measured`/`derived`/`absent` (D16, D25)                                |
| `content.text`                                                      | yes (builder)        | rebuilt in docproc's reading order (H8)                                                                                    |
| `lines[].categ`, `quality_score`, `lang`                            | yes, the only writer | every line, whatever its source (rules 1–3)                                                                                |
| `pages[].quality_score`, `quality_band`; `quality_summary`          | yes, the only writer | one band scheme; empty pages have no page score and are counted apart (rule 7)                                             |
| `tables[]`, `content.reading_order`, `pages[].ocr`, `source.sha256` | **no longer**        | the table stays in docproc's `cellGrid` (its text stays as lines); the engine is the producer's paradata (D9, D26)         |

## Tasks (next rounds)

1. **The docproc reader** (none exists). Today's readers are ALTO (`alto_tools.py`, `extract_ALTO_2_TXT.py`,
   `extract_LytRdr_ALTO_2_TXT.py`, `extract_LLM_ALTO_2_TXT.py`), generic JSON (`extract_JSON_2_TXT.py`;
   `text_formats.json_text_lines()`: no geometry, no order) and text; `TextPage` has no geometry. The new reader
   follows #84's comment 1:
   - pages by `pageNumber` → `page_index`; blocks in `readingOrder` (dense per page), `readingOrderRole` kept for
     the order of footnotes and sidebars; LayoutReader is not run (the order is given);
   - a block's `lines[]` when docproc has them (digital-convert's; Ronald's ask 13), each with its box and box
     precision; otherwise the block's `content` split on line breaks, each line with the block's box and
     `bbox_precision: derived` — and, when the ATR keeps no line breaks, the block as one unit, marked (rule 5;
     ask 7);
   - `cellGrid` tables give lines (their text), no `tables[]`; figure and image blocks give no lines, their OCR
     child (`.t0`) does; `all_engines_failed` or an unread page fails the job (D13; ask 9);
   - the page's verdict, `needs_atr` and the decode flag on lines are read, not copied into the record (D27).
2. **The record builder** (D20; H8, H13, H15). From the seed (`doc_id`, `source.sha512`, `filename`,
   `media_type`) and the stored docproc: `source`, `pages` (a row for every page in routing, `content_status`
   `empty` for a page routed empty), `lines`, `content`. Checks H15: every page of the original is in `pages[]` or
   routed empty, else the job fails. Today the batch path writes the record (`page_split.py`, `extract_*_2_TXT.py`,
   `classify_TEXT._lines_records_from_df()` 1006, `aggregate_STAT.py`), all through
   `document_hook.write_document_block()` (377); `POSITIONAL_BLOCKS` (`document_hook.py:69`) loses `tables`;
   `pages[].ocr` (`document_hook.py:737-752`, `HANDOFF_PAGE_FIELDS` 319) goes.
3. **The merge of two outputs** (H6, D21; a mixed document). Input: digital-convert's line-level docproc (not
   stored; named by SHA-512 and marked in the paradata, D23) and the ATR's (stored, D22). On a page that did not
   need ATR, keep digital-convert's exact lines and set each line's `block` to the ATR block that contains it (box
   containment in the one coordinate space, D28); on the `needs_atr` pages, and for ATR blocks no digital-convert
   line falls into (figure OCR, scanned inserts), take ATR lines (`text_source: atr`). An ATR failure fails the job
   (H9). It replaces the 11-11 plan's decode-based D14 merge and the hub's `OCR_HANDOFF` / `replace_page_rows()`.
4. **Scoring, rules 1–8** (D17, D18; #84 §3). `/score_record` (`service/text_api.py:1258`) and the batch:
   - rule 1–2: score every line; drop the skip of `Garbage`/`Inverted` (`DECODE_VERDICTS`, `document_hook.py:538`,
     its use at 591; `record_scoring.py:144`); the decode flag may be read as a feature, `categ` keeps the five
     values;
   - rule 3: one model, one set of constants, one band scheme for every source; the producer's `confidence` is not
     an input; `quality_band()` (`document_hook.py:755`) is the one band rule;
   - rule 4: the service runs the document passes the evaluated batch runs — `apply_page_perplexity_blend()`
     (`classify_TEXT.py:700`) and `apply_document_postprocessing()` (835), called only in the batch's
     `process_document()` (1166, 1173), not in `service/text_inference.py` or `record_scoring.py`;
   - rule 5: a block scored as one unit is marked and kept out of calibrated statistics;
   - rule 6: `Trash` re-routes nothing; rule 7: empty pages have no lines and no page score, `quality_summary`
     counts them apart; rule 8: the rules and constants are frozen for the pilot.
   A line that cannot be scored (`text_util.PPL_FAILED` 99999, `text_util.py:1467`): the hub's Q7.
5. **ALTO leaves the pipeline path** (D24). The ALTO readers and `reading_order=alto|layoutreader` on `/process`
   (`service/text_api.py:878`, 924) stay for batch and research use; in the pipeline the input is docproc only.
   Existing PCSS ALTO reaches the pipeline through the one-off converter (D31), whose owner is the hub's Q8; if it
   lands here, it reuses the ALTO readers and `normalize_boxes()` (`service/utils.py`), with `PHYSICAL_IMG_NR` as
   the page number and px where the original cannot be matched.
6. **Paradata** (#84 §4). `object`: the stored docproc and the seed by distribution name and SHA-512, plus the
   unstored digital-convert docproc of a mixed document, marked (D23); `result`: the record's blocks and fields
   written. SHA-512 in place of `page_split._sha256_of()`, `text_split.py`, `service/text_api.py`;
   `cleaned_lines.json` leaves the result. Through the re-vendored `atrium_rocrate.file_entity()` (hub task 3).
7. **Limits refuse** (D13): the `partial` batch status does not exist in pipeline mode; `trimmed`/`skipped` notes
   refuse (`tool_limits.py`: `ODF_REPEAT_CAP`, a ZIP member over `MAX_FILE_MB`, LayoutReader's fallback); a line
   scored on its first N tokens (`ppl_max_tokens()`) is the hub's Q6.
8. **No run data, no licence** (D1, D2): no `provenance`, stamps or per-block `run_uuid` (re-vendored
   `atrium_document.py`); `para_licenses.py` keeps its components as facts.

## Paradata

Two actions in `paradata/atrium/document-json`, one per step and document:

- **Record builder:** object = `atr/docproc-json` (SHA-512), the seed, and for a mixed document digital-convert's
  docproc marked not stored; result = `source`, `pages`, `lines`, `content` with the fields written; parameters =
  the reader's version and the coordinate rules.
- **Scoring:** object = the record version read; result = `lines` (`categ`, `quality_score`, `lang`), `pages`
  (`quality_score`, `quality_band`), `quality_summary`; parameters = the frozen rules' version and constants.
- **Limits:** `sampled` and `split` only. **Components** (`setup/para_config.txt`), facts with their terms:
  fastText (CC BY-NC 4.0), Qwen2.5-0.5B (Apache-2.0), and the rest as listed; LayoutReader only where ALTO is still
  read (the PCSS converter). Never merged.

## Status — round 2 (11-13)

Local branch from `test`, on the re-vendored shared modules (record 2.0, paradata 3.0,
`ContentLimitError`); delivered as an archive with the commands to run; pushed 11-13 (below). Nothing posted.

- **Task 1, the docproc reader** (`docproc.py`): pages by `pageNumber`, blocks by `readingOrder`. A block's own
  `lines[]` are `measured`; else its `content` split at line breaks, with the block's box (`derived`). Tables give one
  line per `cellGrid` row; image blocks without text give none. `all_engines_failed` and H15 fail the build.
- **Task 2, the builder** (`build_record.py`, `POST /build_record`): one producer. `source` (`origin`,
  `page_count`, `language`), a page row for every page (`content_status`, `canvas`), lines with `block`,
  `text_source`, `bbox_precision`, `content.text`; then scored. The batch path lost the origin guard and the OCR
  hand-off: origins map to `digital`/`atr`/`legacy`, and the engine is the paradata parameter `ocr_engine`.
- **Task 3, the merge (H6): not built.** A docproc with a `needsAtr` page fails the build (`needs_atr`).
- **Task 4, scoring:** rules 1–3 and 7 done: every line is scored, `skipped_decode_verdict` is gone, an empty page
  has no score, and `quality_summary` counts empty pages apart. **Deferred:** rule 4 (the service's page and
  document passes) and rule 5's exclusion from calibrated statistics (such a line is only marked `derived`).
  _(both done 11-14, round 6)_
- **Task 6, paradata:** `object` is docproc and the seed, or `original` and the record; `result` is the record's
  fields; `cleaned_lines.json` is left out. SHA-512 throughout. `score_record.py` writes exactly the bytes it names.
- **Task 7, D13:** a ZIP member over the limit, the ODF cap, LayoutReader out of memory, a line that fails
  perplexity alone, and a line over the perplexity window all fail the run. The last one is Q6, still open.
- **Fixes found on the way:** a page row is keyed through the record's own labels (`iv` keeps its row);
  `content_status` falls back to `page_index` for a row without a label.
- **Tests:** 2324 passed, 16 skipped; new `tests/test_build_record.py`
  on docproc fixtures in the ATR's shape and from digital-convert. `tests/test_ocr_handoff.py` is deleted.
- **Pushed** to `test` (11-13) without `tests/fixtures/docproc/` and with `tests/test_ocr_handoff.py` still in.
  Round 4 adds the fixtures, removes the test, re-vendors the addendum's shared files and declares `build_record.py`,
  `docproc.py` and `score_record.py` in `.github/production-image.json`: 2324 pass.
- **[atrium-digital-convert#9](https://github.com/ufal/atrium-digital-convert/issues/9#issuecomment-6838301982)**
  (agreed 11-13): born-digital lines are scored here (point 1, as built). TXT, CSV, TSV and JSON skip the visual
  steps and go straight to scoring (point 4); whether digital-convert writes them a docproc or this repository reads
  them is the hub's Q9.

## Status — PR (11-14)

[atrium-ocr-postprocess#11](https://github.com/ufal/atrium-ocr-postprocess/pull/11), head `1a9e778`. Failing checks:
check-drift (canonical), because the callers read the hub at `v1` (`4435ffb`) while the copies are v2; api-contract,
because the record schema went from 1.0 to 2.0 without a major version; build / test: `tests/fixtures/docproc/*.json`
was not committed and `tests/test_ocr_handoff.py` (which imports `OCR_HANDOFF`, gone in 2.0) was still there;
api-contract also reports `ocr_handoff` and `skipped_decode_verdict` gone. The causes across all seven PRs:
[hub plan of #84](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/plans/doc-schema-v2.plan.md),
"Status — PRs".

Round 5 (not pushed):

- `atrium_rocrate.py` and `tests/test_atrium_rocrate.py` re-vendored: a crate root that asserts no licence names
  `#no-licence-asserted` again (RO-Crate 1.2 requires a root licence; atrium-project#6, motyc 11-12). The hub's PR
  failed its RO-Crate check without it.
- `tests/fixtures/docproc/` (4 files) added, `tests/test_ocr_handoff.py` removed (both from round 4, missing from the
  push).

The suite passes: 2325. After the hub's PR merges, `v1` moves to its merge commit and
`scripts/pin_hub_reusables.py --sha` pins this repository's callers to it: drift clears. The record-major check stays
red until the release, which raises the major version in `setup/para_config.txt` (`v1.15.4-beta` now), `CITATION.cff`
and `service/openapi.json`'s `info.version`.

## Status — round 6 (11-14)

The hub's PR (atrium-project#86) merged; `v1` moved to it and K4TEL pinned this repository's callers: drift green. Round
6 (local, delivered as an archive):

- `atrium_document.py`, its schema and `tests/test_document_originators.py` re-vendored: rows may carry
  `lines[].unit: "block"` (rule 5, written by ocr-postprocess only), and `quality_summary.lines` counts them as
  `block_units`. `v2.0.0-beta` as the unreleased version: `setup/para_config.txt`, `CITATION.cff`,
  `service/openapi.json` (re-exported) and a CONTRIBUTING.md row marked Unreleased; api-contract passes with it.
- Rule 4: `record_scoring.document_passes()` runs the batch's `apply_page_perplexity_blend()` and
  `apply_document_postprocessing()` (dedup, Trash islands, page context, inverted sweep) on the scored lines, in
  `build_record` and `/score_record`; `rot_ratio` is computed as the batch does. Answers without the model's signals
  pass through unchanged.
- Rule 5: `docproc.py` marks a block without lines and without a line break `unit: "block"`; `page_records_from_lines()`
  leaves such rows out of the page's score and band.
- No object sampling: `text_formats.py` (this repository owns it) judges a PDF page on all its objects; `PDF_OBJECT_CAP`
  and the `pdf_objects_capped` note gone (`tool_limits.py`, `service/text_inference.py`, `.env.example`, service README,
  `docs/text_inputs.md`, `tests/test_limits.py`).
- `tests/fixtures/docproc/*.json` again, to be added with `git add -f`: the push of 11-14 left them out a second time.

The suite passes: 2332. Next: the hub's second PR for #84 (the E2E lanes, rule 5's mark), then `v1` and the pins again;
then this PR. Hub:
[plan of #84](https://github.com/ufal/atrium-project/blob/test/agent_dev_logs/plans/doc-schema-v2.plan.md),
"Status — round 6".

## Depends on

- Kate's answer on #84 (16 November) and Ronald's on aiscr-docs-pipeline#17: the two-level contract (D20, D24);
  asks 4 (routing of every page), 7 (line breaks), 9 (fail, not warn), 10 (coordinates), 13 (optional `lines[]`).
- Hub tasks 1 (v2 record, the builder's grants, no `replace_page_rows`), 3 (`file_entity`), 4 (`atrium_limits`),
  5 (one line-category originator), 7 (docproc E2E fixtures); re-vendoring with `scripts/revendor_shared.sh`.
- digital-convert's slice: line-level docproc with box precision, the verdict, `needs_atr`, the decode flag.
- Hub questions Q6, Q7, Q8 (the PCSS converter's owner), Q10 (the unstored docproc of a mixed document).
- The proof gate (D12): born-digital, mixed, scanned, image and PCSS documents through the assembled chain.

## Superseded in this repository's logs

Marked in place in open issues' files; closed issues' files are not edited.

- This file's 11-11 version (replaced): the reader for "ATR's JSON or ALTO", ALTO TextLine boxes converted to pt,
  `pages[].origin`, `lines[].handwritten`, `lines[].style.region`, `tables[]` from `cellGrid`, the D14 merge by
  decode verdict, `source.sha512` on the batch path.
- `plans/4.plan.md`, `digests/4.digest.md` (11-12 section): a fine-tuned scorer meets rule 8 — frozen for the
  pilot; validity measured on PCSS + ATR documents first.
- `plans/10.plan.md`, `digests/10.digest.md`: parked (atrium-project#83), unchanged.
- Closed, not edited: `plans/6.plan.md:84-88` (a page merged whole → H6, D21); `digests/1.digest.md:21`
  (`Garbage`/`Inverted` not re-scored → rule 2).

## Not in scope

- The decision models of #10 (parked); fine-tuning the scorer (#4, deferred; rule 8).
- The ATR and docproc's schema (ARÚP's, D30); the route (the pipeline's); page classification (beside the ATR).
- Re-processing `Trash` pages (a later campaign, rule 6); the document's licence (AMČR's RO-Crate).
