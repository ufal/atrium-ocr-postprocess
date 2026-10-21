# Development

Setting up, testing and changing ocr-postprocess. Branches, pull requests, commit messages and the release history
are in [CONTRIBUTING.md](../CONTRIBUTING.md); the ATRIUM documentation rules in the hub's
[documentation-style.md](https://github.com/ufal/atrium-project/blob/main/docs/documentation-style.md).

## Tests

- `pytest -m "not slow"` runs the fast suite, as CI does.
- `tests/test_categorization_walkthrough.py` follows every worked example through the categoriser and checks the
  table in [the rules](reference/categorization_rules.md#worked-examples); after changing a case, regenerate the
  table as that file's `worked_examples_table()` says.
- `tests/test_scoring_single_source.py` and `tests/test_service_hardening.py` keep the batch pipeline, the service
  and the re-scorer on one scoring function.
- The files vendored from the ATRIUM hub (`atrium_*.py`, `para_licenses.py`, `service/atrium_service.py`, the schemas
  and their tests) are never edited here; see the hub's
  [shared_components.md](https://github.com/ufal/atrium-project/blob/main/docs/shared_components.md).

## Measuring a categorisation change

[The evaluation page](evaluation/categorization.md#measuring-a-change) lists the tools:
`tools/recategorize_from_csv.py`, `tools/corpus_change.py` and `tools/rule_coverage_report.py`.

## Vendored code

Some third-party code is **copied into this repository** rather than installed as a dependency. Vendored code
keeps its original licence; the repository's own [LICENSE](../LICENSE) (MIT) does not apply to it. Licence texts
live in [LICENSES/](../LICENSES) .

### `alto_tools.py`: ALTO text extraction and statistics

|                  |                                                                                                                                                             |
|------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Upstream**     | [cneud/alto-tools](https://github.com/cneud/alto-tools) by Clemens Neudecker                                                                                |
| **Commit**       | [`1f4f01e5f6ac3562740e39948442973b9cb94be4`](https://github.com/cneud/alto-tools/commit/1f4f01e5f6ac3562740e39948442973b9cb94be4) (`__version__ = "0.1.0"`) |
| **Source file**  | `src/alto_tools/alto_tools.py`                                                                                                                              |
| **Licence**      | **Apache-2.0** — full text in [LICENSES/alto-tools-Apache-2.0.txt](../LICENSES/alto-tools-Apache-2.0.txt)                                                   |
| **Local copy**   | [alto_tools.py](../alto_tools.py)                                                                                                                           |
| **Used by**      | [alto_stats_create.py](../alto_stats_create.py) (Step 2, statistics) and [extract_ALTO_2_TXT.py](../extract_ALTO_2_TXT.py) (Step 3, alto-tools method)      |
| **Parity tests** | [tests/test_alto_tools.py](../tests/test_alto_tools.py)                                                                                                     |

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

**Why** (issue [#50](https://github.com/ufal/atrium-alto-postprocess/issues/50)) — `alto-tools` has no PyPI
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
> [tests/test_alto_tools.py](../tests/test_alto_tools.py) pins this against output captured from the upstream
> command line at the commit above, and re-derives it independently for every ALTO file in
> [data_samples](../data_samples) .

**Updating it** — re-copy the two code paths from a newer upstream commit, update `UPSTREAM_COMMIT` in
[alto_tools.py](../alto_tools.py) and the table above, then run `pytest tests/test_alto_tools.py`. Golden values
in that file are upstream CLI output and must only change when upstream's behaviour genuinely does.

