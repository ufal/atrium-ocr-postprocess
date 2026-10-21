# Paradata, output licensing and the document record

What each run records about itself, how a licence is computed for its outputs today, and the record this tool
reads and writes.

## Paradata logging

This project incorporates a unified provenance and paradata logging system to seamlessly track the execution
details of every pipeline stage. The logger automatically captures run-time metadata and saves it in a
structured JSON format.

**What gets logged?**

* **Provenance:** Captures the tool name, a tool **version** tag, the repository/runner reference, the running
container image (when set), the Python version, and assigns a unique `run_id` to each execution. The repository
reference is resolved **dynamically** — environment overrides (`ATRIUM_RUNNER_REPO`, `ATRIUM_RUNNER_REF`,
`ATRIUM_RUNNER_IMAGE`) take precedence over the static fallback in [para_config.txt](../../setup/para_config.txt) — so the log
points at the image actually executing rather than a fixed fork.
* **Output license:** Computes the **effective output license** of the run from the licensed components it actually
exercised, and records it as `license` / `license_url` plus a detailed `license_detail` block (per-component licenses,
which component(s) `determined_by` the result, `is_non_commercial` / `is_share_alike` flags, and any unknown licenses).
See [Output licensing](paradata.md#output-licensing) below.
* **Configuration:** Stores run-time configuration, including script
names, input/output paths, and specific model choices.
* **Timing:** Records precise UTC start times, end times, and the total duration of the run in seconds.
* **Statistics:** Tracks the total number of input files, successfully processed documents, and computes performance
throughput (e.g., output files generated per minute).
* **Error Tracking:** Maintains a `skipped_files_detail` list that logs the exact filename and specific error reason
if a file fails to process.

**Log Location**

By default, JSON logs are written to the [paradata](../../paradata) directory following the naming convention
`<YYMMDD-HHmmss>_<program>.json`. Paradata is intended to live alongside the **outputs** (not committed to the
repository); the paradata JSON files themselves are distributed under the **CC BY-NC 4.0** license.

## Output licensing

> [!IMPORTANT]
> **Under review** ([atrium-project#6](https://github.com/ufal/atrium-project/issues/6)). The computed licence below is
> a summary of the components a run used, not a statement of the rights in its outputs: those also depend on the
> rights in the source documents and on the terms of the services used. The project is separating the record of
> components used, the processing policy and the rights statement; until then, read `license` as the components'
> most restrictive licence.

> [!IMPORTANT]
> The license of the files a run **produces** is **not fixed** — it is computed per run as the **most restrictive**
> license among the components (models, data, APIs) that the run actually used. The mechanism is data-driven via
> [para_config.txt](../../setup/para_config.txt) (component → license) and [para_licenses.py](../../para_licenses.py)
> (restrictiveness ranking + share-alike / non-commercial rules), so the licensing owner can adjust it without touching
> the logger.

Each repository ships a [para_config.txt](../../setup/para_config.txt) listing its components. Components flagged `always` count
toward every run (the worst-case baseline); components flagged `conditional` are only counted when the script that uses
them records it. For this repository the components and their effect on the **effective output license** are:

| Component                                                                   | License         | Counted     | Used by                                                       |
|-----------------------------------------------------------------------------|-----------------|-------------|---------------------------------------------------------------|
| alto-tools (https://github.com/cneud/alto-tools) — vendored, see below      | Apache-2.0      | always      | page split, statistics, alto-tools text extraction            |
| FastText (https://huggingface.co/facebook/fasttext-language-identification) | CC BY-NC 4.0    | always      | language identification (`classify_TEXT.py`)                  |
| Qwen2.5-0.5B (https://huggingface.co/Qwen/Qwen2.5-0.5B)                     | Apache-2.0      | conditional | perplexity scoring (default, `classify_TEXT.py`)              |
| distilgpt2                                                                  | Apache-2.0      | conditional | perplexity scoring (English-only alternative)                 |
| **LayoutLMv3** (https://github.com/ppaanngggg/layoutreader)                 | CC BY-NC-SA 4.0 | conditional | LayoutReader text extraction (`extract_LytRdr_ALTO_2_TXT.py`) |
| GLM-4v-9b (https://huggingface.co/THUDM/glm-4v-9b)                          | glm-4           | conditional | generative OCR extraction (`extract_LLM_ALTO_2_TXT.py`)       |
| **pypdfium2** (PDFium) — PDF text layers (#31)                              | Apache-2.0 [^p] | conditional | text-lines PDF reading (`text_split.py`)                      |
| **charset-normalizer** — encoding detection (#31)                           | MIT             | conditional | text-lines non-UTF-8 plain text (`text_split.py`)             |

[^p]: pypdfium2 is dual-licensed Apache-2.0 **or** BSD-3-Clause; `para_licenses` does not parse SPDX `OR`, so it
is recorded as Apache-2.0 (either is permissive). The text-lines readers use no other licensed component: DOCX,
XLSX, PPTX, ODF, EPUB, HTML and XML are read with the standard library and lxml, so a text-lines run stays at the
**CC BY-NC 4.0** baseline.

Because the always-on FastText weights are **CC BY-NC 4.0**, the baseline effective output license for this
repository is **CC BY-NC 4.0** (non-commercial). Runs that additionally use the LayoutReader method escalate to
**CC BY-NC-SA 4.0** (non-commercial **and** share-alike), the most restrictive option here. A run that exercised only
permissive components would resolve to **Apache-2.0**.

> [!NOTE]
> The restrictiveness ordering encoded in [para_licenses.py](../../para_licenses.py) is a mechanical engineering
> approximation, **not legal advice**; unrecognised licenses are treated conservatively as maximally restrictive so a
> missing entry can never silently relax the recorded output license.

## The document record

The per-document record this tool reads and writes — `<doc_id>.document.json`, the pair of the
paradata above — follows [atrium_document.schema.json](../../atrium_document.schema.json) , schema
version **`1.0`**. That version is frozen as the hub tag
[`doc-schema-v1`](https://github.com/ufal/atrium-project/releases/tag/doc-schema-v1)
(ufal/atrium-project@`544298b`), and it is the baseline this tool implements:

* [tests/test_schema_freeze.py](../../tests/test_schema_freeze.py) checks this repository's schema
  against the frozen copy beside it,
  [atrium_document.schema.doc-schema-v1.json](../../atrium_document.schema.doc-schema-v1.json) : the
  copy is exactly the tagged file, nothing declared at the freeze has been removed or renamed, and
  every change made since is registered;
* all three files, like `atrium_document.py`, are vendored from the hub and kept byte-identical to
  its `v1` tag by the `para-drift` CI check, so they are never edited in this repository. The hub
  additionally checks that every record shape the tools write validates under both the frozen and
  the current schema.

Since v1.10.0-beta every write of this tool that touches `pages` or `lines` (each batch stage, `/process`,
`/score_record`) also sets the record's `quality_summary` block (atrium-project#73): page and line totals,
how many pages carry a `quality_score` with their mean, median, min and max, and the lines per `categ`
(digital-convert's `Garbage` / `Inverted` verdicts included). It is recomputed from the record as written,
numbers only — no band, no threshold — and nothing in the pipeline reads it to route or refuse a document.

What may change after the freeze, and what a new major version takes, is in the hub's
[Freeze & conformance](https://github.com/ufal/atrium-project/blob/main/docs/document_schema.md#freeze--conformance).
[CITATION.cff](../../CITATION.cff) carries the same reference under `references`.

