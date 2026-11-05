# Strategy — Decision models on ambiguous text lines (from [page-classification#58](https://github.com/ufal/atrium-page-classification/issues/58))

> _Drafted **2026-11-05** at `v1.15.3-beta` (`9ab887a`; the checkout's head `0b1ce1c`). This plan has no issue of
> its own yet. It is this repository's part of the cross-repository plan for decision models, anchored on
> [page-classification#58](https://github.com/ufal/atrium-page-classification/issues/58) and its
> [plan](https://github.com/ufal/atrium-page-classification/blob/test/agent_dev_logs/plans/58.plan.md). It relates to
> [#4](https://github.com/ufal/atrium-ocr-postprocess/issues/4) (a learned quality model, deferred past the pilot).
> Nothing has been run._

**Status**: 🧪 **Exploratory, offline and in shadow mode.** The category semantics are frozen for the S2 pilot
(`docs/decisions/2026-10-categorization.md`). This plan therefore changes no rule, threshold, column, record field or
default. It measures whether a decision model can settle the lines the rule cascade is least sure of.

## Context

**What a decision model does.** It reads a state (here: a line and its context) and typed questions, in one prefill
pass. It returns a probability for each allowed option and generates no text. The candidates are the same as in #58,
all served at `POST /v1/systemone`:

| Model              | Size | Here                                                                |
|--------------------|------|---------------------------------------------------------------------|
| Clef               | 27B  | the ceiling                                                         |
| Clef-Flash         | 9B   |                                                                     |
| Strands Decider 2B | 1.9B | the **cost target**: text is its native input, and it runs on a CPU |

The work here is text only, so all three apply.

**Where this repository is unsure.** Every caller goes through one scorer, `classify_TEXT.score_line()`
(`classify_TEXT.py:171`), and one rule cascade, `determine_category()` (`text_util.py:1739`). Its uncertain
populations are documented:

- **Short lines without Czech diacritics** (gate 6 and the one- and two-word rules after it).
  - About 26,000 genuine garbage lines read `Clear` (95 % CI 19,300–34,600).
  - In 484 graded promotions: 76.5 % right, 9.6 % borderline, 13.9 % wrong (`docs/evaluation/categorization.md`).
- **Red flags without a conviction.** Gates 9a–9f fire, but `_has_strong_garbage_evidence()` (`text_util.py:2296`)
  is false. The flag shows only through `rule_fire_capture()` (`text_util.py:124`).
- **The `Noisy`/`Trash` boundary**, "where the remaining error is".
  - `Trash` recall: 27 % on Dana's lines, 12–19 % on the gold (`digests/3.digest.md`, AC7).
  - Damaged but readable lines (`1 fraament okraie`, 530 lines) are a policy question for the archive
    (`tools/ocr_neighbours.py`).
- **Band edges.** The stored `quality_score` is clamped into its category's band (`categorize_line`,
  `text_util.py:2222`), so 0.80 does not separate `Clear` from `Noisy`.
  - The real edges are 0.55 and the valid-word ratio of 0.85 in band routing.
- **Foreign-language lines.** The rules are Czech-first: a clean English or German sentence can read `Noisy`
  (`docs/reference/categorization_rules.md`, known limits).
- **Document passes** (batch only): the identical-text vote, surrounded-`Trash`, page context and the inverted-scan
  sweep (`apply_document_postprocessing`, `classify_TEXT.py:835`).

**What exists to judge against.**
- `tools/gold/`: 2,067 hand-labelled lines (`gold_categ`), and the A/B verdict of `tools/recategorize_from_csv.py
  --gold-column gold_categ` (adopt, parity or regression; `tools/gold/GOLD.md`).
- The #30 annotations: 508 lines re-checked, 357 decisions (`docs/issue30/`).
- `tools/corpus_change.py` and the run `DOC_LINE_CATEG_1210` (113,101 documents, 71.8 million lines).

**Repository rules that bound the design.**
- No runtime call to another service exists today. A new environment variable must be in `.env.example`
  (`tests/test_env_contract.py`), and a new module the service imports must be in `.github/production-image.json`.
- Routing policy, i.e. which tool to call next, "stays outside both tools" (`atrium_document.py:305`).

## Phase 0: the client (from page-classification)

- Copy `decision_client.py` from page-classification to the repository root once it exists there. Pin it in
  `tests/test_vendored_decision_parity.py` (`VENDORED` SHA-256 + `ADOPTED`), as digital-convert pins
  `text_formats.py` from here.
- Add page-classification to the caller's `vendored-from`.
- The client needs only `requests` and the standard library; Pillow is not used here.
- While the work is offline, its settings (`DECISION_MODEL_URL`, `DECISION_MODEL_NAME`, `DECISION_TIMEOUT_S`) are CLI
  flags, listed under `NOT_PUBLISHED` in `tests/env_contract_data.py`.

## Phase A: the ambiguity selector

`tools/decide_lines.py`, offline. It reads `DOC_LINE_CATEG` CSVs; the gold CSVs carry every feature column. It
re-scores each row under `rule_fire_capture()` to learn which gates fired, and tags every selected line with its
reason. The selector is deterministic and uses existing signals only:

| Tag | Population                                                                               | Signal                                                                  |
|-----|------------------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| S1  | ≤ 3 tokens, no Czech diacritics, read `Clear` or `Noisy`                                 | `word_count`, `has_cz_diacs`, the gate-6/7 fires                        |
| S2  | a 9a–9f red flag fired, no conviction                                                    | captured fires with no `Trash` verdict                                  |
| S3  | raw score within ±0.05 of 0.55, or valid-word ratio within ±0.05 of 0.85 in band routing | `compute_quality_score()` recomputed (the stored score is clamped)      |
| S4  | `lang` not `ces_Latn`/`slk_Latn`, `lang_score` ≥ 0.85, read `Noisy`                      | `lang`, `lang_score`, `categ`                                           |
| S5  | a document pass changed the line                                                         | `pp_dedup`, `pp_surrounded_trash`, `pp_inverted_run`, `pp_page_context` |

**Output A1:** the size of each population on the gold set and over `DOC_LINE_CATEG_1210`, with `tools/corpus_change.py`.
Those counts and the measured latency give the cost of a full run before any model answer is trusted.

## Phase B: the question set

`decisions/line_category.v1.json`. Its criteria are generated from the vendored vocabulary (`_LINE_CATEGORY_DEFS`).
- **`line_category`:** a `choice` over `Clear`, `Noisy`, `Trash`, `Non-text`, each with its vocabulary definition.
  `Empty` stays deterministic.
- **`recoverable`** (optional): a `noul`, "Could a reader restore the intended words of `line`?" It is the archive's
  `Trash`-vs-`Noisy` policy question, asked separately so that it can be measured separately.
- **State:**
  - `line`, `original_text`;
  - two lines before and after (from the same page);
  - the detected language and its score;
  - the page's dominant language.
  - Numbers are computed in code, never asked of the model.
  - The OCR text is untrusted input; an answer never deletes or rewrites anything.
- **Packing (an ablation on dev):**
  - one line per request;
  - one page window per request, with one `line_category` question per selected line, at most 64. One prefill per
    page is far cheaper, if accuracy holds.

## Phase C: the shadow run

- **Output:** a sidecar `DOC_LINE_DECIDE/<doc>.csv`, one row per selected line.
  - Key: `file, page_num, line_num`. The row carries the selector tag, the rule's `categ`, the model's choice and
    probabilities, `confidence`, `lane`, `status`, `latency_ms`, `schema_digest`.
  - `CSV_HEADER`, the `categ` column, the record and `PAGE_ALTO/` are untouched. nlp-enrich and the translator see
    nothing new.
- **Lanes:** `accept` (the model's answer replaces the rule's in the *adjudicated* column), `escalate` (another model,
  e.g. Clef behind Strands), `review` (a person). The thresholds are set on dev.
- **Paradata:** the model and its revision as a `setup/para_config.txt` component (Apache-2.0, `conditional`). The
  question-set digest and the thresholds go in `config`. The counts per tag, status and lane, and latency p50/p95, go
  in the statistics.

## Phase D: evaluate

- **Split** the gold set **by document** into dev and test (the gold contract is one CSV per document). Tune the
  wording, the packing and the lanes on dev; freeze them; then run test.
- **Gate:** the adjudicated category against `gold_categ`, through the existing verdict of
  `tools/recategorize_from_csv.py --gold-column gold_categ`.
  - Gold errors must fall, and `Clear`-loss must not rise: the same bar every rule change in #30 met.
  - The #30 annotations are a second test set.
- **Reported per tag (S1–S5) and per model:**
  - the fixed and broken lines, confusion matrices, `Trash` recall;
  - Brier and ECE;
  - coverage against error for the `review` lane;
  - latency, the `invalid` count and timeouts.
- **Cost:** lines per hour per model on the cluster GPU and, for Strands, on CPU. Scaled by the A1 counts, this gives
  the price of re-scoring both collections.

## Phase E: decide

- **Stop:** no tag improves on the gold set, or the cost of a full run is out of proportion.
- **Keep for a tag:** e.g. S1 alone.
  - The adoption is a hook after `determine_category()` in `score_line()` (the one scorer), behind a flag that ships
    off, and only after the pilot freeze lifts.
  - It is measured first with `tools/corpus_change.py`, like every change since the freeze.
  - It includes the `.env.example`, `tool_limits.py`, `production-image.json` and `service/openapi.json` work that a
    service setting brings.
- **Relation to #4:** a decision model needs no training data but costs a call per line. A distilled scorer needs
  training but runs free. The adjudicated lines of Phase C are labelled examples that #4's distillation could use,
  once checked against gold.

## The "next connector" question

Which stage to call after this one (the translator, nlp-enrich, keyword-extract, re-acquisition by HTR, or a person)
stays a rule over counts in the orchestrator. That is the repository rule, and the guide's advice for exact
logic. A decision model adds evidence to those rules and no new rule:
- the per-line verdicts;
- a per-page count of `review`-lane lines, which could later feed `quality_summary`, as its own proposal.

Routing scanned pages to processors is page-classification's task B3 in #58.

## Done criteria

- [ ] The selector exists, with the size of each population on the gold set and over both collections.
- [ ] The question set is written, generated from the vocabulary, with its digest recorded.
- [ ] Clef, Clef-Flash and Strands are scored on the gold test half against the existing verdict, per tag.
- [ ] Single-line and page-window packing are compared on dev.
- [ ] The cost of a full re-score is estimated from measured throughput.
- [ ] A per-tag recommendation (stop, keep, follow-up) is recorded; nothing in production changed.

## Out of scope

- Any change to a rule, threshold, weight, column, record field or default during the pilot freeze.
- LLM text correction (that is `tools/quality_model/correct.py`, #4's offline data generation).
- Hosted inference on archive text without a data-governance check.
- Calling another ATRIUM service from this tool.

## Refresh conditions

Refresh after any of these:
- the first selector counts;
- the first scored run;
- the pilot freeze lifting;
- a change of candidates in #58.
