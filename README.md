<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11-blue.svg" title="Python Version"></a>
  <a href="https://huggingface.co/facebook/fasttext-language-identification"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20HF-fasttext--langID-yellow.svg" title="FastText Language Identification"></a>
  <a href="https://huggingface.co/Qwen/Qwen2.5-0.5B"><img src="https://img.shields.io/badge/%F0%9F%A4%97%20HF-Qwen2.5--0.5B-yellow.svg" title="Qwen2.5-0.5B Perplexity"></a>
  <a href="https://github.com/cneud/alto-tools"><img src="https://img.shields.io/badge/vendored-alto--tools-lightgrey.svg" title="alto-tools (vendored, Apache-2.0)"></a>
  <a href="https://opensource.org/license/mit/"><img src="https://img.shields.io/github/license/ufal/atrium-ocr-postprocess" title="MIT License"></a>
  <a href="https://atrium-research.eu/"><img src="https://img.shields.io/badge/funded%20by-ATRIUM-8A2BE2.svg" title="ATRIUM Project"></a>
</p>

---

# 📦 OCR Output Postprocessing Pipeline

This project provides a complete workflow for processing **OCR output** 📄 and other text-bearing
files. It takes the output of OCR — ALTO XML, PAGE XML, hOCR, ABBYY FineReader XML, OCR JSON — and
transforms it into structured **statistics tables** 📊, performs text classification, and filters
low-quality **OCR** 🔍 results. ALTO XML, the format of the PSNC corpus, stays the worked example
throughout this README.

> [!NOTE]
> **Formerly `atrium-alto-postprocess`.** On 1 October 2026 the project continued in this repository under
> its new name, with the pipeline, the scripts, the categories and the scoring unchanged (the ALTO-specific
> scripts keep their names: they name steps that really are ALTO-specific). What changed: the repository and image
> names (`ghcr.io/ufal/atrium-ocr-postprocess`, `…-api`), the service id (`atrium-ocr-postprocess`), and the program id
> this tool stamps into the document record (`ocr-postprocess`; records written before keep `alto-postprocess`, which the
> record contract accepts as the same writer). The images of the old repository stay published for consumers pinned to
> them and receive no new tags; its issues and releases remain readable there.

The core of the quality filtering relies on **language identification** 🌐 and a composite **quality
score** 📈 — combining structural detectors, **perplexity** 📉, and character-level metrics — to identify
and categorize noisy or unreliable **OCR** 🔍 output.

Besides ALTO, the same categorization takes the other OCR formats (PAGE XML, hOCR, ABBYY FineReader
XML, DjVuXML, Tesseract TSV, OCR/Doc-AI JSON) and PDF, office, e-mail and plain-text files — see
[Step 1](#-step-1-split-document-specific-inputs-into-pages-) and the
[input formats reference 📚](docs/text_inputs.md#formats-and-their-standards).

---

## 📖 Table of Contents

- [⚙️ Setup](#-setup)
- [🛤️ Workflow Stages](#-workflow-stages)
  - [🚀 Run the whole pipeline at once](#-run-the-whole-pipeline-at-once)
  - [Step 1: Split Document-Specific Inputs into Pages ✂️](#-step-1-split-document-specific-inputs-into-pages-)
    - [Any other text-bearing input: PDF, DOCX, TXT, … (#31) 📄](#any-other-text-bearing-input-pdf-docx-txt--31-)
      - [Input formats reference 📚](docs/text_inputs.md)
  - [Step 2: Create Page Statistics Table 📈](#-step-2-create-page-statistics-table-)
  - [Step 3: Extract text from ALTO XML ⛏️](#-step-3-extract-text-from-alto-xml-)
    - [LayoutReader method 📐](#1st-choice-layoutreader-method-)
    - [alto-tools method 🧰](#2nd-option-alto-tools-method-)
    - [GLM method 🤖](#3rd-alternative-glm-method-llm-based-)
    - [json-keys method 🔑](#4th-alternative-json-keys-method-generic-json-input-31--37-)
    - [text-lines method 📄](#5th-alternative-text-lines-method-any-text-bearing-input-31-)
  - [Step 4: Classify Page Text Quality & Language 🗂️](#-step-4-classify-page-text-quality--language-)
    - [4.1 Classify Lines (GPU Bound) 🚀](#41-classify-lines-gpu-bound-)
      - [Categorisation logic reference 📚](docs/categorization_logic.md)
    - [4.2 Aggregate Statistics (Memory Bound) 🧠](#42-aggregate-statistics-memory-bound-)
  - [Paradata logging 🗒️](#paradata-logging)
    - [Output licensing ⚖️](#output-licensing-)
    - [Document record schema 📑](#document-record-schema-)
- [Vendored code 📦](#vendored-code-)
- [Acknowledgements 🙏](#acknowledgements-)

---

## ⚙️ Setup

Before you begin, set up your environment.

1.  Create and activate a new **virtual environment** 🖥️ in the project directory.
2.  Install the required **Python** 🐍 packages:
    ```bash
    pip install -r setup/requirements.txt
    ```
3. Download the **FastText** 🌐 model for language identification:
    ```bash
    wget "https://huggingface.co/facebook/fasttext-language-identification/resolve/main/model.bin" -O lid.176.bin
    ```
    The file is the Hub's `model.bin` — the NLLB fastText language-ID model, 1.18 GB, CC BY-NC 4.0 —
    saved under the name the pipeline's config expects (`FASTTEXT_MODEL = lid.176.bin`); it is not the
    126 MB fastText `lid.176.bin`. The Docker image fetches the same file pinned to a Hub revision and
    checked against its SHA-256 (the `FASTTEXT_REVISION` / `FASTTEXT_SHA256` build args in the
    [Dockerfile](Dockerfile) 📎).
4. Copy the `v3` folder from the 📐`layoutreader` 🔧 repository [^9] to the project directory for the LR-based text extraction method:
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
> There is no `alto-tools` 🔧 install step any more. The statistics and text-extraction code paths this
> pipeline used are **vendored** in [alto_tools.py](alto_tools.py) 📎 — see [Vendored code 📦](#vendored-code-).

You are now ready to start the workflow.

---


## 🛤️ Workflow Stages

The process is divided into sequential steps, starting from raw **ALTO** 📄 files — or generic
OCR **JSON**, or (#31) any other text-bearing file: PDF, DOCX, ODT, XLSX, PPTX, EPUB, RTF,
HTML/hOCR, PAGE XML, TEI, ABBYY/DjVu XML, Tesseract TSV, Markdown, CSV, subtitles, e-mail or
plain text, also compressed or as a ZIP of page files — and ending with extracted linguistic
and statistic data 📊.

You can run the **entire pipeline end-to-end** with a single command (see below), or run each
stage individually as described in Steps 1–4.

---

### 🚀 Run the whole pipeline at once

The [run_pipeline.py](run_pipeline.py) 🐍 orchestrator runs every stage sequentially
(**split → statistics → text extraction → classification → aggregation**) and, at the end,
**merges all per-stage paradata** 🗒️ logs into a single run summary describing every stage, the
intermediate file formats produced, and the **effective end-to-end output license** ⚖️ (see
[Paradata logging](#paradata-logging)).

```bash
python3 run_pipeline.py                      # all settings from config.txt
python3 run_pipeline.py --method glm         # override just the extraction backend
python3 run_pipeline.py --method json-keys --input-dir data_samples/JSON   # generic OCR JSON (#31)
python3 run_pipeline.py --method text-lines  # PDF/DOCX/TXT/... from [PIPELINE].INPUT_DIR_TEXT (#31)
python3 run_pipeline.py --skip-split         # PAGE_ALTO already populated
python3 run_pipeline.py --dry-run            # print the resolved plan, run nothing
```

* **Configuration ⚙️:** every setting is read from [config.txt](setup/config.txt) 📎
(section `[PIPELINE]`, with `INPUT_CSV` taken from `[EXTRACT]`). Precedence is
**CLI flag > config value > built-in default**. Point at a different config with `--config`
or the `LANGID_CONFIG` environment variable.
* **Extraction method 🔀:** `[PIPELINE] METHOD` selects the **Step 3** backend —
`alto-tools`, `layoutreader` (**default**), or `glm` for ALTO XML; `json-keys` for generic OCR
JSON; `text-lines` for every other text-bearing format (#31). The method also selects the
Step 1/2 scripts for its input format. The choice flows through to the merged license: a
**LayoutReader** 📐 run resolves to **CC BY-NC-SA 4.0**, an **alto-tools** 🧰 run to
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
* **Output 📤:** a merged `<YYMMDD-HHmmss>_pipeline-run.json` in the [paradata](paradata) 📁
directory, alongside the individual per-stage logs.

> [!NOTE]
> Every stage — Step 1 (`page_split.py` / `text_split.py`) included — writes its own paradata
> log, so a full run merges **five** logged stages, and the dry-run tags all five `[paradata]`.
> The merged license is re-derived from the **union** of components used across all stages, so
> the end-to-end most-restrictive rule holds.

> [!TIP]
> Prefer to inspect or re-run a single stage? The individual scripts below remain fully usable on
> their own — the orchestrator simply calls them in order.

---

### ▶️ Step 1: Split Document-Specific Inputs into Pages ✂️

First, ensure you have a directory 📁 containing your document-level input files. This script
will split them into individual page-specific files — it supports **both** ALTO XML and generic
JSON, dispatched automatically by file extension. Every other text-bearing format goes through
[`text_split.py`](#any-other-text-bearing-input-pdf-docx-txt--31-) instead (`--method text-lines`).

```
python3 page_split.py <input_dir> <output_dir>
```

Each page-specific file retains the header from its original source document 📌.

#### ALTO XML input

* **Input 📥:** `../ALTO/` (input directory with **ALTO XML** 📄 documents)
* **Output 📤:** `../PAGE_ALTO/` (output directory with **ALTO XML** 📄 files split into pages)

> [!NOTE]
> `page_split.py` splits every ALTO version: the namespace is the root element's (v2, v3 — what
> the ATRIUM ABBYY exports use — v4, BnF), and each page file keeps it. A `.xml` whose root is not a
> namespaced `<alto>` (PAGE XML, TEI, namespace-less ALTO) is skipped with its root named; the
> [text-lines method](#any-other-text-bearing-input-pdf-docx-txt--31-) reads those. A re-split
> replaces a document's page files: pages the input no longer yields are removed, also when the
> input now fails. Tesseract numbers its ALTO pages from 0 (`PHYSICAL_IMG_NR="0"`), so its page
> files are `<doc>-0.alto.xml`, `<doc>-1.alto.xml`, …

Example of the output directory with divided per-page XML files: [PAGE_ALTO](data_samples/PAGE_ALTO) 📁.

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

#### Generic JSON input (#31)

Real OCR/Doc-AI engines don't agree on how they represent multi-page documents, so the JSON
path is detected heuristically rather than assuming one vendor's schema: a nested page-list
container (e.g. Azure Document Intelligence's/docTR's `pages` array), a flat element list
tagged with a per-item page field (e.g. AWS Textract), or — when neither pattern is found —
today's single-page-per-file default (e.g. pero-ocr, OCR.space).

* **Input 📥:** `../JSON/` (input directory with generic JSON OCR-engine 📄 documents)
* **Output 📤:** `../PAGE_JSON/` (output directory with **JSON** 📄 files split into pages)

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

#### Any other text-bearing input: PDF, DOCX, TXT, … (#31) 📄

```
python3 text_split.py <input_dir> <output_dir> [--source-origin ocr:<engine>] [--strict | --no-strict]
```

The `<format>-2-txt` step for everything that is not ALTO or json-keys JSON: **PDF** (text
layer), **DOCX**, **ODT/ODS/ODP**, **XLSX**, **PPTX**, **EPUB**, **RTF**, **HTML/XHTML** and
**hOCR**, **PAGE XML**, **TEI/TEITOK**, the OCR exports **ABBYY FineReader XML**, **DjVuXML** and
**Tesseract TSV**, any other **XML**, **JSON/JSONL**, **CSV/TSV**, **Markdown**, **SRT/WebVTT**
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
([Formats and their standards](docs/text_inputs.md#formats-and-their-standards)), the full
per-format matrix, the normalisation rules, the limits and the reason codes are in
**[docs/text_inputs.md](docs/text_inputs.md)** 📚.

* **Input 📥:** `../TEXT/` (any mix of the formats above — example: [data_samples/TEXT](data_samples/TEXT) 📁)
* **Output 📤:** `../PAGE_TEXT/` — `<doc_id>/<doc_id>-<n>.txt` per page, plus two reports:
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
[config.txt](setup/config.txt) 📎; each can also be set as the environment variable
`ATRIUM_TEXT_INGEST_<KEY>`, which wins over the file (atrium-project#53; the full list is
[service/README.md § Limits](service/README.md#limits)).

```
PAGE_TEXT/
├── ingest_report.csv
├── pages_report.csv
├── <file1>
│   ├── <file1>-1.txt
│   └── ...
└── ...
```

#### `source.origin` — how the original input was acquired

This is the pipeline's **first** stage to see the original file, so when
`[DOCUMENT].JSON_DIR` is configured it is also the first writer of the record's `source`
block (`sha256`, `filename`, `media_type`, `page_count`, `origin`). `source` is
immutable — first writer wins — and since the `atrium_document` §1a hardening its
`origin` is what **authorises** this repo to write the positional blocks
(`pages`/`content`/`lines`/`tables`), so that no document can end up with half an
OCR-derived plane and half a digital-born one. A value that matches no known originator
prefix silently switches that check off, which is why the resolution is verified and a
mismatch warns 📣.

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
[service/README.md](service/README.md#merging-an-ocrd-page-into-a-born-digital-record-w4)). The CSV
outputs of the run are produced either way. `--source-origin ocr:<engine>` overrides the default when the
files are known OCR output.

digital-convert reads PDF, DOCX and, since its v1.1.0-beta, ODT, ODS, XLSX and RTF (with this repository's
`text_formats.py`), so born-digital records of the other kinds (PPTX, ODP, EPUB, HTML, e-mail) keep `source`
only. The lines digital-convert does write can be scored on its record by `POST /score_record`
([service/README.md](service/README.md#scoring-a-record-post-score_record)). When files of such a kind really are OCR or transcription exports, say so per kind:
`[DOCUMENT].SOURCE_ORIGIN_BY_KIND = xlsx = ocr:generic, pptx = ocr:generic`. For text-lines the
precedence is CLI flag > env var > `SOURCE_ORIGIN_BY_KIND` > `SOURCE_ORIGIN` > per-format default
([docs/text_inputs.md §6](docs/text_inputs.md#6-provenance-sourceorigin)).

Override it when the engine **is** known — the prefix must stay one this repo owns
(`ABBYY-ALTO`, `ocr:<engine>`, `vlm:<engine>`):

```
python3 page_split.py <input_dir> <output_dir> --source-origin ocr:pero
```

Also settable as `[DOCUMENT].SOURCE_ORIGIN` in [`setup/config.txt`](setup/config.txt) 📎
or via the `DOCUMENT_SOURCE_ORIGIN` env var, so orchestrated `run_pipeline.py` runs
(which invoke this script with no extra CLI flags) can set it too. Precedence: CLI flag >
env var > config value > per-format default.

---

### ▶️ Step 2: Create Page Statistics Table 📈

Next, use the output directory from Step 1 as the input for this script to generate a
foundational **CSV** 📊 statistics file.

```
python3 alto_stats_create.py <input_dir> -o output.csv     # ALTO XML
python3 json_stats_create.py <input_dir> -o output.csv     # generic JSON (json-keys)
python3 text_stats_create.py <input_dir> -o output.csv     # text-lines (#31)
```

The three builders write the same columns. `text_stats_create.py` takes the document id from the
`<doc_id>/` folder, so ids containing `-` stay whole. It counts non-blank lines as `textlines`,
words as `strings` and PDF image objects as `illustrations`.

This script writes a **CSV** 📊 file line-by-line, capturing metadata for each page:

```
file, page, textlines, illustrations, graphics, strings, path
CTX200205348, 1, 33, 1, 10, 163, /lnet/.../A-PAGE/CTX200205348/CTX200205348-1.alto.xml
CTX200205348, 2, 0, 1, 12, 0, /lnet/.../A-PAGE/CTX200205348/CTX200205348-2.alto.xml
...

```

The element counting is powered by the **alto-tools** 🔧 statistics code [^1](https://github.com/cneud/alto-tools),
**vendored** in [alto_tools.py](alto_tools.py) 📎 (Apache-2.0) — see [Vendored code 📦](#vendored-code-).
No external binary is called and nothing is downloaded at run time.

* **Input 📥:** `../PAGE_ALTO/` (input directory with **ALTO XML** 📄 files split into pages from Step 1)
* **Output 📤:** `output.csv` (table with page-level statistics and paths to ALTO files)

> [!IMPORTANT]
> This statistics table is the basis for subsequent processing steps.
> Example: [test_alto_stats.csv](data_samples/test_alto_stats.csv) 📎.

---

### ▶️ Step 3: Extract text from ALTO XML ⛏️

This script runs in parallel ⚡ (using multiple **CPU** 💻 cores) to extract text from **ALTO XMLs** 📄 into `.txt` 📝 files.
It reads the **CSV** 📊 from Step 2 — `--input-csv <stats.csv>`, else `[EXTRACT].INPUT_CSV` — and every
extractor keeps document ids such as `0001` as text. A page whose `.txt` is older than its input page
file is extracted again on the next run; one that is current is skipped (resume).

* **Input 1 📥:** `output.csv` (from Step 2)
* **Input 2 📥:** `../PAGE_ALTO/` (input directory with **ALTO XML** 📄 files split into pages from Step 1)
* **Output 📤:** `../PAGE_TXT/` or `../PAGE_TXT_LR/` (directory containing raw **text** 📝 files)

#### 1st choice: LayoutReader method 📐

> [!CAUTION]
> The model responsible for spatial layout 📐 analysis requires a **GPU** 🚀 to run efficiently.

```
python3 extract_LytRdr_ALTO_2_TXT.py
```

Uses the **LayoutReader** 📐 framework [^9](https://github.com/ppaanngggg/layoutreader) to extract text and bounding boxes of **XML** 📄 elements
(specifically, `<TextLine>` elements containing `String`s with `CONTENT` attribute),
process them to reconstruct the reading order of lines (columns-friendly), handle words split
between two lines (adding the full form of the word), and group page contents into paragraphs
based on the vertical spread of text lines.

Example of per-page text files: [PAGE_TXT_LR](data_samples/PAGE_TXT_LR) 📁.

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

---

#### 2nd option: alto-tools method 🧰

> [!NOTE]
> The method is **CPU** 💻-bound and faster than the LayoutReader method, but the text lines may not be in the correct
> reading order, and full forms of hyphenated split words are not reconstructed.

```
python3 extract_ALTO_2_TXT.py
```

Uses the `alto-tools` 🔧 text extractor [^1](https://github.com/cneud/alto-tools) — **vendored** in
[alto_tools.py](alto_tools.py) 📎 (Apache-2.0), see [Vendored code 📦](#vendored-code-) — to extract text lines from
**XML** 📄 elements directly, with no post-processing. Suitable for a quick overview of raw text content.
The output is byte-identical to what the upstream `alto-tools -t` command line produced.

Example of per-page text files: [PAGE_TXT](data_samples/PAGE_TXT) 📁.

```
PAGE_TXT/
├── <file1>
├── <file2>
│   ├── <file2>-<page>.txt
│   └── ...
└── ...
```

---

#### 3rd alternative: GLM method (LLM-based) 🤖

> [!WARNING]
> The method is **GPU** 🚀-bound, slower than the LayoutReader method, and requires a `gpuram48G` card.

```
python3 extract_LLM_ALTO_2_TXT.py
```

Uses the **GLM-4v-9b** 🤖 multimodal large language model [^10](https://huggingface.co/THUDM/glm-4v-9b) to perform generative **OCR** 🔍 directly from
page images, prompted as `Transcribe all text on this page exactly as it appears`. The script
trims whitespace and resizes high-resolution images to fit model constraints.

> [!NOTE]
> This method is significantly slower than parsing **XML** 📄 but often yields higher quality text for complex
> layouts 📐 or degraded scans. It patches the transformers configuration to run the GLM-4v architecture.

Example of per-page text files: [PAGE_TXT_LLM](data_samples/PAGE_TXT_LLM) 📁.

```
PAGE_TXT_LLM/
├── <file1>
├── <file2>
│   ├── <file2>-<page>.txt
│   └── ...
└── ...
```

---

#### 4th alternative: json-keys method (generic JSON input, #31 / #37) 🔑

> [!NOTE]
> Use this method with the [Generic JSON input](#generic-json-input-31) split from Step 1 —
> the other three methods above all consume **ALTO XML** 📄 instead.

```
python3 extract_JSON_2_TXT.py
```

Reads each page's generic OCR/Doc-AI **JSON** 📄 and walks a whitelist of informative keys
(`content`, `text`, `line`, `word`, ... — no assumption about a particular vendor's schema
beyond "text lives under a key named roughly `text`/`line`/`word`"), yielding the page's text
in document order, each text once at line granularity (see the note below). This is the **Extraction Layer**: it makes no other change to the
JSON and does not know about `doc.json` at all.

Example input: [data_samples/JSON](data_samples/JSON) 📁 (an Azure-style `analyzeResult.pages` document).

> [!NOTE]
> json-keys re-serialises each split page **with its document header**, but reads only the page:
> the page object a page file holds (Azure's whole-document `analyzeResult.content` no longer
> opens every page), each text once at line granularity (a line's `words[]` are not repeated
> after it; AWS Textract's `WORD` blocks are dropped when `LINE` blocks exist) — the rules the
> [text-lines method](#5th-alternative-text-lines-method-any-text-bearing-input-31-) uses for the
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

##### `doc.json` accretion (issue #37)

When [`[DOCUMENT].JSON_DIR`](#paradata-logging) (or the `DOCUMENT_JSON_DIR` env var) is
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
`[EXTRACT].FORCE_SINGLE_PAGE_JSON = true` in [`setup/config.txt`](setup/config.txt) — so
`run_pipeline.py` orchestrated runs (which invoke this script with no extra CLI flags) can
still opt in. The CLI flag takes precedence over the config value when both are given.

---

#### 5th alternative: text-lines method (any text-bearing input, #31) 📄

> [!NOTE]
> Use this method with [`text_split.py`](#any-other-text-bearing-input-pdf-docx-txt--31-) output from Step 1
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

* **Output 📤:** `../PAGE_TXT_TEXT/<file>/<file>-<page>.txt` (what Step 4 reads)
* **Output 📤:** `../DOC_LINES_TEXT/<file>.csv`: the **line table**, i.e. the input's ordered
  text lines as CSV rows (`file,page_num,line_num,text,page_label`) before any categorization.
  Its `page_num`/`line_num` are exactly the numbers Step 4 gives the same lines in `DOC_LINE_CATEG`.
  The script refuses to write it into the `DOC_LINE_CATEG` directory.

Page files in any encoding are accepted, so a hand-made directory of page TXT files works
with `--skip-split`. With `[DOCUMENT].JSON_DIR` set, `pages[].ocr` (`engine: text-lines`) and
`content` are accreted like the other methods (not into born-digital records, see Step 1). A
page, a line table or a document record that fails costs that page or document only; `--strict`
(or `[TEXT_INGEST].STRICT`) turns such a failure into exit code 1.

---

### ▶️ Step 4: Classify Page Text Quality & Language 🗂️

This is a key ⌛ time-consuming step that analyzes the **text quality** 📈 of each page line-by-line,
assigning each line a quality category to filter out **OCR** 🔍 noise.

It uses the [FastText language identification model](https://huggingface.co/facebook/fasttext-language-identification) 🌐
and **perplexity** 📉 scores from [Qwen2.5-0.5B](https://huggingface.co/Qwen/Qwen2.5-0.5B) 🤖 to detect noise [^2](https://huggingface.co/facebook/fasttext-language-identification) [^6](https://huggingface.co/Qwen/Qwen2.5-0.5B).

More post-processing of **TXT** 📝 files can be found in the [GitHub repository](https://github.com/ufal/atrium-nlp-enrich)
of the ATRIUM project, which covers NLP enrichment using Nametag for NER and UDPipe for CONLL-U files with lemmas & POS tags [^5](https://github.com/ufal/atrium-nlp-enrich).

> [!TIP]
> This section is the operational view. **How a line becomes a category, rule by rule**, with its exceptions,
> the foreign-language behaviour and a table of worked examples, is in
> **[docs/categorization_logic.md](docs/categorization_logic.md)**.

#### The five categories 🪧

| Category        | Action                                             | Meaning                                                                                                                                       |
|-----------------|----------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| ✅ **Clear**     | Ready to be processed by further NLP               | Legible: whatever mistakes it has, nobody needs the page image to read it.                                                                    |
| ⚠️ **Noisy**    | Corrections of generally readable words are needed | Easily decipherable through a mistake: a swapped letter, a digit inside a word, a few lost characters.                                        |
| 🗑️ **Trash**   | Should be re-processed by another **OCR** 🔍 tool  | **Illegible**: nobody can read it.                                                                                                            |
| 🔣 **Non-text** | May be checked for identifiers of finds/sites      | Structured but not language: numbers, codes, stamps, dates, measurements (the CPU 💻 pre-filter's verdict; no model is run for it).           |
| 🫙 **Empty**    | Can be ignored                                     | The line holds only whitespace (a paragraph separator).                                                                                       |

The definition is the data providers': **`Trash` means illegible**, whatever the line's usefulness, so a correctly
scanned web address is `Clear` (@david-spacil 2026-09-22; agreed by @DanaKriv 2026-10-01). `Non-text` cannot be told
from `Trash` without the page image and is out of scope for hand annotation. The program approximates this definition;
the [reference](docs/categorization_logic.md#overrides-and-exception-cases) lists where it does not.

#### How a line is categorised

1. **Pre-filter** 💻: blank → `Empty`; numbers, dates, codes, stamps and very short or symbol-only lines → `Non-text`;
   two OCR repairs (`poh1ed` → `pohled`); the rest goes on.
2. **Language** 🌐: FastText's label is kept, or replaced by the collection default when unknown; its confidence times
   the language's tier is the *trust score* the scorer reads.
3. **Signals and quality score** 📈: per-word and per-line measurements, and a weighted score in [0, 1] built from nine
   of them (valid words, weirdness, garbage density, perplexity, vowels, language, gibberish, fused words, length).
4. **Ordered gates**: the first that matches decides (address route, perplexity routes, inverted scan, all-caps scramble,
   garbage density, short garbage, short lines, low perplexity, six structural gates).
5. **Band routing**: below 0.55 → `Trash` (or a rescue); otherwise `Clear` or `Noisy` by the share of valid words and
   damaged tokens.
6. **Document passes** (batch pipeline only): an identical-text vote, surrounded-`Trash` smoothing, page-context rules
   and an inverted-scan sweep. They change the label, never the score.

> [!IMPORTANT]
> **The stored `quality_score` is clamped into the band of its category** (`Trash` < 0.55 ≤ `Noisy` < 0.80 ≤ `Clear`).
> 0.80 is where the `Clear` band starts; it does **not** decide between `Clear` and `Noisy` (that is the valid-word
> ratio and damaged tokens), and the document passes can leave a line's label disagreeing with its score. The
> category, not the score, is the decision.

> [!IMPORTANT]
> **Known accuracy limitation since `v1.5.0-beta` (issue [#30](https://github.com/ufal/atrium-alto-postprocess/issues/30)).**
> `rule_short_garbage` no longer convicts short diacritic-free lines on shape alone. Measured over both collections
> (113,101 documents / 71.8M lines) the change **promoted 367,208 lines and demoted none**, dropping `Trash` by 247,252
> lines (−8.68 %), and human grading of 484 promoted lines scored **76.5 % right / 9.6 % borderline / 13.9 % wrong**, a
> 5.5 : 1 improvement. The cost was accepted deliberately: roughly **26,000 genuine OCR-garbage lines now reach `Clear`**
> (95 % CI [19,300; 34,600]), so **a `Clear` verdict on a 1–3 token line without Czech diacritics carries less
> confidence than the label suggests**. Downstream consumers filtering on `categ == "Clear"` should be aware of it.
> The narrowing that pays this down (`_has_shape_garbage_evidence()`, a phonotactic second witness) is implemented and
> wired but **ships off** (`SHORT_GARBAGE_WITNESS_ENABLE = false`) and is held for the pilot: it passes its adoption
> gate on the 2,067-line human gold set ([`tools/gold/GOLD.md`](tools/gold/GOLD.md)), but the floor that would pair with
> it was measured over both collections and rejected (@david-spacil, 2026-10-05), and the pilot's parameter work ended
> on 2026-10-12. See [`docs/issue30/README.md`](docs/issue30/README.md) and the
> [decision record](docs/categorization_logic.md#decision-record).

#### Shipped decisions for the pilot

The category semantics are **frozen for the S2 pilot** (decision of 2026-09-30 on
[#2](https://github.com/ufal/atrium-ocr-postprocess/issues/2)); a change afterwards is measured on a corpus diff with
`tools/corpus_change.py`. What ships:

| Setting                                                   | Shipped                                | Meaning                                                                                                                            |
|-----------------------------------------------------------|----------------------------------------|------------------------------------------------------------------------------------------------------------------------------------|
| `DOMAIN_NOTATION_CATEG` (`[TEXT_UTILS]`)                  | `Clear`                                | every recognised web or e-mail address gets this category (empty = off); a mis-scanned address gets it too                         |
| `[allowed]` in `setup/word_lists.txt`                     | reviewed entries                       | a listed word scores 0 weirdness, is out of the valid-word ratio and is **never evidence of damage** (Q5a/Q5b)                     |
| `SHORT_GARBAGE_WITNESS_ENABLE`                            | `false`                                | the shape witness that would narrow the short-garbage route is wired and held                                                      |
| `SHORT_GARBAGE_WITNESS_FLOOR`                             | `false`                                | its companion floor, rejected on 2026-10-05                                                                                        |
| `LANG_REMAP_ALWAYS`                                       | `true`                                 | the stored score of a remapped language is the cap (0.75 / 0.50), whatever FastText said                                           |
| `LOWPPL_CLEAR_MAX`                                        | `35.0`                                 | perplexity under which three or more words are `Clear` (lowered from 50 on 2026-10-11, the one change that broke no labelled line) |
| `SHORT_NOISY_QS_PENALTY`                                  | `0.20`                                 | score penalty of a very short string with OCR oddities (0 switches it off)                                                         |
| `QUALITY_VOCABULARY_ENABLE`                               | `false`                                | a real vocabulary in place of the shape-only word test; measured and rejected (212 fixes against 540 breaks)                       |
| `PAGE_PPL_BLEND_ENABLE`                                   | `false`                                | page-relative perplexity for one- and two-word lines; needs calibrating on a real run first                                        |
| `MODEL_NAME`, `PERPLEXITY_THRESHOLD_MAX`, `SHORT_PPL_CAP` | `Qwen/Qwen2.5-0.5B`, `1000.0`, `850.0` | perplexity model and its scale (see below)                                                                                         |

The service and the batch pipeline **refuse to start** when the score bands are inverted
(`CATEG_TRASH_SCORE_MAX` ≥ `CATEG_NOISY_SCORE_MAX`), `SHORT_PPL_CAP` ≥ `PERPLEXITY_THRESHOLD_MAX`, or the quality-score
weights do not sum to a positive number.

> [!NOTE]
> **Hand-editable word lists.** The word lists the categoriser consults (units, reference labels, section headings,
> short Czech function words, the rotation whitelists) live in [`setup/word_lists.txt`](setup/word_lists.txt)
> (`WORD_LISTS_PATH`), one `[section]` each, with the matching `setup/config.txt` keys kept empty as overrides. Editing
> a list changes categories: treat it as a categorisation change.

#### Configuration ⚙️

> [!NOTE]
> This script generates two primary output directories: `DOC_LINE_LANG_CLASS/` and `DOC_LINE_STATS/`, while the
> raw **text** 📝 files (primary input) are stored in `../PAGE_TXT/` generated from `../PAGE_ALTO/`.

All input/output paths and tunable parameters are configured ⚙️ in [config.txt](setup/config.txt) 📎, in three
sections: `[CLASSIFY]`, `[AGGREGATE]` and `[TEXT_UTILS]`. The file is INI: **comments go on their own line**, and any
key can be overridden by an environment variable `ATRIUM_<SECTION>_<KEY>` (for example
`ATRIUM_TEXT_UTILS_SHORT_PPL_CAP=900`), read when the process starts.

```ini
[CLASSIFY]
# Lines per perplexity pass of the batch pipeline (the service: PPL_BATCH_LINES, 32).
BATCH_SIZE = 128
# CPU workers for FastText and the structural signals.
WORKERS_MAX = 32
# Expected languages (ISO 639-3); the first is the default for an unknown language.
EXPECTED_LANGS = ces,deu,eng
# Foreign languages kept as they are, at a reduced trust.
TRUSTED_FOREIGN_LANGS = deu,eng,fra,pol,ita,slk
# Language model for perplexity; for English-only collections use distilgpt2 and re-tune the table below.
MODEL_NAME = Qwen/Qwen2.5-0.5B

[TEXT_UTILS]
# Quality-score weights (the score is divided by their sum).
QS_WEIGHT_VALID_WORD = 0.35
QS_WEIGHT_WEIRD = 0.18
QS_WEIGHT_GARBAGE = 0.18
QS_WEIGHT_PERPLEXITY = 0.08
QS_WEIGHT_VOWEL = 0.07
QS_WEIGHT_LANG = 0.05
QS_WEIGHT_GIBBERISH = 0.04
QS_WEIGHT_FUSED = 0.03
QS_WEIGHT_LENGTH = 0.02
QS_LENGTH_MAX = 100.0
# Band edges of the stored score: Trash < 0.55 <= Noisy < 0.80 <= Clear.
CATEG_TRASH_SCORE_MAX = 0.55
CATEG_NOISY_SCORE_MAX = 0.80
# Gate 8: perplexity under which three or more words are Clear (2026-10-11: 50 -> 35).
LOWPPL_CLEAR_MAX = 35.0
# Gate 1: FastText confidence under which, with a high perplexity, a line is Trash.
HARD_SWEEP_LANG_MAX = 0.45
HARD_SWEEP_PPL_MIN = 1000.0
# Weirdness added to a token containing w, q or x (rare in Czech).
WORD_W_PENALTY = 0.20
# Confidence ceiling for the page-level rotation arm.
ROT_HIGH_LANG_CONF = 0.90
```

Parameters that scale with the **perplexity** 📉 model. They must be re-tuned whenever you switch between multilingual
`Qwen2.5-0.5B`🤖 and English-adapted `distilgpt2`🤖, because the two produce **perplexity** 📉 on very different scales
(`Qwen2.5-0.5B` roughly 3× lower on the same Czech 🇨🇿 text); the guard in `classify_TEXT.py` warns when the ceiling is at
`distilgpt2` scale with a Qwen model:

| Parameter                  | Qwen2.5-0.5B | distilgpt2 | What it controls                                                                                                                                                                                                                                                                                                                          |
|----------------------------|--------------|------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `PERPLEXITY_THRESHOLD_MAX` | 1000.0       | 3000.0     | The ceiling used to normalise raw **perplexity** 📉 into [0, 1] for the **quality score** 📈. A value at or above this ceiling contributes 0 to the score (worst); a value of 0 contributes 1 (best).                                                                                                                                     |
| `SHORT_PPL_CAP`            | 850.0        | 2500.0     | Maximum **perplexity** 📉 applied to 1–2 word lines before scoring. Short fragments get extreme **perplexity** 📉 from any LM (no context to condition on); the cap stops legitimate short labels and codes being penalised. It sits below every perplexity route's floor, so **no perplexity rule can fire on a one- or two-word line**. |
| `PPL_INVERTED_MIN`         | 200.0        | 500.0      | **Perplexity** 📉 floor for the page-level inverted-scan arms B and C: the LM must also be uncertain about the line.                                                                                                                                                                                                                      |

Parameters that are model-independent 🤖 (ratios and score fractions, stable across either model):

| Parameter                   | Default | What it controls                                                                                                                                                                                                                              |
|-----------------------------|---------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `ROT_RATIO_INVERTED_MIN`    | 0.55    | Minimum share of structurally rotatable characters (`pbqdnuwmoxszeyv`) among a line's letters for the page sweep's rotation arm (arm C).                                                                                                      |
| `MOSTLY_READABLE_VALID_MIN` | 0.85    | Minimum share of structurally valid words for `Clear`. Below it a line is `Noisy`, unless it is a confident, low-perplexity, upright-Czech line (`lm_confident_czech`).                                                                       |

> [!NOTE]
> The near-boundary "clean prose" promotion (Override 4) and its `CLEAN_PROSE_*` keys were **removed**; no path reads
> them. `WEIRD_RATIO_INVERTED_MIN` is still loaded (and swept) but no longer read by any rule.

Language- and collection-specific data 🇨🇿 lives in the config rather than in Python (#7 Tier 1):

| Parameter                                | Section        | Default                                                               | What it controls                                                                                                                                                                                                                    |
|------------------------------------------|----------------|-----------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `WQX_CHARS`                              | `[TEXT_UTILS]` | `wqxWQX`                                                              | Letters rare in Czech 🇨🇿: a token holding one is weighted as OCR noise (`score_word`), and they feed the `wqx`/rotation gate.                                                                                                     |
| `ROT_WHITELIST`                          | `[TEXT_UTILS]` | `po,pod,do,od,on,ony,by,bez,ne,nebo,ven,den,zde,se,ve,mez,pouze,bude` | Czech 🇨🇿 function words recognisable upright; a line containing one counts as *upright Czech*. Their mirror/rotation ghost images (`ROT_GHOSTLIST`) are **derived at import time**: changing this key requires a re-import.       |
| `GHOST_WORD_COLLISIONS`                  | `[TEXT_UTILS]` | `no,bo`                                                               | Ghost images that collide with real words and must never count as ghost hits.                                                                                                                                                       |
| `TRAILING_FILL_CHARS`                    | `[TEXT_UTILS]` | `\x20._:-<–—`                                                         | Trailing filler characters stripped before headline/short-line checks. Unicode-escape decoded; the leading space is written `\x20` because configparser strips leading whitespace from values.                                      |
| `NONTEXT_MARKERS`                        | `[TEXT_UTILS]` | `IVerc`                                                               | Collection-specific literal markers (the ARUP/B stamp) forcing `Non-text` in `pre_filter_line()`.                                                                                                                                   |
| `DEU_DIACS`, `DIACRITIC_INFER_THRESHOLD` | `[TEXT_UTILS]` | `äöüßÄÖÜ`, `0.07`                                                     | Reserved: read only by `infer_lang_from_diacritics()`, which no scoring path calls.                                                                                                                                                 |
| `FASTTEXT_MODEL`                         | `[CLASSIFY]`   | `lid.176.bin`                                                         | Path to the **FastText** 🌐 language-ID weights loaded by each CPU worker.                                                                                                                                                          |
| `TRUST_TIER_TRUSTED`                     | `[CLASSIFY]`   | 0.85                                                                  | Trust multiplier on the FastText confidence for a *known but unexpected* language. The product (`trust_lang_score`) feeds **both** the quality score and the structural gates, never the stored `lang_score`.                       |
| `TRUST_TIER_UNKNOWN`                     | `[CLASSIFY]`   | 0.50                                                                  | Trust multiplier for an *unknown* language. It caps the trust at 0.50, below `LANG_SCORE_REMAP` (0.75), so a gate written `lang_score <= LANG_SCORE_REMAP` is always true for unknown-language lines.                               |
| `REMAP_KEEP_SCORE_LANGS`                 | `[CLASSIFY]`   | `slk`                                                                 | Languages **outside** the expected and trusted lists that keep their confidence when relabelled to the default. `slk` is also in `TRUSTED_FOREIGN_LANGS`, so it is kept as Slovak and this key has no effect in the shipped config. |

---

#### 4.1 Classify Lines (GPU Bound) 🚀

This script reads the extracted **text** 📝 files, batches lines together 📦, and runs the **FastText** 🌐 and
**Qwen2.5-0.5B** 🤖 models. It uses a **CPU** 💻/**GPU** 🚀 split architecture:

* A single dedicated **GPU** 🚀 worker holds the only **Qwen2.5-0.5B** 🤖 instance and processes **perplexity** 📉
  batches to prevent VRAM OOM errors. A batch that does run out of memory is **split in halves and retried**; only a
  line that fails even on its own is scored 99999.
* Multiple **CPU** 💻 workers (up to `WORKERS_MAX`, default 32) read files, run the pre-filter, **FastText** 🌐 and the
  structural signals, and submit text batches to the **GPU** 🚀 worker via a shared queue. **CPU** 💻 workers poll the
  result dictionary while the GPU processes, running **language identification** 🌐 concurrently.

> [!WARNING]
> The **first** item of the `EXPECTED_LANGS` list should be the most expected language in the processed collection: it
> is the default replacement of unknown language predictions.

```bash
python3 classify_TEXT.py [--input-csv <stats.csv>]

```

* **Input 1 📥:** `../PAGE_TXT/` from Step 3
* **Input 2 📥:** `output.csv` from Step 2 (`--input-csv`, else `[CLASSIFY].INPUT_CSV`)
* **Output 📤:** `DOC_LINE_LANG_CLASS/` containing per-document **CSVs** 📊 (e.g., [DOC_LINE_CATEG](data_samples/DOC_LINE_CATEG) 📁)
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

`<doc_name>.csv` 📊: detailed classification results for every single line within a document, **columns**:

* `file` — document identifier 🆔
* `page_num` — page number 📄
* `line_num` — line number, starts from 1 for each page 🔢
* `text` — cleaned text of the line (after the pre-filter's OCR repairs) 📝
* `original_text` — original pre-repair text of the line 📝
* `split_ws` — hyphenated word prefix at the end of the line (split word start)
* `split_we` — hyphenated word suffix at the start of the line (split word end)
* `word_count` — **count** of whitespace-delimited tokens in the line
* `char_count` — **count** of characters in the cleaned line
* `garbage_density` — ratio of non-alphanumeric characters to total line length (calculated on `original_text`)
* `upper` — **count** of words with unexpected mid-word uppercase letters
* `repeated` — **count** of words with a triple character run, a doubled non-`o`/`u` character occurring twice, or one character making up ≥ 30 % of the word
* `ldl_fuses` — **count** of words with a letter–digit–letter sandwich (e.g., `vyt1ačená`), excluding valid measurements
* `fused_words` — **count** of tokens that appear to be fused words (abnormal consonant/vowel runs or extreme length)
* `gibberish` — **count** of words flagged as gibberish (high vowel ratio)
* `weird_wx` — **count** of words with an abnormal density of `w`/`x` glyphs
* `word_weird` — mean per-word weirdness in [0, 1]: per word a strange symbol (0.40), a repeated character (0.35), a
  letter–digit–letter sandwich (0.15), a mid-word capital (0.25), a capital prefix (0.20), any of `w`/`q`/`x`
  (`WORD_W_PENALTY`, 0.20) and a vowelless word of 3+ letters (0.50), clamped to 1; isolated single letters score 0.85
  (noise) or 0.25 (a digit); then the line-level rotation (+0.45 per word) and fragmentation (+0.35 per fragment) penalties
  (see [the reference](docs/categorization_logic.md#structural-detectors))
* `vowel_ratio` — ratio of vowels to alphabetic and symbol characters in the `original_text`
* `rot_ratio` — ratio of structurally ambiguous/rotatable characters (`pbqdnuwmoxszeyv`) to the alphabetic characters of the line
* `valid_word_ratio` — share of words that pass the structural word test (the readability measure of gate 10)
* `structured` — **boolean**: a measurement, reference or similar structured line (exempt from several gates)
* `damaged` — **boolean**: the line holds a character-level damaged token (a digit or symbol inside a word)

The key resulting output **columns** that depict the final classification and quality assessment:

* `quality_score` — composite **quality score** 📈 in [0, 1] from **9** combined signals, higher = cleaner; clamped into the band of `categ`
* `categ` — assigned category: **Clear** ✅, **Noisy** ⚠️, **Trash** 🗑️, **Non-text** 🔣, or **Empty** 🫙

**Columns** useful for archive managers apart from the **quality score** 📈 and category:

* `lang` — predicted ISO **language code** from the **FastText** 🌐 model (remapped if unknown)
* `lang_score` — the *stored* confidence: FastText's, or the cap (0.75 / 0.50) for a remapped language (`LANG_REMAP_ALWAYS`)
* `original_lang` — predicted language **before** remapping
* `orig_lang_score` — original **FastText** confidence **before** remapping
* `perplex` — the perplexity used for scoring: Qwen2.5-0.5B 🤖 **perplexity** 📉 of the line, **capped at `SHORT_PPL_CAP` for one- and two-word lines**
* `perplex_raw` — the model's own perplexity, uncapped (blank-equivalent to `perplex` except for those short lines)
* `perplex_blend` — the page-relative perplexity, written only when the blend moved a value (`PAGE_PPL_BLEND_ENABLE`, off by default)
* `caps_header` — **boolean**: all alphabetic words of the line are uppercase (typical of section headers)

**Diagnostic flags.** Five boolean audit columns name the rule that decided the line (exactly one `True`, none for
`Empty` and `Non-text`): `allcaps_novowel`, `lowppl_clear`, `trash_threshold`, `noisy_threshold`, `clear_threshold`
(`cleanprose_clear` is always `False` since Override 4 was removed). The hard-sweep and inverted-scan routes are folded
into `trash_threshold`, so the CSV is coarser than the categoriser. Four more name the document-level pass that later
changed the line: `pp_dedup`, `pp_surrounded_trash`, `pp_inverted_run`, `pp_page_context`. A decision flag and a `pp_`
flag can both be `True`: that is the trail from the original decision to the override.

---

##### Categorisation logic reference 📚

The full decision logic lives in **[docs/categorization_logic.md](docs/categorization_logic.md)**:

| Section                                                                                     | What it covers                                                                                             |
|---------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|
| [CPU 💻 Pre-filter](docs/categorization_logic.md#cpu--pre-filter)                           | `pre_filter_line()`: `Empty`/`Non-text` routing and the two OCR repairs, before any model runs             |
| [Language 🌐 Handling](docs/categorization_logic.md#language--handling)                     | FastText trust tiers, `remap_lang()`, `LANG_REMAP_ALWAYS`, and which language number is read where         |
| [Structural Detectors](docs/categorization_logic.md#structural-detectors)                   | the per-line signal detectors and per-word weirdness                                                       |
| [Composite Quality Score](docs/categorization_logic.md#composite-quality-score)             | the weighted `compute_quality_score()` formula and its adjustments                                         |
| [Categorisation Logic](docs/categorization_logic.md#categorisation-logic)                   | `determine_category()`: the ordered gates, strong garbage evidence, rescues, band routing and the clamp    |
| [Overrides and exception cases](docs/categorization_logic.md#overrides-and-exception-cases) | every route that decides without or against the score band, what blocks it, and where to see it in the CSV |
| [Foreign-language lines](docs/categorization_logic.md#foreign-language-lines)               | the tiers, the Czech-first heuristics that act on every language, and the known limits                     |
| [Post-Processing Smoothing](docs/categorization_logic.md#post-processing-smoothing)         | `apply_document_postprocessing()`: the four document passes                                                |
| [Worked examples](docs/categorization_logic.md#worked-examples)                             | one input after another through every step; each is a test case                                            |

To replay a config change over already-scored CSVs without a GPU, use
[`tools/recategorize_from_csv.py`](tools/recategorize_from_csv.py): it reads the same constants and calls the very same
scoring function as the pipeline (`classify_TEXT.score_line()`), so a re-scored corpus differs from the shipped batch only
by the change under test. The FastAPI `/process` and `/score_record` endpoints use that same function, behind the same
pre-filter; see [who runs it](docs/categorization_logic.md#who-runs-it).

---

Example of per-document **CSV** 📊 files: [DOC_LINE_CATEG](data_samples/DOC_LINE_CATEG) 📁 by **Qwen2.5-0.5B** 🤖
and [DOC_LINE_CATEG_gpt](data_samples/DOC_LINE_CATEG_gpt) 📁 by **distilgpt2** 🤖.

```
DOC_LINE_LANG_CLASS/
├── <docname1>.csv
├── <docname2>.csv
└── ...
```

---

#### 4.2 Aggregate Statistics (Memory Bound) 🧠

This script processes the `DOC_LINE_LANG_CLASS/` directory with **CSV** 📊 files in chunks 🧩 to produce
final page-level statistics. It is **CPU** 💻-bound and parallelized with `ProcessPoolExecutor`.

```
python3 aggregate_STAT.py
```

* **Input 📥:** `DOC_LINE_LANG_CLASS/` (directory with **CSV** 📊 files from the previous step)
* **Output 1 📤:** `final_page_stats.csv` 📊 (configurable via `OUTPUT_STATS`) — global page-level summary across all documents
* **Output 2 📤:** `DOC_LINE_STAT/` (configurable via `OUTPUT_DOC_DIR`) — per-document **CSVs** 📊 with the same schema

For each page, the aggregation computes features outputted in the following strict schema order:

**Totals & Counts:**

* `num_lines` — the total number of valid lines processed on the page
* `Clear`, `Noisy`, `Trash`, `Non-text`, `Empty` — integer count of lines in each category
* `total_word_count` — total number of words across scoreable lines
* `total_char_count` — total number of characters across scoreable lines

**Averages** (mean over the same `Clear` ✅ and `Noisy` ⚠️ lines):

* `avg_quality_score` — mean composite **quality score** 📈 in [0, 1]; higher = cleaner **OCR** 🔍 output
* `avg_word_weird` — mean per-word weirdness ratio in [0, 1]; 0 = fully clean, lower is better 📉
* `avg_lang_score` — mean **FastText** 🌐 confidence score
* `avg_perplex` — mean **Qwen2.5-0.5B** 🤖 **perplexity** 📉 score
* `avg_vowel_ratio` — mean vowel-to-alphabetic-character ratio per line
* `avg_rot_ratio` — mean rotatable character ratio per line
* `ch_ratio` — mean fraction of lines flagged as all-caps headers (`caps_header = True`)

**Language profile:**

* `main_lang` — the statistical mode (most frequent) language 🌐 predicted for the page

> [!NOTE]
> `avg_*` columns and `main_lang` will be `NaN` / `None` for pages with no `Clear` or `Noisy` line, i.e. pages
> whose only lines are `Trash`, `Empty` or `Non-text`.
> `avg_quality_score` leaves `Trash` lines out, so a page made mostly of `Trash` lines can still score ≥ 0.80:
> read it together with the page's `Trash` count. In a document record, a page with no `Clear`, `Noisy` or
> `Trash` line carries no `quality_band` (the service path omits it too) and no `quality_score`.
> Additional per-line diagnostic variables (e.g. `weird_wx`, `original_lang`, `original_text`) and flags added
> and ignored for this page-level aggregation to ensure stability.

All numeric averages are rounded to 4 decimal places; totals are stored as integers.

* *Examples*: [arup_page_stats_SHORT.csv](data_samples/arup_page_stats_SHORT.csv) 📊, [arub_page_stats_SHORT.csv](data_samples/arub_page_stats_SHORT.csv) 📊

Example of per-document aggregate **CSV** 📊 files: [DOC_LINE_STATS](data_samples/DOC_LINE_STATS) 📁 by **Qwen2.5-0.5B** 🤖
and [DOC_LINE_STATS_gpt](data_samples/DOC_LINE_STATS_gpt) 📁 by **distilgpt2** 🤖:

```
DOC_LINE_STAT/
├── stats_<docname1>.csv
├── stats_<docname2>.csv
└── ...
```

This is the end of the text quality classification and filtering step. You can now use [arup_page_stats_SHORT.csv](data_samples/arup_page_stats_SHORT.csv) 📎 to
identify files that need another round of **OCR** 🔍 or manual correction based on the line type counts. Pages with the
majority of **Clear** ✅ lines can be marked for further processing. The absence of clear lines combined with a high proportion
of **Trash** 🗑️ lines may also indicate handwritten content, which can be excluded before Handwritten Text Recognition (HTR) is applied.

## API Service Integration

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
settings — including the default **Qwen2.5-0.5B** 🤖 perplexity model — to ensure zero drift between local processing
and web uploads. Both run the CPU pre-filter first, so a numeric, code-like or blank line is answered `Non-text` or
`Empty` without a model call, as in the batch CSV. The document passes of [Step 4.1](#41-classify-lines-gpu-bound-)
(identical-text vote, surrounded Trash, page context, inverted-scan sweep) are applied by the batch pipeline only; the
service answers each line from its own signals. `POST /score_record` scores the lines of a digital-convert record in
place; its page figure averages the `Clear`, `Noisy` and `Trash` lines and leaves out the `Non-text` and `Empty`
ones.

For deployment instructions, endpoint specifications (`/process`, `/score_record`, `/info`), and frontend integration details,
please see the dedicated **[Service Documentation](service/README.md)**.

## Paradata logging

This project incorporates a unified provenance and **paradata** 🗒️ logging system to seamlessly track the execution
details of every pipeline stage. The logger automatically captures run-time metadata and saves it in a
structured **JSON** 📄 format.

**What gets logged?**

* **Provenance 🏛️:** Captures the tool name, a tool **version** 🏷️ tag, the repository/runner reference, the running
container image (when set), the **Python** 🐍 version, and assigns a unique `run_id` to each execution. The repository
reference is resolved **dynamically** — environment overrides (`ATRIUM_RUNNER_REPO`, `ATRIUM_RUNNER_REF`,
`ATRIUM_RUNNER_IMAGE`) take precedence over the static fallback in [para_config.txt](setup/para_config.txt) 📎 — so the log
points at the image actually executing rather than a fixed fork.
* **Output license ⚖️:** Computes the **effective output license** 📜 of the run from the licensed components it actually
exercised, and records it as `license` / `license_url` plus a detailed `license_detail` block (per-component licenses,
which component(s) `determined_by` the result, `is_non_commercial` / `is_share_alike` flags, and any unknown licenses).
See [Output licensing](#output-licensing-) below.
* **Configuration ⚙️:** Stores run-time configuration ⚙️, including script
names, input/output paths, and specific model choices.
* **Timing ⏱️:** Records precise UTC start times, end times, and the total duration of the run in seconds.
* **Statistics 📊:** Tracks the total number of input files, successfully processed documents, and computes performance
throughput (e.g., output files generated per minute).
* **Error Tracking 🐛:** Maintains a `skipped_files_detail` list that logs the exact filename and specific error reason
if a file fails to process.

**Log Location**

By default, **JSON** 📄 logs are written to the [paradata](paradata) 📁 directory following the naming convention
`<YYMMDD-HHmmss>_<program>.json`. Paradata is intended to live alongside the **outputs** 📤 (not committed to the
repository); the **paradata** 🗒️ JSON files themselves are distributed under the **CC BY-NC 4.0** license.

---

### Output licensing ⚖️

> [!IMPORTANT]
> The license of the files a run **produces** is **not fixed** — it is computed per run as the **most restrictive**
> license among the components (models, data, APIs) that the run actually used. The mechanism is data-driven via
> [para_config.txt](setup/para_config.txt) 📎 (component → license) and [para_licenses.py](para_licenses.py) 📎
> (restrictiveness ranking + share-alike / non-commercial rules), so the licensing owner can adjust it without touching
> the logger.

Each repository ships a [para_config.txt](setup/para_config.txt) 📎 listing its components. Components flagged `always` count
toward every run (the worst-case baseline); components flagged `conditional` are only counted when the script that uses
them records it. For this repository the components and their effect on the **effective output license** 📜 are:

| Component                                                                              | License         | Counted     | Used by                                                        |
|----------------------------------------------------------------------------------------|-----------------|-------------|----------------------------------------------------------------|
| **alto-tools** 🔧 [^1](https://github.com/cneud/alto-tools) — vendored, see below      | Apache-2.0      | always      | page split, statistics, alto-tools text extraction             |
| **FastText** 🌐 [^2](https://huggingface.co/facebook/fasttext-language-identification) | CC BY-NC 4.0    | always      | language identification (`classify_TEXT.py`)                   |
| **Qwen2.5-0.5B** 🤖 [^6](https://huggingface.co/Qwen/Qwen2.5-0.5B)                     | Apache-2.0      | conditional | **perplexity** 📉 scoring (default, `classify_TEXT.py`)        |
| **distilgpt2** 🤖                                                                      | Apache-2.0      | conditional | **perplexity** 📉 scoring (English-only alternative)           |
| **LayoutLMv3** 📐 [^9](https://github.com/ppaanngggg/layoutreader)                     | CC BY-NC-SA 4.0 | conditional | LayoutReader text extraction (`extract_LytRdr_ALTO_2_TXT.py`)  |
| **GLM-4v-9b** 🤖 [^10](https://huggingface.co/THUDM/glm-4v-9b)                         | glm-4           | conditional | generative **OCR** 🔍 extraction (`extract_LLM_ALTO_2_TXT.py`) |
| **pypdfium2** 📄 (PDFium) — PDF text layers (#31)                                      | Apache-2.0 [^p] | conditional | text-lines PDF reading (`text_split.py`)                       |
| **charset-normalizer** 🔤 — encoding detection (#31)                                   | MIT             | conditional | text-lines non-UTF-8 plain text (`text_split.py`)              |

[^p]: pypdfium2 is dual-licensed Apache-2.0 **or** BSD-3-Clause; `para_licenses` does not parse SPDX `OR`, so it
is recorded as Apache-2.0 (either is permissive). The text-lines readers use no other licensed component: DOCX,
XLSX, PPTX, ODF, EPUB, HTML and XML are read with the standard library and lxml, so a text-lines run stays at the
**CC BY-NC 4.0** baseline.

Because the always-on **FastText** 🌐 weights are **CC BY-NC 4.0**, the baseline effective output license for this
repository is **CC BY-NC 4.0** (non-commercial). Runs that additionally use the **LayoutReader** 📐 method escalate to
**CC BY-NC-SA 4.0** (non-commercial **and** share-alike), the most restrictive option here. A run that exercised only
permissive components would resolve to **Apache-2.0**.

> [!NOTE]
> The restrictiveness ordering encoded in [para_licenses.py](para_licenses.py) 📎 is a mechanical engineering
> approximation, **not legal advice**; unrecognised licenses are treated conservatively as maximally restrictive so a
> missing entry can never silently relax the recorded output license.

### Document record schema 📑

The per-document record this tool reads and writes — `<doc_id>.document.json`, the pair of the
paradata above — follows [atrium_document.schema.json](atrium_document.schema.json) 📎, schema
version **`1.0`**. That version is frozen as the hub tag
[`doc-schema-v1`](https://github.com/ufal/atrium-project/releases/tag/doc-schema-v1)
(ufal/atrium-project@`544298b`), and it is the baseline this tool implements:

* [tests/test_schema_freeze.py](tests/test_schema_freeze.py) 📎 checks this repository's schema
  against the frozen copy beside it,
  [atrium_document.schema.doc-schema-v1.json](atrium_document.schema.doc-schema-v1.json) 📎: the
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
[CITATION.cff](CITATION.cff) 📎 carries the same reference under `references`.

---

## Vendored code 📦

Some third-party code is **copied into this repository** rather than installed as a dependency. Vendored code
keeps its original licence; the repository's own [LICENSE](LICENSE) 📎 (MIT) does not apply to it. Licence texts
live in [LICENSES/](LICENSES) 📁.

### `alto_tools.py` — ALTO text extraction & statistics 🔧

|                  |                                                                                                                                                             |
|------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Upstream**     | [cneud/alto-tools](https://github.com/cneud/alto-tools) [^1] by Clemens Neudecker                                                                           |
| **Commit**       | [`1f4f01e5f6ac3562740e39948442973b9cb94be4`](https://github.com/cneud/alto-tools/commit/1f4f01e5f6ac3562740e39948442973b9cb94be4) (`__version__ = "0.1.0"`) |
| **Source file**  | `src/alto_tools/alto_tools.py`                                                                                                                              |
| **Licence**      | **Apache-2.0** — full text in [LICENSES/alto-tools-Apache-2.0.txt](LICENSES/alto-tools-Apache-2.0.txt) 📎                                                   |
| **Local copy**   | [alto_tools.py](alto_tools.py) 📎                                                                                                                           |
| **Used by**      | [alto_stats_create.py](alto_stats_create.py) 📎 (Step 2, statistics) and [extract_ALTO_2_TXT.py](extract_ALTO_2_TXT.py) 📎 (Step 3, alto-tools method)      |
| **Parity tests** | [tests/test_alto_tools.py](tests/test_alto_tools.py) 📎                                                                                                     |

**Only the used functionality was copied** — the code reachable from the two command-line flags this pipeline
ever called:

| Vendored                                       | Upstream flag   | Used by                 |
|------------------------------------------------|-----------------|-------------------------|
| `alto_parse()`                                 | (shared)        | both                    |
| `alto_text()` / `text_from_file()`             | `alto-tools -t` | `extract_ALTO_2_TXT.py` |
| `alto_statistics()` / `statistics_from_file()` | `alto-tools -s` | `alto_stats_create.py`  |

Left behind, because nothing here calls it: `-c` mean word confidence, `-i` illustration boxes, `-g` graphic
boxes, the `--dehyphenate` / `--detect-hyphens` branches (this repo does its own de-hyphenation in
`extract_ALTO_2_TXT._dehyphenate`), the `argparse` command line, stdin input, directory walking and the
`--xml-encoding` sniffing path. Every deviation from the original is marked with a `VENDORED:` comment in the
file, as Apache-2.0 §4(b) requires for a derived work.

**Why 📌** (issue [#50](https://github.com/ufal/atrium-alto-postprocess/issues/50)) — `alto-tools` has no PyPI
release carrying the `-s` statistics flag, so it had to be declared as
`alto-tools @ git+https://github.com/cneud/alto-tools.git@<commit>`. That made the published Docker image
resolve a GitHub URL in order to build itself, and the end-to-end lane then re-installed a **moving `master`
inside the already-released image, at run time** — so what CI exercised was not the artefact being shipped.
Copying the two used code paths retires the `git+` requirement, the `shutil.which("alto-tools")` guard, the
`PATH` export and the run-time `pip install`, all at once, and replaces two subprocess calls per page with two
function calls.

> [!IMPORTANT]
> The vendored code produces **byte-identical output** to the upstream CLI it replaced: the same page text
> (block/line separators, spacing, `<HYP>` handling, reading order) and the same element counts.
> [tests/test_alto_tools.py](tests/test_alto_tools.py) 📎 pins this against output captured from the upstream
> command line at the commit above, and re-derives it independently for every ALTO file in
> [data_samples](data_samples) 📁.

**Updating it 🔄** — re-copy the two code paths from a newer upstream commit, update `UPSTREAM_COMMIT` in
[alto_tools.py](alto_tools.py) 📎 and the table above, then run `pytest tests/test_alto_tools.py`. Golden values
in that file are upstream CLI output and must only change when upstream's behaviour genuinely does.

---

## Acknowledgements 🙏

**For support write to:** lutsai.k@gmail.com — responsible for this GitHub repository [^8](https://github.com/ufal/atrium-ocr-postprocess) 🔗

* **Developed by** UFAL [^7](https://ufal.mff.cuni.cz/home-page) 👥
* **Funded by** ATRIUM [^4](https://atrium-research.eu/) 💰
* **Shared by** ATRIUM [^4](https://atrium-research.eu/) & UFAL [^7](https://ufal.mff.cuni.cz/home-page) 🔗
* **Vendored code**:
  * **alto-tools** 🔧 [^1](https://github.com/cneud/alto-tools) by Clemens Neudecker, Apache-2.0 — ALTO text
    extraction and element statistics ([alto_tools.py](alto_tools.py) 📎)
* **Models used**:
  * **FastText** 🌐 [^2](https://huggingface.co/facebook/fasttext-language-identification) for language identification
  * **Qwen2.5-0.5B** 🤖 [^6](https://huggingface.co/Qwen/Qwen2.5-0.5B) for **perplexity** 📉 scoring
  * **GLM-4v-9b** 🤖 [^10](https://huggingface.co/THUDM/glm-4v-9b) for generative **OCR** 🔍 (LLM-based method)
  * **LayoutLMv3** 📐 [^9](https://github.com/ppaanngggg/layoutreader) for layout-aware text extraction

**©️ 2026 UFAL & ATRIUM**

[^1]: https://github.com/cneud/alto-tools
[^2]: https://huggingface.co/facebook/fasttext-language-identification
[^4]: https://atrium-research.eu/
[^5]: https://github.com/ufal/atrium-nlp-enrich
[^6]: https://huggingface.co/Qwen/Qwen2.5-0.5B
[^7]: https://ufal.mff.cuni.cz/home-page
[^8]: https://github.com/ufal/atrium-ocr-postprocess
[^9]: https://github.com/ppaanngggg/layoutreader
[^10]: https://huggingface.co/THUDM/glm-4v-9b
