# ATRIUM OCR post-processing API service

The HTTP service of ocr-postprocess: upload an OCR page (ALTO XML), a text file or another text-bearing
document, and get back its ordered lines, each with a language, a quality score and one of the five categories
(`Clear`, `Noisy`, `Trash`, `Non-text`, `Empty`). `POST /score_record` scores the lines of an ATRIUM record another
stage wrote. For ALTO it also puts the lines in reading order (LayoutLMv3, for multi-column pages) and joins
words hyphenated across lines (ALTO `SUBS_TYPE` / `SUBS_CONTENT`, then a regular-expression reconstruction).
Lines are classified by the same pre-filter and scoring function as the batch pipeline
(`text_util.pre_filter_line()`, `classify_TEXT.score_line()`), so a line gets the same category from both; what
the categories mean and how they are decided: [How a line is categorised](../docs/categorization_logic.md). The
service version is read from `setup/para_config.txt` `[tool]`.

## Start

**The published image** (the release without its leading `v`; a named volume keeps the models it downloads on
first start, see [Models and caches](#models-and-caches)):

```bash
docker run --rm -p 8000:8000 -v ocr-hf-cache:/cache/huggingface ghcr.io/ufal/atrium-ocr-postprocess-api:<version>
curl --fail-with-body -sS http://localhost:8000/ready                       # 200 once the models are loaded
curl --fail-with-body -sS -X POST http://localhost:8000/process \
     -F "file=@data_samples/PAGE_ALTO/CTX000000001/CTX000000001-1.alto.xml" -F task_type=alto -o out.json
```

Expected: HTTP 200 and `out.json` with `"type": "alto_xml"` and `cleaned_lines`, each line with `text`, `lang`,
`quality_score` and `category`.

**With Compose**, from a checkout: `docker compose --profile api up -d`. Compose sets its own `ALLOWED_ORIGINS`
default, `http://localhost:8080,http://localhost:5500`; the code default is `*` (every origin).

**From a checkout**, for development (Python 3.11, as the image and CI):

```bash
git clone https://github.com/ufal/atrium-ocr-postprocess.git && cd atrium-ocr-postprocess
./setup/setup_api_server.sh          # a virtual environment, the dependencies (service/requirements.txt), LayoutReader's v3/ scripts, the FastText model
source venv-ocr/bin/activate
python service/text_api.py           # honours PORT and HOST; or: uvicorn service.text_api:app --reload
```

A CUDA GPU is used when present; the service runs on a CPU. On Kubernetes, start from the hub's
[reference manifest and acceptance runbook](https://github.com/ufal/atrium-project/blob/main/docs/k8s_acceptance_runbook.md).

## Models and caches

| Model                                                                                                              | Used for                        | Where it comes from                                                                                                        |
|--------------------------------------------------------------------------------------------------------------------|---------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| FastText language identification (`facebook/fasttext-language-identification`, `model.bin`, 1.18 GB, CC BY-NC 4.0) | the language of each line       | **in the image** at `/app/models/lid.176.bin` (`MODEL_DIR`), Hub revision `3af127d…`, checked by size and SHA-256 at build |
| LayoutLMv3 through LayoutReader (`hantian/layoutreader`, `LAYOUT_MODEL_PATH`)                                      | the reading order of ALTO lines | **downloaded on first start** from the Hugging Face Hub into `HF_HOME` (`/cache/huggingface` in the image)                 |
| Qwen2.5-0.5B (`Qwen/Qwen2.5-0.5B`, `GPT2_MODEL_NAME`)                                                              | each line's perplexity          | **downloaded on first start**, as above                                                                                    |

The two downloads take the Hub's current revision. A container without a cache volume downloads them on every
start; a container **without egress** cannot start: a model that does not load stops the service at startup on
purpose, so the pod crash-loops on its startup probe instead of sitting "not ready". To run without egress, fill a
volume once and start with it offline:

```bash
docker volume create ocr-hf-cache
docker run --rm -v ocr-hf-cache:/cache/huggingface --entrypoint python ghcr.io/ufal/atrium-ocr-postprocess-api:<version> -c \
  "from transformers import AutoModelForCausalLM, AutoTokenizer, LayoutLMv3ForTokenClassification as L; \
   L.from_pretrained('hantian/layoutreader'); AutoTokenizer.from_pretrained('Qwen/Qwen2.5-0.5B'); AutoModelForCausalLM.from_pretrained('Qwen/Qwen2.5-0.5B')"
docker run --rm -p 8000:8000 -v ocr-hf-cache:/cache/huggingface -e HF_HUB_OFFLINE=1 ghcr.io/ufal/atrium-ocr-postprocess-api:<version>
```

On Kubernetes, mount a PersistentVolumeClaim at `/cache/huggingface` (the reference manifest's `hf-cache` volume)
and fill it the same way with a one-off Job.

### Endpoints

| Method | Path            | Description                                                                                                                                                                                                                             |
|--------|-----------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `GET`  | `/`             | Serves the standalone `index.html` interface for manual testing.                                                                                                                                                                        |
| `GET`  | `/info`         | Service identity + capabilities: `service`, `version`, `endpoints`, `limits` (every [limit](#limits)), `limits_meta` (the variable behind each), plus status, device, line fields, quality categories.                                  |
| `GET`  | `/health`       | Liveness probe — 200 always, even mid-shutdown. `?deep=true` also checks the quality/language models are loaded (503 on failure or while draining).                                                                                     |
| `GET`  | `/ready`        | Readiness probe (issue #55) — 503 until model load finishes, 200 while serving, 503 the instant `SIGTERM` arrives. The Kubernetes `readinessProbe`/`startupProbe` target.                                                               |
| `POST` | `/process`      | Uploads a file for layout analysis, cleaning, and line-level classification; with a born-digital record, an ALTO page re-acquires a page it flags `needs_ocr`: see [OCR hand-off](#merging-an-ocrd-page-into-a-born-digital-record-w4). |
| `POST` | `/score_record` | Scores the lines of an existing ATRIUM record with the same quality model, in place: see [Scoring a record](#scoring-a-record-post-score_record).                                                                                       |

### Request example

**Endpoint:** `/process`

**Parameters (Form Data):**

* `file`: The document file (`.xml` ALTO, `.txt` plain text, or `.json` generic OCR JSON) — or (#31) any other
  text-bearing document: PDF, DOCX, ODT/ODS/ODP, XLSX, PPTX, EPUB, RTF, HTML/hOCR, PAGE XML, TEI, Markdown,
  CSV/TSV, JSON Lines.
* `task_type`: `alto`, `text`, `json`, `document`, or `auto` (default). `auto` maps `.txt`→`text` and
  `.json`→`json` by extension; `.xml` and every other upload are decided **from the bytes** — an ALTO root →
  `alto`, anything else readable → `document`. Any other value is a `422` (atrium-project#32 round 2; it used
  to be read as `text`). An unsupported file (image, legacy `.doc`, other binary) is a `415`
  `unsupported_media_type` naming the reader's code (a `400` before round 2); a document over a
  [limit](#limits) is a `413` (`422` for the reader's time limit) with `reason: "limit_exceeded"`; an
  unreadable document (encrypted, corrupt, no text), a JSON upload that does not parse or an ALTO upload that
  is not ALTO (broken XML, no `<Page>`; `cause: "malformed"`) is a `422`. See
  [Errors](#errors).
* `document_json` (optional): a baseline ATRIUM document record, or an AMČR seed (`doc_id`, `source`). The
  response then carries `document_json`: the record with this tool's `pages`/`lines` fields merged in, its
  `quality_summary` recomputed from them (atrium-project#73; numbers only), and `source` given what it lacks. This service reads the source, so it records `source.origin`; every value
  the record already has is kept (first writer wins), and the upload's `sha256` never joins a seed's
  `sha512`, since the ALTO is not the archive's original (atrium-project#71). A
  record that cannot be opened (not JSON, not an object, a newer `schema_version` major) is a `422`
  `invalid_record`; an empty part counts as none.
* `document_record` (optional, **deprecated**): the earlier name of `document_json`; its record comes back as
  `document_json_out`. Both names keep working; send one of them, not both (`422`).
* `include_record` (optional, default `false`): with no record sent, `document_json` comes back anyway, a new
  record holding this upload's `source`, `pages` and `lines`, as the batch pipeline writes one without a baseline.
  A born-digital upload (DOCX, visible-text PDF, …) gets none: its record is digital-convert's to originate.
  The CreateAction lists the new record as an output only.
* `reading_order` (optional, ALTO only): `layoutreader` (default) reorders the lines with LayoutReader, as the
  batch `--method layoutreader`; `alto` keeps the ALTO's own order, as `--method alto-tools`, and runs no
  LayoutReader, so no CC BY-NC-SA 4.0 component is logged. Any other value is a `422`.

Every response carries `limits_applied`: the limits that shaped the result without refusing it (see
[Limits](#limits)), `[]` when none did.

Every response also carries **`paradata`**, the call's provenance (atrium-project#71): one Process Run
Crate `CreateAction`, built by `atrium_rocrate.create_action()`. Its `@id` is the call's `run_uuid`,
which also stamps every block the call wrote into the record; `object` is the upload (by content hash)
and the record sent, `result` the blocks written and the classified lines (`cleaned_lines.json`);
`agent` is `ATRIUM_RUN_AGENT` when set. The service writes no paradata file. An error response carries
no action. Hub
[`docs/rocrate_export.md`](https://github.com/ufal/atrium-project/blob/main/docs/rocrate_export.md) §5
describes it.

```bash
curl -X POST "http://localhost:8000/process" \
  -F "file=@/path/to/page_01.xml" \
  -F "task_type=auto"
```

For a generic JSON OCR-engine export, the endpoint pages the document as the batch pipeline's
`page_split.py` does (`page_split.json_pages`: a page list, or items tagged with a page number), and reads
each page as `extract_JSON_2_TXT.py` reads one (`page_text_lines`): the same key whitelist (`content`,
`text`, `line`, `word`, …) and each text once at line granularity (#31 Phase 5). A plain-text upload is
paged at form feeds, as the batch `text_split.py` pages it (atrium-project#72):

```bash
curl -X POST "http://localhost:8000/process" \
  -F "file=@/path/to/page_01.json" \
  -F "task_type=auto"
```

### Response schema

The top-level `type` field is `alto_xml`, `plain_text`, `json`, or `document`, matching the routed `task_type`.
A `document` result also carries `format` (the detected kind), `origin` (the truthful `source.origin` class),
a `pages` list (`page`, `page_label`, `lines`, PDF `text_layer` / `needs_ocr_reason`), and `page` /
`page_label` on every line, whose `line_num` restarts per page as in the batch `DOC_LINE_CATEG`. A text upload
with form feeds and a JSON upload of several pages come back the same way (since v1.13.0-beta; their
`page_label`, `text_layer` and `needs_ocr_reason` are null), and each page goes into the record under its own
key; one page comes back as before, without `page`. Lines are read
and shaped by the same `text_formats.py` code as the batch text-lines method (blank lines dropped, lines over
1000 characters wrapped). With a record (`document_json`), lines accrete per page — except for born-digital
uploads (DOCX, visible-text PDF, …), whose record belongs to `digital-convert` (atrium_document
§1a) and comes back as it was sent, and except a born-digital record's `needs_ocr` pages, which an ALTO of
that page re-acquires ([the OCR hand-off](#merging-an-ocrd-page-into-a-born-digital-record-w4)). Each item in `cleaned_lines` carries the fields used by the
classification pipeline. Every field is typed in [`openapi.json`](openapi.json) (`ProcessResponse`,
`AltoLine`, `AltoPage`); the table below is the short form.

```json
{
  "type": "alto_xml",
  "filename": "page_01.xml",
  "cleaned_lines": [
    {
      "line_num": 1,
      "text": "The quick brown fox jumps over the lazy dog.",
      "lang": "eng",
      "lang_score": 0.9821,
      "perplexity": 12.5,
      "sym_count": 0,
      "upper_count": 0,
      "word_weird": 0.0,
      "quality_score": 0.9501,
      "category": "Clear"
    },
    {
      "line_num": 2,
      "text": "TYRSOVA5===aras T>r«l",
      "lang": "ces",
      "lang_score": 0.4201,
      "perplexity": 4800.0,
      "sym_count": 2,
      "upper_count": 0,
      "word_weird": 0.85,
      "quality_score": 0.1205,
      "category": "Trash"
    },
    {
      "line_num": 3,
      "text": "1956–1959",
      "lang": "N/A",
      "lang_score": 0.0,
      "perplexity": 0.0,
      "sym_count": 0,
      "upper_count": 0,
      "word_weird": 0.0,
      "quality_score": 0.0,
      "category": "Non-text"
    }
  ]
}
```

**Response fields:**

| Field           | Type   | Description                                                                                                                                                                                                                |
|-----------------|--------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `line_num`      | int    | 1-based line position after layout reordering.                                                                                                                                                                             |
| `text`          | string | Cleaned line text with split-word merges applied.                                                                                                                                                                          |
| `lang`          | string | ISO language code predicted by FastText (e.g., `eng`, `ces`).                                                                                                                                                              |
| `lang_score`    | float  | FastText confidence score `[0, 1]`.                                                                                                                                                                                        |
| `perplexity`    | float  | Qwen2.5-0.5B perplexity. `0` means the line was pre-filtered and inference was skipped.                                                                                                                                    |
| `sym_count`     | int    | Tokens containing characters outside the allowed internal set (`detect_strange_symbols`).                                                                                                                                  |
| `upper_count`   | int    | Tokens with mid-word uppercase artefacts — Patterns 1–3 (`detect_mid_uppercase`).                                                                                                                                          |
| `word_weird`    | float  | Mean per-word weirdness score `[0, 1]`; combines strange-symbol, repeated-char, LDL-fusion, mid-uppercase and mirror-OCR (`w` / caps-prefix) signals; `0` = fully clean.                                                   |
| `quality_score` | float  | Composite quality score `[0, 1]`; weighted sum of nine signals (valid-word ratio, word-weirdness, perplexity, length, garbage density, vowel quality, language confidence, gibberish, fused-word ratio); higher = cleaner. |
| `category`      | string | One of: `Clear`, `Noisy`, `Trash`, `Non-text`, `Empty`.                                                                                                                                                                    |

### Scoring a record (`POST /score_record`)

atrium-digital-convert#4 W3. A born-digital record (PDF, DOCX, ODT, … read by atrium-digital-convert)
has its lines from the document's own text layer, so `/process` contributes nothing to it (§1a). Whether
that text reads is still this service's question, and the pilot wants one quality answer for scanned and
born-digital documents alike. `/score_record` answers it on the record itself:

```bash
curl -X POST "http://localhost:8000/score_record" \
  -F "document_json=@report.document.json"          # optional: -F "pages=i,ii,3"
```

* **In:** `document_json` (required) — any record, born-digital or OCR; `pages` (optional) — the record's own page
  keys, comma-separated (a key the record lacks is refused, `422`).
* **Scored:** each `lines[]` row with text, **exactly as it is** — not re-read, split or reordered, so every answer
  maps back onto its row — in batches of `PPL_BATCH_LINES`, by the same scorer as every other path. A line carrying
  atrium-digital-convert's decode verdict (`categ` `Garbage` or `Inverted`: the embedded text layer does not decode)
  is left as it is; a line without text is not scored.
* **Written into the record** (`document_hook.write_scores()`): `lines[].categ` / `quality_score` / `lang` and
  `pages[].quality_score` / `quality_band`, on rows the record already has — never `text`, never `bbox`, never a new
  row, never `source`. On a born-digital record that is the shared module's scoring co-contribution
  (`atrium_document.SCORING_FIELDS`): the record stays atrium-digital-convert's, and the block's stamp reads
  `"program": "ocr-postprocess", "contribution": "scoring"`. The record's `quality_summary` block
  (atrium-project#73), this tool's on every origin, is recomputed from the scored record: page and line
  totals, the mean/median/min/max of the pages' `quality_score`, and the lines per `categ`, the decode verdicts
  included.
* **Out** (`ScoreRecordResponse`): `cleaned_lines` (every `AltoLine` field plus the record's `page` and `line`; `line_num`
  restarts per page), `pages` (`lines_scored`, `skipped_decode_verdict`, `skipped_empty`, `quality_score`,
  `quality_band`), `limits_applied`, `document_json` (the record, scored; as sent when nothing was scored) and
  `paradata`.
* **Limits:** `MAX_UPLOAD_MB` for the record part, `ATRIUM_TEXT_INGEST_MAX_PAGES` for the pages scored and
  `ATRIUM_TEXT_INGEST_MAX_LINES_PER_PAGE` for the lines of one page (`413 limit_exceeded`). A record that cannot be
  opened, or has no `doc_id`, is `422 invalid_record`.

atrium-digital-convert's `POST /describe` calls this endpoint when `OCR_POSTPROCESS_URL` is set (and falls back
to `/process` page by page against an older version of this service).

**On the command line:** `python3 score_record.py <record or folder> [--pages i,ii] [--out-dir DIR] [--scores]`
runs the same code ([record_scoring.py](../record_scoring.py)) over `*.document.json` files, in place unless
`--out-dir` is given, without the two page and line limits; `--scores` writes the answer without the record as
`<doc>.scores.json` ([usage](../docs/usage.md#scoring-an-existing-record-score_recordpy)).

### Merging an OCR'd page into a born-digital record (W4)

atrium-digital-convert#4 W4. atrium-digital-convert flags a page of a born-digital PDF `needs_ocr` when its embedded
text layer is missing, does not decode, or is an earlier OCR run (`pages[].needs_ocr_reason`, `pages[].text_layer`).
The AMČR pipeline renders that page, runs ATR on it, and sends the page's ALTO here **with the record**: the page's
lines come back into the same record, and the document's born-digital pages stay as the converter wrote them.

```bash
curl -X POST "http://localhost:8000/process" \
  -F "file=@C-202000543A-DT-27-ii.alto.xml" \
  -F "document_json=@C-202000543A-DT-27.document.json" \
  -F "page=ii"                                        # the record's key for the page
```

* **Which page:** `page`, the record's own key (`ii`, `A-1`, …). Without it, the ALTO page's `PHYSICAL_IMG_NR` is
  read as a physical position and mapped through the record's `pages[].page_index` (position 3 of a PDF labelled
  `i, ii, 1` is the page `1`; a number is never matched to a label). One ALTO page per request.
* **Refused before any model runs (`422`):** a page the record lacks, a page it does not flag `needs_ocr`, a
  multi-page ALTO, a text with form feeds or a JSON of several pages with `page`, `page` without `document_json`
  or with a document upload, and `page` on a born-digital record that flags nothing. A JSON or text upload names no page of its own, so on a record that flags
  pages it needs `page`; without it nothing is written.
* **Written:** the page's `lines[]` are **replaced** by the classified lines (`text`, `categ`, `quality_score`,
  `lang`) — the converter's undecodable rows of that page go, in the record's page order. The page's `pages[]` row
  gets `quality_score`, `quality_band` and `ocr.engine` (the engine the ALTO names, e.g. `ocr:pero`, else the
  configured ALTO origin) and keeps `page_index`, `canvas`, `needs_ocr`, `text_layer` and page-classification's
  `category`. `source` (the original's), `content` and every other page are left as they are. Both stamps read
  `"program": "ocr-postprocess", "contribution": "ocr-handoff"`, the shared module's hand-off
  (`atrium_document.OCR_HANDOFF`), which `merge_document_records()` accepts while the record flags a page.
* **Out:** the usual `/process` response, plus `ocr_handoff` (`page`, `lines_replaced`, `lines_written`).

The batch stages make the same write through `document_hook.write_document_block()`: on a record that flags pages,
only those pages' rows are written (page keys mapped the same way), their lines replaced; `content`, `tables`, other
pages' rows and an OCR input's `source` are held back with one warning.

## Errors

Every error has one JSON body (hub `docs/agent_skill_strategy.md` §4.4, atrium-project#32 item 2):
`{"status": <int>, "reason": <code or null>, "detail": "<text>"}`. `detail` starts with the reader's own code where
there is one (`too_large: …`, `corrupt: …`); a `limit_exceeded` body adds `limit` (`key`, `env`, `value`,
`observed`, `unit`), and a request-validation 422 adds `errors`. A reader refusal also carries the reader's code as
`cause` (informational, not registered: it may change; `reason` is the stable one), and a `415` lists the extensions
the readers take as `accepted`.

| Code | `reason`                 | When                                                                                                                                                                                                                          | What to do                                                                                                  |
|------|--------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| 413  | `limit_exceeded`         | over `MAX_UPLOAD_MB` or a `[TEXT_INGEST]` size or count limit (`too_large`, `zip_limits_exceeded`)                                                                                                                            | split the document, or raise the setting `limit.env` names                                                  |
| 415  | `unsupported_media_type` | a kind of file this service does not read (`binary_content`, `image_needs_ocr`, `legacy_office_unsupported`, `archive_unsupported`)                                                                                           | send a kind in `accepted`; an image needs OCR first; convert DOC or XLS to DOCX or XLSX                     |
| 422  | `invalid_record`         | the `document_json` / `document_record` part cannot be opened (not UTF-8 JSON, not an object, a newer `schema_version` major)                                                                                                 | send the record the previous stage returned, or none; update this service if the record is newer            |
| 422  | `limit_exceeded`         | the PDF reader's time limit (`timeout`)                                                                                                                                                                                       | split the PDF or raise `ATRIUM_TEXT_INGEST_READER_TIMEOUT_S`; a retry of the same file will not help        |
| 422  | `null`                   | a supported file that cannot be read (`corrupt`, `encrypted`, `malformed`, `no_text`, …), a JSON upload that does not parse, an ALTO upload that is not ALTO (`malformed`), or request validation (an unknown `task_type`, …) | repair or decrypt the file `cause` names; send ALTO with `task_type=alto`; fix the field `errors` names     |
| 500  | `null`                   | processing failure                                                                                                                                                                                                            | read the service log; retry once; report a repeat with the input                                            |
| 501  | `null`                   | this deployment lacks a reader's optional dependency (`dependency_missing`: pypdfium2, lxml)                                                                                                                                  | use the published image, which has them, or install the dependency `detail` names                           |
| 503  | `null`                   | the service is warming up, or shutting down                                                                                                                                                                                   | retry with backoff, against a live replica                                                                  |

```json
{"status": 413, "reason": "limit_exceeded",
 "detail": "too_large: 25000 pages > MAX_PAGES=20000 (setting: ATRIUM_TEXT_INGEST_MAX_PAGES, or [TEXT_INGEST] MAX_PAGES in the config)",
 "limit": {"key": "max_pages", "env": "ATRIUM_TEXT_INGEST_MAX_PAGES", "value": 20000, "observed": null, "unit": "pages"}}
```

## Partial results

A 200 can be a result a limit or a reader shaped. It says so in three places:

- **`limits_applied`** (also in the paradata): `split` (a long line or page read in pieces, classified in full),
  `trimmed` (repeated spreadsheet cells, or a line's tokens past the perplexity model's length), `sampled` (a PDF's
  text layer judged on its first objects) and `skipped` (a ZIP member over the size limit, a page kept in document
  order after layout ordering ran out of memory, or a line that failed perplexity even alone and was scored 99999).
  [Limits](#limits) says which limit produces which note.
- **A document's `pages`**: per page, a PDF's `text_layer` and its `needs_ocr_reason` when the page's text should
  come from OCR instead.
- **`/score_record`'s `pages`**: `lines_scored`, `skipped_decode_verdict` (lines the converter marked `Garbage` or
  `Inverted`, left as they are) and `skipped_empty`.

The batch pipeline has the same notes in its paradata, and a text-lines input read with a possible loss is
`partial` in its `ingest_report.csv`.

## Known limits

- **The rules are Czech-first:** lines in other languages are scored less reliably
  ([details](../docs/categorization_logic.md#foreign-language-lines)).
- **Short lines without Czech diacritics** read `Clear` more often than they should
  ([measurements](../docs/evaluation/categorization.md)).
- **No document passes in the service:** each line is answered on its own, so a whole document's categories can
  differ from a batch run's, and a page's `quality_score` averages its `Clear`, `Noisy` and `Trash` lines. Blank
  lines are dropped, so `line_num` counts only the lines with text. Every difference from the command line, and
  why: [docs/usage.md](../docs/usage.md#the-command-line-and-the-service).
- **Two models are downloaded on first start** at the Hub's current revision (see
  [Models and caches](#models-and-caches)).
- **FastText is CC BY-NC 4.0** and runs on every request; what a run records about licences is in
  [docs/reference/paradata.md](../docs/reference/paradata.md).

## Configuration (environment)

| Variable              | Default   | Meaning                                                                       |
|-----------------------|-----------|-------------------------------------------------------------------------------|
| `PORT`                | `8000`    | port the service **binds**, and the one `service/healthcheck.py` probes       |
| `HOST`                | `0.0.0.0` | bind address. see the warning below                                           |
| `GRACEFUL_SHUTDOWN_S` | `20`      | seconds uvicorn waits for in-flight requests before closing them              |
| `RELOAD`              | `false`   | filesystem auto-reload — development only, never in a deployment              |
| `LOG_LEVEL`           | `INFO`    | root logger level for the `python service/text_api.py` start path (issue #61) |
| `ALLOWED_ORIGINS`     | `*`       | CSV of CORS origins — no shared default across the six services               |
| `MAX_UPLOAD_MB`       | `25`      | canonical upload limit — no shared default across the six services            |
| `MODEL_DIR`           | see below | directory models are loaded from                                              |
| `GPT2_MODEL_NAME`     | see below | quality-estimation model id                                                   |

This table is the deployment-facing subset. The complete ledger — every variable this
image reads, including `MODEL_DIR`/`GPT2_MODEL_NAME`/`LAYOUT_MODEL_PATH`/`LANGID_CONFIG`
and the other algorithmic knobs a deployment does not normally touch — is
[`.env.example`](../.env.example) at the repo root, whose layout is fixed by
`docs/templates/env.example.template` in ufal/atrium-project. The cross-service operator
reference is `docs/k8s_deployment.md` in that same repo.

`PORT` and `HOST` are read by `service/text_api.py`'s `__main__` block, which is what the `api` image's `ENTRYPOINT` runs.

ocr-postprocess is the reference implementation for this contract: it has honoured
`PORT`/`HOST` since issue #55, and issue #58 brought the other services into line with
it. Its entrypoint is `python service/text_api.py` (a script launch, made viable by the
`sys.path` bootstrap at the top of that file) rather than the `python -m service.api` the
others use; the environment contract is identical either way.

> `HOST=127.0.0.1` yields a container that reports **healthy** and serves nobody:
> `service/healthcheck.py` always probes loopback by design and never reads `HOST`, so a
> loopback bind passes every probe while being unreachable from outside the container.

## Limits

Every limit this service has (atrium-project#53). Each is an environment setting, declared once in
[`tool_limits.py`](../tool_limits.py), reported with its current value in `GET /info` `limits` and with the
variable that sets it in `limits_meta`. The `[TEXT_INGEST]` caps are also keys of the config file
(`LANGID_CONFIG`, `setup/config.txt`), which the batch pipeline reads: the variable wins over the file, and the
file over the default below. A malformed value stops the service at startup, naming it. Over a limit the
service **refuses** (`reason: "limit_exceeded"`) or **processes the input in full** and says how the limit
shaped the result, in the response's `limits_applied` (and the paradata). `tests/test_limits_contract.py`
checks this table against `tool_limits.py` and `.env.example`.

| Key (`/info`)        | Variable                                | Default | Unit    | Over the limit                                                                                              |
|----------------------|-----------------------------------------|---------|---------|-------------------------------------------------------------------------------------------------------------|
| `max_upload_mb`      | `MAX_UPLOAD_MB`                         | 25      | MB      | 413 `limit_exceeded` — per part: the file and the document record                                           |
| `max_file_mb`        | `ATRIUM_TEXT_INGEST_MAX_FILE_MB`        | 256     | MB      | 413 `limit_exceeded` (`too_large`); a ZIP member over it is not read — `skipped` note                       |
| `zip_max_members`    | `ATRIUM_TEXT_INGEST_ZIP_MAX_MEMBERS`    | 10000   | members | 413 `limit_exceeded` (`zip_limits_exceeded`)                                                                |
| `zip_max_total_mb`   | `ATRIUM_TEXT_INGEST_ZIP_MAX_TOTAL_MB`   | 1024    | MB      | 413 `limit_exceeded`                                                                                        |
| `zip_max_member_mb`  | `ATRIUM_TEXT_INGEST_ZIP_MAX_MEMBER_MB`  | 256     | MB      | 413 `limit_exceeded`                                                                                        |
| `zip_max_ratio`      | `ATRIUM_TEXT_INGEST_ZIP_MAX_RATIO`      | 200     | ratio   | 413 `limit_exceeded`                                                                                        |
| `max_pages`          | `ATRIUM_TEXT_INGEST_MAX_PAGES`          | 20000   | pages   | 413 `limit_exceeded` (`too_large`)                                                                          |
| `max_lines_per_page` | `ATRIUM_TEXT_INGEST_MAX_LINES_PER_PAGE` | 100000  | lines   | a paged format: 413 `limit_exceeded`; any other: split onto continuation pages, read in full — `split` note |
| `reader_timeout_s`   | `ATRIUM_TEXT_INGEST_READER_TIMEOUT_S`   | 300     | s       | 422 `limit_exceeded` (`timeout`, PDF reader) — a per-input budget, a retry will not help                    |
| `max_line_chars`     | `ATRIUM_TEXT_INGEST_MAX_LINE_CHARS`     | 1000    | chars   | wrapped at a word boundary, classified in full as several lines — `split` note                              |
| `odf_repeat_cap`     | `ATRIUM_TEXT_INGEST_ODF_REPEAT_CAP`     | 100     | repeats | repeated ODS/ODT cells or rows past it are not read — `trimmed` note                                        |
| `pdf_object_cap`     | `ATRIUM_TEXT_INGEST_PDF_OBJECT_CAP`     | 20000   | objects | the text layer is judged on the first N objects (the text is read in full) — `sampled` note                 |
| `lr_chunk_size`      | `LR_CHUNK_SIZE`                         | 350     | lines   | an ALTO page is ordered window by window — `split` note                                                     |
| `lr_min_chunk_size`  | `LR_MIN_CHUNK_SIZE`                     | 50      | lines   | out of memory below it: the page keeps its document order — `skipped` note                                  |
| `ppl_batch_lines`    | `PPL_BATCH_LINES`                       | 32      | lines   | lines per perplexity pass; out of memory splits it; 99999 only if one line fails alone — `skipped` note     |
| `ppl_max_tokens`     | — (derived from `GPT2_MODEL_NAME`)      | —       | tokens  | the perplexity model reads the first N tokens of a line — `trimmed` note (`null` until the model loads)     |

Platform limits (not settings): libxml2's default limits (parsed without `huge_tree`); Starlette's multipart
defaults.

## OpenAPI (the typed contract)

The service's OpenAPI document is committed as [`service/openapi.json`](openapi.json) and
attached to every release as `openapi.json` with its `openapi.json.sha256` (atrium-project#32
round 2). It is what a client is generated from: every request and response field is typed,
every error response is the `ErrorBody` above, the registered `reason` codes are listed in
`x-atrium-reason-codes`, and a returned record is typed by the vendored record schema
(`AtriumDocument`). `GET /info` reports `openapi_sha256`, the digest of the spec the running
image serves — equal to the release's `openapi.json.sha256` for an image built from that tag.
`document_record` and `document_json_out` are marked `deprecated` there.

- **After an API change**, regenerate and commit it:
  `python atrium_openapi.py export --app service.text_api:app --out service/openapi.json`.
  `tests/test_openapi_contract.py` fails while it is stale.
- **Compatibility.** Each release compares its spec with the previous release's
  (`release.yml`, `atrium_openapi.py compare` with oasdiff): a breaking change fails the
  release unless the major version went up (for 0.x, that means 1.0), and a removed reason
  code always fails. New fields, endpoints and reason codes are additive.
- **fastapi and pydantic are pinned** exactly (`service/requirements.txt`,
  `setup/requirements-test.txt`): the spec is generated by them. Bump both by hand and regenerate.
- **Tests.** `tests/test_api_contract.py` drives `/process` with canned model results and holds every
  response — 200s and refusals — to the published schema; `tests/test_openapi_contract.py` (vendored from
  the hub) checks the committed spec itself.

## Shutdown behaviour

Issue [#55](https://github.com/ufal/atrium-project/issues/55). The published `api` image
(`ghcr.io/ufal/atrium-ocr-postprocess-api:<version>`, new in that issue — before it this
service was only reachable via a compose entrypoint override, so no API image existed to
deploy) declares `HEALTHCHECK` (shallow `GET /health`, via the vendored
`service/healthcheck.py`) and `STOPSIGNAL SIGTERM`. `service/text_api.py`'s own
`__main__` block — which is this repo's production start path — passes
`timeout_graceful_shutdown` (`GRACEFUL_SHUTDOWN_S`, default 20s). See
[Configuration (environment)](#configuration-environment) for the full set.

On `SIGTERM` the service flips `GET /ready` to **503** at once so an orchestrator stops
routing to it, answers new `/process` calls with 503, and lets in-flight processing finish
before exiting. `GET /health` deliberately stays 200 throughout — a liveness probe failing
mid-shutdown would get the container killed before the drain completed.

Inference now runs in a worker thread (`asyncio.to_thread`) rather than inline on the
event loop. That was a prerequisite, not a tidy-up: uvicorn's `SIGTERM` handler is an
event-loop callback, so while a synchronous `process_alto()` held the loop the signal
could not be processed at all. Draining also matters here specifically because `/process`
writes a `delete=False` temp file that only its own `finally` clause removes — a request
killed by `SIGKILL` mid-flight leaves that file behind.

 Model load failure at startup deliberately raises, so a misconfigured deployment
crash-loops on the startup probe rather than sitting "not ready" forever. That is intended
— see `docs/k8s_deployment.md` ("Known limits") in the hub.

A clean shutdown exits **143** (128 + SIGTERM), not 0: uvicorn re-raises the captured
signal on purpose so a supervisor sees the real cause. That is a normal stop, not a crash.

## Frontends

### Standalone frontend

`service/frontend/` is a self-contained interface with no build step or external framework required.
It is served directly by the FastAPI server at `http://localhost:8000` and works out of the box.

**To use it**, start the server (see [Start](#start)) and open `http://localhost:8000` in your browser.

Features:
- Drag-and-drop or click-to-upload for `.xml`, `.txt` and `.json` files and (#31) PDF, DOCX, ODT, XLSX, PPTX, EPUB,
  RTF, HTML/hOCR, Markdown and CSV documents.
- Processing mode selector (`auto` / `alto` / `text` / `json` / `document`).
- Results table with `Sym`, `Upper`, and `PPL` columns aligned to `text_util.py`.
- Category breakdown bar showing counts for all five labels.
- Raw extracted text toggle.

> [!NOTE]
> If you are running the frontend from a local dev server (e.g. Live Server on port `5500`),
> `script.js` automatically redirects API calls to `http://localhost:8000`.

### LINDAT-integrated frontend

`service/frontend-lindat/` is the frontend variant styled and bundled for deployment within the
[LINDAT Common](https://github.com/ufal/lindat-common) framework. It requires NodeJS and the
`lindat-common` webpack build.

Open a **second terminal window** alongside your running server and follow these steps:

**1. Place the project inside `lindat-common`:**

```bash
git clone https://github.com/ufal/lindat-common.git
cd lindat-common
cp -r /path/to/atrium-ocr-postprocess .
```

**2. Install NodeJS and dependencies:**

```bash
curl -o- https://raw.githubusercontent.com/creationix/nvm/v0.25.4/install.sh | bash
nvm install stable
nvm use stable
export NODE_OPTIONS=--openssl-legacy-provider
npm install
```

**3. Start the webpack dev server:**

```bash
make run
```

Expected output:

```
> lindat-common@3.5.0 start
> webpack-dev-server -p --debug --quiet

> Project is running at http://localhost:8080/
> webpack output is served from /
> Content not from webpack is served from /home/.../lindat-common
```

Open `http://localhost:8080` and navigate to the
`atrium-ocr-postprocess/service/frontend-lindat` directory in the file tree.

For further details on the LINDAT development workflow see the
[LINDAT Common Development Guide](https://github.com/ufal/lindat-common/?tab=readme-ov-file#development).

## Hardware and model notes

* **GLM-4v VRAM Requirements:** The GLM-4v Vision-Language Model requires massive GPU memory. You **must have a GPU
with at least 48 GB of VRAM** (e.g., an NVIDIA RTX A6000 or a multi-GPU setup) to run the extraction pipeline
successfully. Running this on consumer GPUs (like a 3090/4090) will likely result in Out-Of-Memory (OOM) crashes.
* **Perplexity Threshold Coupling:** The service uses **Qwen2.5-0.5B** by default, matched to `PERPLEXITY_THRESHOLD_MAX
= 1000.0` in `config.txt`. If you switch the perplexity model via the `GPT2_MODEL_NAME` environment variable
(e.g., to the English-only `distilgpt2`), you **must** recalibrate `PERPLEXITY_THRESHOLD_MAX` — perplexity scales differ
wildly between architectures (≈ `3000.0` suits `distilgpt2`), so a value tuned for one model is mis-calibrated for the other.

---

## Contacts and acknowledgements

Issues: the [tracker](https://github.com/ufal/atrium-ocr-postprocess/issues). Developed by
[ÚFAL](https://ufal.mff.cuni.cz/home-page), funded by [ATRIUM](https://atrium-research.eu/). Models: LayoutLMv3
through [LayoutReader](https://github.com/ppaanngggg/layoutreader), [FastText language
identification](https://huggingface.co/facebook/fasttext-language-identification) and
[Qwen2.5-0.5B](https://huggingface.co/Qwen/Qwen2.5-0.5B).
