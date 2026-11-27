# ATRIUM ocr-postprocess: the document record, with every text line sorted into five quality categories

ocr-postprocess takes the text of a document, as text recognition read it from the scans or as a born-digital
file carries it, and builds the ATRIUM document record from it: one structured JSON record per document, with
its pages and its text lines. It judges every line: its language, a quality score from 0 to 1, and one of five
categories, `Clear`, `Noisy`, `Trash`, `Non-text` or `Empty`. The categories tell an archive which lines can be
read and passed on to language processing, and which pages are legible at all.

[Releases](https://github.com/ufal/atrium-ocr-postprocess/releases) · [MIT licence](LICENSE) ·
[ATRIUM documentation](https://ufal.github.io/atrium-project/workflows/ocr-postprocess/)

## Inputs and outputs

| Input                                                                                                                                                                                         | Output                                                                                                                                |
|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------|
| docproc (`atr/docproc-json`), the layout format of the ATR and of digital-convert; both docprocs for a mixed document                                                                         | the ATRIUM document record (`atrium/document-json`): `source`, `pages`, `lines` and `content`, every line scored                      |
| optionally, AMČR's seed of the record: the document's identifier and the checksum of its original                                                                                             | the paradata of the run: what it read and wrote, by distribution name and SHA-512                                                     |
| an existing record (`*.document.json`)                                                                                                                                                        | the same record with its lines and pages scored again                                                                                 |
| for research collections: ALTO XML, PAGE XML, hOCR, ABBYY FineReader XML, DjVuXML, Tesseract TSV, OCR JSON, PDF text layers, office files, EPUB, RTF, HTML, e-mail, Markdown, CSV, plain text | a per-line CSV with the language, the quality score and the category of every line; page statistics; optionally a record per document |

The ATR is AIS CR's text-recognition service; a mixed document has pages with a usable text layer and pages
only the ATR can read. The record follows the hub's
[document contract](https://ufal.github.io/atrium-project/ecosystem/document-contract/), and this tool is its
builder. The tools write record schema 2.0. The published schema is `doc-schema-v1`; 2.0 is published as
`doc-schema-v2` once the pilot has proven it. Every research input format, with its standard and what is kept
of it: [docs/text_inputs.md](docs/text_inputs.md).

| Entry point                      | Takes                                                              | Returns                                                                              |
|----------------------------------|--------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| `POST /build_record`             | one docproc (two for a mixed document), optionally the AMČR seed   | the record, built and scored, and the run's paradata (the AMČR pilot uses this call) |
| `build_record.py`                | the same, from the command line                                    | the record and a paradata file                                                       |
| `POST /score_record`             | one record                                                         | the scored lines and the record                                                      |
| `score_record.py`                | records (`*.document.json`), or folders of them                    | the records with this tool's scores; with `--scores`, the scored lines               |
| `POST /process`                  | one page or document in any research format, optionally its record | the scored lines (page by page for a document), the record when sent or asked for    |
| `run_pipeline.py` and its stages | folders of documents in the research formats                       | per-page files, the per-line CSV, page statistics, records, a paradata file          |

What only one of them does, and why: [The command line and the service](docs/usage.md#the-command-line-and-the-service).

## Quick start

**What you need:** Python 3.11 and a checkout, or Docker. Network access to the Hugging Face Hub, once, for the
models: the language-identification model (1.18 GB) and the perplexity model, Qwen2.5-0.5B (about 1 GB); the
image already holds the first. No GPU: both run on a CPU. No other host is called.

Build a record from the sample docproc in this repository, the ATR's reading of a three-page scan:

```bash
git clone https://github.com/ufal/atrium-ocr-postprocess.git && cd atrium-ocr-postprocess
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r setup/requirements.txt
mkdir -p models && wget -O models/lid.176.bin \
  "https://huggingface.co/facebook/fasttext-language-identification/resolve/3af127d4124fc58b75666f3594bb5143b9757e78/model.bin"
python3 build_record.py tests/fixtures/docproc/scan.atr.docproc.json --doc-id CTX000000001 \
  --out-dir out --paradata-dir out/paradata
```

The same as a service, from the published image. Start it in the background, wait until it is ready, call it,
and stop it:

```bash
docker run -d --name atrium-ocr-postprocess -p 8000:8000 ghcr.io/ufal/atrium-ocr-postprocess-api:<version>   # 2.0.0-beta or later, without its v
until curl -fsS localhost:8000/ready >/dev/null; do sleep 5; done
curl --fail-with-body -sS -X POST localhost:8000/build_record --create-dirs -o out/record.json \
     -F "docproc=@tests/fixtures/docproc/scan.atr.docproc.json" -F doc_id=CTX000000001
docker rm -f atrium-ocr-postprocess
```

Expected result (abridged): `out/CTX000000001.document.json` from the command line, and the same record under
`document_json` in `out/record.json` from the service:

```json
{
  "schema_version": "2.0",
  "doc_id": "CTX000000001",
  "pages": [{"page_index": 1, "page": "1", "content_status": "text", "quality_score": 0.8951, "quality_band": "Clear"}, …],
  "lines": [{"page": "1", "line": 1, "text": "Zpráva o archeologickém výzkumu", "text_source": "atr",
             "categ": "Clear", "quality_score": 0.9462, "lang": "ces_Latn"}, …]
}
```

Three pages, one of them empty, and six lines, all `Clear`; the scores can differ in the last digits on a GPU.
The service downloads Qwen2.5-0.5B and LayoutReader on its first start ([Models and
caches](service/README.md#models-and-caches)); without access to Hugging Face it stops at startup. Releases
before 2.0.0-beta have no `/build_record` and are described by the README at their tag.

## How it works

Each line is read with two models as witnesses: FastText for its language and Qwen2.5-0.5B for its perplexity,
how surprising the text is. A quality score is built from nine measurements of the text, and a short list of
rules decides the cases the score gets wrong, such as an upside-down scan or a web address. When a whole
document is scored, a few document passes then correct isolated mistakes from the rest of the page.

| Category   | Meaning                                                                 | In the pipeline                                                 |
|------------|-------------------------------------------------------------------------|-----------------------------------------------------------------|
| `Clear`    | legible; nobody needs the page image to read it                         | goes on to language processing and keywords                     |
| `Noisy`    | decipherable through a mistake: a swapped letter, a digit inside a word | goes on as well; its readable words can be corrected            |
| `Trash`    | illegible                                                               | left out of language processing and keywords; it is not re-read |
| `Non-text` | numbers, codes, stamps, dates, measurements                             | goes on; worth checking for identifiers of finds or sites       |
| `Empty`    | nothing on the line                                                     | left out                                                        |

`Trash` means illegible, however useful the line is: that is the data providers' definition. No step sends a
page back for another recognition pass; re-processing is a later, separate campaign. How each step decides:
[How a line is categorised](docs/categorization_logic.md).

## Where it fits in ATRIUM

In the AMČR pipeline, digital-convert and the ATR read each document and write its layout as docproc; one
docproc is stored per document. ocr-postprocess builds the record from it with `POST /build_record`. For a
mixed document it takes the ATR's docproc and digital-convert's together, and keeps the text layer's lines on
the pages where they are good, and an office document's own lines on every page, adding the ATR's text where they
lack it (a pasted scan). It is the only tool that scores lines. page-classification's page categories then
join the record's page rows, nlp-enrich adds the linguistic layer and keyword-extract the keywords. The whole
route: the hub's [Pipelines](https://ufal.github.io/atrium-project/pipelines/#the-route-through-the-pipeline)
page and this tool's [workflow page](https://ufal.github.io/atrium-project/workflows/ocr-postprocess/). Its SSH
Open Marketplace record:
[OCR output post-processing and quality control (text-line categorization) - ATRIUM](https://marketplace.sshopencloud.eu/tool-or-service/YParYU).

## Limits and maturity

- **The rules are Czech-first.** Lines in other languages are scored less reliably: a clean English or German
  sentence can read `Noisy`, and words with `w`, `q` or `x` count against a line. Check a collection in another
  language before you trust its categories ([details](docs/categorization_logic.md#foreign-language-lines)).
- **Short lines without Czech diacritics.** About 26,000 genuine garbage lines over both AMČR collections read
  `Clear`. On a line of one to three tokens without diacritics, `Clear` is less certain than it looks
  ([measurements](docs/evaluation/categorization.md)).
- **`/process` judges each line on its own.** Building or re-scoring a record, and the batch pipeline, run the
  document passes over the whole document; `/process` does not. The same document can therefore get different
  categories from the two ([every difference](docs/usage.md#the-command-line-and-the-service)).
- **The category rules are frozen for the AMČR pilot.** Their accuracy has been measured on a gold set of 2,067
  hand-labelled lines and on corpus diffs, not validated in full. The tests pin the rules, not their accuracy
  ([what was measured](docs/evaluation/categorization.md)).
- **The research paths do not stop at a bad file.** Building a record fails as a whole: a step that cannot finish
  fails the job and writes nothing. The batch pipeline and `/process` instead go on past what they cannot read,
  so they are for research collections, not for writing to AMČR
  ([details](docs/usage.md#a-file-that-cannot-be-read)).
- **The published images run on the CPU.** A GPU needs a CUDA build of torch: a checkout, or the batch image
  built locally with `docker-compose.gpu.yml`. The batch classification and the LayoutReader reading order are
  slow without one, and the GLM extraction method needs about 48 GB of GPU memory
  ([the GLM method](docs/usage.md#glm-method)).

## Documentation

| For                 | To                                                   | Read                                                                                 |
|---------------------|------------------------------------------------------|--------------------------------------------------------------------------------------|
| using it            | build records, run the batch pipeline stage by stage | [docs/usage.md](docs/usage.md)                                                       |
|                     | compare the command line with the service            | [docs/usage.md](docs/usage.md#the-command-line-and-the-service)                      |
|                     | look up every output column                          | [docs/reference/outputs.md](docs/reference/outputs.md)                               |
|                     | read every input format                              | [docs/text_inputs.md](docs/text_inputs.md)                                           |
|                     | see the paradata, licences and the document record   | [docs/reference/paradata.md](docs/reference/paradata.md)                             |
| running the service | run and operate it, read its errors and limits       | [service/README.md](service/README.md)                                               |
|                     | see the API contract                                 | [service/openapi.json](service/openapi.json)                                         |
|                     | set every option                                     | [docs/configuration.md](docs/configuration.md)                                       |
| judging the results | see what was measured                                | [docs/evaluation/categorization.md](docs/evaluation/categorization.md)               |
|                     | see why the rules are as they are                    | [docs/decisions/2026-10-categorization.md](docs/decisions/2026-10-categorization.md) |
| changing the code   | understand how a line is categorised                 | [docs/categorization_logic.md](docs/categorization_logic.md)                         |
|                     | look up every rule and constant                      | [docs/reference/categorization_rules.md](docs/reference/categorization_rules.md)     |
|                     | test, re-vendor shared code; find the history        | [docs/development.md](docs/development.md)                                           |
|                     | follow the conventions                               | [CONTRIBUTING.md](CONTRIBUTING.md)                                                   |

Terms used across the tools are explained in the
[ATRIUM glossary](https://ufal.github.io/atrium-project/glossary/). The documentation follows the ATRIUM
[documentation style](https://github.com/ufal/atrium-project/blob/main/docs/documentation-style.md).

## Licence, citation and acknowledgements

The code is MIT ([LICENSE](LICENSE)). Vendored code keeps its own licence: `alto_tools.py` is from
[cneud/alto-tools](https://github.com/cneud/alto-tools) by Clemens Neudecker, Apache-2.0
([LICENSES/](LICENSES)). The models have their own terms: FastText language identification
([facebook/fasttext-language-identification](https://huggingface.co/facebook/fasttext-language-identification), CC BY-NC 4.0),
[Qwen2.5-0.5B](https://huggingface.co/Qwen/Qwen2.5-0.5B) (Apache-2.0), LayoutLMv3 through
[LayoutReader](https://huggingface.co/hantian/layoutreader) (CC BY-NC-SA 4.0) and
[GLM-4v-9b](https://huggingface.co/THUDM/glm-4v-9b) (GLM-4 licence). Each run lists the components it used,
each with its own licence or terms. No licence of an output is computed: the rights in an output follow the
source document. The inventory is [setup/para_config.txt](setup/para_config.txt); what a run records is in
[docs/reference/paradata.md](docs/reference/paradata.md). Cite with [CITATION.cff](CITATION.cff).

Developed by [ÚFAL](https://ufal.mff.cuni.cz/), Charles University. Funded by the European Union under
Horizon Europe ([ATRIUM](https://atrium-research.eu/), GA 101132163), with the support of the
LINDAT/CLARIAH-CZ (LM2023062) and AIS CR (LM2023031) research infrastructures, funded by the Ministry of
Education, Youth and Sports of the Czech Republic. Contact: the
[issue tracker](https://github.com/ufal/atrium-ocr-postprocess/issues).

Formerly `atrium-alto-postprocess`; its history is in [docs/development.md](docs/development.md#history-of-the-repository).
