# Usage

How to run ocr-postprocess from a checkout: the batch pipeline, stage by stage, and where the service fits.
For the service itself (the published image, its endpoints, errors and limits) read
[service/README.md](../service/README.md). Every output column is listed in
[the output reference](reference/outputs.md); every setting in [configuration](configuration.md).

## Setup

1.  Create and activate a new **virtual environment** in the project directory.
2.  Install the required Python packages:
    ```bash
    pip install -r setup/requirements.txt
    ```
3. Download the FastText model for language identification, at the revision the Docker image pins:
    ```bash
    wget "https://huggingface.co/facebook/fasttext-language-identification/resolve/3af127d4124fc58b75666f3594bb5143b9757e78/model.bin" -O lid.176.bin
    echo "8ded5749a2ad79ae9ab7c9190c7c8b97ff20d54ad8b9527ffa50107238fc7f6a  lid.176.bin" | sha256sum -c -
    ```
    The file is the Hub's `model.bin` — the NLLB fastText language-ID model, 1.18 GB, CC BY-NC 4.0 —
    saved under the name the pipeline's config expects (`FASTTEXT_MODEL = lid.176.bin`); it is not the
    126 MB fastText `lid.176.bin`. The Docker image fetches the same file pinned to a Hub revision and
    checked against its SHA-256 (the `FASTTEXT_REVISION` / `FASTTEXT_SHA256` build args in the
    [Dockerfile](../Dockerfile)).
4. Copy the `v3` folder from the `layoutreader` repository to the project directory for the LR-based text extraction method:
    ```bash
    git clone https://github.com/ppaanngggg/layoutreader.git
    cp -r layoutreader/v3/ ./
    rm -rf layoutreader/
    ```

> [!NOTE]
> **Docker on Linux: run as yourself.** The published images are `ghcr.io/ufal/atrium-ocr-postprocess:<version>`
> (the batch pipeline) and `ghcr.io/ufal/atrium-ocr-postprocess-api:<version>`, where `<version>` is the release
> without its leading `v`. `./data` is part of the clone and belongs to you, while the images run as uid 10001 by
> default: `docker-compose.yml` runs the services as `user: "${ATRIUM_UID:-10001}:0"`, so put your uid in `.env`
> once — `echo "ATRIUM_UID=$(id -u)" >> .env`. With `docker run`, pass `--user "$(id -u):0"`. Docker Desktop
> (macOS, Windows) needs neither. (atrium-project#69)

> [!NOTE]
> There is no `alto-tools` install step any more. The statistics and text-extraction code paths this
> pipeline used are **vendored** in [alto_tools.py](../alto_tools.py) — see [Vendored code](development.md#vendored-code).


## Running the whole pipeline

The [run_pipeline.py](../run_pipeline.py) orchestrator runs every stage sequentially
(**split → statistics → text extraction → classification → aggregation**) and, at the end,
**merges all per-stage paradata** logs into a single run summary describing every stage, the
intermediate file formats produced, and the **effective end-to-end output license** (see
[Paradata logging](reference/paradata.md)).

```bash
python3 run_pipeline.py                      # all settings from config.txt
python3 run_pipeline.py --method glm         # override just the extraction backend
python3 run_pipeline.py --method json-keys --input-dir data_samples/JSON   # generic OCR JSON (#31)
python3 run_pipeline.py --method text-lines  # PDF/DOCX/TXT/... from [PIPELINE].INPUT_DIR_TEXT (#31)
python3 run_pipeline.py --skip-split         # PAGE_ALTO already populated
python3 run_pipeline.py --dry-run            # print the resolved plan, run nothing
```

* **Configuration:** every setting is read from [config.txt](../setup/config.txt)
(section `[PIPELINE]`, with `INPUT_CSV` taken from `[EXTRACT]`). Precedence is
**CLI flag > config value > built-in default**. Point at a different config with `--config`
or the `LANGID_CONFIG` environment variable.
* **Extraction method:** `[PIPELINE] METHOD` selects the **Step 3** backend —
`alto-tools`, `layoutreader` (**default**), or `glm` for ALTO XML; `json-keys` for generic OCR
JSON; `text-lines` for every other text-bearing format (#31). The method also selects the
Step 1/2 scripts for its input format. The choice flows through to the merged license: a
LayoutReader run resolves to **CC BY-NC-SA 4.0**, an alto-tools run to
**CC BY-NC 4.0**.
* **`--input-csv`:** the page statistics CSV. Step 2 writes it, and Steps 3 and 4 read it — every
extractor and `classify_TEXT.py` take `--input-csv` (#31 Phase 5; the flag used to reach Step 2
only). Without the flag the ALTO/JSON methods read `[EXTRACT].INPUT_CSV` / `[CLASSIFY].INPUT_CSV`
as before, and `text-lines` uses its own CSV, `[EXTRACT].INPUT_CSV_TEXT`
(`data_samples/text_stats.csv`), which the orchestrator passes to Steps 3 and 4 — so a text-lines
run no longer overwrites the ALTO methods' `data_samples/test_alto_stats.csv`.
* **text-lines extras (#31):** without `--input-dir` it reads `[PIPELINE].INPUT_DIR_TEXT`
(`data_samples/TEXT`); `--strict` / `--no-strict` reach `text_split.py` and
`extract_TEXT_2_TXT.py` (a strict failure stops the pipeline); `--source-origin ocr:<engine>`
reaches the split stage of every method.
* **Output:** a merged `<YYMMDD-HHmmss>_pipeline-run.json` in the [paradata](../paradata)
directory, alongside the individual per-stage logs.

> [!NOTE]
> Every stage — Step 1 (`page_split.py` / `text_split.py`) included — writes its own paradata
> log, so a full run merges **five** logged stages, and the dry-run tags all five `[paradata]`.
> The merged license is re-derived from the **union** of components used across all stages, so
> the end-to-end most-restrictive rule holds.

> [!TIP]
> Prefer to inspect or re-run a single stage? The individual scripts below remain fully usable on
> their own — the orchestrator simply calls them in order.

## Step 1: split documents into pages

First, ensure you have a directory  containing your document-level input files. This script
will split them into individual page-specific files — it supports **both** ALTO XML and generic
JSON, dispatched automatically by file extension. Every other text-bearing format goes through
[`text_split.py`](#any-other-text-bearing-input) instead (`--method text-lines`).

```
python3 page_split.py <input_dir> <output_dir>
```

Each page-specific file retains the header from its original source document.

### ALTO XML input

* **Input:** `../ALTO/` (input directory with ALTO XML documents)
* **Output:** `../PAGE_ALTO/` (output directory with ALTO XML files split into pages)

> [!NOTE]
> `page_split.py` splits every ALTO version: the namespace is the root element's (v2, v3 — what
> the ATRIUM ABBYY exports use — v4, BnF), and each page file keeps it. A `.xml` whose root is not a
> namespaced `<alto>` (PAGE XML, TEI, namespace-less ALTO) is skipped with its root named; the
> [text-lines method](#any-other-text-bearing-input) reads those. A re-split
> replaces a document's page files: pages the input no longer yields are removed, also when the
> input now fails. Tesseract numbers its ALTO pages from 0 (`PHYSICAL_IMG_NR="0"`), so its page
> files are `<doc>-0.alto.xml`, `<doc>-1.alto.xml`, …

Example of the output directory with divided per-page XML files: [PAGE_ALTO](../data_samples/PAGE_ALTO) .

```
PAGE_ALTO/
├── <file1>
│   ├── <file1>-<page>.alto.xml
│   └── ...
├── <file2>
│   ├── <file2>-<page>.alto.xml
│   └── ...
└── ...
```

### Generic JSON input

Real OCR/Doc-AI engines don't agree on how they represent multi-page documents, so the JSON
path is detected heuristically rather than assuming one vendor's schema: a nested page-list
container (e.g. Azure Document Intelligence's/docTR's `pages` array), a flat element list
tagged with a per-item page field (e.g. AWS Textract), or — when neither pattern is found —
today's single-page-per-file default (e.g. pero-ocr, OCR.space).

* **Input:** `../JSON/` (input directory with generic JSON OCR-engine documents)
* **Output:** `../PAGE_JSON/` (output directory with JSON files split into pages)

```
PAGE_JSON/
├── <file1>
│   ├── <file1>-<page>.json
│   └── ...
├── <file2>
│   ├── <file2>-<page>.json
│   └── ...
└── ...
```

### Any other text-bearing input

```
python3 text_split.py <input_dir> <output_dir> [--source-origin ocr:<engine>] [--strict | --no-strict]
```

The `<format>-2-txt` step for everything that is not ALTO or json-keys JSON: **PDF** (text
layer), **DOCX**, **ODT/ODS/ODP**, **XLSX**, **PPTX**, **EPUB**, **RTF**, **HTML/XHTML** and
**hOCR**, **PAGE XML**, **TEI/TEITOK**, the OCR exports **ABBYY FineReader XML**, **DjVuXML** and
**Tesseract TSV**, any other XML, **JSON/JSONL**, **CSV/TSV**, **Markdown**, **SRT/WebVTT**
subtitles, **EML/MBOX** e-mail and **plain text** in UTF-8/16/32 or a legacy code page (cp1250
first). A **gzip/bzip2/xz**-compressed file is read through (`scan.txt.gz`), and a **ZIP of
per-page files** — a Transkribus or eScriptorium export — is one multi-page document. Formats
are recognised by **content**, not by extension, so a misnamed file is still read correctly and
an unreadable one is refused with a reason.

Every file becomes an ordered list of **pages**, each an ordered list of **lines**. A page is a
real page where the format has one (PDF, PAGE XML, hOCR, ALTO, DOCX/ODT page breaks). Otherwise
it is the format's natural block: a sheet, a slide, a JSON child object, a JSONL record, an EPUB
chapter, a form-feed section of a text file. A line is the format's own unit: a physical
line, a paragraph, a table cell or a spreadsheet row. The standard behind each format, the tools
that write it and what this repo keeps of it
([Formats and their standards](text_inputs.md#formats-and-their-standards)), the full
per-format matrix, the normalisation rules, the limits and the reason codes are in
**[docs/text_inputs.md](text_inputs.md)**.

* **Input:** `../TEXT/` (any mix of the formats above — example: [data_samples/TEXT](../data_samples/TEXT))
* **Output:** `../PAGE_TEXT/` — `<doc_id>/<doc_id>-<n>.txt` per page, plus two reports:
  * `ingest_report.csv`: one row per input file, with its status (`ok` / `partial` / `error` /
    `ignored`) and reason code, the detected kind and encoding, page/line counts and the
    `source.origin` recorded;
  * `pages_report.csv`: one row per page, with its original label (sheet name, PDF page label,
    JSON page number, bundle member) and, for PDFs, the text-layer class (`none` / `garbled` /
    `ocr` / `digital`), a needs-OCR reason and report-only flags (`mojibake_cp1252`,
    `mirrored_text=N`, `rotated_text=N`).

A file that cannot be read costs **that file only**: images (`image_needs_ocr`), legacy
`.doc/.xls` (`legacy_office_unsupported`), encrypted, corrupt, empty or oversized files are
listed with their reason and the run continues. A file read with a possible loss (recovered XML,
a skipped bundle member, a stray CSV quote, a bad JSONL record) is `partial`: written, processed
downstream, its reasons listed. `--strict` turns any failure or partial read into exit code 1.
PDFs are read in an isolated child process with a timeout, ZIP containers and compressed files are
checked against bomb caps before anything is unpacked, and XML with entity declarations is refused.
A re-run never leaves stale pages of a document that now fails, and a page directory is only ever
replaced when it holds nothing but that document's page files. DOCX/ODT footnotes end the page
that cites them (`[TEXT_INGEST].NOTES`). All caps live in `[TEXT_INGEST]` in
[config.txt](../setup/config.txt) ; each can also be set as the environment variable
`ATRIUM_TEXT_INGEST_<KEY>`, which wins over the file (atrium-project#53; the full list is
[service/README.md § Limits](../service/README.md#limits)).

```
PAGE_TEXT/
├── ingest_report.csv
├── pages_report.csv
├── <file1>
│   ├── <file1>-1.txt
│   └── ...
└── ...
```

### Where the input came from: `source.origin`

This is the pipeline's **first** stage to see the original file, so when
`[DOCUMENT].JSON_DIR` is configured it is also the first writer of the record's `source`
block (`sha256`, `filename`, `media_type`, `page_count`, `origin`). `source` is
immutable — first writer wins — and since the `atrium_document` §1a hardening its
`origin` is what **authorises** this repo to write the positional blocks
(`pages`/`content`/`lines`/`tables`), so that no document can end up with half an
OCR-derived plane and half a digital-born one. A value that matches no known originator
prefix silently switches that check off, which is why the resolution is verified and a
mismatch warns.

| Input    | Default `origin` | Meaning                                                                                          |
|----------|------------------|--------------------------------------------------------------------------------------------------|
| ALTO XML | `ABBYY-ALTO`     | ALTO from the ABBYY toolchain the extractors already assume (see `extract_LytRdr_ALTO_2_TXT.py`) |
| JSON     | `ocr:generic`    | Generic OCR/Doc-AI export whose specific engine the file does not name                           |

For `text_split.py` (#31) the default is **truthful per format**. The OCR outputs are
`ABBYY-ALTO` (ALTO), `ocr:page-xml`, `ocr:hocr` — for an ALTO or hOCR file that names its engine
in its header, that engine instead (`ocr:tesseract`, `ocr:pero`, `ocr:kraken`, `ocr:transkribus`,
`ocr:ocrd`, …; #31 Phase 5) —, `ocr:abbyy-finereader`, `ocr:djvu`,
`ocr:tesseract`, `ocr:pdf-text-layer` (a PDF whose text is an invisible OCR layer under the page
image) and `ocr:generic` (TXT, Markdown, CSV/TSV, JSON/JSONL, TEI, XML, subtitles); a ZIP bundle
takes its members' origin. Born-digital documents are `digital-born-<kind>` (DOCX, ODT/ODS/ODP,
XLSX, PPTX, EPUB, RTF, plain HTML, e-mail, a PDF with visible text). A born-digital record is
originated by [`digital-convert`](https://github.com/ufal/atrium-digital-convert), so this repo then writes **only** `source` into
it: `document_hook` holds back the `pages`/`content`/`lines` blocks of every stage
(`atrium_document` §1a), except for the pages digital-convert flags `needs_ocr`. Those pages'
rows are written page by page, their lines replaced by this repo's, and every other page is left
alone (the OCR hand-off, atrium-digital-convert#4 W4;
[service/README.md](../service/README.md#merging-an-ocrd-page-into-a-born-digital-record-w4)). The CSV
outputs of the run are produced either way. `--source-origin ocr:<engine>` overrides the default when the
files are known OCR output.

digital-convert reads PDF, DOCX and, since its v1.1.0-beta, ODT, ODS, XLSX and RTF (with this repository's
`text_formats.py`), so born-digital records of the other kinds (PPTX, ODP, EPUB, HTML, e-mail) keep `source`
only. The lines digital-convert does write can be scored on its record by `POST /score_record`
([service/README.md](../service/README.md#scoring-a-record-post-score_record)). When files of such a kind really are OCR or transcription exports, say so per kind:
`[DOCUMENT].SOURCE_ORIGIN_BY_KIND = xlsx = ocr:generic, pptx = ocr:generic`. For text-lines the
precedence is CLI flag > env var > `SOURCE_ORIGIN_BY_KIND` > `SOURCE_ORIGIN` > per-format default
([docs/text_inputs.md §6](text_inputs.md#6-provenance-sourceorigin)).

Override it when the engine **is** known — the prefix must stay one this repo owns
(`ABBYY-ALTO`, `ocr:<engine>`, `vlm:<engine>`):

```
python3 page_split.py <input_dir> <output_dir> --source-origin ocr:pero
```

Also settable as `[DOCUMENT].SOURCE_ORIGIN` in [`setup/config.txt`](../setup/config.txt)
or via the `DOCUMENT_SOURCE_ORIGIN` env var, so orchestrated `run_pipeline.py` runs
(which invoke this script with no extra CLI flags) can set it too. Precedence: CLI flag >
env var > config value > per-format default.

## Step 2: page statistics

Next, use the output directory from Step 1 as the input for this script to generate a
foundational CSV statistics file.

```
python3 alto_stats_create.py <input_dir> -o output.csv     # ALTO XML
python3 json_stats_create.py <input_dir> -o output.csv     # generic JSON (json-keys)
python3 text_stats_create.py <input_dir> -o output.csv     # text-lines (#31)
```

The three builders write the same columns. `text_stats_create.py` takes the document id from the
`<doc_id>/` folder, so ids containing `-` stay whole. It counts non-blank lines as `textlines`,
words as `strings` and PDF image objects as `illustrations`.

The columns are listed in [the output reference](reference/outputs.md#step-2-page-statistics).

The element counting is powered by the alto-tools statistics code (https://github.com/cneud/alto-tools),
**vendored** in [alto_tools.py](../alto_tools.py) (Apache-2.0) — see [Vendored code](development.md#vendored-code).
No external binary is called and nothing is downloaded at run time.

* **Input:** `../PAGE_ALTO/` (input directory with ALTO XML files split into pages from Step 1)
* **Output:** `output.csv` (table with page-level statistics and paths to ALTO files)

> [!IMPORTANT]
> This statistics table is the basis for subsequent processing steps.
> Example: [test_alto_stats.csv](../data_samples/test_alto_stats.csv) .

## Step 3: extract the text

This script runs in parallel (using multiple CPU cores) to extract text from ALTO XMLs into `.txt` files.
It reads the CSV from Step 2 — `--input-csv <stats.csv>`, else `[EXTRACT].INPUT_CSV` — and every
extractor keeps document ids such as `0001` as text. A page whose `.txt` is older than its input page
file is extracted again on the next run; one that is current is skipped (resume).

* **Input 1:** `output.csv` (from Step 2)
* **Input 2:** `../PAGE_ALTO/` (input directory with ALTO XML files split into pages from Step 1)
* **Output:** `../PAGE_TXT/` or `../PAGE_TXT_LR/` (directory containing raw text files)

### LayoutReader method (the default)

> [!CAUTION]
> The model responsible for spatial layout analysis requires a GPU to run efficiently.

```
python3 extract_LytRdr_ALTO_2_TXT.py
```

Uses the LayoutReader framework (https://github.com/ppaanngggg/layoutreader) to extract text and bounding boxes of XML elements
(specifically, `<TextLine>` elements containing `String`s with `CONTENT` attribute),
process them to reconstruct the reading order of lines (columns-friendly), handle words split
between two lines (adding the full form of the word), and group page contents into paragraphs
based on the vertical spread of text lines.

Example of per-page text files: [PAGE_TXT_LR](../data_samples/PAGE_TXT_LR) .

```
PAGE_TXT_LR/
├── <file1>
│   ├── <file1>-<page>.txt
│   └── ...
├── <file2>
│   ├── <file2>-<page>.txt
│   └── ...
└── ...
```

### alto-tools method

> [!NOTE]
> The method is CPU-bound and faster than the LayoutReader method, but the text lines may not be in the correct
> reading order, and full forms of hyphenated split words are not reconstructed.

```
python3 extract_ALTO_2_TXT.py
```

Uses the `alto-tools` text extractor (https://github.com/cneud/alto-tools) — **vendored** in
[alto_tools.py](../alto_tools.py) (Apache-2.0), see [Vendored code](development.md#vendored-code) — to extract text lines from
XML elements directly, with no post-processing. Suitable for a quick overview of raw text content.
The output is byte-identical to what the upstream `alto-tools -t` command line produced.

Example of per-page text files: [PAGE_TXT](../data_samples/PAGE_TXT) .

```
PAGE_TXT/
├── <file1>
├── <file2>
│   ├── <file2>-<page>.txt
│   └── ...
└── ...
```

### GLM method

> [!WARNING]
> The method is GPU-bound, slower than the LayoutReader method, and requires a `gpuram48G` card.

```
python3 extract_LLM_ALTO_2_TXT.py
```

Uses the GLM-4v-9b multimodal large language model (https://huggingface.co/THUDM/glm-4v-9b) to perform generative OCR directly from
page images, prompted as `Transcribe all text on this page exactly as it appears`. The script
trims whitespace and resizes high-resolution images to fit model constraints.

> [!NOTE]
> This method is significantly slower than parsing XML but often yields higher quality text for complex
> layouts or degraded scans. It patches the transformers configuration to run the GLM-4v architecture.

Example of per-page text files: [PAGE_TXT_LLM](../data_samples/PAGE_TXT_LLM) .

```
PAGE_TXT_LLM/
├── <file1>
├── <file2>
│   ├── <file2>-<page>.txt
│   └── ...
└── ...
```

### json-keys method (generic JSON)

> [!NOTE]
> Use this method with the [Generic JSON input](#generic-json-input) split from Step 1 —
> the other three methods above all consume ALTO XML instead.

```
python3 extract_JSON_2_TXT.py
```

Reads each page's generic OCR/Doc-AI JSON and walks a whitelist of informative keys
(`content`, `text`, `line`, `word`, ... — no assumption about a particular vendor's schema
beyond "text lives under a key named roughly `text`/`line`/`word`"), yielding the page's text
in document order, each text once at line granularity (see the note below). This is the **Extraction Layer**: it makes no other change to the
JSON and does not know about `doc.json` at all.

Example input: [data_samples/JSON](../data_samples/JSON) (an Azure-style `analyzeResult.pages` document).

> [!NOTE]
> json-keys re-serialises each split page **with its document header**, but reads only the page:
> the page object a page file holds (Azure's whole-document `analyzeResult.content` no longer
> opens every page), each text once at line granularity (a line's `words[]` are not repeated
> after it; AWS Textract's `WORD` blocks are dropped when `LINE` blocks exist) — the rules the
> [text-lines method](#text-lines-method) uses for the
> same JSON (#31 Phase 5). On real AWS Textract responses and Microsoft's Azure example result both
> methods give exactly the engine's `LINE`s per page; json-keys used to write 3–6× as many lines.

```
PAGE_TXT_JSON/
├── <file1>
│   ├── <file1>-<page>.txt
│   └── ...
├── <file2>
│   ├── <file2>-<page>.txt
│   └── ...
└── ...
```

#### Accreting into the document record

When [`[DOCUMENT].JSON_DIR`](reference/paradata.md) (or the `DOCUMENT_JSON_DIR` env var) is
configured, a second, separate **Accretion Layer** runs after extraction: for every document,
it reads back that document's already-written `.txt` pages and merges them into
`<doc_id>.document.json` as this repo's owned fields — `pages[].ocr`
(`ocr-postprocess`'s share of the field-split `pages` block) and the whole `content` block —
leaving every other block (`page_categories`, `entities`, `source`, ...) untouched, per the
`atrium_document.schema.json` paired-hook contract. With no baseline `doc.json` yet on disk,
the record is created holding just this contribution (accretion rule 3). This mirrors what
the other three extraction methods above already do.

`--force-single-page` is a **document-assembly policy**, not an extraction-rule change — it
only decides how many `pages[]` rows describe the pages already extracted above:

```
python3 extract_JSON_2_TXT.py --force-single-page
```

|                       | `pages[]` shape                                                                                                                             | `content.text`                                                           |
|-----------------------|---------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| default               | one row per source page (`page: "1"`, `"2"`, ...)                                                                                           | always the full document: every page's text concatenated in source order |
| `--force-single-page` | every source page collapses into **one** row (`page: "1"`), with `ocr.source_pages` listing the original page labels in concatenation order | unchanged — same joined text either way                                  |

`--force-single-page` can also be set as a config-file default —
`[EXTRACT].FORCE_SINGLE_PAGE_JSON = true` in [`setup/config.txt`](../setup/config.txt) — so
`run_pipeline.py` orchestrated runs (which invoke this script with no extra CLI flags) can
still opt in. The CLI flag takes precedence over the config value when both are given.

### text-lines method

> [!NOTE]
> Use this method with [`text_split.py`](#any-other-text-bearing-input) output from Step 1
> and `text_stats_create.py` from Step 2.

```
python3 extract_TEXT_2_TXT.py [--input-csv CSV] [--output-dir DIR] [--lines-dir DIR] [--strict | --no-strict]
```

Prepares each page's text for categorization. Lines are normalized (NFC; control, zero-width
and bidi characters removed; soft hyphens resolved). Blank lines are dropped
(`[TEXT_INGEST].KEEP_BLANK_LINES`). Any line longer than `[TEXT_INGEST].MAX_LINE_CHARS`
(default 1000) is wrapped at a word boundary, because one giant paragraph would otherwise pad a
whole perplexity batch to the model's full context. A paragraph, cell or physical line otherwise
stays **one line**.

* **Output:** `../PAGE_TXT_TEXT/<file>/<file>-<page>.txt` (what Step 4 reads)
* **Output:** `../DOC_LINES_TEXT/<file>.csv`: the **line table**, i.e. the input's ordered
  text lines as CSV rows (`file,page_num,line_num,text,page_label`) before any categorization.
  Its `page_num`/`line_num` are exactly the numbers Step 4 gives the same lines in `DOC_LINE_CATEG`.
  The script refuses to write it into the `DOC_LINE_CATEG` directory.

Page files in any encoding are accepted, so a hand-made directory of page TXT files works
with `--skip-split`. With `[DOCUMENT].JSON_DIR` set, `pages[].ocr` (`engine: text-lines`) and
`content` are accreted like the other methods (not into born-digital records, see Step 1). A
page, a line table or a document record that fails costs that page or document only; `--strict`
(or `[TEXT_INGEST].STRICT`) turns such a failure into exit code 1.

## Step 4.1: classify lines

This script reads the extracted text files, batches lines together, and runs the FastText and
Qwen2.5-0.5B models. It uses a CPU/GPU split architecture:

* A single dedicated GPU worker holds the only Qwen2.5-0.5B instance and processes perplexity
  batches to prevent VRAM OOM errors. A batch that does run out of memory is **split in halves and retried**; only a
  line that fails even on its own is scored 99999.
* Multiple CPU workers (up to `WORKERS_MAX`, default 32) read files, run the pre-filter, FastText and the
  structural signals, and submit text batches to the GPU worker via a shared queue. CPU workers poll the
  result dictionary while the GPU processes, running **language identification** concurrently.

> [!WARNING]
> The **first** item of the `EXPECTED_LANGS` list should be the most expected language in the processed collection: it
> is the default replacement of unknown language predictions.

```bash
python3 classify_TEXT.py [--input-csv <stats.csv>]

```

* **Input 1:** `../PAGE_TXT/` from Step 3
* **Input 2:** `output.csv` from Step 2 (`--input-csv`, else `[CLASSIFY].INPUT_CSV`)
* **Output:** `DOC_LINE_LANG_CLASS/` containing per-document **CSVs** (e.g., [DOC_LINE_CATEG](../data_samples/DOC_LINE_CATEG))
* **Exit status:** non-zero when the stage did not complete: every document failed, or the GPU worker was lost, or the
  scoring constants are invalid. Some documents failing while the rest are classified exits 0 (see the paradata's
  `skipped_files_detail`).

> [!TIP]
> This script is resume-capable. If interrupted, run it again and documents whose output CSV is at least as new as all of
> their page texts are skipped; a document with a page text newer than its output (re-ingested, re-extracted) is
> classified again. A document's rows are built in `<doc>.csv.part` and renamed once the document is complete, so a
> `<doc>.csv` that exists is a finished one and a run that died leaves nothing a resume could mistake for it. Output
> CSVs of documents that are not in the page CSV are listed in a warning, never deleted: `aggregate_STAT.py` aggregates
> every CSV in the directory. The Step 3 extractors resume the same way, page by page, and Steps 1 and 3 rewrite a page
> file only when its content changed, so a full re-run over unchanged inputs skips everything again.

The columns, the diagnostic flags and what they mean are in
[the output reference](reference/outputs.md#step-41-per-line-categories); the rules behind `categ` in
[How a line is categorised](categorization_logic.md).

Example of per-document CSV files: [DOC_LINE_CATEG](../data_samples/DOC_LINE_CATEG) by Qwen2.5-0.5B
and [DOC_LINE_CATEG_gpt](../data_samples/DOC_LINE_CATEG_gpt) by distilgpt2.

```
DOC_LINE_LANG_CLASS/
├── <docname1>.csv
├── <docname2>.csv
└── ...
```

## Step 4.2: aggregate page statistics

This script processes the `DOC_LINE_LANG_CLASS/` directory with CSV files in chunks to produce
final page-level statistics. It is CPU-bound and parallelized with `ProcessPoolExecutor`.

```
python3 aggregate_STAT.py
```

* **Input:** `DOC_LINE_LANG_CLASS/` (directory with CSV files from the previous step)
* **Output 1:** `final_page_stats.csv` (configurable via `OUTPUT_STATS`) — global page-level summary across all documents
* **Output 2:** `DOC_LINE_STAT/` (configurable via `OUTPUT_DOC_DIR`) — per-document **CSVs** with the same schema

The page-level schema is in [the output reference](reference/outputs.md#step-42-page-statistics).

All numeric averages are rounded to 4 decimal places; totals are stored as integers.

* *Examples*: [arup_page_stats_SHORT.csv](../data_samples/arup_page_stats_SHORT.csv), [arub_page_stats_SHORT.csv](../data_samples/arub_page_stats_SHORT.csv)

Example of per-document aggregate CSV files: [DOC_LINE_STATS](../data_samples/DOC_LINE_STATS) by Qwen2.5-0.5B
and [DOC_LINE_STATS_gpt](../data_samples/DOC_LINE_STATS_gpt) by distilgpt2:

```
DOC_LINE_STAT/
├── stats_<docname1>.csv
├── stats_<docname2>.csv
└── ...
```

This is the end of the text quality classification and filtering step. You can now use [arup_page_stats_SHORT.csv](../data_samples/arup_page_stats_SHORT.csv) to
identify files that need another round of OCR or manual correction based on the line type counts. Pages with the
majority of **Clear** lines can be marked for further processing. The absence of clear lines combined with a high proportion
of **Trash** lines may also indicate handwritten content, which can be excluded before Handwritten Text Recognition (HTR) is applied.

## Calling the API instead

In addition to the batch pipeline, this repository ships with a FastAPI wrapper (`service/text_api.py`) that exposes
the core `text_util` quality classification engine over HTTP. The `/process` endpoint accepts ALTO XML,
plain-text, and generic JSON uploads (`task_type` `alto` / `text` / `json`, or `auto`-detected from the file
extension), returning the same per-line classification fields as the batch pipeline for all three formats.
(#31) It also accepts **every text-lines format** (PDF, DOCX, ODT, XLSX, PPTX, EPUB, RTF, HTML/hOCR, PAGE XML,
TEI, ABBYY/DjVu XML, Tesseract TSV, Markdown, CSV/TSV, JSONL, SRT/VTT, EML/MBOX, compressed files and ZIP bundles)
as `task_type` `document`. `auto` keeps `.txt`/`.json` as before and decides `.xml` and every other upload
**from its bytes**: an uncompressed ALTO root stays on the ALTO path, a file of a kind the service does not read is
a 415 `unsupported_media_type` naming the reader's code (a 400 before atrium-project#32 round 2), a supported file
that cannot be read — a document without a single text line
included (`no_text`) — is a 422, and one over a `[TEXT_INGEST]` size or count cap is a 413 `limit_exceeded`.
Document results carry `page`/`page_label` per line and a `pages` summary, and are read and shaped by the same
`text_formats.py` code and the same `[TEXT_INGEST]` settings as the batch method; a `.txt` upload
is decoded the same way (any common encoding, cp1250 first).

The batch pipeline and the API service share the same `text_util` categorization engine and `config.txt`
settings — including the default Qwen2.5-0.5B perplexity model — to ensure zero drift between local processing
and web uploads. Both run the CPU pre-filter first, so a numeric, code-like or blank line is answered `Non-text` or
`Empty` without a model call, as in the batch CSV. The document passes of [Step 4.1](#step-41-classify-lines)
(identical-text vote, surrounded Trash, page context, inverted-scan sweep) are applied by the batch pipeline only; the
service answers each line from its own signals. `POST /score_record` scores the lines of a digital-convert record in
place; its page figure averages the `Clear`, `Noisy` and `Trash` lines and leaves out the `Non-text` and `Empty`
ones.

For deployment instructions, endpoint specifications (`/process`, `/score_record`, `/info`), and frontend integration details,
please see the dedicated **[Service Documentation](../service/README.md)**.
