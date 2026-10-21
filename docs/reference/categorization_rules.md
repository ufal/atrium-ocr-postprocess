# Categorisation rules (reference)

The complete rules of the per-line categoriser in [`text_util.py`](../../text_util.py), run by
[`classify_TEXT.py`](../../classify_TEXT.py) (batch Step 4.1) and by the service
([`service/text_inference.py`](../../service/text_inference.py)). Each rule is given with its constants,
in the order the code applies it. For the ideas behind them, read
[How a line is categorised](../categorization_logic.md) first; for what was measured, see
[the evaluation page](../evaluation/categorization.md); for why the argued rules are as they are, see
[the decision record](../decisions/2026-10-categorization.md).

Constants are read from `setup/config.txt` (sections `[CLASSIFY]` and `[TEXT_UTILS]`), with defaults declared in
`text_util.py`, and are frozen for the S2 pilot. Every value and how to change it:
[configuration](../configuration.md).

## Contents

- [Pre-filter](#pre-filter)
- [Language handling](#language-handling)
- [Structural detectors](#structural-detectors)
- [Composite quality score](#composite-quality-score)
- [Categorisation logic](#categorisation-logic): the ordered gates, rescues, band routing, the clamp
- [Overrides and exception cases](#overrides-and-exception-cases)
- [Foreign-language lines](#foreign-language-lines)
- [Page-relative perplexity (default OFF)](#page-relative-perplexity-default-off)
- [Post-processing smoothing](#post-processing-smoothing)
- [Worked examples](#worked-examples): one input through every step; each row is a test case
- [Configuration invariants](#configuration-invariants)

---

## Pre-filter

`pre_filter_line()` answers `Empty` and `Non-text` with no model at all (the CSV carries a score of 0.0,
language `N/A` and perplexity 0 for them), repairs two systematic OCR errors, and forwards every other
line as `Process`. The batch pipeline and the service both run it first; its answers never reach FastText
or the perplexity model.

**In the order the code takes them:**

1. Blank or whitespace only → `Empty`.
2. **Repairs.** The repaired text is what is scored; the line as submitted is kept as `original_text`, and
   garbage density and the vowel ratio are measured on that original, so cleaning cannot hide noise.
   * a `1` between two letters becomes `l` (`poh1ed` → `pohled`); a `2` that starts a token before a
     lowercase letter becomes `z` (not after a digit, `.` or `,`);
   * spaced capitals collapse (`P R A H A` → `Praha`).
3. **Forwarded as `Process` before any `Non-text` rule runs:**
   * a line containing a *metadata marker* (`METADATA_MARKERS`, case-insensitive: `Tb.`, `č.neg`, `neg.`,
     `obr.`, `č.`, `str.`, `Datum`, …): short and heavily abbreviated, but meaningful for cataloguing;
   * a *forgiven* line (`is_forgiven_headline()`): a short numbered headline such as
     `2, Popis nálezu i - 3`, or a bare unit or abbreviation such as `mm`, `Tb.`, `č.neg.` (#3, 2026-07-02).
4. An unbalanced leading or trailing `"` gets its pair.
5. **`Non-text`**, any of:
   * `is_non_text()`: only digits, separators and punctuation (`1998`, `5.3.`, `- 14 -`); a single token
     that is an archive code (up to three letters, then three or more digits, optional `/suffix`: `A1739`,
     `CTX200205348`, `A679/2015`), or any ASCII token of five or more characters containing a digit, or an
     all-caps ASCII token containing `X` or of ten or more letters with a vowel ratio under 0.20; a spaced
     archive reference of up to 20 characters (one to five letters, then digits: `váha 90,9g`); a line under
     15 characters whose digit share exceeds 0.5 (`5.XI.1946`). A standalone all-caps word with vowels
     (`LITERATURA`, `ARCHEOLOGIE`) is a headline, not a code. These all-caps rules are **language-blind**:
     `LUXEMBOURG` and `SCHLOSSBERG` are `Non-text` too;
   * a Roman numeral, optionally followed by a full stop (`XIV.`);
   * a stamp ratio pattern (`123/456`, `NZ1998/01`), or a literal `NONTEXT_MARKERS` entry (`IVerc`, the
     ARUP/B stamp).
6. **Forwarded as `Process`, the anchor bypass.** At least `ANCHOR_MIN_WORDS` (2) alphabetic tokens of at
   least `ANCHOR_WORD_LEN` (3) letters with a vowel ratio of at least `ANCHOR_VOWEL_RATIO` (0.10): real
   words are on the line, whatever else is.
7. **Forwarded as `Process`, the digit bypass.** Digits are more than 40 % of the characters: numeric-heavy
   lines that no earlier rule took (`12 ks keramiky, 3 ks kosti`). It does not rescue what rule 5 already
   took, so `váha 90,9g` and `5.XI.1946` stay `Non-text`.
8. Fewer than 4 characters, or fewer than 3 distinct non-space characters → `Non-text`.
9. Letters under 30 % of the characters → `Non-text`.
10. A single token that mixes symbols, letters and digits (`SYM_LET_DIG_NONTEXT`) → `Non-text`.
11. **Isolated characters.** A line of `ISOLATED_CHAR_MIN_TOKENS` (3) or more tokens in which every
    alphabetic token is a single letter, or at least `ISOLATED_CHAR_RATIO_MAX` (40 %) are single letters
    that are not valid Czech one-letter words (`SINGLE_CHAR_ALLOWED`), → `Non-text`, unless a run of three
    or more single letters collapses to a string that reads as a word (vowel ratio over 0.15, garbage
    density under 0.20).
12. Otherwise → `Process`.

---

## Language handling

[FastText](https://huggingface.co/facebook/fasttext-language-identification) runs on the
**lowercased** line and returns a label with its script (`ces_Latn`, `deu_Latn`, `rus_Cyrl`) and a
confidence between 0 and 1. `remap_lang()` then decides what is *stored*, and `score_line()` derives the
*trust score* the rest of the scorer reads (see
[Which language number is read where](#which-language-number-is-read-where)).

**Configuration keys (`[CLASSIFY]`):**

* `EXPECTED_LANGS` (shipped `ces,deu,eng`): the languages the collection is expected to contain. The
  **first** entry is the default the program falls back to when FastText predicts a language it does not
  know. If the collection is mainly German, put `deu` first and re-tune the perplexity thresholds.
* `TRUSTED_FOREIGN_LANGS` (shipped `deu,eng,fra,pol,ita,slk`): foreign languages whose presence is
  genuine (German summaries in a Czech report, Latin citations, English abstracts). They are **not
  remapped**, whatever the confidence.

**Language score constants (`[TEXT_UTILS]`):**

* `LANG_SCORE_REMAP = 0.75`: the stored score of an unknown Latin-script line replaced by the default.
* `LANG_SCORE_REMAP_FAR = 0.50`: the same for an unknown non-Latin script (Cyrillic, Hangul, CJK, …).
* `LANG_SCORE_ROUGH = 0.45`: a stored confidence below this is too unreliable to trust. It is read **only**
  by the page-level inverted-scan sweep (see [Post-processing smoothing](#post-processing-smoothing));
  the hard-sweep route has its own constant, `HARD_SWEEP_LANG_MAX` (0.45).

**What is stored (`remap_lang()`, per line, in order):**

1. The predicted language is in `EXPECTED_LANGS` or `TRUSTED_FOREIGN_LANGS`: label and confidence are
   **kept unchanged**. In the shipped configuration this includes Slovak (`slk`).
2. Otherwise, if the language is in `REMAP_KEEP_SCORE_LANGS` (shipped `slk`; it matters only for a
   configuration that leaves `slk` out of the trusted list): relabelled to the default, **confidence kept**.
3. Otherwise (Danish `dan` or Latin `lat` on a Czech line, say): relabelled to the first `EXPECTED_LANGS`
   entry, with the script suffix kept, and the stored confidence set according to `LANG_REMAP_ALWAYS`.

> [!IMPORTANT]
> **`LANG_REMAP_ALWAYS`** (`[TEXT_UTILS]`, default **`true`**) decides the stored score of step 3:
> * **`true`:** *unconditionally* `LANG_SCORE_REMAP` (0.75, Latin script) or `LANG_SCORE_REMAP_FAR` (0.50,
>   other scripts), whatever FastText said. A weak and a strong foreign guess land on the same value
>   (@DanaKriv, 2026-07-02: "the original lang score should not matter").
> * **`false`:** `min(original, cap)`, the earlier behaviour: a weak guess stays below the cap.
>
> The switch moves the **stored** score only. The trust score, and so every line-level decision, is built
> from FastText's own confidence either way; what the switch can change is the page-level sweep, which reads
> the stored score.

### Which language number is read where

Three numbers exist per line, and mixing them up is the easiest way to mis-calibrate the categoriser:

| Value                                  | How it is computed                                        | Read by                                                                                                                                                                                             |
|----------------------------------------|-----------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `original_lang`, **`orig_lang_score`** | FastText's label and confidence, untouched                | gates 1, 1a and 9a (`orig_lang_score`); the strong-evidence clause `trust ≤ 0.20 and orig ≤ 0.50`; the language-aware vowel-run clause of the shape witness (label); the page-context rules (label) |
| `lang`, `lang_score` (stored)          | `remap_lang()`                                            | the CSV and the record; the page-level inverted-scan sweep: arm A reads *stored* `< LANG_SCORE_ROUGH`, arms B and C *stored* `< ROT_HIGH_LANG_CONF` (0.90)                                          |
| **`trust_lang_score`**                 | `orig_lang_score` × the tier of the **raw** label (below) | the quality score's language term; every `lang_score` guard of the cascade (gate 6's `≤ LANG_SCORE_REMAP`, the strong-evidence clauses)                                                             |

The tier (`score_line()`): a label in `EXPECTED_LANGS` → **× 1.0**; in `TRUSTED_FOREIGN_LANGS` but not
expected → **× `TRUST_TIER_TRUSTED`** (0.85); anything else → **× `TRUST_TIER_UNKNOWN`** (0.50).

> [!WARNING]
> The cascade sees `trust_lang_score`, **never** the stored `lang_score`. An unknown-language line with
> confidence 0.9163 is stored at 0.75 (the Latin-script cap) but reaches the guards as
> 0.9163 × 0.50 = 0.4582. Any offline analysis that feeds the stored value where the pipeline feeds the
> trust value measures something the pipeline never computes.

Two consequences:

* The unknown tier multiplies by 0.50, so an unknown language's trust never exceeds 0.50, below
  `LANG_SCORE_REMAP` (0.75): a guard written `lang_score <= LANG_SCORE_REMAP` is **always true** for it,
  whatever FastText reported (gate 6 reads this way).
* A remapped line is stored at 0.75 (or 0.50), above `LANG_SCORE_ROUGH`, so the page sweep's arm A never
  counts it as unplaceable, while arms B and C (`< 0.90`) always do.

---

## Structural detectors

Lines that pass the pre-filter are analysed by the structural detectors of [text_util.py](../../text_util.py):

| Detector                     | What it counts                                                                                                                                                                                                                                                                                                                                                                         |
|------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `detect_strange_symbols`     | **Occurrences** of any character that is not alphanumeric and not in the **allowed** set `{ . - , + ( ) " ' — – : % ; ? ! / }`. Edge punctuation is stripped before inspection. The service reports the count as `sym_count`; it is not scored on its own, but the same class of character is scored per word (`has_strange`, below) and makes a word invalid in the valid-word ratio. |
| `detect_letter_digit_letter` | Words with a **letter–digit–letter sandwich** — the fingerprint of **OCR** digit insertions mid-word (e.g., `vyt1ačená`). **Legitimate** measurements (`30cm`, `90,9g`) do not trigger.                                                                                                                                                                                                |
| `detect_mid_uppercase`       | Words with unexpected uppercase mid-word (`dalSÍ`). Academic titles (`PhDr`, `MUDr`) are **excluded**.                                                                                                                                                                                                                                                                                 |
| `detect_repeated_chars`      | Words (of at least 4 characters) with a triple run of one character, **or** a doubled character other than `o` / `u` occurring at least `REPEATED_DOUBLE_MIN` (2) times, **or** one non-digit character making up at least 30 % of the word (and occurring 3+ times). **Known limit:** the second clause also catches ordinary Czech doubled consonants (`denní`, `oddělení`).         |
| `detect_gibberish_words`     | Words of length ≥ 4 with a vowel ratio above `VOWEL_RATIO_HIGH` (0.70). All-caps and mostly numeric words are **excluded**. Sub-tokens are split on internal punctuation first.                                                                                                                                                                                                        |
| `compute_rotatable_ratio`    | Measures the concentration of structurally ambiguous/rotatable letters (`pbqdnuwmoxszeyv`) to catch severe visual noise interpreting graphical textures as characters.                                                                                                                                                                                                                 |
| `detect_fused_words`         | Counts tokens that are likely multiple words merged without a space (token length > 14, consonant run of 5+, or vowel run of 3+). Sub-tokens are split on internal punctuation first.                                                                                                                                                                                                  |
| `detect_wx_words`            | Tokens with an abnormal density of 'w'/'x' glyphs (≥ 2 per sub-token). By default, this is folded into the gibberish ratio to punish severe mirror scans.                                                                                                                                                                                                                              |

**Per-word weirdness** (`score_word()`, in [0, 1]) is what `word_weird` averages over a line. A word on the
`[allowed]` list scores 0; a single letter scores 0.85 (an invalid one), 0.25 (a digit) or 0 (a valid Czech
one-letter word); otherwise the contributions add, clamped to 1.0:

| Contribution                                                                                   | Weight                       |
|------------------------------------------------------------------------------------------------|------------------------------|
| a strange symbol inside the word                                                               | 0.40                         |
| a repeated character (`detect_repeated_chars` above)                                           | 0.35                         |
| a letter–digit–letter sandwich                                                                 | 0.15                         |
| a mid-word capital                                                                             | 0.25                         |
| a capital prefix (two or more capitals, then lowercase, in a word of 4+ characters)            | 0.20                         |
| any of `w`, `q`, `x` (`WQX_CHARS`)                                                             | `WORD_W_PENALTY` (0.20)      |
| three or more letters and no vowel (academic titles excepted)                                  | 0.50                         |

Two **line-level** additions follow: `INVERTED_WEIRD_PENALTY` (+0.45 per word) when the line is
ghost-dominated or has a rotatable-letter share above `SUSPICIOUS_ROT_RATIO` (0.65) with at least
`SUSPICIOUS_WQX_RATIO` (15 %) of its words containing `w`/`q`/`x`, and it is not upright Czech; and +0.35 on
each digit or one-to-two-character token of a *highly fragmented* line (more than 60 % such tokens, at least
four words).

## Composite quality score

After structural detection, each line receives a single floating-point `quality_score` in [0, 1] computed by
`compute_quality_score()` in [text_util.py](../../text_util.py). The score is a weighted sum of **nine**
normalised signals, **dynamically divided by the total sum of weights** to strictly bound the maximum
score to 1.0 (preventing score inflation):

```text
base_score =
    QS_WEIGHT_VALID_WORD  (def: 0.35) × valid_word_ratio
  + QS_WEIGHT_WEIRD       (def: 0.18) × (1 − min(word_weird_ratio, 1.0))
  + QS_WEIGHT_PERPLEXITY  (def: 0.08) × (1 − min(perplexity / PERPLEXITY_THRESHOLD_MAX, 1.0))
  + QS_WEIGHT_LENGTH      (def: 0.02) × min(char_count / QS_LENGTH_MAX, 1.0)
  + active_garbage_wt     (def: 0.18) × (1 − min(garbage_density / QS_GARBAGE_NORM_MAX, 1.0))
  + QS_WEIGHT_VOWEL       (def: 0.07) × vowel_quality_score
  + QS_WEIGHT_LANG        (def: 0.05) × trust_lang_score
  + QS_WEIGHT_GIBBERISH   (def: 0.04) × (1 − min(gibberish_ratio, 1.0))
  + QS_WEIGHT_FUSED       (def: 0.03) × (1 − min(fused_ratio, 1.0))

quality_score = max(0.0, (base_score / total_weight) − short_penalty)
```

The garbage-density term is normalised by `QS_GARBAGE_NORM_MAX` (0.35), a constant of its own; the hard gate 4
reads `CATEG_GARBAGE_DENSITY_HIGH` (also 0.35). `compute_quality_score()` still accepts an `is_upright_czech`
argument, which has no effect on the value.

> [!NOTE]
> **(#30) The archive's own allow list, `setup/word_lists.txt` `[allowed]`.** A listed token is skipped
> by the four per-token measurements — `score_word()` returns 0.0 for it, and `detect_gibberish_words()`,
> `detect_wx_words()` and `detect_fused_words()` do not count it — and `compute_valid_ratio()` treats it
> as non-evaluable, the way a unit is (it leaves the denominator rather than joining the numerator).
> Matching is exact and case-folded and does **not** fold diacritics. The section shipped empty until
> 2026-10-01; it now carries the entries @david-spacil reviewed (Q5a), the rest commented out.
>
> **A listed word is never evidence of damage** (issue #30 Q5b, answered yes by @david-spacil and
> @DanaKriv on 2026-10-01). Because a line made only of listed words has no gibberish and no
> weirdness, `rule_short_garbage` (gate 5) cannot open on it; and the shape witness (5c) skips a
> listed token in every clause, so it can never be the reason a line is convicted. Before the answer
> it could: with `ssuti` listed and the witness on, `ssuti vfetennl` was `Trash` on `ssuti`'s
> `initial_geminate` clause alone (probed 2026-09-22). The other words on a line are still read.

**Dynamic adjustments inside `compute_quality_score()` formula:**

**1. Garbage Penalty Guard (short clean strings)**

*Trigger:* `char_count ≤ 12`, `word_weird == 0.0`, **and** `garbage_density < QS_GARBAGE_NORM_MAX`.

*What happens:* `active_garbage_wt` is **halved** from `QS_WEIGHT_GARBAGE` (default 0.18) to 0.09. A compensating
constant of the same amount is added back to `base_score` so the total effective weight sum is unchanged and the
maximum possible score remains 1.0.

*Why:* Short archival label strings — `Lokalita:`, `Osada:`, `Okres:`, `Datum:` — contain a colon or other
structural punctuation that is counted as "garbage". Since the line is short, completely structurally clean (no weirdness),
and mostly legible, the reduced weight prevents the label from being unfairly penalised.

**2. Short Noisy Strings Penalty (`SHORT_NOISY_QS_PENALTY`)**

*Trigger:* `char_count ≤ 12` **and** (`word_weird > 0.0` **or** `garbage_density ≥ QS_GARBAGE_NORM_MAX`).

*Applied:* Subtracts the configurable `SHORT_NOISY_QS_PENALTY` (default 0.20) directly from the final score
(the result is floored at 0.0).

*Why:* Sinks very short noisy strings that might otherwise float into acceptable score ranges on their minimal features. It is **on** in the shipped configuration (0.20); set `SHORT_NOISY_QS_PENALTY = 0` to switch it off.

**3. Short **Perplexity** Cap (`SHORT_PPL_CAP`)**

*Trigger:* `word_count ≤ 2` **and** raw LM **perplexity** `> SHORT_PPL_CAP` (default **850.0** for Qwen2.5-0.5B,
**2500.0** for distilgpt2).

*Applied:* in `score_line()`, **before** `compute_quality_score()` is called and before any perplexity rule
reads it. The capped value is what is scored **and** what the `perplex` column holds; the raw model value is
kept in `perplex_raw`. Because 850 is below every perplexity route's floor (`HARD_SWEEP_PPL_MIN` 1000,
`PPL_EXTREME_MIN` 3000, `PPL_GARBAGE_ABSOLUTE` 30000), **no perplexity rule can fire on a one- or two-word
line**.

*Why:* Language models assign **perplexity** by predicting tokens. With only 1–2
words, there is almost no context available, so the model assigns extremely high **perplexity** even to perfectly valid words.
Without this cap, every single-word or two-word line would receive a near-zero **perplexity** component in its **quality score**.

---

## Categorisation logic

`determine_category()` in [text_util.py](../../text_util.py) holds all of the routing and returns
`(category, reason)`. `categorize_line()` calls it and then clamps the stored `quality_score` into the band
of the category it got (see [Score clamping](#score-clamping-after-category-assignment)). The *reason* is
the CSV's diagnostic column (`clear_threshold`, `noisy_threshold`, `trash_threshold`, `trash_hard_sweep`,
`trash_inverted`, `allcaps_novowel`, `lowppl_clear`); every gate also reports itself as `rule_*` to
`tools/rule_coverage_report.py`, and each can be switched off by the ablation kill-switch
(`DISABLED_RULES`).

The gates are checked **in this order; the first that matches decides and skips everything after it**,
the band routing included. The numbering follows the comments in the code. The conditions are the
*decisive* ones; [Overrides and exception cases](#overrides-and-exception-cases) lists what blocks or
relaxes each, and the [Decision record](../decisions/2026-10-categorization.md) keeps the measurements behind the long-argued ones.

| #    | Rule                         | Fires when                                                                                                                                                                                                                                     | Result (reason)                                                                                |
|------|------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| 0    | *(implicit)*                 | the line is blank                                                                                                                                                                                                                              | `Empty`                                                                                        |
| 0b   | `rule_domain_notation_categ` | `DOMAIN_NOTATION_CATEG` is set (shipped `Clear`) and the line holds a web or e-mail address anywhere. **Runs first**, above the hard sweep: an address is not trying to be a word, so every word-likeness signal below is noise on it          | the configured category (`clear_threshold` for `Clear`)                                        |
| 1    | `rule_hard_sweep`            | `orig_lang_score < HARD_SWEEP_LANG_MAX` (0.45) **and** `ppl > HARD_SWEEP_PPL_MIN` (1000): FastText cannot place it and the model is surprised                                                                                                  | `Trash` (`trash_hard_sweep`)                                                                   |
| 1a   | `rule_extreme_ppl`           | `ppl ≥ PPL_EXTREME_MIN` (3000) **and** `orig_lang_score < EXTREME_LANG_CONF` (0.85). Not for domain notation                                                                                                                                   | `Trash` (`trash_hard_sweep`)                                                                   |
| 1b   | `rule_absolute_ppl`          | `ppl ≥ PPL_GARBAGE_ABSOLUTE` (30000) **and not** upright Czech. Not for domain notation                                                                                                                                                        | `Trash` (`trash_hard_sweep`)                                                                   |
| 2    | `rule_inverted`              | not upright Czech **and** (`ghost_dominated`, or no Czech diacritics with `rot_ratio ≥ SUSPICIOUS_ROT_RATIO` (0.65), `ppl ≥ PPL_INVERTED_MIN` (200) and `GHOST_HITS_INVERTED_MIN` (1) ghost-word hit)                                          | `Trash` (`trash_inverted`)                                                                     |
| 3    | `rule_allcaps`               | every alphabetic word is uppercase, vowel ratio < 0.10, not a structured line                                                                                                                                                                  | `Trash` (`allcaps_novowel`)                                                                    |
| 4    | `rule_garbage_density`       | `garbage_density ≥ CATEG_GARBAGE_DENSITY_HIGH` (0.35), unless the line is structured, a siglum of up to two words, or the **trailing-fill rescue** applies (below)                                                                             | `Trash` (`trash_threshold`)                                                                    |
| —    | *forgiven*                   | computed here, once: `is_forgiven_headline()` (see the [pre-filter](#pre-filter)). A forgiven line skips gate 6, is `Noisy` instead of `Trash` at gates 5b and 7 and in the rescues, and can end `Clear` (`mm`)                                | a flag                                                                                         |
| 5b   | `rule_zero_alpha`            | not structured, not upright Czech, and no letter at all. Rarely reached: the pre-filter takes lines without letters                                                                                                                            | `Trash` (`Noisy` if forgiven)                                                                  |
| 6    | `rule_short_garbage`         | not forgiven, structured or domain notation; **at most 3 words**; no Czech diacritics; trust ≤ `LANG_SCORE_REMAP` (0.75) **or** `rot_ratio ≥ 0.65`; gibberish present **or** weirdness > 0; **and** score < 0.90 with a second witness (below) | `Trash` (`trash_threshold`)                                                                    |
| 7    | `rule_short_line`            | **at most 2 words**. Always returns (below), so such a line never reaches gates 8–10                                                                                                                                                           | `Clear` / `Noisy` / `Trash` (below)                                                            |
| 8    | `rule_lowppl_clear`          | `ppl < LOWPPL_CLEAR_MAX` (35) and at least 3 words: the model is near certain                                                                                                                                                                  | `Clear` (`lowppl_clear`); `Noisy` if the valid-word ratio < 0.85 or a damaged token is present |
| 9a–f | late gates                   | six structural red flags (below), each only with a score under 0.90 **and** strong garbage evidence                                                                                                                                            | a rescue or `Trash`, or falls through                                                          |
| 10   | band routing                 | nothing above decided                                                                                                                                                                                                                          | `Trash` / `Noisy` / `Clear` (below)                                                            |

**Gate 6, the second witness.** `rule_short_garbage` is the only route in the short-line path that returns
`Trash`. It convicts only when the score is under `CATEG_TRASH_SCORE_MAX + 0.35` (0.90) **and** a second
witness holds: `_has_strong_garbage_evidence()` (below), or the *shape witness* `_has_shape_garbage_evidence()`.
The witness is wired and measured but **ships off** (`SHORT_GARBAGE_WITNESS_ENABLE = false`, held with its
companion floor `SHORT_GARBAGE_WITNESS_FLOOR = false`), so on most short diacritic-free lines no witness holds
and the line is routed on. The gate reports `rule_short_garbage` whenever its *shape* matches, whether or not
it convicts, so coverage counts are not conviction counts. This is the debt behind the accuracy note in the
README (`oueussd` reads `Clear`); the measurements are in the [Decision record](../decisions/2026-10-categorization.md).

**Gate 7, short lines** (one or two words). In order:

1. a siglum (`Reg.Bez.`, `k.ú.`) with at least two letters → `Clear`;
2. a solitary single letter → `Trash`;
3. a bigram run (`IDID…`) **with** strong garbage evidence → `Trash`;
4. *damage*: weirdness ≥ 0.40, or a bigram run on a non-structured line, or gibberish/fused words on a line
   that is not structurally clean, or garbage density ≥ 0.35 that the trailing-fill rescue does not take →
   `Noisy`;
5. valid-word ratio 0, not upright Czech, not structured → `Trash` (`Noisy` if forgiven);
6. upright Czech, a clean valid-word ratio (1.0) or a structured line → `Clear`, or `Noisy` when it holds a
   damaged token or a non-Czech weirdness ≥ 0.40;
7. otherwise `Noisy`.

So a forgiven unit such as `mm` is `Clear`, not the `Noisy` the 2026-07-02 request anticipated.

**Late gates (9a–9f).** Each fires on its red flag, and acts only when the score is under **0.90**
(`CATEG_TRASH_SCORE_MAX + 0.35`) **and** `_has_strong_garbage_evidence()` holds; the verdict is then
`check_rescues()`. With no strong evidence a gate that fired changes nothing, and the line is routed by its
score.

| Gate | Rule                        | Red flag                                                                                                                           |
|------|-----------------------------|------------------------------------------------------------------------------------------------------------------------------------|
| 9a   | `rule_wqx_rot`              | (`rot_ratio > 0.50` or more than 10 % of the words contain `w`/`q`/`x`) **and** `orig_lang_score < 0.75` **and not** upright Czech |
| 9b   | `rule_vowelless`            | up to 3 words, vowel ratio < 0.30, all caps, not upright Czech, not structured                                                     |
| 9c   | `rule_ledger_fragmentation` | at least 4 words, more than 60 % of them bare digits or of one or two characters                                                   |
| 9d   | `rule_mid_uppercase`        | up to 2 words with a mid-word capital. **Unreachable**: gate 7 returns first for such lines                                        |
| 9e   | `rule_bigram_run`           | a word with a repeated two-letter run (`IDIDID`)                                                                                   |
| 9f   | `rule_fragment_tokens`      | the mean token length is under 2                                                                                                   |

**Strong garbage evidence** (`_has_strong_garbage_evidence()`): a structured line **never** has it; otherwise
any one of: gibberish is present; the valid-word ratio ≤ 0.20; trust ≤ 0.20 and `orig_lang_score` ≤ 0.50; garbage
density ≥ 0.35; weirdness ≥ 0.75; not upright Czech with trust ≤ 0.40 and weirdness ≥ 0.40. On the short,
diacritic-free lines the program is most criticised for, none of these usually holds (their words pass the
shape test, so the valid-word ratio is 1.0), which is why such lines read `Clear`.

**Rescues** (`check_rescues()`, in order; the first that applies turns the `Trash` into `Noisy`):

1. **trailing-fill rescue**: stripping trailing filler (spaces, `._:-–—<`) leaves a clean core
   (`compute_garbage_density(core) < 0.35`) that has a Czech diacritic or is short (up to 4 words and 25
   characters), with a valid-word ratio above 0 — a genuine short entry that trails off with dots or dashes;
2. **forgiven** headline or abbreviation;
3. **reference floor** (`rule_reference_floor`): a catalogue, measurement or inventory line with no damaged
   token.

**Band routing (gate 10)**, reached only when no gate returned:

```text
quality_score < CATEG_TRASH_SCORE_MAX (0.55)      →  check_rescues()   (Trash, unless rescued to Noisy)
otherwise:
    valid_word_ratio < MOSTLY_READABLE_VALID_MIN (0.85) and not lm_confident_czech   →  Noisy
    a damaged token (a digit or symbol inside a word)                                →  Noisy
    otherwise                                                                        →  Clear
```

`lm_confident_czech` is true for an upright-Czech line with `ppl < LOWPPL_CZECH_CLEAR_MAX` (180) and
`garbage_density < CZECH_CLEAR_GARBAGE_MAX` (0.15): a confidently Czech, low-perplexity, structurally clean
line may reach `Clear` even below the mostly-readable floor.

> [!IMPORTANT]
> **`CATEG_NOISY_SCORE_MAX` (0.80) decides nothing here.** It is read in one place, the clamp below. A line
> above the Trash band is `Clear` or `Noisy` by its valid-word ratio and damaged tokens; a `Noisy` line can
> carry a raw score of 0.91 (`Die Ausgrabung wurde im Jahre 1932 durchgeführt.`) and a `Clear` line one of 0.63
> (`WVL A KRST`).

### Score clamping after category assignment

`categorize_line()` clamps the stored `quality_score` into the range of the assigned band, so the CSV value
is consistent with the `categ` label *at that moment*:

* `Trash` → `min(score, CATEG_TRASH_SCORE_MAX − ε)`: always below 0.55;
* `Noisy` → `[CATEG_TRASH_SCORE_MAX, CATEG_NOISY_SCORE_MAX − ε]`: in `[0.55, 0.80)`;
* `Clear` → `max(score, CATEG_NOISY_SCORE_MAX)`: always at least 0.80.

The [document passes](#post-processing-smoothing) change labels afterwards and never recompute a score, so
after them a `Clear` line can carry 0.15, or a `Trash` line 0.80.

---

## Overrides and exception cases

Most lines are decided by the band routing (gate 10). Everything below decides a category **without or
against** the score band, and each has exceptions. The last column names the case of
[Worked examples](#worked-examples) that follows a line through it.

**Reading a decision in the CSV.** The reason that decided a line is in the boolean columns `allcaps_novowel`,
`lowppl_clear`, `trash_threshold`, `noisy_threshold` and `clear_threshold` (exactly one is `True`; none for
`Empty` and `Non-text`; `cleanprose_clear` is always `False` since Override 4 was removed). `trash_hard_sweep`
and `trash_inverted` are folded into `trash_threshold`, so the CSV is coarser than the code. A document pass
that moved the line later is in `pp_dedup`, `pp_surrounded_trash`, `pp_page_context` or `pp_inverted_run`; a
categoriser flag and a `pp_*` flag can both be `True`, which is the trail from the first decision to the override.

### Per-line overrides

| Override                     | Fires when                                                                                          | Result                             | What blocks or relaxes it                                                                                                                                                                        | Case                                                                                                  |
|------------------------------|-----------------------------------------------------------------------------------------------------|------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| **Address** (0b)             | a web or e-mail address anywhere on the line                                                        | the configured category (`Clear`)  | switched off when `DOMAIN_NOTATION_CATEG` is empty. A mis-scanned address is treated like a correct one                                                                                          | `address_is_clear`                                                                                    |
| **Hard sweep** (1)           | FastText < 0.45 **and** ppl > 1000                                                                  | `Trash`                            | lines of one or two words (their perplexity is capped at 850); domain notation is **not** exempt                                                                                                 | `hard_sweep`                                                                                          |
| **Extreme perplexity** (1a)  | ppl ≥ 3000 **and** FastText < 0.85                                                                  | `Trash`                            | a confident language guess; domain notation; one- and two-word lines                                                                                                                             | `extreme_ppl_weak_language`, `extreme_ppl_confident_language`                                         |
| **Absolute perplexity** (1b) | ppl ≥ 30000                                                                                         | `Trash`                            | upright Czech (a Czech diacritic or function word); domain notation; one- and two-word lines                                                                                                     | `absolute_ppl_foreign`, `absolute_ppl_upright_czech`                                                  |
| **Inverted scan** (2)        | most words are flip-images of common Czech words, or rotatable letters + diacritic-free + ppl ≥ 200 | `Trash`                            | upright Czech (any Czech diacritic or upright function word)                                                                                                                                     | `inverted_scan`                                                                                       |
| **All-caps scramble** (3)    | all words uppercase and vowel ratio < 0.10                                                          | `Trash`                            | structured lines                                                                                                                                                                                 | `allcaps_without_vowels`                                                                              |
| **Garbage density** (4)      | non-alphanumeric characters ≥ 35 % of the line                                                      | `Trash`                            | structured lines; a siglum of up to two words; the **trailing-fill rescue** (a clean entry followed by dots or dashes)                                                                           | `garbage_density`, `garbage_density_trailing_fill`                                                    |
| **Short garbage** (6)        | ≤ 3 words, no Czech diacritics, weak language guess (trust ≤ 0.75), gibberish or weirdness          | `Trash` only with a second witness | forgiven or structured lines; domain notation; Czech diacritics; a confident guess (trust > 0.75 and `rot_ratio` < 0.65); **no witness holds** → the line is routed on (`oueussd` reads `Clear`) | `short_garbage_weak_language`, `short_garbage_sure_language`, `short_garbage_is_not_always_convicted` |
| **Short line** (7)           | one or two words                                                                                    | `Clear` / `Noisy` / `Trash`        | decides alone: such a line never reaches gates 8–10 or the late gates                                                                                                                            | `short_line_*`, `notation_grid_reference`, `forgiven_unit`                                            |
| **Low perplexity** (8)       | ppl < 35 and at least 3 words                                                                       | `Clear`                            | capped at `Noisy` when the valid-word ratio is under 0.85 or a token is damaged                                                                                                                  | `low_perplexity_clear`, `low_perplexity_capped_by_readability`                                        |
| **Late gates** (9a–f)        | a structural red flag (rotation/`wqx`, vowelless caps, ledger, bigram run, fragment tokens)         | `Trash` or `Noisy` (rescues)       | **score ≥ 0.90**; **no strong garbage evidence** (the usual case on natural-looking lines); upright Czech for 9a and 9b                                                                          | `late_gate_*`, `fragment_tokens_without_evidence`, `ledger_line`                                      |
| **Readability cap** (10)     | valid-word ratio < 0.85                                                                             | `Noisy` instead of `Clear`         | `lm_confident_czech`: upright Czech, ppl < 180, garbage density < 0.15                                                                                                                           | `mostly_readable_cap`, `mostly_readable_relaxed_for_czech`                                            |
| **Damaged token** (8, 10)    | a digit or symbol inside a word, three or more words                                                | `Noisy` instead of `Clear`         | —                                                                                                                                                                                                | `band_noisy_damaged_token`                                                                            |
| **Rescue** (`check_rescues`) | a `Trash` verdict from the band or a late gate                                                      | `Noisy`                            | applies, in order: trailing-fill rescue, forgiven headline, clean reference line                                                                                                                 | `garbage_density_trailing_fill`                                                                       |

### Pre-filter bypasses and the allow list

| Mechanism                      | Effect                                                                                                                                                                                                                                                      |
|--------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Metadata marker, forgiven line | forwarded as `Process` even when short or letter-poor, instead of `Non-text` (see the [pre-filter](#pre-filter))                                                                                                                                            |
| Anchor bypass, digit bypass    | forwarded as `Process` when real words are present, or when digits are more than 40 % of a line no earlier rule took                                                                                                                                        |
| `[allowed]` word list          | a listed word (`setup/word_lists.txt`, matched exactly, case-folded) scores 0 weirdness, leaves the valid-word ratio's denominator like a unit, and **is never evidence of damage** (Q5b): it cannot be the reason a line is convicted by the shape witness |
| Language tiers                 | see [Foreign-language lines](#foreign-language-lines)                                                                                                                                                                                                       |

### Document-level overrides

| Pass (flag)                                  | Fires when                                                                                                                | Result                                                                          | What blocks or relaxes it                                                                                                                                     | Case            |
|----------------------------------------------|---------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------|
| **Identical-text vote** (`pp_dedup`)         | the same text got different categories in one document                                                                    | its most common category                                                        | a tie goes to the alphabetically first (`Clear` < `Empty` < `Noisy` < `Non-text` < `Trash`), so **a tie never lands on `Trash`**                              | `test_pass_1_*` |
| **Surrounded Trash** (`pp_surrounded_trash`) | a `Noisy` line with two `Trash` lines on each side and a score < 0.70                                                     | `Trash`                                                                         | the window crosses page boundaries; an `Empty` or `Non-text` line takes a place in it and breaks it; documents under 5 lines                                  | `test_pass_2_*` |
| **Page context** (`pp_page_context`)         | a page with ≤ 5 % `Clear`, < 50 % trusted-language lines and a median score < 0.55; or > 60 % `Clear` and a median > 0.80 | `Noisy` below 0.80 → `Trash`; `Trash` at ≥ 0.45 in a trusted language → `Noisy` | the language is the **raw** FastText label; an untrusted language is never promoted                                                                           | `test_pass_3_*` |
| **Inverted-scan sweep** (`pp_inverted_run`)  | suspicious lines (see below) are a run of at least 4, or at least 60 % of the page's scoreable lines                      | `Trash`                                                                         | Czech diacritics and a confident stored language make a line not suspicious; `Empty`/`Non-text` lines are left out of the count (they do **not** break a run) | `test_pass_4_*` |

---

## Foreign-language lines

The categoriser was calibrated on Czech archaeological reports, with German and English passages in them.
What it does with a line in another language comes from two separate mechanisms: how the **language label**
is used (the tiers), and the **Czech-first heuristics** that act on every line whatever its label. The first
is by design; the second is behaviour the pilot froze, listed here so it is not mistaken for a decision.

### What the language label changes

Shipped configuration: `EXPECTED_LANGS = ces,deu,eng`, `TRUSTED_FOREIGN_LANGS = deu,eng,fra,pol,ita,slk`.
"FastText" below is its raw confidence (`orig_lang_score`).

|                                                                     | Expected (`ces`, `deu`, `eng`) | Trusted foreign (`fra`, `pol`, `ita`, `slk`) | Unknown Latin script (`nld`, `lat`, `isl`, …)        | Unknown other script (`rus`, `kor`, …) |
|---------------------------------------------------------------------|--------------------------------|----------------------------------------------|------------------------------------------------------|----------------------------------------|
| Stored `lang` / `lang_score`                                        | unchanged                      | unchanged (`slk` too)                        | `ces_Latn`, 0.75                                     | `ces_<script>`, 0.50                   |
| Trust score                                                         | FastText × 1.0                 | × 0.85                                       | × 0.50                                               | × 0.50                                 |
| Gate 6's "trust ≤ 0.75" holds when FastText is                      | ≤ 0.75                         | ≤ 0.88                                       | always                                               | always                                 |
| Strong evidence "trust ≤ 0.20 and FastText ≤ 0.50" when FastText is | ≤ 0.20                         | ≤ 0.235                                      | ≤ 0.40                                               | ≤ 0.40                                 |
| Strong evidence "not upright Czech, trust ≤ 0.40, weirdness ≥ 0.40" | FastText ≤ 0.40                | ≤ 0.47                                       | ≤ 0.80                                               | ≤ 0.80                                 |
| Hard sweep, extreme and absolute perplexity                         | FastText and perplexity only   | same                                         | same                                                 | same                                   |
| Page sweep arm A (stored < 0.45)                                    | FastText < 0.45                | FastText < 0.45                              | never (stored 0.75)                                  | never (stored 0.50)                    |
| Page sweep arms B and C (stored < 0.90)                             | FastText < 0.90                | FastText < 0.90                              | always                                               | always                                 |
| Page-context rules: "trusted language"                              | yes                            | yes                                          | no: never promoted, and counts toward a garbage page | no                                     |

A **confident wrong label** therefore costs only its trust: a Dutch label at 0.99 on a Czech line reaches
the guards as 0.495. A **weak** label on a short line is what opens gate 6, and the unknown tier makes it
open for every unknown language: `Fundort unbekannt` at FastText 0.30 is `Clear` when labelled `deu` (expected;
no strong evidence) and `Trash` when labelled `nld` (a trust of 0.15 is strong evidence on its own).

### Czech-first heuristics that act on every line

These ignore the label entirely:

* **Upright Czech** (`is_upright_czech`). A line is "upright Czech" when it has a Czech diacritic **or
  contains a word of the rotation whitelist** (`po pod do od on ony by bez ne nebo ven den zde se ve mez pouze
  bude`). `CZ_DIACS` includes `é á í ó ú ý`, so French, Polish and Slovak lines with those letters qualify, and a
  German sentence containing `den` or an English one containing `on` or `by` does too. Upright Czech bypasses the
  inverted-scan route, absolute perplexity, the `wqx`/rotation and vowelless gates and the rotation penalty,
  and qualifies a line for `lm_confident_czech`.
* **The word test knows Czech function words only.** `[short_valid]` and `SINGLE_CHAR_ALLOWED` list the Czech
  short words. `in`, `im`, `of`, `is`, `de`, `la` and a year are *invalid* tokens, so a clean English or German
  sentence of six or seven words can fall under the 0.85 valid-word floor and read `Noisy`.
* **`w`, `q`, `x`.** Rare in Czech. Each such token adds `WORD_W_PENALTY` (0.20) to its weirdness, counts toward
  the `wqx` gate (9a), and a word with two of them (`Wasserwirtschaft`, `windows`) is counted as `gibberish`,
  which is strong garbage evidence. German, Polish and English are hit hardest.
* **Rotatable letters.** `p b d q n u w m o x s z e y v` are the letters an upside-down scan turns into one
  another. A line whose letters are more than 65 % of them **and** in which at least 15 % of the words contain
  `w`, `q` or `x` takes the rotation penalty (`INVERTED_WEIRD_PENALTY`, +0.45 per word) unless it is upright
  Czech: a German sentence with `wurden … der` can drop to `Noisy` where the same sentence with `den` does not.
* **The all-caps code rule** of the pre-filter is language-blind: `LUXEMBOURG`, `SCHLOSSBERG` and `TEXTIL` are
  `Non-text`.
* **Non-Latin scripts.** The vowel class is Latin, so a Cyrillic word has a vowel ratio of 0 and a
  "vowelless" weirdness of 0.50: `Москва` reads `Noisy`.

### Known limits, and what to measure next

Pinned as `known_limit` cases in the walkthrough tests, so that fixing one shows up there:

| Line (FastText · perplexity)                                       | What happens                                                                                                                 | Case                                         |
|--------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------|
| `The excavation was carried out in 1932.` (eng 0.97 · 40)          | a clean English sentence reads `Noisy` (valid-word ratio 0.83)                                                               | `limit_english_function_words`               |
| `Die Ausgrabung wurde im Jahre 1932 durchgeführt.` (deu 0.98 · 30) | the same for German, even with a perplexity under 35                                                                         | `low_perplexity_capped_by_readability`       |
| `Musée de l'Homme` (fra 0.95 · 300)                                | a clean French name reads `Noisy`                                                                                            | `mostly_readable_cap`                        |
| `Wasserwirtschaft` (deu 0.60 · 900)                                | a German compound is convicted by gate 6                                                                                     | `limit_w_words_german`                       |
| `wall with two windows` (eng 0.70 · 400)                           | `Trash` at FastText 0.70, `Clear` at 0.90                                                                                    | `limit_w_words_english_low_confidence`       |
| `Sie wurden in der Grube gefunden` (deu 0.95 · 40)                 | `Noisy` through the rotation penalty; with `den` it is `Clear`                                                               | `limit_rotation_penalty_german`              |
| `Москва` (rus 0.99 · 900)                                          | `Noisy`, from the Latin-only vowel class                                                                                     | `tier_unknown_non_latin_language`            |
| `denní`, `oddělení`                                                | a doubled consonant occurring twice counts as a "repeated character" (weirdness 0.35 each), although they are ordinary Czech | `test_known_limit_czech_doubled_consonants…` |

None of these is a release fix: the category semantics are frozen for the S2 pilot (decision of 2026-09-30
on ocr-postprocess#2), and each would move categories over millions of lines. **After the pilot**, measure
each candidate with `tools/corpus_change.py` (`reach`, then `recat` and `diff`) against the labelled sets in
`tools/gold/` before adopting it:

1. German, English and French function words in the `[short_valid]` list of `setup/word_lists.txt` (the
   existing lever; no code change);
2. language-aware `w`/`q`/`x` handling: no weirdness and no gate 9a for an expected or trusted language
   whose raw confidence is high;
3. the readability floor (`MOSTLY_READABLE_VALID_MIN`) read per language;
4. a doubled-consonant exemption in `detect_repeated_chars`.

---

## Page-relative perplexity (default OFF)

`SHORT_PPL_CAP` (850) caps perplexity for `wc <= 2`, and **the capped value is what is scored and what the
`perplex` column holds** (the raw LM number is kept in `perplex_raw`). Since 850 is below `HARD_SWEEP_PPL_MIN`
(1 000), `PPL_EXTREME_MIN` (3 000) and `PPL_GARBAGE_ABSOLUTE` (30 000), the consequence is blunt:

> [!IMPORTANT]
> **No perplexity rule can fire on a one- or two-token line.** For that population `rule_short_garbage`
> is the only Trash route that is not score-band routing.

Simply removing the cap does not fix it, because both surviving perplexity routes *also* gate on **low**
language-ID confidence — and on this population confidence is anti-correlated with quality: `oueussd`
scores 0.9163 while `malakofauna` scores 0.56. Uncapped, real domain vocabulary is demoted around
perplexity 3 000 while `oueussd` survives to 30 000.

`apply_page_perplexity_blend()` in [classify_TEXT.py](../../classify_TEXT.py) instead reads a short line
against **the long lines on its own page**, which is the comparison a human makes:

```
reference = median( perplex_raw of lines with word_count >= PAGE_PPL_LONG_MIN_WC )
blended   = exp( w · ln(own) + (1 − w) · ln(reference) )        w = PAGE_PPL_BLEND_WEIGHT
```

Both choices carry weight. The reference is a **median** so one outlier cannot poison a page — a page
containing a 6·10⁷ line still yields a reference near 39, where a mean would give 1.5·10⁷. And the
averaging happens in **log space** because perplexity here spans six orders of magnitude; an arithmetic
blend is simply the largest value. Fallback order is page → document → today's `SHORT_PPL_CAP`
behaviour, unchanged.

The pass runs immediately **before** `apply_document_postprocessing()` in both the live pipeline and the
offline re-scorer, and re-scores the affected rows through `score_line(..., apply_short_cap=False)` — the
smoothing pass only harmonises existing labels, it never recomputes a quality score. Without
`apply_short_cap=False` the cap would pin every blended value back to 850 before a rule reads it
(`TestBlendReachesTheRules` asserts that a category actually moves).

| constant                  | default | role                                                                      |
|---------------------------|---------|---------------------------------------------------------------------------|
| `PAGE_PPL_BLEND_ENABLE`   | `false` | feature flag, **not** a tunable — deliberately absent from `SEARCH_SPACE` |
| `PAGE_PPL_BLEND_WEIGHT`   | 0.5     | weight on the line's own perplexity                                       |
| `PAGE_PPL_LONG_MIN_WC`    | 4       | minimum word count to count toward the reference                          |
| `PAGE_PPL_MIN_LONG_LINES` | 3       | minimum long lines before a page reference is trusted                     |

Two **CSV** columns support it (40 → 42): `perplex_raw`, the uncapped LM value — required because
blending from the already-capped `perplex` would blend a constant, and it makes the blend re-tunable
offline with no GPU — and `perplex_blend`, written only when the blend actually moved a value and left
**blank** otherwise. Blending always reads `perplex_raw`, so repeated passes are a fixed point.

> [!WARNING]
> This is a **page-consistency prior, not a garbage detector**. On a mixed page it pulls a garbage token
> toward its clean neighbours, and on a garbage page it pushes a clean token up. It also does **not**, on
> its own, make it safe to gate `rule_short_garbage`: it clears `PPL_EXTREME_MIN` but not the lang gate
> in front of it, so confidently-mislabelled garbage such as `oueussd` still escapes. The constants need
> calibrating on a real ARÚP/ARUB run before the flag is turned on, and
> `service/text_inference.py::_classify_lines` must be wired first — it holds a whole page, while
> `_classify_line` is single-line by construction.

---

## Post-processing smoothing

After all lines of a document are classified and written to CSV, `apply_document_postprocessing()` in
[classify_TEXT.py](../../classify_TEXT.py) runs **four passes, in this order**. The offline re-scorer
(`tools/recategorize_from_csv.py`) reuses the same function, so production output and offline re-measurement do
not drift; the service does not run them (see [Who runs it](../categorization_logic.md#who-runs-it)). **Each pass changes `categ` only,
never `quality_score`.** When `PAGE_PPL_BLEND_ENABLE` is on (default off), the page-relative perplexity blend
re-scores short lines first.

**1. Identical-text vote (`pp_dedup`).** All occurrences of the same text in a document are harmonised to the
**most common** category given to that text. *Why:* repeating strings are boilerplate (page headers, footers,
running titles, form labels); the same physical text should get the same label throughout, and the majority is
the most reliable estimate. **A tie goes to the alphabetically first category** (`Clear` < `Empty` < `Noisy` <
`Non-text` < `Trash`), so a tie can never land on `Trash`; that is an accident of the spelling of the five names,
pinned by a test (`tests/test_page_postprocess.py`), and renaming a category would silently change it. Its measured effect is in
[the evaluation page](../evaluation/categorization.md#document-passes).

**2. Surrounded-Trash smoothing (`pp_surrounded_trash`).** A `Noisy` line with two `Trash` lines on each side
(positions −2, −1, +1, +2) and a score below `CATEG_TRASH_SCORE_MAX + SURROUNDED_TRASH_QS_MARGIN` (0.55 + 0.15 =
0.70) becomes `Trash`. The window runs over the whole document in page and line order, so it **crosses page
boundaries**, and an `Empty` or `Non-text` line takes a place in it and breaks it. Documents under five lines are
skipped. *Why:* an isolated `Noisy` island inside four `Trash` lines is almost certainly corrupted text that
narrowly escaped the threshold; the score guard leaves a `Noisy` line of 0.78 alone even there.

**3. Page-context rules (`pp_page_context`).** Per page, over its scoreable lines (everything but `Empty` and
`Non-text`), using the median score, the `Clear` share and the share of lines in a *trusted* language
(`EXPECTED_LANGS ∪ TRUSTED_FOREIGN_LANGS`, judged on the **raw** FastText label):

* **Heavily-garbage page.** `Clear` share ≤ `PAGE_GARBAGE_CLEAR_MAX` (0.05), trusted share <
  `PAGE_GARBAGE_LANG_MAX` (0.50) and median < `PAGE_GARBAGE_MEDIAN_QS_MAX` (0.55): every `Noisy` line scoring below
  `PAGE_GARBAGE_NOISY_QS_MAX` (0.80) becomes `Trash`.
* **Predominantly-clean page.** `Clear` share > `PAGE_CLEAN_CLEAR_MIN` (0.60) and median >
  `PAGE_CLEAN_MEDIAN_QS_MIN` (0.80): every `Trash` line scoring at least `PAGE_CLEAN_RECOVER_QS_MIN` (0.45)
  **and** in a trusted language becomes `Noisy`.

*Why:* a page that is almost all garbage rarely holds a genuinely recoverable `Noisy` line, and a page that is
almost all clean rarely holds an unrecoverable `Trash` one; the page is context the per-line categoriser
cannot see. An untrusted-language line is never promoted.

**4. Inverted-scan sweep (`pp_inverted_run`).** Run last, per page, over every line not `Empty` or `Non-text`
(those are **left out before anything is counted**; they neither count toward the page nor break a run). A line
is *suspicious* when it meets **any** of three arms. The language number is the **stored** `lang_score`, and
`perplex` is the stored (short-line-capped) perplexity:

* **Arm A:** no Czech diacritic **and** stored `lang_score < LANG_SCORE_ROUGH` (0.45).
* **Arm B:** `perplex ≥ PPL_INVERTED_MIN` (200) **and** `word_weird > 0` **and** stored `lang_score <
  ROT_HIGH_LANG_CONF` (0.90).
* **Arm C:** no Czech diacritic **and** `rot_ratio ≥ ROT_RATIO_INVERTED_MIN` (0.55) **and** `perplex ≥
  PPL_INVERTED_MIN` **and** stored `lang_score < ROT_HIGH_LANG_CONF`.

Suspicious lines become `Trash` when they are **at least `INVERTED_PAGE_MAJORITY` (0.60) of the page's
scoreable lines** (the page-majority arm, checked first, applied to the whole page and skipping the run rule for
that page), **or**, failing that, form a **contiguous run of at least `INVERTED_RUN_MIN` (4)**.

*Why a page-majority arm?* Some inverted or garbage scans break into short isolated fragments separated by clean
or short lines, so the suspicious lines never form a run of four and escape the run rule. When most of a page's
scoreable lines are individually suspicious, the page is an inverted or garbage scan, and every suspicious line
on it is downgraded whatever the run length. Lines with Czech diacritics or a confident stored language are never
suspicious, so genuine content on the page is kept. *Why three arms?* An inverted scan sometimes produces partial
diacritics (the OCR engine reads upside-down glyphs as plausible accented letters), so arm A alone would miss it;
B and C each catch it with language-model uncertainty, the second with the rotatable-letter share.

---

The table consolidates every factor that influences the quality score or the category, where it is controlled
and its known edge cases.

| Factor                                           | Where applied                                                        | Config key(s)                                                                                                                                                                                 | Edge cases / exceptions                                                                                                                                                                                                                                                                              |
|--------------------------------------------------|----------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Valid word ratio                                 | `compute_quality_score` (35% weight)                                 | `QS_WEIGHT_VALID_WORD`                                                                                                                                                                        | All-caps **OCR** prefix guard: tokens like `AAMMNAbSSOAO` are excluded from valid-word count even though they are alphabetically dominant.                                                                                                                                                           |
| Word weirdness ratio                             | `compute_quality_score` (18% weight)                                 | `QS_WEIGHT_WEIRD`                                                                                                                                                                             | Isolated single letters score 0.85 (**OCR** spaced-out noise); isolated digits/measurements score 0.25 (tolerable). All-caps words and academic titles excluded from mid-uppercase detection.                                                                                                        |
| Perplexity (LM)                                  | `compute_quality_score` (8% weight)                                  | `QS_WEIGHT_PERPLEXITY`, `PERPLEXITY_THRESHOLD_MAX`                                                                                                                                            | Short-text **perplexity** is capped at `SHORT_PPL_CAP` before scoring. `rule_lowppl_clear` (`ppl < 35`, three or more words) decides alone for highly confident predictions.                                                                                                                         |
| Text length                                      | `compute_quality_score` (2% weight)                                  | `QS_WEIGHT_LENGTH`, `QS_LENGTH_MAX`                                                                                                                                                           | Full reward for lines ≥ 100 chars; no minimum penalty for short lines.                                                                                                                                                                                                                               |
| Garbage density                                  | `compute_quality_score` (18% weight)                                 | `QS_WEIGHT_GARBAGE`, `QS_GARBAGE_NORM_MAX`                                                                                                                                                    | **Halved** to 9% for lines ≤ 12 characters with zero weirdness and low density (short-string guard). Evaluated on the original text string. Normalisation constant (`QS_GARBAGE_NORM_MAX`) is separate from the hard-gate constant (`CATEG_GARBAGE_DENSITY_HIGH`), see B2 note above.                |
| Vowel quality                                    | `compute_quality_score` (7% weight)                                  | `QS_WEIGHT_VOWEL`, `VOWEL_RATIO_LOW`, `VOWEL_RATIO_HIGH`                                                                                                                                      | Linear ramp: full score in [0.20, 0.70] vowel ratio, ramps to 0.0 outside that range.                                                                                                                                                                                                                |
| Language confidence                              | `compute_quality_score` (5% weight)                                  | `QS_WEIGHT_LANG`                                                                                                                                                                              | Uses the **trust score** (FastText × the language's tier), never the stored `lang_score` (see [Which language number is read where](#which-language-number-is-read-where)); defaults to 0.5 when unavailable.                                                                                        |
| Gibberish ratio                                  | `compute_quality_score` (4% weight)                                  | `QS_WEIGHT_GIBBERISH`                                                                                                                                                                         | Words ≥ 60% digits/separators excluded. Detection only on words ≥ 4 characters. Folds in the w/x count.                                                                                                                                                                                              |
| Fused word ratio                                 | `compute_quality_score` (3% weight)                                  | `QS_WEIGHT_FUSED`, `FUSED_VOWEL_RUN_MIN`                                                                                                                                                      | Triggers on tokens > 14 chars, consonant runs of 5+, or vowel runs of 3+.                                                                                                                                                                                                                            |
| Hard sweep / extreme / absolute PPL (gates 1–1b) | `determine_category`                                                 | `HARD_SWEEP_LANG_MAX`, `HARD_SWEEP_PPL_MIN`, `PPL_EXTREME_MIN`, `EXTREME_LANG_CONF`, `PPL_GARBAGE_ABSOLUTE`                                                                                   | Three independent hard-Trash routes; all fold to `trash_hard_sweep`. Fire before any other check, including forgiveness.                                                                                                                                                                             |
| Inverted/mirrored lexicon (gate 2)               | `determine_category`, `analyze_rotation_signals`, `ghost_word_share` | `GHOST_DOMINATED_MIN_RATIO`, `SUSPICIOUS_ROT_RATIO`, `PPL_INVERTED_MIN`, `GHOST_HITS_INVERTED_MIN`                                                                                            | Bypassed by any Czech diacritic or upright whitelist word (`is_upright_czech`). Recorded as `trash_inverted`.                                                                                                                                                                                        |
| All-caps vowel-less (gate 3)                     | `determine_category`                                                 | none (hardcoded 0.10 vowel-ratio floor)                                                                                                                                                       | Fires only if **all** alphabetic words are uppercase **and** `vowel_ratio < 0.10`. Recorded as `allcaps_novowel`.                                                                                                                                                                                    |
| Garbage-density hard override (gate 4)           | `determine_category`                                                 | `CATEG_GARBAGE_DENSITY_HIGH`                                                                                                                                                                  | Bypassed by `rule_trailing_fill_rescue`. Recorded as `trash_threshold`.                                                                                                                                                                                                                              |
| Forgiven headline/abbreviation                   | `determine_category`, `is_forgiven_headline`, also `pre_filter_line` | `SHORT_EXCEPTION_TOKENS`, `HEADLINE_MAX_WORDS`, `HEADLINE_MAX_DIGITS`                                                                                                                         | Computed once after gate 4; only ever lifts an otherwise-Trash outcome to `Noisy`, never bypasses gates 1–4. Also used directly in the CPU pre-filter to route straight to `Process`.                                                                                                                |
| Structural short-garbage route (gate 6)          | `determine_category`                                                 | `ISOLATED_CHAR_MIN_TOKENS`, `LANG_SCORE_REMAP`                                                                                                                                                | Skipped for `forgiven`, structured and domain-notation lines; convicts only with a second witness. Recorded as `trash_threshold`.                                                                                                                                                                    |
| High LM confidence override (gate 8)             | `determine_category`                                                 | `LOWPPL_CLEAR_MAX` (NOT `PERPLEXITY_THRESHOLD_MAX`)                                                                                                                                           | Requires `ppl < 35.0` **and** `word_count ≥ 3`. Capped at `Noisy` if `valid_word_ratio < MOSTLY_READABLE_VALID_MIN`. Recorded as `lowppl_clear` / `noisy_threshold`.                                                                                                                                 |
| Late-stage structural penalty gates              | `determine_category`                                                 | none new — reuses `rot_ratio`, `wqx` density, fragmentation ratio, mid-uppercase                                                                                                              | `rule_wqx_rot`, `rule_vowelless`, `rule_ledger_fragmentation`, `rule_mid_uppercase`; each only forces a rescue/Trash outcome when `quality_score < CATEG_TRASH_SCORE_MAX + 0.35` (def. 0.90).                                                                                                        |
| Trailing-fill rescue                             | `determine_category`, `_trailing_fill_rescued`                       | `CATEG_GARBAGE_DENSITY_HIGH`                                                                                                                                                                  | Rescues short/diacritic-bearing lines whose only issue is trailing punctuation/dashes. Used at gate 4 and inside `check_rescues()`.                                                                                                                                                                  |
| Mostly readable valid cap                        | `determine_category`                                                 | `MOSTLY_READABLE_VALID_MIN`                                                                                                                                                                   | Caps semi-readable strings at `Noisy` unless `lm_confident_czech` (below) relaxes it.                                                                                                                                                                                                                |
| LM-confident-Czech relaxation                    | `determine_category`, `_lm_confident_czech`                          | `LOWPPL_CZECH_CLEAR_MAX`, `CZECH_CLEAR_GARBAGE_MAX`                                                                                                                                           | A confidently-Czech, low-perplexity, structurally clean line can reach `Clear` even below the mostly-readable floor.                                                                                                                                                                                 |
| ~~Near-boundary clean-prose promotion~~          | *(removed)*                                                          | *(removed: `CLEAN_PROSE_*` constants no longer exist)*                                                                                                                                        | Previously promoted borderline `Noisy` lines to `Clear`; this path has been removed from the current implementation. See the note in [Categorisation logic](#categorisation-logic).                                                                                                                  |
| Short **perplexity** cap                         | `classify_TEXT.score_line()` (before scoring)                        | `SHORT_PPL_CAP`                                                                                                                                                                               | Applied only to lines with ≤ 2 words. The `perplex` column holds the capped value; `perplex_raw` holds the model's.                                                                                                                                                                                  |
| Language remapping                               | `classify_TEXT.py` / `remap_lang` (before scoring)                   | `EXPECTED_LANGS`, `TRUSTED_FOREIGN_LANGS`, `LANG_SCORE_REMAP`, `LANG_SCORE_REMAP_FAR`, `LANG_REMAP_ALWAYS`                                                                                    | Unknown languages remapped to first entry of `EXPECTED_LANGS`. Stored score set unconditionally (`LANG_REMAP_ALWAYS=true`, default) or capped (`=false`), except `slk` which always retains its original score. `orig_lang_score` is untouched either way and drives gates 1/2/late-stage penalties. |
| Context smoothing (rolling window)               | Post-processing pass 2, `classify_TEXT.py`                           | `CATEG_TRASH_SCORE_MAX`, `SURROUNDED_TRASH_QS_MARGIN`                                                                                                                                         | `Noisy` line must be surrounded by 2 `Trash` lines on **each** side (4 total); score must be < trash threshold + 0.15. Recorded as `pp_surrounded_trash`.                                                                                                                                            |
| Page-context rules                               | Post-processing pass 3, `classify_TEXT.py`                           | `PAGE_GARBAGE_CLEAR_MAX`, `PAGE_GARBAGE_LANG_MAX`, `PAGE_GARBAGE_MEDIAN_QS_MAX`, `PAGE_GARBAGE_NOISY_QS_MAX`, `PAGE_CLEAN_CLEAR_MIN`, `PAGE_CLEAN_MEDIAN_QS_MIN`, `PAGE_CLEAN_RECOVER_QS_MIN` | Symmetric garbage-page-pulls-down / clean-page-promotes-up rules, run **after** dedup and rolling-window smoothing, **before** the inverted-scan sweep. Recorded as `pp_page_context`.                                                                                                               |
| Page-level inverted-scan sweep                   | Post-processing pass 4, `classify_TEXT.py`                           | `ROT_RATIO_INVERTED_MIN`, `PPL_INVERTED_MIN`, `LANG_SCORE_ROUGH`, `ROT_HIGH_LANG_CONF`, `INVERTED_RUN_MIN`, `INVERTED_PAGE_MAJORITY`                                                          | Three arms (diacritic-absence, perplexity/weirdness, rotation) — see above. Suspicious lines Trashed via page-majority (checked first) or a run of ≥ 4. Recorded as `pp_inverted_run`.                                                                                                               |
| Header/footer deduplication                      | Post-processing pass 1, `classify_TEXT.py`                           | none                                                                                                                                                                                          | Based on **exact text match** across the whole document; harmonises to modal category. Recorded as `pp_dedup`. Runs **first**, before the other three passes.                                                                                                                                        |

---

## Worked examples

Each row follows one input through the program, with the model values given as inputs. They are the cases of
[`tests/test_categorization_walkthrough.py`](../../tests/test_categorization_walkthrough.py): the table is generated
from them (`worked_examples_table()`), so it cannot drift from the code. **Known limit** marks behaviour that is
what the program does today, pinned so it is not mistaken for a decision; see
[Foreign-language lines](#foreign-language-lines) and [Overrides and exception cases](#overrides-and-exception-cases).
"Rules that fired" lists every `rule_*` the line reached, which can be more than the one that decided.

The pre-filter's own routes (`Empty`, `Non-text`, the repairs) are pinned in the same file by
`test_the_prefilter_routes_before_any_model_runs`; the four document passes by `test_pass_1_*` to `test_pass_4_*`.

<!-- worked-examples:start -->
| Line                                                           | FastText · perplexity | Rules that fired                                     | Category | Reason             | What happens                                                                                                                                                                                                           |
|----------------------------------------------------------------|-----------------------|------------------------------------------------------|----------|--------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `http://www.arub.cz`                                           | eng 0.4 · 2000        | `domain_notation_categ`                              | Clear    | `clear_threshold`  | gate 0b: every web or e-mail address gets `DOMAIN_NOTATION_CATEG` (`Clear`) before any signal is read; one word, so the perplexity is capped                                                                           |
| `xqzv wkpl mnbv`                                               | ces 0.3 · 2500        | `hard_sweep`                                         | Trash    | `trash_hard_sweep` | gate 1: FastText cannot place it (`orig_lang_score` < 0.45) and the model is surprised (ppl > 1000)                                                                                                                    |
| `Opomenulé nebo opozděné ohlášení trestů se pokutou`           | ces 0.6 · 3500        | `extreme_ppl`                                        | Trash    | `trash_hard_sweep` | gate 1a: ppl >= 3000 and FastText below 0.85 -- perplexity alone is enough once the language is unsure                                                                                                                 |
| `Opomenulé nebo opozděné ohlášení trestů se pokutou`           | ces 0.95 · 2500       | —                                                    | Clear    | `clear_threshold`  | the same text with FastText sure of Czech: no perplexity route opens                                                                                                                                                   |
| `The excavation was carried out in the summer`                 | eng 0.95 · 35000      | `absolute_ppl`                                       | Trash    | `trash_hard_sweep` | gate 1b: ppl >= 30000, whatever FastText says -- unless the line is upright Czech                                                                                                                                      |
| `Opomenulé nebo opozděné ohlášení trestů se pokutou`           | ces 0.95 · 35000      | —                                                    | Clear    | `clear_threshold`  | a Czech diacritic (or an upright function word) protects the line from gate 1b                                                                                                                                         |
| `noywqued noqnsoa es yasoq yuasvyo quqpzodo oqou onuauodo`     | ces 0.75 · 856        | `inverted`                                           | Trash    | `trash_inverted`   | gate 2: upside-down Czech reads as plausible letters; most words are flip-images of common words                                                                                                                       |
| `XCVBN PQRST ZZXWV`                                            | ces 0.8 · 300         | `allcaps`                                            | Trash    | `allcaps_novowel`  | gate 3: all words uppercase and a vowel ratio under 0.10                                                                                                                                                               |
| `\|\|\|\|\| ~~~ ##### abc def`                                 | ces 0.8 · 300         | `garbage_density`                                    | Trash    | `trash_threshold`  | gate 4: non-alphanumeric characters are 56 % of the line (>= 0.35)                                                                                                                                                     |
| `Popis nálezu ........`                                        | ces 0.8 · 300         | —                                                    | Clear    | `clear_threshold`  | the dots are filler after a clean entry (`rule_trailing_fill_rescue`): gate 4 lets it through                                                                                                                          |
| `olie`                                                         | ces 0.5 · 900         | `short_garbage`                                      | Trash    | `trash_threshold`  | gate 6: one to three words, no Czech diacritics, FastText <= 0.75, and a second witness (its vowels make it `gibberish`)                                                                                               |
| `olie`                                                         | ces 0.9 · 900         | `short_line`                                         | Clear    | `clear_threshold`  | the same word with FastText at 0.90: the language gate of gate 6 stays shut; gate 7 passes it                                                                                                                          |
| `oueussd`                                                      | isl 0.9163 · 4820     | `short_garbage`, `short_line`                        | Clear    | `clear_threshold`  | **Known limit.** gate 6 opens but no second witness holds: the shape witness that would convict it ships OFF (`SHORT_GARBAGE_WITNESS_ENABLE`), so this reads `Clear`                                                   |
| `xyzq`                                                         | ces 0.5 · 900         | `short_garbage`, `short_line`                        | Noisy    | `noisy_threshold`  | gate 7: a one- or two-word line never reaches the late gates; weirdness >= 0.40 is `Noisy`                                                                                                                             |
| `ClAŕ,`                                                        | ces 0.4 · 850         | `short_garbage`                                      | Trash    | `trash_threshold`  | gate 6: nothing on the line passes the word test (valid-word ratio 0)                                                                                                                                                  |
| `Praha`                                                        | ces 0.9 · 900         | `short_line`                                         | Clear    | `clear_threshold`  | gate 7: a clean word                                                                                                                                                                                                   |
| `II/C`                                                         | ces 0.5 · 900         | `domain_notation`, `short_line`                      | Clear    | `clear_threshold`  | an excavation grid reference: notation suppresses gate 6, so the short line passes                                                                                                                                     |
| `mm`                                                           | ces 0.5 · 900         | `short_line`                                         | Clear    | `clear_threshold`  | a bare unit is a `forgiven` abbreviation (#3 2026-07-02): scored, not dropped as `Non-text`                                                                                                                            |
| `2, Popis nálezu i - 3`                                        | ces 0.6 · 900         | `ledger_fragmentation`                               | Clear    | `clear_threshold`  | a numbered headline is `forgiven`; the ledger gate fires but a score of 0.79 is above 0.55 and the line has no strong garbage evidence                                                                                 |
| `Dle zprávy z roku 1932 byla nalezena keramika`                | ces 0.95 · 25         | `lowppl_clear`                                       | Clear    | `lowppl_clear`     | gate 8: perplexity < 35 on three or more words -- the model is near certain                                                                                                                                            |
| `Die Ausgrabung wurde im Jahre 1932 durchgeführt.`             | deu 0.98 · 30         | `lowppl_clear`                                       | Noisy    | `noisy_threshold`  | **Known limit.** gate 8 never yields `Clear` for a line whose valid-word ratio is under 0.85 (`1932` and `im` fail the word test)                                                                                      |
| `wxq pbqd wqx bdpq nuxw mwq`                                   | ces 0.6 · 300         | `wqx_rot`                                            | Trash    | `trash_threshold`  | gate 9: rotated-glyph density with FastText under 0.75, a score under 0.90 and strong evidence (weirdness >= 0.75)                                                                                                     |
| `wxq pbqd wqx bdpq nuxw mwq`                                   | ces 0.9 · 300         | `damaged_token`                                      | Noisy    | `noisy_threshold`  | the same line with FastText at 0.90: the language condition of the late gate fails; a damaged token caps it at `Noisy`                                                                                                 |
| `Zwei Gefäße aus Wien wurden gewaschen`                        | deu 0.7 · 400         | `wqx_rot`                                            | Clear    | `clear_threshold`  | the late gates fire but a score >= 0.90 is left alone                                                                                                                                                                  |
| `WVL A KRST`                                                   | ces 0.4 · 300         | `short_garbage`, `vowelless`, `wqx_rot`              | Clear    | `clear_threshold`  | **Known limit.** three late gates fire and none convicts: none of the six kinds of strong garbage evidence holds, so the line is routed by its score (0.63 >= 0.55) and every word passes the shape test               |
| `k z o s v u`                                                  | ces 0.7 · 120         | `fragment_tokens`, `ledger_fragmentation`, `wqx_rot` | Clear    | `clear_threshold`  | **Known limit.** single letters are valid Czech prepositions, so the valid-word ratio is 1.0 and no evidence holds                                                                                                     |
| `nonč, mI 47 žn dn A' 1074 484 "Nik 1726.`                     | ces 0.5 · 900         | `ledger_fragmentation`, `mostly_readable_noisy`      | Noisy    | `noisy_threshold`  | **Known limit.** #2's table-garble example: more than 60 % of the tokens are digits or one or two letters; the valid-word ratio (0.60) caps it at `Noisy`, not `Trash`                                                 |
| `a b c d e f`                                                  | ces 0.1 · 120         | `ledger_fragmentation`                               | Trash    | `trash_threshold`  | the same gate with a score of 0.28: band routing sends it to `Trash`                                                                                                                                                   |
| `Dle zpr4vy z r0ku 1932 byla nal3zena kera1ika`                | ces 0.6 · 500         | `damaged_token`                                      | Noisy    | `noisy_threshold`  | the pre-filter turns a `1` between letters into `l` (`kera1ika`), nothing else; gate 10: the digits left inside words (`zpr4vy`, `r0ku`) cap the line at `Noisy`                                                       |
| `Opomenulé nebo opozděné ohlášení trestů se pokutou peněžitou` | ces 1 · 153           | —                                                    | Clear    | `clear_threshold`  | gate 10: a high score and every word valid                                                                                                                                                                             |
| `Musée de l'Homme`                                             | fra 0.95 · 300        | `mostly_readable_noisy`                              | Noisy    | `noisy_threshold`  | **Known limit.** gate 10: only 2 of 3 words pass the (Czech) word test, under `MOSTLY_READABLE_VALID_MIN` 0.85                                                                                                         |
| `Sie wurden in den Gräben gefunden`                            | deu 0.95 · 40         | —                                                    | Clear    | `clear_threshold`  | **Known limit.** the same shortfall (0.83) is forgiven for a confident, low-perplexity, upright-Czech line; `den` is a Czech word, so a German sentence containing it qualifies                                        |
| `Die Ausgrabung wurde von dem Museum durchgeführt.`            | deu 0.98 · 60         | —                                                    | Clear    | `clear_threshold`  | expected language (`ces,deu,eng`): kept, trusted at x1.0                                                                                                                                                               |
| `Les fouilles de la villa`                                     | fra 0.3 · 1200        | `hard_sweep`                                         | Trash    | `trash_hard_sweep` | trusted foreign language (`deu,eng,fra,pol,ita,slk`): kept, trusted at x0.85 (`TRUST_TIER_TRUSTED`); gate 1 reads the RAW confidence                                                                                   |
| `Náčrt sondy.`                                                 | slk 0.9858 · 604      | `short_line`                                         | Clear    | `clear_threshold`  | `slk` is in `TRUSTED_FOREIGN_LANGS`, so it is NOT relabelled (`REMAP_KEEP_SCORE_LANGS` never applies)                                                                                                                  |
| `Fundort unbekannt`                                            | nld 0.3 · 1500        | `short_garbage`                                      | Trash    | `trash_threshold`  | unknown Latin-script language: stored as the default `ces_Latn` at 0.75 (`LANG_SCORE_REMAP`), trusted at x0.50; a trust of 0.15 <= 0.20 is strong evidence on its own                                                  |
| `Fundort unbekannt`                                            | deu 0.3 · 1500        | `short_garbage`, `short_line`                        | Clear    | `clear_threshold`  | the same line and confidence, labelled `deu`: no strong evidence, so `Clear`                                                                                                                                           |
| `Москва`                                                       | rus 0.99 · 900        | `short_garbage`, `short_line`                        | Noisy    | `noisy_threshold`  | **Known limit.** unknown non-Latin script: stored at 0.50 (`LANG_SCORE_REMAP_FAR`); Latin-only vowels give a vowel ratio of 0                                                                                          |
| `Zwei Gefäße aus Wien wurden gewaschen`                        | nld 0.99 · 400        | —                                                    | Clear    | `clear_threshold`  | a confident wrong label (`nld`) costs only its trust: x0.50 of 0.99                                                                                                                                                    |
| `The excavation was carried out in 1932.`                      | eng 0.97 · 40         | `mostly_readable_noisy`                              | Noisy    | `noisy_threshold`  | **Known limit.** the word test knows Czech function words only: `in` and `1932` are invalid, 5 of 6 words pass (0.83 < 0.85), so a clean English sentence reads `Noisy`                                                |
| `Wasserwirtschaft`                                             | deu 0.6 · 900         | `short_garbage`                                      | Trash    | `trash_threshold`  | **Known limit.** `w` is rare in Czech: the weirdness measure penalises a `w` token, and a German compound with FastText <= 0.75 is convicted by gate 6                                                                 |
| `wall with two windows`                                        | eng 0.7 · 400         | `wqx_rot`                                            | Trash    | `trash_threshold`  | **Known limit.** `wall` and `windows` count as w-heavy 'gibberish' (strong evidence), FastText is under 0.75: late gate                                                                                                |
| `wall with two windows`                                        | eng 0.9 · 400         | —                                                    | Clear    | `clear_threshold`  | **Known limit.** the same line with FastText at 0.90: the late gate needs a weak language guess                                                                                                                        |
| `Sie wurden in der Grube gefunden`                             | deu 0.95 · 40         | `mostly_readable_noisy`                              | Noisy    | `noisy_threshold`  | **Known limit.** 70 % of its letters are rotatable and one word in six holds a `w`: the rotation penalty (+0.45 per word) applies unless the line is upright Czech; swapping `der` for the Czech word `den` removes it |
<!-- worked-examples:end -->

---

## Configuration invariants

**Three relations must hold:** `CATEG_TRASH_SCORE_MAX < CATEG_NOISY_SCORE_MAX`, `SHORT_PPL_CAP <
PERPLEXITY_THRESHOLD_MAX`, and the nine `QS_WEIGHT_*` constants sum to a positive number. One function owns them,
`text_util.constant_invariant_errors()`, and three entry points apply it: **the service refuses to start** (a
`RuntimeError` naming the broken relation), **`classify_TEXT.py` exits before any model is loaded**, and
`tools/recategorize_from_csv.py` rejects the candidate configuration. It is deliberately **not** run at import:
`tests/test_config_constants.py` sets `SHORT_PPL_CAP` above the threshold on purpose to test the reader, and
parameter sweeps vary constants through `ATRIUM_TEXT_UTILS_*`, so a research process must be able to import the
module. Note that `CATEG_NOISY_SCORE_MAX` is only the start of the stored `Clear` band
([Score clamping](#score-clamping-after-category-assignment)).

**Where the original request (ocr-postprocess#1) landed.** Digits glued to letters and symbols inside words are
separate measures: `detect_letter_digit_letter` counts the letter–digit–letter sandwiches (`ldl_fuses` in the CSV
and the API), and `detect_strange_symbols` counts the symbols (`sym_count`, which the service reports and does not
score); the garbage density, which is scored, counts the non-alphanumeric characters over the whole line. A
poorly scored language is not ignored but discounted: the guards read the trust score, not the stored `lang_score`
([Which language number is read where](#which-language-number-is-read-where)).
