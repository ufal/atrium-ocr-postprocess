# Output reference

The files the batch pipeline writes, column by column. How to produce them is in [usage](../usage.md); what the
categories mean is in [How a line is categorised](../categorization_logic.md). The service returns the same per-line
fields; its JSON is typed in [`service/openapi.json`](../../service/openapi.json).

## Step 2: page statistics

One row per page:

```
file, page, textlines, illustrations, graphics, strings, path
CTX200205348, 1, 33, 1, 10, 163, /lnet/.../A-PAGE/CTX200205348/CTX200205348-1.alto.xml
CTX200205348, 2, 0, 1, 12, 0, /lnet/.../A-PAGE/CTX200205348/CTX200205348-2.alto.xml
...

```

The three builders (`alto_stats_create.py`, `json_stats_create.py`, `text_stats_create.py`) write the same columns.

## Step 4.1: per-line categories

`DOC_LINE_LANG_CLASS/<doc_name>.csv` (example: [DOC_LINE_CATEG](../../data_samples/DOC_LINE_CATEG)), one row per line.

`<doc_name>.csv`: detailed classification results for every single line within a document, **columns**:

* `file` — document identifier
* `page_num` — page number
* `line_num` — line number, starts from 1 for each page
* `text` — cleaned text of the line (after the pre-filter's OCR repairs)
* `original_text` — original pre-repair text of the line
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
  (see [the rules](categorization_rules.md#structural-detectors))
* `vowel_ratio` — ratio of vowels to alphabetic and symbol characters in the `original_text`
* `rot_ratio` — ratio of structurally ambiguous/rotatable characters (`pbqdnuwmoxszeyv`) to the alphabetic characters of the line
* `valid_word_ratio` — share of words that pass the structural word test (the readability measure of gate 10)
* `structured` — **boolean**: a measurement, reference or similar structured line (exempt from several gates)
* `damaged` — **boolean**: the line holds a character-level damaged token (a digit or symbol inside a word)

The key resulting output **columns** that depict the final classification and quality assessment:

* `quality_score` — composite quality score in [0, 1] from **9** combined signals, higher = cleaner; clamped into the band of `categ`
* `categ` — assigned category: **Clear**, **Noisy**, **Trash**, **Non-text**, or **Empty**

**Columns** useful for archive managers apart from the quality score and category:

* `lang` — predicted ISO **language code** from the FastText model (remapped if unknown)
* `lang_score` — the *stored* confidence: FastText's, or the cap (0.75 / 0.50) for a remapped language (`LANG_REMAP_ALWAYS`)
* `original_lang` — predicted language **before** remapping
* `orig_lang_score` — original FastText confidence **before** remapping
* `perplex` — the perplexity used for scoring: Qwen2.5-0.5B perplexity of the line, **capped at `SHORT_PPL_CAP` for one- and two-word lines**
* `perplex_raw` — the model's own perplexity, uncapped (blank-equivalent to `perplex` except for those short lines)
* `perplex_blend` — the page-relative perplexity, written only when the blend moved a value (`PAGE_PPL_BLEND_ENABLE`, off by default)
* `caps_header` — **boolean**: all alphabetic words of the line are uppercase (typical of section headers)

**Diagnostic flags.** Five boolean audit columns name the rule that decided the line (exactly one `True`, none for
`Empty` and `Non-text`): `allcaps_novowel`, `lowppl_clear`, `trash_threshold`, `noisy_threshold`, `clear_threshold`
(`cleanprose_clear` is always `False` since Override 4 was removed). The hard-sweep and inverted-scan routes are folded
into `trash_threshold`, so the CSV is coarser than the categoriser. Four more name the document-level pass that later
changed the line: `pp_dedup`, `pp_surrounded_trash`, `pp_inverted_run`, `pp_page_context`. A decision flag and a `pp_`
flag can both be `True`: that is the trail from the original decision to the override.

## Step 4.2: page statistics

`final_page_stats.csv` (`OUTPUT_STATS`) and `DOC_LINE_STAT/stats_<doc>.csv` (`OUTPUT_DOC_DIR`), one row per page.

For each page, the aggregation computes features outputted in the following strict schema order:

**Totals & Counts:**

* `num_lines` — the total number of valid lines processed on the page
* `Clear`, `Noisy`, `Trash`, `Non-text`, `Empty` — integer count of lines in each category

**Category ratios** (decimal fractions in [0, 1], rounded to 4 places; [#8](https://github.com/ufal/atrium-ocr-postprocess/issues/8)):

* `clear_rat`, `noisy_rat`, `trash_ratio`, `non-text_rat` — the category's share of the page's lines that are not
  `Empty`: `Clear / (num_lines − Empty)`, and so on. On a page with text lines the four sum to 1 (up to rounding)
* `empty_rat` — `Empty / num_lines`, the share of all lines that are empty

A ratio whose denominator is 0 is an empty cell (`NaN`), never 0 or an error: a page whose lines are all `Empty`
has `empty_rat` 1.0 and the other four empty; a page with `num_lines` 0 has all five empty. Read the columns by
name: they sit between `Empty` and `total_word_count`, so a consumer that reads by position must be updated.

**Text size:**

* `total_word_count` — total number of words across scoreable lines
* `total_char_count` — total number of characters across scoreable lines

**Averages** (mean over the same `Clear` and `Noisy` lines):

* `avg_quality_score` — mean composite quality score in [0, 1]; higher = cleaner OCR output
* `avg_word_weird` — mean per-word weirdness ratio in [0, 1]; 0 = fully clean, lower is better
* `avg_lang_score` — mean FastText confidence score
* `avg_perplex` — mean Qwen2.5-0.5B perplexity score
* `avg_vowel_ratio` — mean vowel-to-alphabetic-character ratio per line
* `avg_rot_ratio` — mean rotatable character ratio per line
* `ch_ratio` — mean fraction of lines flagged as all-caps headers (`caps_header = True`)

**Language profile:**

* `main_lang` — the statistical mode (most frequent) language predicted for the page

> [!NOTE]
> `avg_*` columns and `main_lang` will be `NaN` / `None` for pages with no `Clear` or `Noisy` line, i.e. pages
> whose only lines are `Trash`, `Empty` or `Non-text`.
> `avg_quality_score` leaves `Trash` lines out, so a page made mostly of `Trash` lines can still score ≥ 0.80:
> read it together with the page's `Trash` count. In a document record, a page with no `Clear`, `Noisy` or
> `Trash` line carries no `quality_band` (the service path omits it too) and no `quality_score`.
> Additional per-line diagnostic variables (e.g. `weird_wx`, `original_lang`, `original_text`) and flags added
> and ignored for this page-level aggregation to ensure stability.

## Step 1: the text-lines reports

`PAGE_TEXT/ingest_report.csv` (one row per input file) and `PAGE_TEXT/pages_report.csv` (one row per page) are
described with Step 1 in [usage](../usage.md#any-other-text-bearing-input).
