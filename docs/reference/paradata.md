# Paradata, component licences and the document record

What each run records about itself, the components it lists with their licences, and the record this tool reads
and writes.

## Paradata logging

Every stage, and every service call, records its run as paradata, schema 3.0
([atrium-project#84](https://github.com/ufal/atrium-project/issues/84); ARÚP's output contract of 12 November
2026). What a run read and wrote is named by AMČR distribution name and SHA-512, never by a path:

* **Inputs** (`inputs`, the action's `object`): the upload as `original` (AMČR's name for that distribution is still
  open, #84), the docproc as `atr/docproc-json`, and the record version read (the seed, or an earlier stage's record)
  as `atrium/document-json`.
* **Outputs** (`outputs`, the action's `result`): the record written, `atrium/document-json`, with the blocks and
  fields the run wrote (`record_fields`). The page texts, CSVs, `cleaned_lines` and the `/score_record` answer are
  development outputs, not stored distributions, so they are not named.
* **Parameters** (`parameters`): what shaped the output, e.g. the OCR engine of the extract stages (`ocr_engine`;
  until record schema 2.0 it was `pages[].ocr` in the record) and the docproc version a record was built from.
* **A failed run** says `action_status: failed`, with the error, and writes no record (D13).

The record carries no run data: no stamp, no run id, no licence. A service call returns its paradata as a Process
Run Crate `CreateAction` (`atrium_rocrate.action_from_paradata`).

**What else gets logged?**

* **Provenance:** Captures the tool name, a tool **version** tag, the repository/runner reference, the running
container image (when set), the Python version, and assigns a unique `run_id` to each execution. The repository
reference is resolved **dynamically** — environment overrides (`ATRIUM_RUNNER_REPO`, `ATRIUM_RUNNER_REF`,
`ATRIUM_RUNNER_IMAGE`) take precedence over the static fallback in [para_config.txt](../../setup/para_config.txt). The log
therefore points at the image actually executing rather than a fixed fork.
* **Components:** the licensed components the run actually exercised, each with its own licence terms, in
`components`, as facts. No licence of the output is computed or recorded. See
[The components a run lists](paradata.md#the-components-a-run-lists) below.
* **Configuration:** Stores run-time configuration, including script names and specific model choices.
* **Timing:** Records precise UTC start times, end times, and the total duration of the run in seconds.
* **Statistics:** Tracks the total number of input files, successfully processed documents, and computes performance
throughput (e.g., output files generated per minute).
* **Error Tracking:** Maintains a `skipped_files_detail` list that logs the exact filename and specific error reason
if a file fails to process.

**Log Location**

By default, JSON logs are written to the [paradata](../../paradata) directory following the naming convention
`<YYMMDD-HHmmss>_<program>.json`. The sample logs committed there are from June 2026 and predate paradata 3.0. Paradata is intended to live alongside the **outputs** (not committed to the
repository). The paradata JSON files themselves are distributed under the **CC BY-NC 4.0** licence.

## The components a run lists

> [!IMPORTANT]
> The licences below are those of the components a run used, **not the licence of the files it produces**: the rights in an output depend first on the source document and on the terms of the services used, and
> they are stated by whoever holds them (for the AMČR pilot, AMČR; agreed on 22 October 2026,
> [atrium-project#6](https://github.com/ufal/atrium-project/issues/6)). No tool asserts an output licence.

[para_config.txt](../../setup/para_config.txt) declares each component (models, data, APIs) and its licence, and
[para_licenses.py](../../para_licenses.py) normalises the licence names, so the licensing owner can adjust them
without touching the logger. Components flagged `always` are listed for every run; components flagged `conditional`
only when the script that uses them records it. For this repository:

| Component                                                                   | License         | Listed      | Used by                                                       |
|-----------------------------------------------------------------------------|-----------------|-------------|---------------------------------------------------------------|
| alto-tools (https://github.com/cneud/alto-tools) — vendored, see below      | Apache-2.0      | always      | page split, statistics, alto-tools text extraction            |
| FastText (https://huggingface.co/facebook/fasttext-language-identification) | CC BY-NC 4.0    | always      | language identification (`classify_TEXT.py`)                  |
| Qwen2.5-0.5B (https://huggingface.co/Qwen/Qwen2.5-0.5B)                     | Apache-2.0      | conditional | perplexity scoring (default, `classify_TEXT.py`)              |
| distilgpt2                                                                  | Apache-2.0      | conditional | perplexity scoring (English-only alternative)                 |
| **LayoutLMv3** (https://github.com/ppaanngggg/layoutreader)                 | CC BY-NC-SA 4.0 | conditional | LayoutReader text extraction (`extract_LytRdr_ALTO_2_TXT.py`) |
| GLM-4v-9b (https://huggingface.co/THUDM/glm-4v-9b)                          | glm-4           | conditional | generative OCR extraction (`extract_LLM_ALTO_2_TXT.py`)       |
| **pypdfium2** (PDFium) — PDF text layers (#31)                              | Apache-2.0 [^p] | conditional | text-lines PDF reading (`text_split.py`)                      |
| **charset-normalizer** — encoding detection (#31)                           | MIT             | conditional | text-lines non-UTF-8 plain text (`text_split.py`)             |
| Clef, Clef-Flash, Strands Decider 2B — decision models (#10)                | Apache-2.0      | conditional | decision-model experiments (`tools/decide_lines.py --decide`) |

[^p]: pypdfium2 is dual-licensed Apache-2.0 **or** BSD-3-Clause; `para_licenses` does not parse SPDX `OR`, so it
is recorded as Apache-2.0 (either is permissive). The text-lines readers use no other licensed component: DOCX,
XLSX, PPTX, ODF, EPUB, HTML and XML are read with the standard library and lxml.

Every run lists the FastText weights, which are **CC BY-NC 4.0** (non-commercial). A run with the LayoutReader method
also lists LayoutLMv3, **CC BY-NC-SA 4.0** (non-commercial **and** share-alike).

> [!NOTE]
> Paradata 3.0 has no `license`, `license_url` or `license_detail` field: the older calculated value is gone
> (#84 D2; [Licences and paradata](https://ufal.github.io/atrium-project/ecosystem/licences/) on the hub).

## The document record

The per-document record this tool builds and scores, `<doc_id>.document.json`, follows
[atrium_document.schema.json](../../atrium_document.schema.json), record schema **2.0**
([atrium-project#84](https://github.com/ufal/atrium-project/issues/84)). This tool is its builder
(`atrium_document.RECORD_BUILDER`): it writes `source.origin`, `page_count` and `language`, a `pages[]` row for
every page of the original (`page_index`, `page`, `content_status`, `canvas`), the `lines[]` (`text`, `bbox`,
`block`, `text_source`, `bbox_precision`) and `content`, and, as the only scorer, every line's `categ`,
`quality_score` and `lang` and each page's `quality_score` and `quality_band`.

`doc-schema-v1` stays the frozen, published record schema
([`doc-schema-v1`](https://github.com/ufal/atrium-project/releases/tag/doc-schema-v1)), and
[tests/test_schema_freeze.py](../../tests/test_schema_freeze.py) still checks against the frozen copy beside it,
[atrium_document.schema.doc-schema-v1.json](../../atrium_document.schema.doc-schema-v1.json). `doc-schema-v2` is not
tagged yet: it will be once it is proven end to end. The schema files, like `atrium_document.py`, are vendored from
the hub and kept byte-identical by the `para-drift` CI check, so they are never edited in this repository.

Every write of this tool that touches `pages` or `lines` (each batch stage, `/process`, `/build_record`,
`/score_record`, `build_record.py` and `score_record.py`) also sets the record's `quality_summary` block. It holds
page totals (empty pages counted apart), how many pages carry a `quality_score` with their mean, median, min and max,
and the lines per `categ`. It is recomputed from the record as written, numbers only, with no band and no threshold.
Nothing in the pipeline reads it to route or refuse a document.

What may change after the freeze, and what a new major version takes, is in the hub's
[Freeze & conformance](https://github.com/ufal/atrium-project/blob/main/docs/document_schema.md#freeze--conformance).
[CITATION.cff](../../CITATION.cff) carries the `doc-schema-v1` reference under `references`.
