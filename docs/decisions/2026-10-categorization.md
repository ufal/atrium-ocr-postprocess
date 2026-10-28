# Categorisation decisions, 2026

Dated decisions behind the line categoriser of ocr-postprocess, kept with the measurements that settled
them. The rules themselves are in [the reference](../reference/categorization_rules.md); the measurements
are summarised on [the evaluation page](../evaluation/categorization.md). Nothing here describes current
behaviour that those pages do not already state. Moved here from `docs/categorization_logic.md` on
2026-10-20 (atrium-project#79), unchanged except for links and headings.

## Frozen for the S2 pilot

**Frozen for the pilot.** The category semantics are frozen for the S2 pilot (the decision of 2026-09-30 on
ocr-postprocess#2). A change after it is measured on a corpus diff with `tools/corpus_change.py`. One threshold
moved inside the pilot: `LOWPPL_CLEAR_MAX` 50 → 35 (ocr-postprocess#3, stage 16, 2026-10-11), which broke no line on
any labelled set. Measured in memory over both collections (`tools/corpus_change.py reach`, stage 17, 2026-10-12),
it moves 62,859 lines and the page band of 1,128 of 1,226,349 pages, and of the labelled lines it moves 63 become
right and none wrong; the pilot's parameter work ended there, and the collections are re-scored with it as
`DOC_LINE_CATEG_1210`. Validating the categories on a gold set and fine-tuning a model on it
(ocr-postprocess#4) are deferred by the AMČR baseline (atrium-project#67). The known limits listed in
[Foreign-language lines](../reference/categorization_rules.md#foreign-language-lines) and [Overrides and exception cases](../reference/categorization_rules.md#overrides-and-exception-cases)
are measurements to make after the pilot, not defects to patch inside it.

---

## Removed and renamed mechanisms

Notes that stood in the rules until 2026-10-20, about mechanisms earlier revisions had:

> [!NOTE]
> There is no `symbol_ratio` term and no `rot_penalty` subtraction in the current implementation — both
> appeared in earlier revisions. Symbol density is no longer fed into the quality score (the `symbol`
> per-line column has also been removed); rotation/inversion is now handled entirely by the
> per-line lexicon override and the page-level sweep described in the reference. `compute_quality_score()`
> still accepts an `is_upright_czech` parameter for signature compatibility, but it has no effect on the computed value.

> [!NOTE]
> **(B2) `QS_GARBAGE_NORM_MAX` vs. `CATEG_GARBAGE_DENSITY_HIGH`.** The garbage-density term inside the quality-score
> formula is now normalised against its own constant, `QS_GARBAGE_NORM_MAX` (default **0.35**), separate from
> `CATEG_GARBAGE_DENSITY_HIGH` (also default **0.35**), which gates the hard Trash override in
> [Categorisation logic](../reference/categorization_rules.md#categorisation-logic). The two constants were previously the same value reused in both
> places, which made their individual contribution to the importance sweep inseparable. At default configuration
> both equal 0.35, so behaviour is bit-identical to before; they can now be tuned independently.

> [!NOTE]
> An earlier revision subtracted cumulative penalties from the score inside `categorize_line()`. That is
> gone: the quality score is never changed by the categoriser, only read, and then clamped.

> [!NOTE]
> The near-boundary "clean prose promotion" (Override 4) of earlier revisions is **removed**: its
> `CLEAN_PROSE_*` constants no longer exist and no path reads them. The closest surviving mechanisms are
> gate 8 and `lm_confident_czech`.

> [!CAUTION]
> `apply_short_cap=False` is load-bearing, not a detail. `score_line()` applies `SHORT_PPL_CAP` to
> `wc <= 2`, which is the blend's *only* target population, so re-scoring with the default pinned every
> blended value straight back to 850 before any rule could read it. The pass computed a number, wrote it
> to `perplex`, and then produced a category for a different number — leaving each affected row
> internally inconsistent and the whole feature unable to move a category at any magnitude. Every blend
> test passed throughout, because they assert on the blended *number*; the only `categ` assertion was an
> idempotence check, which passes trivially when nothing moves. `TestBlendReachesTheRules` now asserts on
> a category actually changing.

## The definition note (2026-10-01 revision)

> [!IMPORTANT]
> **This page describes what the program does, not what the five labels mean to a reader — and
> the two are not currently the same thing.** The definition an annotator works to was settled by
> @david-spacil on 2026-09-22 (issue #30 § 6) and agreed by @DanaKriv on 2026-10-01 (Q1): **`Trash` means illegible.**
> Anything legible is `Clear`; anything easily decipherable through a mistake is `Noisy` —
> **regardless of how useful the line is**. `Non-text` cannot be told apart from `Trash` without
> the page image and is out of scope for hand annotation.
>
> Measured against that, the routes below send readable text to `Trash` in at least two places:
> a correctly scanned web address (`rule_domain_notation`, § 5, lifted as far as `Noisy` by D33)
> and the short-garbage route, whose only verdict is `Trash` even for damage a person can
> decipher.
>
> The first now has a route that can finish the job: **`rule_domain_notation_categ`** (gate 0b in
> the table below, issue #30 D43). It is driven by **`DOMAIN_NOTATION_CATEG`**, which holds a
> **category name**, not an on/off switch. It shipped empty (off) until 2026-10-01 and **ships
> `Clear` since** — @david-spacil and @DanaKriv's answer to Q4. *Every* line `_RE_NOTATION_URL` recognises as a web or
> e-mail address gets that category, and the route runs **first** in the cascade, above even
> `rule_hard_sweep`: every signal below it measures how word-like a string is, and an address is
> not trying to be a word (`weird_ratio` is 1.00 on a URL, `detect_fused_words` fires on the
> address shape, an `@` halves `valid_word_ratio`). Its measured limit: nothing in it separates a
> correctly scanned address from a mis-scanned one, so the configured category applies to both —
> and `e-mail: officeauappmost.cz`, whose `@` the scanner lost, already reads `Clear` without the
> route. An unrecognised value fails at import rather than silently doing nothing.
>
> *(Corrected 2026-09-22. An earlier revision of this note described a `rule_domain_notation_clear`
> in gate 7 behind `SHORT_LINE_URL_CLEAR_ENABLE`. Neither exists in the code; that was an earlier
> design, replaced by the category-name route above before it landed on `master`.)*
>
> The second is not changed and is not described here as a defect: the short-garbage route's
> target category is a decision with an owner. **Answered 2026-10-01 (Q6): `Trash` stays.**
> The owners asked for the annotation to decide and for `Trash` if it could not. @DanaKriv's 357
> decisions put about a third of what the shape witness convicts at `Noisy`, but no signal the
> route can read separates those lines: a clean companion word on the line moves the census head
> the right way (358 → 275 errors) and the sample tail the wrong way (1,180 → 1,636), and the
> recoverability bands are 60/40 at best. So the fallback applies.
> See [`issue30/issue30_review_request.md`](../issue30/issue30_review_request.md) § 6 and
> [`../README.md`](../../README.md)'s five-category table.

## Gate 0b, the address route

**Issue #30 D43.** Runs **first**, above `rule_hard_sweep`, because every signal below it measures how word-like a string is and an address is not trying to be a word. A category *name* rather than a switch, because this line has had three answers in the issue (`Trash`, then `Noisy` via D33, then `Clear` under @david-spacil's legibility definition, confirmed with @DanaKriv as Q4 on 2026-10-01). Reported under the matching threshold reason (`clear_threshold`, `noisy_threshold`, `trash_threshold`) or the pre-filter's (`non_text`, `empty`). Listed in `CONFIG_GATED_RULES`, so the rule-coverage sweep reports it `INERT`, not `DEAD`, whenever it is emptied. Known limit: a mis-scanned address gets the same category as a correct one.

## Gate 6, `rule_short_garbage`

Structural short-garbage route (e.g. `olie`). Recorded as `trash_threshold`. It does **not** consult `check_rescues()`. Since `070620f` (issue #30) it no longer returns unconditionally: it convicts only when `quality_score < CATEG_TRASH_SCORE_MAX + 0.35` **and** a second witness holds. On the short diacritic-free population `_has_strong_garbage_evidence()` returns `False` for *everything* — garbage and real domain vocabulary alike — so requiring it here was a suspension of the rule rather than a narrowing, at a measured cost of roughly **26,000 garbage lines reaching `Clear`**. `_has_shape_garbage_evidence()` (`SHORT_GARBAGE_WITNESS_ENABLE`, **off by default**) is the second witness that does discriminate; it is now wired here as an additional disjunct and is documented under §5c below. This is the **only** site in the short-line path that returns `Trash` — section 7's `damage` branch returns `Noisy` — which is why the witness belongs here and nowhere else.

## Gate 6, `rule_domain_notation`

Consulted at **two** narrow sites — the outer guard of `rule_short_garbage`, and the two section-1 routes that convict on perplexity alone (`rule_extreme_ppl`, `rule_absolute_ppl`). It confers no other exemption, unlike `is_structured_line()`, which is read at eleven places and vetoes `_has_strong_garbage_evidence()`. Recognises **notation, not vocabulary**: `malakofauna` and `Equus caballus` need a lexicon and are deliberately left to gate 5. Dimensions (`12,5 cm`) already satisfy `is_structured_line()` and are not duplicated here. **Not exempt from `rule_hard_sweep`** — see the note below.

## Gate 6, the shape witness (`_has_shape_garbage_evidence()`)

Condition and rationale, as the issue #30 round left them:

**Off by default** (`SHORT_GARBAGE_WITNESS_ENABLE=false`). A phonotactic **shape** test over sub-tokens of ≥ `SHORT_GARBAGE_WITNESS_MIN_ALPHA` (def: 4) alphabetic characters: a **vowel run** (3 in a row by default, `SHORT_GARBAGE_WITNESS_VOWEL_RUN_MIN`; **4 when the raw FastText label is German or French**, `SHORT_GARBAGE_WITNESS_VOWEL_RUN_EXEMPT_LANGS` = `deu,fra` / `..._MIN_EXEMPT` = 4, issue #30 D44), the same character three times in a token of ≤ 8 letters, a doubled consonant in first position, or fewer than `SHORT_GARBAGE_WITNESS_VARIETY_MAX` (def: 0.50) distinct letters at ≥ 7 letters. Vetoed for the whole line by `has_cz_diacs()`, `is_structured_line()`, `is_domain_notation()` and `_RE_FUSED_GRID_REF` (a grid reference with its sub-letter fused, `S-VIIIb` — the hyphenless twin of a shape `is_domain_notation()` accepts); sub-tokens are split on `. - –` **and on joining punctuation** (`, / ; : ( ) " = _ ? ! * #`, `_witness_subtokens()`, issue #30 D46 — a comma-list like `erhalten,Siedelungsfund,gefunden` used to be read as one 30-letter token, and `low_variety` fires on any string that long); per sub-token it skips the `[neutral]` / `[short_exception]` / `[short_valid]` word lists, uppercase roman numerals (≤ 7 glyphs), Latin taxonomic suffixes (`-aceae`, `-idae`, …) and, once `SHORT_GARBAGE_LEXICON_PATH` points at a built table, any token attested in ≥ `SHORT_GARBAGE_LEXICON_MIN_DF` documents. It skips any token listed in `setup/word_lists.txt` `[allowed]` (#30 Q5b).

Reaches `oueussd`, `sektlll`, `cuxoaid`, `Tthts I`, `rragment`, `NINNNIC`, `vansasaasasa` — the garbage `_has_strong_garbage_evidence()` misses — while leaving `malakofauna`, `diapozitiv`, `Equus caballus`, `Skelettmaterial` and `vrstva` alone. It deliberately does **not** reuse `detect_fused_words()`'s other two clauses: `len(core) > 14` flags `Skelettmaterial`, and the 5+ consonant run flags `vrstva`, `ctvrtek`, `zmrzl` — ordinary diacritic-free Czech. The language label is the RAW `original_lang`, never the stored `lang`, which `remap_lang()` rewrites to Czech; a caller that passes no label gets the strict threshold. **Known limits:** the phonotactically legal residue (`edelite`, `vfetennl k.`) is out of reach by construction and needs a lexicon (#30 D14), with an optional `no_vocabulary` clause behind `SHORT_GARBAGE_LEXICON_CONVICT` (false); and the language label on a short damaged line is often wrong (#30 Z5). **Measured against the 2,064 scored rows of the gold sidecar** (`tools/gold/sidecars/issue30_gold_2067.csv`): with the document cascade it passes the adoption gate — errors 513 → 503, `Clear`-loss 38 → 38, 12 fixes / 2 breaks, p = 0.013, and 503 → 502 / 38 → 37 with the language split; per line (`--no-postprocessing`) it fails it, `Clear`-loss 53 → 54. @david-spacil's independent re-check of his 508 lines on the **default config (no lexicon)**, 2026-09-23: agreement 326 → 335/508 on `4ed309a`, 16 lines moved (11 fixed, 2 broken, 3 wrong either way). One of the two breaks was `S-VIIIb`, whose `_RE_FUSED_GRID_REF` guard had been deleted by an unrelated commit (`cc4990e`, 2026-09-18) and is restored and pinned by tests; the other, `Lokolieace: •VIII,` (gold `Noisy`), is damage a reader can decipher, which this route can only answer with `Trash`. **Confirmed by him on `3b02959`, with the guard restored: 336/508**, 15 lines moved (11 fixed, 1 broken, 3 wrong either way), `Lokolieace: •VIII,` the only break, and 0 of 508 changed with the flag off. **@DanaKriv's 357 decisions (2026-10-01, returned in place in `docs/issue30/census.csv` + `sample.csv`), text-only, no lexicon, after D46:** projected tail errors 4,844 → 1,180 of 5,646 lines, census head 339 → 358 of 558 (decipherable damage, which this route answers `Trash` — Q6), `Clear`-loss 0 in both. Before D46 her only two at-risk `Clear` labels (`XXX,1937,str. 21`, `okraj sekt.III,konec`) were convicted. **Stage 12, the same labels on the real lines, lexicon armed (2026-10-01):** on Dana's 826 lines errors 648 → 642, `Clear`-loss 1 = 1, p = 0.24 — the projection above is the witness's verdict on the text, and this gate consults it on about 3% of those lines; on the 2,064 gold errors 510 → 499, `Clear`-loss 38 = 38, p = 0.0034. The flip rule is met; the flag is held to be decided together with the proposed witness floor (#30 D47, stage 13).

## The witness floor

**Witness floor** (`rule_short_garbage_witness_floor`, `SHORT_GARBAGE_WITNESS_FLOOR`, **off by default**) — applied
*after* the cascade in `categorize_line()`: when the cascade would answer `Clear` for a short line
(`word_count ≤ ISOLATED_CHAR_MIN_TOKENS`) that `shape_garbage_clauses()` convicts, the answer becomes `Noisy` /
`noisy_threshold`. It never touches a `Trash`, `Noisy`, `Non-text` or `Empty` verdict, it does not need the signal
half of gate 6, and it is independent of `SHORT_GARBAGE_WITNESS_ENABLE`. The page-level smoothing in
`apply_document_postprocessing()` acts after it, so with the floor on a line it created can still be moved to `Trash`
by the surrounded-`Trash` pass or page rule 1. Per line it only ever answers `Noisy`, but by lowering a page's `Clear`
share and median score it can fire page rule 1, which sends every `Noisy` line on that page to `Trash` — so it can
move `Clear`-loss in the pipeline even though no A/B on the sidecars showed it. The A/B tool also reports
`Clear-demoted` (gold `Clear` → any other class) for rules like this one. **Stays off** (@david-spacil, 2026-10-05):
over both collections with the shipped config (no token table) it moved 25,448 lines, and a blind annotation of the
change found ≈ 3,920 fixed against ≈ 13,509 broken, including ≈ 1,250 lines annotated `Clear` (e.g. the `IV-2`
table cells) taken to `Trash` by page rule 1. Measure it on a corpus diff (`tools/corpus_change.py`), not on the
sidecars.

## Why notation is *not* exempt from `rule_hard_sweep`

Reported on issue #30: with `SHORT_PPL_CAP` lifted, `II/C`, `1 ks` and `Reg.Bez.Aussig.` still route to
`trash_hard_sweep`, so exempting notation from the perplexity routes changes nothing for an uncapping
experiment. That is correct, and it is **deliberate**.

The three section-1 routes are not equivalent. `rule_extreme_ppl` and `rule_absolute_ppl` convict on
perplexity **alone**, and perplexity is not merely weak on this population — it is *inverted*: real
notation demotes around 3 000 while `oueussd` survives to 30 000. Notation is exempt from both.
`rule_hard_sweep` additionally requires `orig_lang_score < HARD_SWEEP_LANG_MAX` (0.45) — an independent
second witness that **FastText also failed to place the line**. Notation is not exempt from it.

The reason is measured, not stylistic. `_RE_NOTATION_ABBR` requires a capital initial per segment, which
closed the *lowercase* dot-chain hole completely (< 5 % accepted). It does not close the capitalised
one: OCR garbage is frequently capitalised, and `Vvbn.Slaot.Vansas.` is structurally indistinguishable
from `Reg.Bez.Aussig.` — **over half of generated capitalised dot-chained garbage is accepted by the
predicate.** Two narrower rules were measured and rejected:

| candidate rule                 | garbage still accepted | real chains lost    |
|--------------------------------|------------------------|---------------------|
| a vowel in every segment       | ~40 %                  | `Kr.Hr.`, `St.Pol.` |
| majority of segments ≤ 4 chars | ~64 %                  | none                |

Neither earns its complexity, and both confirm that shape alone cannot separate these — the same wall
the *vocabulary* half of issue #30 runs into. So the predicate is not strong enough to carry a
hard-sweep exemption on its own, and a notation line whose language FastText also cannot place is
convicted. That is the safer error of the two available.

`TestNotationIsNotExemptFromHardSweep` pins the hole as **still present**; if a future change closes it,
that test goes red and the exemption becomes worth re-measuring.
