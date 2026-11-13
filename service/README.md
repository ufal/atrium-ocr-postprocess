# ATRIUM OCR post-processing API service

The HTTP service of ocr-postprocess: upload an OCR page (ALTO XML), a text file or another text-bearing
document, and get back its ordered lines, each with a language, a quality score and one of the five categories
(`Clear`, `Noisy`, `Trash`, `Non-text`, `Empty`). `POST /score_record` scores the lines of an ATRIUM record another
stage wrote. The service version is read from `setup/para_config.txt` `[tool]`.

## Start

**At a glance:**

| Image                                               | CPU / GPU                                                                     | Memory                                                                                                                                                                        | Volumes                                             | Calls out to                                                          | Ready after                                                     |
|-----------------------------------------------------|-------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------|-----------------------------------------------------------------------|-----------------------------------------------------------------|
| `ghcr.io/ufal/atrium-ocr-postprocess-api:<version>` | a CUDA GPU when present, otherwise the CPU (slower); the image is a CPU build | GPU: about 9.5 GB at the default `PPL_BATCH_LINES` of 32 ([hub deployment guide](https://github.com/ufal/atrium-project/blob/main/docs/k8s_deployment.md)); RAM: not measured | `/cache/huggingface`, for the two downloaded models | the Hugging Face Hub at first start (two models); no host per request | not measured; the image's health check has a 180 s start period |

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

Open `http://localhost:8000` for the standalone test page ([Frontends](#frontends)). On Kubernetes, start from the
hub's [reference manifest and acceptance
runbook](https://github.com/ufal/atrium-project/blob/main/docs/k8s_acceptance_runbook.md).

## Models and caches

| What                                                                                                                                     | From where                                      | Revision                                         | Into                                                      | Without egress                     |
|------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------|--------------------------------------------------|-----------------------------------------------------------|------------------------------------|
| FastText language identification (`facebook/fasttext-language-identification`, `model.bin`, 1.18 GB, CC BY-NC 4.0): each line's language | Hugging Face Hub, once, when the image is built | `3af127d…`, checked by size and SHA-256 at build | `/app/models/lid.176.bin` (`MODEL_DIR`), **in the image** | nothing to do                      |
| LayoutLMv3 through LayoutReader (`hantian/layoutreader`, `LAYOUT_MODEL_PATH`): the reading order of ALTO lines                           | Hugging Face Hub, **downloaded on first start** | the Hub's current revision                       | `HF_HOME` (`/cache/huggingface` in the image)             | fill a cache volume once, as below |
| Qwen2.5-0.5B (`Qwen/Qwen2.5-0.5B`, `GPT2_MODEL_NAME`): each line's perplexity                                                            | Hugging Face Hub, **downloaded on first start** | the Hub's current revision                       | `HF_HOME` (`/cache/huggingface` in the image)             | fill a cache volume once, as below |

A container without a cache volume downloads the two models on every start. A container **without egress** cannot
start: a model that does not load stops the service at startup on purpose, so the pod crash-loops on its startup
probe instead of sitting "not ready". To run without egress, fill a volume once and start with it offline:

```bash
docker volume create ocr-hf-cache
docker run --rm -v ocr-hf-cache:/cache/huggingface --entrypoint python ghcr.io/ufal/atrium-ocr-postprocess-api:<version> -c \
  "from transformers import AutoModelForCausalLM, AutoTokenizer, LayoutLMv3ForTokenClassification as L; \
   L.from_pretrained('hantian/layoutreader'); AutoTokenizer.from_pretrained('Qwen/Qwen2.5-0.5B'); AutoModelForCausalLM.from_pretrained('Qwen/Qwen2.5-0.5B')"
docker run --rm -p 8000:8000 -v ocr-hf-cache:/cache/huggingface -e HF_HUB_OFFLINE=1 ghcr.io/ufal/atrium-ocr-postprocess-api:<version>
```

On Kubernetes, mount a PersistentVolumeClaim at `/cache/huggingface` (the reference manifest's `hf-cache` volume)
and fill it the same way with a one-off Job.

**The perplexity model sets the thresholds.** The service uses Qwen2.5-0.5B by default, matched to
`PERPLEXITY_THRESHOLD_MAX = 1000.0` in `config.txt`. If `GPT2_MODEL_NAME` names another model, for example the
English-only `distilgpt2`, recalibrate `PERPLEXITY_THRESHOLD_MAX` first: perplexity scales differ widely between
architectures (about `3000.0` suits `distilgpt2`), so a value tuned for one model is wrong for the other
([the parameters that scale with the model](../docs/configuration.md)).

**GLM-4v-9b is not loaded by the service.** Only the batch pipeline's `--method glm` uses it. It needs a GPU with
at least 48 GB of memory (an NVIDIA RTX A6000, for example, or several GPUs); on a consumer GPU such as a 3090 or
4090 it will probably run out of memory.

## Endpoints

Every request and response field, and the error body, is typed in [`openapi.json`](openapi.json) (see
[OpenAPI](#openapi-the-typed-contract)); the tables below are the short form.

| Method | Path            | Purpose                                                                                                                                                                                                                                     |
|--------|-----------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `GET`  | `/`             | the standalone test page (`index.html`)                                                                                                                                                                                                     |
| `GET`  | `/info`         | service identity and capabilities: `service`, `version`, `endpoints`, `limits` (every [limit](#limits)), `limits_meta` (the variable behind each), status, device, line fields, quality categories                                          |
| `GET`  | `/health`       | liveness probe: 200 always, even mid-shutdown; `?deep=true` also checks that the quality and language models are loaded (503 on failure or while draining)                                                                                  |
| `GET`  | `/ready`        | readiness probe: 503 until the models are loaded, 200 while serving, 503 as soon as `SIGTERM` arrives; the Kubernetes `readinessProbe` and `startupProbe` target                                                                            |
| `POST` | `/process`      | scores the lines of an upload (layout analysis, cleaning, line classification)                                                                                                                                                              |
| `POST` | `/build_record` | builds the ATRIUM record from docproc and scores its lines: the pipeline's record builder ([Building the record](#building-the-record-post-build_record))                                                                                    |
| `POST` | `/score_record` | scores the lines of an existing ATRIUM record with the same quality model, in place ([Scoring a record](#scoring-a-record-post-score_record))                                                                                               |

### Request example

**Endpoint:** `/process`

**Parameters (form data):**

* `file`: the document. It is an ALTO `.xml`, a `.txt` plain text or a `.json` generic OCR export, or any other
  text-bearing document: PDF, DOCX, ODT/ODS/ODP, XLSX, PPTX, EPUB, RTF, HTML/hOCR, PAGE XML, TEI, Markdown,
  CSV/TSV, JSON Lines.
* `task_type`: `alto`, `text`, `json`, `document` or `auto` (default). `auto` maps `.txt` to `text` and `.json`
  to `json` by extension. An `.xml` and every other upload is decided **from the bytes**: an ALTO root means
  `alto`, anything else readable means `document`. Any other value is a `422`. The refusals:
  * An unsupported file (an image, a legacy `.doc`, another binary) is a `415` `unsupported_media_type` that names
    the reader's code.
  * A document over a [limit](#limits) is a `413` with `reason: "limit_exceeded"`; the reader's time limit is a
    `422`.
  * An unreadable document (encrypted, corrupt, no text), a JSON upload that does not parse, or an ALTO upload
    that is not ALTO (broken XML, no `<Page>`; `cause: "malformed"`) is a `422`.

  See [Errors](#errors).
* `document_json` (optional): a baseline ATRIUM document record, or an AMČR seed (`doc_id`, `source`). The
  response then carries `document_json`: the record with this tool's `pages` and `lines` fields merged in, its
  `quality_summary` recomputed from them (numbers only), and `source` given what it lacks. As the record builder
  it records `source.origin` (`digital`, `atr` or `legacy`; record schema 2.0). Every value the record already has
  is kept (first writer wins): a seed's `sha512` names the archive's original, and the upload's SHA-512 (an ALTO is
  not the original) is not written over it. A record that
  cannot be opened (not JSON, not an object, a newer `schema_version` major) is a `422` `invalid_record`; an empty
  part counts as none.
* `document_record` (optional, **deprecated**): an alias of `document_json`; its record comes back as
  `document_json_out`. Both names keep working; send one of them, not both (`422`).
* `include_record` (optional, default `false`): with no record sent, `document_json` comes back anyway, a new
  record holding this upload's `source`, `pages` and `lines`, as the batch pipeline writes one without a baseline.
  The CreateAction lists the new record as an output only.
* `reading_order` (optional, ALTO only): `layoutreader` (default) reorders the lines with LayoutReader, as the
  batch `--method layoutreader`. `alto` reads the page with alto-tools, as `--method alto-tools`: the ALTO's own
  order, a blank line before each text block, a hyphenated line joined to the next. It runs no LayoutReader, so no
  CC BY-NC-SA 4.0 component is logged. Any other value is a `422`. Either way a blank line of the page text is an
  `Empty` line and counts in `line_num`, as in the batch output.

Every response carries `limits_applied`: the limits that shaped the result without refusing it (see
[Limits](#limits)), `[]` when none did. It also carries `paradata`, the call's provenance
([How it works](#how-it-works)). An error response carries no paradata.

```bash
curl -X POST "http://localhost:8000/process" \
  -F "file=@/path/to/page_01.xml" \
  -F "task_type=auto"
```

For a generic JSON OCR-engine export, the endpoint pages the document as the batch pipeline's
`page_split.py` does (`page_split.json_pages`: a page list, or items tagged with a page number), and reads
each page as `extract_JSON_2_TXT.py` reads one (`page_text_lines`): the same key whitelist (`content`,
`text`, `line`, `word`, …) and each text once at line granularity. A plain-text upload is paged at form feeds, as
the batch `text_split.py` pages it:

```bash
curl -X POST "http://localhost:8000/process" \
  -F "file=@/path/to/page_01.json" \
  -F "task_type=auto"
```

### Response schema

The top-level `type` field is `alto_xml`, `plain_text`, `json` or `document`, matching the routed `task_type`.
A `document` result also carries `format` (the detected kind), `origin` (the truthful `source.origin` class), a
`pages` list (`page`, `page_label`, `lines`, and a PDF's `text_layer` and `needs_ocr_reason`), and `page` and
`page_label` on every line. A line's `line_num` restarts per page, as in the batch `DOC_LINE_CATEG`. A text upload
with form feeds and a JSON upload of several pages come back the same way (their `page_label`, `text_layer` and
`needs_ocr_reason` are null), and each page goes into the record under its own key. One page comes back without
`page`. The reader's page verdict is in the response only; the record carries none (record schema 2.0).

Lines are read and shaped by the same `text_formats.py` code as the batch text-lines method: blank lines are dropped
unless `[TEXT_INGEST] KEEP_BLANK_LINES` is set, and lines over 1000 characters are wrapped. With a record
(`document_json`), lines accrete per page, keyed by `page_index`, each with its `text_source` (`text-layer`, `atr`
or `legacy`, from the origin) and, when it has a box, `bbox_precision: measured`. Since record schema 2.0 this
service writes the lines of every record, a born-digital one too (atrium-project#84): the OCR hand-off into a
born-digital record is gone, and the merge of a mixed document is the record builder's (H6, not built yet).

Each item in `cleaned_lines` carries the fields used by the classification pipeline. Every field is typed in
[`openapi.json`](openapi.json) (`ProcessResponse`, `AltoLine`, `AltoPage`); the table below is the short form.

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

### Building the record (`POST /build_record`)

Since ARÚP's output contract of 12 November 2026 ([atrium-project#84](https://github.com/ufal/atrium-project/issues/84)
§1, D20) this service is the record builder: it builds every record from the stored docproc (`atr/docproc-json`),
whichever producer wrote it, the ATR for a scanned document or atrium-digital-convert for a born-digital one, and
is the only scorer of its lines.

```bash
curl -X POST "http://localhost:8000/build_record" \
  -F "docproc=@C-202000543A-DT-27.docproc.json" \
  -F "document_json=@C-202000543A-DT-27.seed.json"     # or: -F "doc_id=C-202000543A-DT-27"
```

* **In:** `docproc` (required): the ATR's schema as delivered (#84 comment 1) or atrium-digital-convert's (the same
  schema with asks 3 and 13 applied); `document_json` (optional): the AMČR seed; `doc_id` when no seed is sent;
  `score` (default `true`).
* **Read** ([`docproc.py`](../docproc.py)): pages by `pageNumber`, blocks in `readingOrder`. A block's own
  `lines[]` are its lines, each with its box (`bbox_precision: measured`); otherwise its `content` split at line
  breaks, each line with the block's box (`derived`), and a block without a line break is one line. A table gives
  one line per `cellGrid` row, its cells joined by a tab. An image block without text gives no line.
* **Written:** `source` (the seed's identity, and `origin`, `page_count`, `language`), `pages[]` (a row for every
  page of the original: `page_index`, `page`, `content_status`, and `canvas` when docproc gives the page a size),
  `lines[]` (from 1 per page in reading order, each with its docproc `block`, `text_source` and `bbox_precision`)
  and `content.text`; with `score`, every line's `categ`, `quality_score` and `lang`, each page's `quality_score`
  and `quality_band`, and `quality_summary`, as `/score_record` writes them. A page routed `empty` has
  `content_status: empty` and no score (D18 rule 7).
* **Refused (`422`, `cause` the reader's code), nothing returned (D13):** a docproc that cannot be read
  (`invalid_docproc`), a page neither in `pages[]` nor routed `empty` (`unread_pages`, H15), a block whose text could
  not be read (`all_engines_failed`), and a page flagged `needsAtr` (`needs_atr`): its text comes in the ATR's
  docproc, and the merge of a mixed document (H6) is not built yet.
* **Out** (`BuildRecordResponse`): `document_json`, `limits_applied` and `paradata`: `object` the docproc and the
  seed, `result` the record with the blocks and fields written, each by SHA-512.

**On the command line:** `python3 build_record.py DOC.docproc.json --seed SEED.json --out-dir records/`
(`--doc-id` without a seed, `--no-score`, `--paradata-dir`) runs the same code
([build_record.py](../build_record.py)). A failed build exits 1, writes no record, and its paradata say
`action_status: failed`.

### Scoring a record (`POST /score_record`)

`/score_record` scores the lines of an existing record with the common quality model, in place: one model, one set
of constants and one band scheme for every text source (D18 rules 1–3). `/build_record` scores as it builds; this
endpoint re-scores a record, and is what the batch stages and a record built elsewhere use.

```bash
curl -X POST "http://localhost:8000/score_record" \
  -F "document_json=@report.document.json"          # optional: -F "pages=i,ii,3"
```

* **In:** `document_json` (required): any record; `pages` (optional): the record's own page keys, comma-separated
  (a key the record lacks is refused, `422`).
* **Scored:** each `lines[]` row with text, **exactly as it is**: not re-read, split or reordered, so every answer
  maps back onto its row, in batches of `PPL_BATCH_LINES`, by the same scorer as every other path. Every line with
  text is scored, whatever its text source; until #84 a line carrying atrium-digital-convert's decode verdict was
  left out, and that verdict is now a docproc flag, not a category. A line without text is not scored.
* **Written into the record** (`document_hook.write_scores()`): `lines[].categ` / `quality_score` / `lang` and
  `pages[].quality_score` / `quality_band`, on rows the record already has: never `text`, never `bbox`, never a new
  row, never `source`, and no page score on an empty page. The record's `quality_summary` is recomputed from the
  scored record: page totals (empty pages counted apart), the mean, median, minimum and maximum of the pages'
  `quality_score`, and the lines per `categ`.
* **Out** (`ScoreRecordResponse`): `cleaned_lines` (every `AltoLine` field plus the record's `page` and `line`;
  `line_num` restarts per page), `pages` (`lines_scored`, `skipped_empty`, `quality_score`, `quality_band`),
  `limits_applied`, `document_json` (the record, scored; as sent when nothing was scored) and `paradata`.
* **Limits:** `MAX_UPLOAD_MB` for the record part, `ATRIUM_TEXT_INGEST_MAX_PAGES` for the pages scored and
  `ATRIUM_TEXT_INGEST_MAX_LINES_PER_PAGE` for the lines of one page (`413 limit_exceeded`). A record that cannot be
  opened, or has no `doc_id`, is `422 invalid_record`.

atrium-digital-convert's `POST /describe` (parked, H11) still calls this endpoint when `OCR_POSTPROCESS_URL` is set.

**On the command line:** `python3 score_record.py <record or folder> [--pages i,ii] [--out-dir DIR] [--scores]`
runs the same code ([record_scoring.py](../record_scoring.py)) over `*.document.json` files, in place unless
`--out-dir` is given, without the two page and line limits; `--scores` writes the answer without the record as
`<doc>.scores.json` ([usage](../docs/usage.md#score-an-existing-record-score_recordpy)). The record file it writes
is byte for byte the one its paradata name by SHA-512.

## Errors

Every error has one JSON body (hub `docs/agent_skill_strategy.md` §4.4):
`{"status": <int>, "reason": <code or null>, "detail": "<text>"}`. `detail` starts with the reader's own code where
there is one (`too_large: …`, `corrupt: …`); a `limit_exceeded` body adds `limit` (`key`, `env`, `value`,
`observed`, `unit`), and a request-validation 422 adds `errors`. A reader refusal also carries the reader's code as
`cause` (informational, not registered: it may change; `reason` is the stable one), and a `415` lists the extensions
the readers take as `accepted`.

| Code | `reason`                 | Meaning                                                                                                                                                                                                                       | What to do                                                                                              |
|------|--------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------|
| 413  | `limit_exceeded`         | over `MAX_UPLOAD_MB` or a `[TEXT_INGEST]` size or count limit (`too_large`, `zip_limits_exceeded`)                                                                                                                            | split the document, or raise the setting `limit.env` names                                              |
| 415  | `unsupported_media_type` | a kind of file this service does not read (`binary_content`, `image_needs_ocr`, `legacy_office_unsupported`, `archive_unsupported`)                                                                                           | send a kind in `accepted`; an image needs OCR first; convert DOC or XLS to DOCX or XLSX                 |
| 422  | `invalid_record`         | the `document_json` / `document_record` part cannot be opened (not UTF-8 JSON, not an object, a newer `schema_version` major), or its `pages` or `lines` is not a list of objects                                             | send the record the previous stage returned, or none; update this service if the record is newer        |
| 422  | `limit_exceeded`         | the PDF reader's time limit (`timeout`)                                                                                                                                                                                       | split the PDF or raise `ATRIUM_TEXT_INGEST_READER_TIMEOUT_S`; a retry of the same file will not help    |
| 422  | `null`                   | a supported file that cannot be read (`corrupt`, `encrypted`, `malformed`, `no_text`, …), a JSON upload that does not parse, an ALTO upload that is not ALTO (`malformed`), or request validation (an unknown `task_type`, …) | repair or decrypt the file `cause` names; send ALTO with `task_type=alto`; fix the field `errors` names |
| 500  | `null`                   | processing failure                                                                                                                                                                                                            | read the service log; retry once; report a repeat with the input                                        |
| 501  | `null`                   | this deployment lacks a reader's optional dependency (`dependency_missing`: pypdfium2, lxml)                                                                                                                                  | use the published image, which has them, or install the dependency `detail` names                       |
| 503  | `null`                   | the service is warming up, or shutting down                                                                                                                                                                                   | retry with backoff, against a live replica                                                              |

```json
{"status": 413, "reason": "limit_exceeded",
 "detail": "too_large: 25000 pages > MAX_PAGES=20000 (setting: ATRIUM_TEXT_INGEST_MAX_PAGES, or [TEXT_INGEST] MAX_PAGES in the config)",
 "limit": {"key": "max_pages", "env": "ATRIUM_TEXT_INGEST_MAX_PAGES", "value": 20000, "observed": null, "unit": "pages"}}
```

## Limits

Every limit this service has is an environment setting, declared once in
[`tool_limits.py`](../tool_limits.py), reported with its current value in `GET /info` `limits` and with the
variable that sets it in `limits_meta`. The `[TEXT_INGEST]` caps are also keys of the config file
(`LANGID_CONFIG`, `setup/config.txt`), which the batch pipeline reads: the variable wins over the file, and the
file over the default below. A malformed value stops the service at startup, naming it. Over a limit the
service **refuses** (`reason: "limit_exceeded"`) or **processes the input in full** and says how the limit
shaped the result, in the response's `limits_applied` (and the paradata). A limit that would trim, skip or stop
content refuses: nothing partial is returned (D13, atrium-project#84). `tests/test_limits_contract.py`
checks this table against `tool_limits.py` and `.env.example`.

| Key (`/info`)        | Variable                                | Default | Unit    | Over the limit                                                                                              |
|----------------------|-----------------------------------------|---------|---------|-------------------------------------------------------------------------------------------------------------|
| `max_upload_mb`      | `MAX_UPLOAD_MB`                         | 25      | MB      | 413 `limit_exceeded` — per part: the file and the document record                                           |
| `max_file_mb`        | `ATRIUM_TEXT_INGEST_MAX_FILE_MB`        | 256     | MB      | 413 `limit_exceeded` (`too_large`); a ZIP member over it: 422 `limit_exceeded` (D13)                          |
| `zip_max_members`    | `ATRIUM_TEXT_INGEST_ZIP_MAX_MEMBERS`    | 10000   | members | 413 `limit_exceeded` (`zip_limits_exceeded`)                                                                |
| `zip_max_total_mb`   | `ATRIUM_TEXT_INGEST_ZIP_MAX_TOTAL_MB`   | 1024    | MB      | 413 `limit_exceeded`                                                                                        |
| `zip_max_member_mb`  | `ATRIUM_TEXT_INGEST_ZIP_MAX_MEMBER_MB`  | 256     | MB      | 413 `limit_exceeded`                                                                                        |
| `zip_max_ratio`      | `ATRIUM_TEXT_INGEST_ZIP_MAX_RATIO`      | 200     | ratio   | 413 `limit_exceeded`                                                                                        |
| `max_pages`          | `ATRIUM_TEXT_INGEST_MAX_PAGES`          | 20000   | pages   | 413 `limit_exceeded` (`too_large`)                                                                          |
| `max_lines_per_page` | `ATRIUM_TEXT_INGEST_MAX_LINES_PER_PAGE` | 100000  | lines   | a paged format: 413 `limit_exceeded`; any other: split onto continuation pages, read in full — `split` note |
| `reader_timeout_s`   | `ATRIUM_TEXT_INGEST_READER_TIMEOUT_S`   | 300     | s       | 422 `limit_exceeded` (`timeout`, PDF reader) — a per-input budget, a retry will not help                    |
| `max_line_chars`     | `ATRIUM_TEXT_INGEST_MAX_LINE_CHARS`     | 1000    | chars   | wrapped at a word boundary, classified in full as several lines — `split` note                              |
| `odf_repeat_cap`     | `ATRIUM_TEXT_INGEST_ODF_REPEAT_CAP`     | 100     | repeats | repeated ODS/ODT cells or rows past it would not be read: 422 `limit_exceeded` (D13)                        |
| `pdf_object_cap`     | `ATRIUM_TEXT_INGEST_PDF_OBJECT_CAP`     | 20000   | objects | the text layer is judged on the first N objects (the text is read in full) — `sampled` note                 |
| `lr_chunk_size`      | `LR_CHUNK_SIZE`                         | 350     | lines   | an ALTO page is ordered window by window — `split` note                                                     |
| `lr_min_chunk_size`  | `LR_MIN_CHUNK_SIZE`                     | 50      | lines   | out of memory below it: 422 `limit_exceeded` (D13), the page is not left unordered                          |
| `ppl_batch_lines`    | `PPL_BATCH_LINES`                       | 32      | lines   | lines per perplexity pass; out of memory splits it; a line that fails alone: 422 `limit_exceeded` (D13)     |
| `ppl_max_tokens`     | — (derived from `GPT2_MODEL_NAME`)      | —       | tokens  | a line longer than N tokens: 422 `limit_exceeded` (D13; `null` until the model loads)                       |

Platform limits (not settings): libxml2's default limits (parsed without `huge_tree`); Starlette's multipart
defaults.

## Partial results

A 200 can be a result a limit or a reader shaped. It says so in three places:

- **`limits_applied`** (also in the paradata): `split` (a long line or page read in pieces, classified in full) and
  `sampled` (a PDF's text layer judged on its first objects). [Limits](#limits) says which limit produces which
  note. Since atrium-project#84 (D13) a limit that would trim, skip or stop content fails the call instead
  (`422 limit_exceeded`, nothing returned): repeated spreadsheet cells over the cap, a line's tokens past the
  perplexity model's length, a ZIP member over the size limit, a page layout ordering could not order for lack of
  memory, a line that failed perplexity even alone.
- **A document's `pages`**: per page, a PDF's `text_layer` and its `needs_ocr_reason` when the page's text should
  come from OCR instead.
- **`/score_record`'s `pages`**: `lines_scored` and `skipped_empty`.

The batch pipeline has the same notes in its paradata, and a text-lines input read with a possible loss is
`partial` in its `ingest_report.csv`.

## Known limits

- **The rules are Czech-first:** lines in other languages are scored less reliably
  ([details](../docs/categorization_logic.md#foreign-language-lines)).
- **Short lines without Czech diacritics** read `Clear` more often than they should
  ([measurements](../docs/evaluation/categorization.md)).
- **No document passes in the service:** each line is answered on its own, so a whole document's categories can
  differ from a batch run's. Every difference from the command line, and why:
  [docs/usage.md](../docs/usage.md#the-command-line-and-the-service).
- **Two models are downloaded on first start** at the Hub's current revision (see
  [Models and caches](#models-and-caches)).
- **FastText is CC BY-NC 4.0** and runs on every request; what a run records about licences is in
  [docs/reference/paradata.md](../docs/reference/paradata.md).

## Configuration (environment)

| Variable              | Default   | Meaning                                                                 |
|-----------------------|-----------|-------------------------------------------------------------------------|
| `PORT`                | `8000`    | port the service **binds**, and the one `service/healthcheck.py` probes |
| `HOST`                | `0.0.0.0` | bind address; see the warning below                                     |
| `GRACEFUL_SHUTDOWN_S` | `20`      | seconds uvicorn waits for in-flight requests before closing them        |
| `RELOAD`              | `false`   | filesystem auto-reload — development only, never in a deployment        |
| `LOG_LEVEL`           | `INFO`    | root logger level for the `python service/text_api.py` start path       |
| `ALLOWED_ORIGINS`     | `*`       | CSV of CORS origins — no shared default across the six services         |
| `MAX_UPLOAD_MB`       | `25`      | canonical upload limit — no shared default across the six services      |
| `MODEL_DIR`           | see below | directory models are loaded from                                        |
| `GPT2_MODEL_NAME`     | see below | quality-estimation model id                                             |

This table is the deployment-facing subset. The complete ledger — every variable this
image reads, including `MODEL_DIR`/`GPT2_MODEL_NAME`/`LAYOUT_MODEL_PATH`/`LANGID_CONFIG`
and the other algorithmic knobs a deployment does not normally touch — is
[`.env.example`](../.env.example) at the repo root, whose layout is fixed by
`docs/templates/env.example.template` in ufal/atrium-project. The cross-service operator
reference is `docs/k8s_deployment.md` in that same repo.

`PORT` and `HOST` are read by `service/text_api.py`'s `__main__` block, which is what the `api` image's `ENTRYPOINT` runs.

ocr-postprocess starts with `python service/text_api.py` (a script launch, made viable by the `sys.path` bootstrap at
the top of that file) rather than the `python -m service.api` the other services use; the environment contract is
the same either way.

> `HOST=127.0.0.1` yields a container that reports **healthy** and serves nobody:
> `service/healthcheck.py` always probes loopback by design and never reads `HOST`, so a
> loopback bind passes every probe while being unreachable from outside the container.

## How it works

`/process` reads the upload into pages and lines with the same code as the batch pipeline (`text_formats.py` for
every format but ALTO). For ALTO it puts the lines in reading order with LayoutLMv3, which matters for multi-column
pages, and joins words hyphenated across lines (ALTO `SUBS_TYPE` / `SUBS_CONTENT`, then a regular-expression
reconstruction). Each line then goes through the batch pipeline's pre-filter and scoring function
(`text_util.pre_filter_line()`, `classify_TEXT.score_line()`): FastText gives its language, Qwen2.5-0.5B its
perplexity, and a quality score from nine signals, with a short list of rules, gives its category. A line therefore
gets the same category from
the service and from the batch pipeline. What the categories mean and how they are decided:
[How a line is categorised](../docs/categorization_logic.md).

Inference runs in a worker thread, and the GPU work of concurrent requests takes turns under one lock.
`POST /score_record` scores the lines of a record as they are, in batches of `PPL_BATCH_LINES`.

Every `/process`, `/build_record` and `/score_record` response carries **`paradata`**, the call's provenance
(paradata 3.0, atrium-project#84): one Process Run Crate `CreateAction`, built by
`atrium_rocrate.action_from_paradata()`. `object` names what the call read, by AMČR distribution name and SHA-512:
the upload as `original` (AMČR's name for that distribution is still open), the docproc as `atr/docproc-json`, the
record or seed sent as `atrium/document-json`. `result` names the record written, with the blocks and fields the
call wrote. The classified lines are the answer, not a stored output, so they are not named. `agent` is
`ATRIUM_RUN_AGENT` when set. The record itself carries no run data, and the service writes no paradata file. Hub
[`docs/rocrate_export.md`](https://github.com/ufal/atrium-project/blob/main/docs/rocrate_export.md) §5 describes it.

## Shutdown behaviour

The published `api` image (`ghcr.io/ufal/atrium-ocr-postprocess-api:<version>`) declares `HEALTHCHECK` (shallow
`GET /health`, through the vendored `service/healthcheck.py`) and `STOPSIGNAL SIGTERM`. `service/text_api.py`'s own
`__main__` block, which is this repository's production start path, passes `timeout_graceful_shutdown`
(`GRACEFUL_SHUTDOWN_S`, default 20 s). See [Configuration (environment)](#configuration-environment) for the full
set.

On `SIGTERM` the service flips `GET /ready` to **503** at once so an orchestrator stops routing to it, answers new
`/process` calls with 503, and lets in-flight processing finish before exiting. `GET /health` deliberately stays 200
throughout: a liveness probe that failed mid-shutdown would get the container killed before the drain completed.

Inference runs in a worker thread (`asyncio.to_thread`), not inline on the event loop. Uvicorn's `SIGTERM` handler
is an event-loop callback, so while a synchronous `process_alto()` held the loop the signal could not be processed
at all. Draining also matters here because `/process` writes a `delete=False` temp file that only its own `finally`
clause removes: a request killed by `SIGKILL` mid-flight leaves that file behind.

A model that fails to load at startup deliberately raises, so a misconfigured deployment crash-loops on the startup
probe rather than sitting "not ready" forever; see `docs/k8s_deployment.md` ("Known limits") in the hub.

A clean shutdown exits **143** (128 + SIGTERM), not 0: uvicorn re-raises the captured signal on purpose so a
supervisor sees the real cause. That is a normal stop, not a crash.

## Frontends

### Standalone frontend

`service/frontend/` is a self-contained interface with no build step or external framework required.
It is served directly by the FastAPI server at `http://localhost:8000` and works out of the box.

**To use it**, start the server (see [Start](#start)) and open `http://localhost:8000` in your browser.

Features:
- Drag-and-drop or click-to-upload for `.xml`, `.txt` and `.json` files and for PDF, DOCX, ODT, XLSX, PPTX, EPUB,
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

## OpenAPI (the typed contract)

The service's OpenAPI document is committed as [`service/openapi.json`](openapi.json) and attached to every release
as `openapi.json` with its `openapi.json.sha256`. It is what a client is generated from. Every request and response
field is typed, every error response is the `ErrorBody` above, the registered `reason` codes are listed in
`x-atrium-reason-codes`, and a returned record is typed by the vendored record schema (`AtriumDocument`).
`GET /info` reports `openapi_sha256`, the digest of the spec the running image serves: equal to the release's
`openapi.json.sha256` for an image built from that tag. `document_record` and `document_json_out` are marked
`deprecated` there.

- **After an API change**, regenerate and commit it:
  `python atrium_openapi.py export --app service.text_api:app --out service/openapi.json`.
  `tests/test_openapi_contract.py` fails while it is stale.
- **Compatibility.** Each release compares its spec with the previous release's
  (`release.yml`, `atrium_openapi.py compare` with oasdiff): a breaking change fails the
  release unless the major version went up (for 0.x, that means 1.0), and a removed reason
  code always fails. New fields, endpoints and reason codes are additive.
- **fastapi and pydantic are pinned** exactly (`service/requirements.txt`,
  `setup/requirements-test.txt`): the spec is generated by them. Bump both by hand and regenerate.

## Tests

The fast lane needs no model, GPU or network:

```bash
pip install -r setup/requirements-test.txt
pytest -m "not slow" tests/test_service_api.py tests/test_api_contract.py tests/test_openapi_contract.py \
       tests/test_service_hardening.py tests/test_score_record.py tests/test_build_record.py \
       tests/test_limits_contract.py tests/test_env_contract.py          # the service and its contracts
pytest -m "not slow"                                                    # the whole fast lane
```

- `tests/test_api_contract.py` drives `/process` with canned model results and holds every response, the 200s and the
  refusals, to the published schema.
- `tests/test_openapi_contract.py` (vendored from the hub) checks the committed spec itself; it fails while the spec
  is stale.
- `tests/test_limits_contract.py` and `tests/test_env_contract.py` check the [Limits](#limits) table and the
  [Configuration](#configuration-environment) table against the code and `.env.example`.
- `tests/test_service_hardening.py`, `tests/test_score_record.py` and `tests/test_build_record.py` cover the shutdown,
  the record scoring and the record builder (the docproc reader, `build_record.py` and `/build_record`, on docproc
  fixtures in the ATR's shape and from atrium-digital-convert).
- `tests/test_cli_service_parity.py` pins what the command line and the service answer alike, and what differs on
  purpose ([the table](../docs/usage.md#the-command-line-and-the-service)).

## Contacts and acknowledgements

Contact, licences and acknowledgements: the [README](../README.md#licence-citation-and-acknowledgements).
