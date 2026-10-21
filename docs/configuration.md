# Configuration

How ocr-postprocess is configured: the pipeline's config file, the categoriser's constants and what ships
for the S2 pilot. The service's own environment (port, limits, models) is in
[service/README.md](../service/README.md#configuration-environment); the complete list of variables is
[`.env.example`](../.env.example).

## Where settings come from

- **The config file** is [setup/config.txt](../setup/config.txt), in INI form; **comments go on their own line**.
  `run_pipeline.py --config <file>` or the `LANGID_CONFIG` variable points at another one.
- **Any key can be overridden** by the environment variable `ATRIUM_<SECTION>_<KEY>` (for example
  `ATRIUM_TEXT_UTILS_SHORT_PPL_CAP=900`), read when the process starts. The `[TEXT_INGEST]` caps are also
  `ATRIUM_TEXT_INGEST_<KEY>`, and the variable wins over the file.
- **For `run_pipeline.py`:** CLI flag > config value > built-in default.
- **The word lists** the categoriser consults (units, reference labels, section headings, short Czech function
  words, the rotation whitelists) are in [setup/word_lists.txt](../setup/word_lists.txt) (`WORD_LISTS_PATH`), one
  `[section]` each; the matching `setup/config.txt` keys stay empty as overrides. Editing a list changes categories:
  treat it as a categorisation change.

The sections: `[PIPELINE]` (the orchestrator: `METHOD`, input directories), `[EXTRACT]` (Step 3), `[CLASSIFY]` and
`[TEXT_UTILS]` (Step 4.1, the categoriser), `[AGGREGATE]` (Step 4.2), `[TEXT_INGEST]` (the text-lines readers' caps,
listed with their defaults in [service/README.md § Limits](../service/README.md#limits)) and `[DOCUMENT]` (the
document record: `JSON_DIR`, `SOURCE_ORIGIN`, `SOURCE_ORIGIN_BY_KIND`).

## What ships for the pilot

The category semantics are **frozen for the S2 pilot** (decision of 2026-09-30 on
[#2](https://github.com/ufal/atrium-ocr-postprocess/issues/2)); a change afterwards is measured on a corpus diff with
`tools/corpus_change.py`. What ships (the measurements behind each are on
[the evaluation page](evaluation/categorization.md)):

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

## The categoriser's constants

The main keys of `[CLASSIFY]` and `[TEXT_UTILS]`, as shipped:

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

Parameters that scale with the perplexity model. They must be re-tuned whenever you switch between multilingual
`Qwen2.5-0.5B` and English-adapted `distilgpt2`, because the two produce perplexity on very different scales
(`Qwen2.5-0.5B` roughly 3× lower on the same Czech text); the guard in `classify_TEXT.py` warns when the ceiling is at
`distilgpt2` scale with a Qwen model:

| Parameter                  | Qwen2.5-0.5B | distilgpt2 | What it controls                                                                                                                                                                                                                                                                                                            |
|----------------------------|--------------|------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `PERPLEXITY_THRESHOLD_MAX` | 1000.0       | 3000.0     | The ceiling used to normalise raw perplexity into [0, 1] for the quality score. A value at or above this ceiling contributes 0 to the score (worst); a value of 0 contributes 1 (best).                                                                                                                                     |
| `SHORT_PPL_CAP`            | 850.0        | 2500.0     | Maximum perplexity applied to 1–2 word lines before scoring. Short fragments get extreme perplexity from any LM (no context to condition on); the cap stops legitimate short labels and codes being penalised. It sits below every perplexity route's floor, so **no perplexity rule can fire on a one- or two-word line**. |
| `PPL_INVERTED_MIN`         | 200.0        | 500.0      | **Perplexity** floor for the page-level inverted-scan arms B and C: the LM must also be uncertain about the line.                                                                                                                                                                                                           |

Parameters that are model-independent (ratios and score fractions, stable across either model):

| Parameter                   | Default | What it controls                                                                                                                                                                                                                              |
|-----------------------------|---------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `ROT_RATIO_INVERTED_MIN`    | 0.55    | Minimum share of structurally rotatable characters (`pbqdnuwmoxszeyv`) among a line's letters for the page sweep's rotation arm (arm C).                                                                                                      |
| `MOSTLY_READABLE_VALID_MIN` | 0.85    | Minimum share of structurally valid words for `Clear`. Below it a line is `Noisy`, unless it is a confident, low-perplexity, upright-Czech line (`lm_confident_czech`).                                                                       |

`WEIRD_RATIO_INVERTED_MIN` is still loaded (and swept) but no rule reads it; the `CLEAN_PROSE_*` keys no longer
exist ([removed mechanisms](decisions/2026-10-categorization.md#removed-and-renamed-mechanisms)).

Language- and collection-specific data lives in the config rather than in Python (#7 Tier 1):

| Parameter                                | Section        | Default                                                               | What it controls                                                                                                                                                                                                                    |
|------------------------------------------|----------------|-----------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `WQX_CHARS`                              | `[TEXT_UTILS]` | `wqxWQX`                                                              | Letters rare in Czech: a token holding one is weighted as OCR noise (`score_word`), and they feed the `wqx`/rotation gate.                                                                                                          |
| `ROT_WHITELIST`                          | `[TEXT_UTILS]` | `po,pod,do,od,on,ony,by,bez,ne,nebo,ven,den,zde,se,ve,mez,pouze,bude` | Czech function words recognisable upright; a line containing one counts as *upright Czech*. Their mirror/rotation ghost images (`ROT_GHOSTLIST`) are **derived at import time**: changing this key requires a re-import.            |
| `GHOST_WORD_COLLISIONS`                  | `[TEXT_UTILS]` | `no,bo`                                                               | Ghost images that collide with real words and must never count as ghost hits.                                                                                                                                                       |
| `TRAILING_FILL_CHARS`                    | `[TEXT_UTILS]` | `\x20._:-<–—`                                                         | Trailing filler characters stripped before headline/short-line checks. Unicode-escape decoded; the leading space is written `\x20` because configparser strips leading whitespace from values.                                      |
| `NONTEXT_MARKERS`                        | `[TEXT_UTILS]` | `IVerc`                                                               | Collection-specific literal markers (the ARUP/B stamp) forcing `Non-text` in `pre_filter_line()`.                                                                                                                                   |
| `DEU_DIACS`, `DIACRITIC_INFER_THRESHOLD` | `[TEXT_UTILS]` | `äöüßÄÖÜ`, `0.07`                                                     | Reserved: read only by `infer_lang_from_diacritics()`, which no scoring path calls.                                                                                                                                                 |
| `FASTTEXT_MODEL`                         | `[CLASSIFY]`   | `lid.176.bin`                                                         | Path to the FastText language-ID weights loaded by each CPU worker.                                                                                                                                                                 |
| `TRUST_TIER_TRUSTED`                     | `[CLASSIFY]`   | 0.85                                                                  | Trust multiplier on the FastText confidence for a *known but unexpected* language. The product (`trust_lang_score`) feeds **both** the quality score and the structural gates, never the stored `lang_score`.                       |
| `TRUST_TIER_UNKNOWN`                     | `[CLASSIFY]`   | 0.50                                                                  | Trust multiplier for an *unknown* language. It caps the trust at 0.50, below `LANG_SCORE_REMAP` (0.75), so a gate written `lang_score <= LANG_SCORE_REMAP` is always true for unknown-language lines.                               |
| `REMAP_KEEP_SCORE_LANGS`                 | `[CLASSIFY]`   | `slk`                                                                 | Languages **outside** the expected and trusted lists that keep their confidence when relabelled to the default. `slk` is also in `TRUSTED_FOREIGN_LANGS`, so it is kept as Slovak and this key has no effect in the shipped config. |

The rules that read each constant are in [the reference](reference/categorization_rules.md).
