# OCR Output Postprocessing Pipeline

ocr-postprocess turns the output of OCR, and other text-bearing files, into ordered text lines with a
quality category for each: `Clear`, `Noisy`, `Trash`, `Non-text` or `Empty`. Use it to decide which pages and
lines can go on to NLP, which need correcting, and which need another OCR pass. It is stage 2 of the ATRIUM
processing chain for scanned archaeological documents.

[Releases](https://github.com/ufal/atrium-ocr-postprocess/releases) · [MIT licence](LICENSE) ·
[ATRIUM documentation](https://ufal.github.io/atrium-project/workflows/ocr-postprocess/)

Formerly `atrium-alto-postprocess`: on 1 October 2026 the project continued here under its new name, with the
pipeline, the categories and the scoring unchanged. Records written before keep the program id `alto-postprocess`,
which the record contract accepts as the same writer.

## Inputs and outputs

| Input                                                                                                            | Output                                                                                                         |
|------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------|
| ALTO XML (the worked example throughout), PAGE XML, hOCR, ABBYY FineReader XML, DjVuXML, Tesseract TSV, OCR JSON | per-page ALTO and text files                                                                                   |
| PDF text layers, office files (DOCX, ODT, XLSX, PPTX, …), EPUB, RTF, HTML, e-mail, Markdown, CSV, plain text     | a per-line CSV with the language, a quality score in [0, 1] and the category of every line                     |
| optionally, the ATRIUM document record of the document                                                           | per-page statistics; the record with this tool's `pages`, `content` and `lines` fields and a `quality_summary` |

The record follows the hub's [document contract](https://ufal.github.io/atrium-project/ecosystem/document-contract/).
Every input format, with its standard and what is kept of it: [docs/text_inputs.md](docs/text_inputs.md).

## Quick start

The service, from the published image (the first start downloads two models from Hugging Face; see
[Models and caches](service/README.md#models-and-caches)):

```bash
docker run --rm -p 8000:8000 ghcr.io/ufal/atrium-ocr-postprocess-api:<version>     # the release without its v
curl --fail-with-body -sS -X POST localhost:8000/process \
     -F "file=@data_samples/PAGE_ALTO/CTX000000001/CTX000000001-1.alto.xml" -F task_type=alto -o out.json
```

Expected: HTTP 200 and `out.json` with `"type": "alto_xml"` and a `cleaned_lines` list in which every line has
`text`, `lang`, `quality_score` and `category`.

The batch pipeline, from a checkout with its [setup](docs/usage.md#setup) done, over the samples in
`data_samples/`:

```bash
python3 run_pipeline.py --dry-run      # the resolved plan: five stages, their inputs and outputs
python3 run_pipeline.py                # split → statistics → text extraction → classification → aggregation
```

Expected, with the shipped `setup/config.txt`: one per-line CSV per document in `data_samples/DOC_LINE_CATEG/`,
page statistics in `samples_page_stats.csv` and `data_samples/DOC_LINE_STATS/`, and one merged
`paradata/<YYMMDD-HHmmss>_pipeline-run.json` describing the run. The default extraction method, LayoutReader, is
slow without a GPU; `--method alto-tools` runs on a CPU.

## How it works

The pipeline splits each document into pages, writes a statistics table of the pages, extracts each page's text
in reading order (five methods, from layout-aware LayoutReader to plain ALTO text), and then classifies every
line. Classification reads each line with two models as witnesses — FastText for its language and Qwen2.5-0.5B
for its perplexity — builds a quality score from nine measurements of the text, and applies a short list of rules
for the cases the score gets wrong. The batch pipeline then corrects isolated mistakes from the rest of the page.

| Category   | Meaning                                                                    | What to do with it                         |
|------------|----------------------------------------------------------------------------|--------------------------------------------|
| `Clear`    | legible; nobody needs the page image to read it                            | pass it on to NLP                          |
| `Noisy`    | decipherable through a mistake: a swapped letter, a digit inside a word    | correct the readable words                 |
| `Trash`    | illegible                                                                  | re-process the page with another OCR tool  |
| `Non-text` | numbers, codes, stamps, dates, measurements                                | check it for identifiers of finds or sites |
| `Empty`    | nothing on the line                                                        | ignore it                                  |

`Trash` means illegible, however useful the line is (the data providers' definition). How each step decides:
[How a line is categorised](docs/categorization_logic.md).

## Where it fits in ATRIUM

It reads what OCR produced for the pages page-classification sent to text recognition, and its lines go on to
nlp-enrich (morphology, named entities, TEITOK) and keyword-extract. For born-digital documents, digital-convert
writes the record and this service scores its lines (`POST /score_record`) and re-reads pages the converter
flags for OCR. The whole chain: the [hub's workflow page](https://ufal.github.io/atrium-project/workflows/ocr-postprocess/).
Its SSH Open Marketplace record:
[OCR output post-processing and quality control (text-line categorization) - ATRIUM](https://marketplace.sshopencloud.eu/tool-or-service/YParYU).

## Limits and maturity

- **The rules are Czech-first.** Lines in other languages are scored less reliably: a clean English or German
  sentence can read `Noisy`, and words with `w`, `q` or `x` count against a line
  ([details](docs/categorization_logic.md#foreign-language-lines)).
- **Short lines without Czech diacritics.** About 26,000 genuine garbage lines over both AMČR collections read
  `Clear`; on a line of one to three tokens without diacritics, `Clear` is less certain than it looks
  ([measurements](docs/evaluation/categorization.md)).
- **The service and the batch pipeline differ on whole documents:** only the batch pipeline runs the document
  passes, so a service call answers each line on its own.
- **The category semantics are frozen for the S2 pilot.** Accuracy has been measured on a gold set of 2,067 lines
  and on corpus diffs, not validated in full; the tests pin the rules, not their accuracy.
- **GPU.** The batch classification and the LayoutReader method want a GPU; the GLM method needs about 48 GB of
  GPU memory. The service runs on a CPU.

## Documentation

| To                                                 | Read                                                                                 |
|----------------------------------------------------|--------------------------------------------------------------------------------------|
| run the pipeline, stage by stage                   | [docs/usage.md](docs/usage.md)                                                       |
| run and operate the service, its errors and limits | [service/README.md](service/README.md)                                               |
| see the API contract                               | [service/openapi.json](service/openapi.json)                                         |
| set every option                                   | [docs/configuration.md](docs/configuration.md)                                       |
| understand how a line is categorised               | [docs/categorization_logic.md](docs/categorization_logic.md)                         |
| look up every rule and constant                    | [docs/reference/categorization_rules.md](docs/reference/categorization_rules.md)     |
| look up every output column                        | [docs/reference/outputs.md](docs/reference/outputs.md)                               |
| read every input format                            | [docs/text_inputs.md](docs/text_inputs.md)                                           |
| see the paradata, licences and the document record | [docs/reference/paradata.md](docs/reference/paradata.md)                             |
| see what was measured                              | [docs/evaluation/categorization.md](docs/evaluation/categorization.md)               |
| see why the rules are as they are                  | [docs/decisions/2026-10-categorization.md](docs/decisions/2026-10-categorization.md) |
| contribute, test, re-vendor code                   | [docs/development.md](docs/development.md), [CONTRIBUTING.md](CONTRIBUTING.md)       |

The documentation follows the ATRIUM
[documentation style](https://github.com/ufal/atrium-project/blob/main/docs/documentation-style.md).

## Licence, citation and acknowledgements

The code is MIT ([LICENSE](LICENSE)). Vendored code keeps its own licence: `alto_tools.py` is from
[cneud/alto-tools](https://github.com/cneud/alto-tools) by Clemens Neudecker, Apache-2.0
([LICENSES/](LICENSES)). The models have their own terms: FastText language identification
([facebook/fasttext-language-identification](https://huggingface.co/facebook/fasttext-language-identification), CC BY-NC 4.0),
[Qwen2.5-0.5B](https://huggingface.co/Qwen/Qwen2.5-0.5B) (Apache-2.0), LayoutLMv3 through
[LayoutReader](https://github.com/ppaanngggg/layoutreader) (CC BY-NC-SA 4.0) and
[GLM-4v-9b](https://huggingface.co/THUDM/glm-4v-9b) (GLM-4 licence); the inventory is
[setup/para_config.txt](setup/para_config.txt), and what a run records about them is in
[docs/reference/paradata.md](docs/reference/paradata.md). Cite with [CITATION.cff](CITATION.cff).

Developed by [ÚFAL](https://ufal.mff.cuni.cz/home-page), Charles University. Funded by
[ATRIUM](https://atrium-research.eu/) (GA 101132163), with the support of the LINDAT/CLARIAH-CZ research
infrastructure and the AIS CR infrastructure (LM2023031). Contact: the
[issue tracker](https://github.com/ufal/atrium-ocr-postprocess/issues).
