# Issue #30 — findings from the gold-scored parameter runs

*Analysis date 2026-09-18. Sources: the stage 1–5 logs of the issue-30 runbook job, the
two non-log artefacts (`token_df.tsv`, `04_witness_candidates.csv`), and
`ufal/atrium-alto-postprocess` at `test` HEAD `b8a38f6`. Every figure below that is not
quoted from a log was recomputed from those artefacts against the committed predicate.*

---

## 2026-09-23 — D45 and the 508 re-check

**Two guards this document added were deleted the evening they were measured, and every
witness run from 5f on scored the witness without them.** §4 and §7 below describe
`_RE_FUSED_GRID_REF` (`S-VIIIb`) and `has_expected_lang_diacs()` (the German-diacritic veto,
`Frauenzimmerbad`) as being in `text_util.py`. They landed in `9bc218b` (2026-09-18, 08:03) and
round-2 stage 5a measured them: 14 fixed / 1 broken, p = 0.00098. The same evening `cc4990e`
(17:14), a lexicon commit, deleted both from a stale working copy together with their veto line,
and `031fa58` (17:55) put back the pre-round-2 `setup/config.txt` witness block. No test pinned
either guard, so the suite stayed green and nothing recorded the loss. Two consequences:

* **Every witness run from 5f onward measured the guard-less tree** — 5f, 07d, 08b/08c, 10d–10f,
  11b. Those figures stand as measurements of that tree. The stage-8-and-later runs never saw
  `S-VIIIb` break only because they ran with a lexicon, under which `viiib` is attested; 5f,
  with none, did — it is one of 05f's two extra breaks below.
* **The 05a-vs-05f comparison crossed the deletion.** 05a had the guards and broke 1 line; 05f did
  not and broke 3, and its two extra breaks are exactly the two lines the guards vetoed. A4's
  reading of that pair is confounded (marked there). That the cascade is a precondition still
  stands, on same-tree evidence: 08c and 10f.

**@david-spacil found it, and his re-check confirms the repair.** His 508 lines, whole documents
re-scored with `tools/recategorize_from_csv.py`, witness on, no lexicon (the shipped default):

| revision                                         | agreement with gold |
|--------------------------------------------------|--------------------:|
| `4ed309a`, witness off                           |             326/508 |
| `09c9640` (`v1.5.1-beta`), witness on            |             334/508 |
| `4ed309a`, witness on                            |             335/508 |
| `4ed309a`, witness on, `VOWEL_RUN_EXEMPT_LANGS=` |             334/508 |
| **`3b02959`, witness on — grid guard restored**  |         **336/508** |

On `3b02959` the witness moves 15 lines: **11 fixed, 1 broken, 3 wrong either way**. `S-VIIIb` is
`Clear` again, and `Lokolieace: •VIII,` (gold `Noisy`) is the only break — the
decipherable-but-damaged case Q6 asks about. With the flag off, 0 of the 508 change against
`4ed309a`, so the restored guard is inert until the flag flips.

**What changed in the tree.**

* **The grid guard is restored** (`3b02959`), witness-local as before, and **pinned three ways**:
  the seven-string series in `tests/test_shape_witness_vocabulary.py`, a narrowness test
  (`Aa/III 116`, `OUUITN` and `Lokolieace: •VIII,` stay outside), and an `S-VIIIb` row in
  `tests/test_short_garbage_witness_wiring.py`'s end-to-end keep list. The tests fail without it.
* **The German-diacritic veto is not restored, and stays open** as a maintainer item. D44's
  language split now covers `Frauenzimmerbad`, but on a `deu` label at 0.359 where the veto
  rested on the letters themselves; restoring it would change the witness's reach on the ~239
  corpus lines §4 counts (156 kept today, such as `Grauer, geschlämmter Ton.`). It needs its own
  gold A/B and exposure pass first.
* **The config block and the advisory are re-stated from current evidence.** §3's argument and
  the block `031fa58` restored both leaned on readings of `ppole` that W2 overturned. The rule
  stays — never arm the witness without a table — and its reason is now corpus exposure: 37,555
  kept lines at risk with no table against 6,714 with the 113,100-document one (08f → 9b; 9b
  also carries D33). Gold cannot show that difference: it labels 23 of the ~20k witnessed lines,
  and there shape-only and armed score alike (round-2 5a 500 errors, 5a-bis 502).

---

## Stage 10f + stage 11 — the cascade is a precondition, and the split passes (2026-09-22)

**10f — the same flag A/B with `apply_document_postprocessing()` disabled.**

|               | errors |   cost | `Clear`-loss | `Trash`-recall |
|---------------|-------:|-------:|-------------:|----------------|
| per-line, off |    502 | 0.3055 |           53 | 42/180 = 23.3% |
| per-line, on  |    492 | 0.2977 |       **54** | 54/180 = 30.0% |

Same 12 fixes / 2 breaks, same p = 0.01294, and the verdict is
`REJECT - macro_f1 rises but Clear-loss worsen`. Read against 10e (38 → 38 with
the cascade), this reproduces 08c and confirms it twice: **the document-level
dedup is a precondition for the witness, not a refinement of it.**

**Stage 11 — three joins over the gold corpus, no re-score.**

* **11a / W6 — zero.** No gold row contains `ssuti`, `ssutí` or `ssutě`. The
  `Clear`-loss figure needs no correction.
* **11c / X1 — confirmed.** Exactly two gold-`Clear` rows are stored `Trash` and
  match `_RE_NOTATION_URL`, both `http://www.arub.cz`. D33 moves those two
  `Trash` → `Noisy`, which is the 40 → 38 shift exactly, with `errors` unchanged
  because both stay wrong. A third URL row was already `Noisy` and never
  contributed. **The 42 / 41 / 40 / 38 drift is accounted for.**
  *(The stage printed "3, not 2 — the direction is right and the size is not": its
  pass condition counted every URL-shaped row rather than those stored `Trash`.
  The artefact was right and the verdict written into it was wrong.)*
* **11b / D44 — the split measures 12 fixes / 1 break.** errors 503 → **502**,
  `Clear`-loss 38 → **37**. Both down, so the adoption gate passes unchanged.

| watched row                                         | detected   | conf. | exempt? | outcome       |
|-----------------------------------------------------|------------|------:|---------|---------------|
| `Frauenzimmerbad", sämtlic …` (break, gold `Clear`) | `deu_Latn` | 0.359 | yes     | break removed |
| `deutendes. Alhimiaal` (fix, gold `Trash`)          | `afr_Latn` | 0.738 | no      | fix kept      |
| `Lokolieace: •VIII,` (break, gold `Noisy`)          | `fin_Latn` | 0.883 | no      | break stays   |
| `MI*I\ EOOCO` (fix, gold `Trash`)                   | `eng_Latn` | 0.343 | no      | fix kept      |

**Two cautions that outlast the result.**

1. **It passes partly by accident.** `deutendes. Alhimiaal` reads German and was
   detected Afrikaans; that is the only reason its conviction survives.
   `Frauenzimmerbad` is exempted on a `deu` label at 0.359, and
   `http://www.arub.cz` is detected `yue_Hant`. The detector's label on a short,
   damaged, diacritic-free line is largely noise — the population the witness
   exists for. **Open:** a confidence floor on `_vowel_run_min_for()`, which would
   put `Frauenzimmerbad` back at risk. Its own measurement, not a default.
2. **44.5% is the wrong number; 5.8% is the right one.** Raw non-Czech is 791 of
   1,777 scored gold rows, but the tail is `vie` 5.0%, `est` 1.9%, `slv` 1.1%,
   `fin` 1.0%, `nld`/`xho`/`uzn` 0.8% each — the detector failing, which is what
   `remap_lang()` absorbs (the stored column reads `ces` 77.6% against a raw
   55.5%). The split acts on `deu` + `fra` only: **5.8%**.

---

## Stage 10 — the threshold closed, the gate confirmed (2026-09-22)

Two gold-scored A/Bs over the same 2,064-row sidecar, both on the post-D33 tree.

**10d — `SHORT_GARBAGE_WITNESS_VOWEL_RUN_MIN` 3 vs 4.** A null that costs recall.

| min | macro_f1 | errors |   cost | `Clear`-loss | `Trash`-recall |
|----:|---------:|-------:|-------:|-------------:|----------------|
|   3 |   0.6345 |    503 | 0.2810 |           38 | 34/180 = 18.9% |
|   4 |   0.6325 |    503 | 0.2810 |           38 | 32/180 = 17.8% |

Fixes 2 / breaks 2, exact McNemar p = 1. The gate is indifferent and both
secondary measures move the wrong way. **The global threshold question is closed**
in favour of the language split (D44), which is @david-spacil's other suggestion.

**10e — `SHORT_GARBAGE_WITNESS_ENABLE` false vs true.** The gate passes.

| flag  | macro_f1 |  errors |       cost | `Clear`-loss | `Trash`-recall     |
|-------|---------:|--------:|-----------:|-------------:|--------------------|
| false |   0.6174 |     513 |     0.2897 |           38 | 22/180 = 12.2%     |
| true  |   0.6345 | **503** | **0.2810** |           38 | **34/180 = 18.9%** |

Fixes 12 / breaks 2, exact McNemar p = 0.01294, significant. Errors −10, cost
−0.0087, `Clear`-loss unchanged. This is what 08b reported before D33 and what
stage 9a was owed to confirm.

**Two things to carry forward.**

1. **`Clear`-loss is 38, not 40, and it is 38 in both arms** — where 08b reported
   40 in both of its own. The shift is flag-independent, so it belongs to the tree
   rather than to the witness, which is the shape D33 predicts.
2. **The witness's only two errors are 10d's two fixes.** Both are `vowel_run`-only
   — `Frauenzimmerbad", sämtlic Gesellschastsbäder,` (gold `Clear`) and
   `Lokolieace: •VIII,` (gold `Noisy`). One run's fix is the other's break, and
   everything else the witness does on gold is uncontested.

**What the split would do, computed on those 14 rows.** Both breaks are
`vowel_run`-only, and so are exactly two of the twelve fixes (`deutendes.
Alhimiaal`, `MI*I\ EOOCO`); the other ten fire `triple`, `initial_geminate` or
`low_variety` and are untouched at any threshold. The likely case gives **11 fixes
and 1 break** — errors 503 → 504, `Clear`-loss 38 → 37. It trades a gold-`Clear`
error for a gold-`Trash` one, improving the composition and not the count, **and
the adoption gate as written rejects any +1 on errors**. The gate is what needs
the argument, not the split. Contingent on four language labels nobody has read.

> 🛑 **Overturned 2026-09-22 by 11b** (top of this document): the split measured **12 fixes /
> 1 break**, errors 503 → 502 and `Clear`-loss 38 → 37 — both down, so the gate passes as written.
> The four labels were not what this paragraph assumed: `deutendes. Alhimiaal` was detected
> Afrikaans, which is the only reason its fix survived (the Z5 caution).

---

## 0. Provenance, before anything else

Two checks first, because several conclusions below contradict things currently written
in the repository and in the issue thread.

**The candidate file was produced by the committed code.** All 20,324 rows of
`04_witness_candidates.csv` reproduce exactly when `text_util.shape_garbage_clauses()` at
`b8a38f6` is re-run over their text — 20,324/20,324, zero clause-string differences. The
run measured what the tree contains.

**The A/B logs did not.** They were produced by an older `tools/ab_constant_eval.py`.
Since then that tool has gained an `errors` column, `mcnemar_exact()`, and an adoption
gate built on errors/cost/Clear-loss rather than `macro_f1`. Any log whose table has no
`errors` column predates that upgrade, and every stage-5 log in hand is one. **Re-running
stage 5 on a current checkout is not a formality — the verdict lines change.**

---

## 1. The gold set is smaller than it looks, in the way that matters

`01_preflight.log` is good news and was read as better news than it is: 2,064 of 2,067
sidecar keys join, no document absent, three locator drifts. All three drifted keys are
annotated `Clear`, so the joined class supports are exact:

| gold label |    rows |    share |
|------------|--------:|---------:|
| Clear      |   1,305 |    63.1% |
| Noisy      |     332 |    16.1% |
| Empty      |     205 |     9.9% |
| **Trash**  | **180** | **8.7%** |
| Non-text   |      45 |     2.2% |

That 180 is the denominator of every `Trash-recall` figure in the stage-5 logs, and **the
logs never print it.** `0.1222` is 22/180. It is also exactly 11/90, and nothing in the
output distinguishes them. This is not a hypothetical hazard: reconstructing the paired
counts from the recall deltas on the smaller denominator halves `b` and turns an exact
McNemar p of 0.004 into 0.18 — "underpowered, do not quote it" for a result significant at
the 0.01 level. One printed integer prevents it, and §7 adds it.

**The number that actually bounds this decision is smaller still.** Of the 20,324 lines
the witness fires on, **25 carry a gold label — 0.12%.** The witness cannot change the
category of a line it does not fire on, so every macro_f1 delta, every Clear-loss figure
and every adopt/reject verdict in stages 5a–5c is computed off those 25 rows plus whatever
the page cascade moves downstream of them. Document coverage is not the problem (453 of
the 457 witness-touched documents are gold documents); *line* coverage is.

Here is the whole decision surface:

| gold      | current `categ` | effect of arming the witness | text                                            |
|-----------|-----------------|------------------------------|-------------------------------------------------|
| Trash     | Clear           | fixes                        | `utlll`                                         |
| Trash     | Clear           | fixes                        | `deutendes. Alhimiaal`                          |
| Trash     | Clear           | fixes                        | `mpsssssdym"" ! IU`                             |
| Trash     | Noisy           | fixes                        | `NWIIIIIIIIIIII`                                |
| Trash     | Clear           | fixes                        | `ymeememe, mieme?`                              |
| Trash     | Clear           | fixes                        | `sektlll`                                       |
| Trash     | Noisy           | fixes                        | `SEEEEEEEEEEEE:`                                |
| Trash     | Clear           | fixes                        | `vdoaotacth.`                                   |
| Trash     | Noisy           | fixes                        | `MI*I\ EOOCO`                                   |
| Trash     | Clear           | fixes                        | `IAALAILIL`                                     |
| Trash     | Clear           | fixes                        | `LLRIOAILCIIL\`                                 |
| Trash     | Noisy           | fixes                        | `HHCCJCJ`                                       |
| Trash     | Clear           | fixes                        | `tirrttitt`                                     |
| Trash     | Clear           | fixes                        | `NINNNIC`                                       |
| Trash     | Clear           | fixes                        | `cuxoaid ,`                                     |
| Trash     | Clear           | fixes                        | `cuxoaid ,` *(second locator)*                  |
| **Clear** | **Clear**       | **breaks**                   | `Frauenzimmerbad", sämtlic Gesellschastsbäder,` |
| **Clear** | **Clear**       | **breaks**                   | `S-VIIIb`                                       |
| Noisy     | Noisy           | breaks                       | `Poue`                                          |
| Noisy     | Noisy           | breaks                       | `Lokolieace: •VIII,`                            |
| Noisy     | Clear           | wrong before, wrong after    | `Iokalisqce: HVIII,`                            |
| Non-text  | Noisy           | wrong before, wrong after    | `IDIDIDIDIDIDUOID`                              |
| Noisy     | Trash           | already Trash                | `kmixxxx`                                       |
| Clear     | Trash           | already Trash                | `http://www.arub.cz` ×2                         |

16 fixes, 4 breaks. The reported deltas — Trash-recall 22→36 (+14), Clear-loss 40→42 (+2)
— are the same rows after the full stack, including the signal half of gate 5 and document
post-processing, has had its say.

---

## 2. The witness works, and it is significant

| arm                                   |   macro_f1 |       Trash-recall | Clear-loss | verdict                   |
|---------------------------------------|-----------:|-------------------:|-----------:|---------------------------|
| shipped (witness off)                 |     0.6173 |     22/180 = 12.2% |         40 | incumbent                 |
| **5a — witness on, no lexicon**       | **0.6366** | **36/180 = 20.0%** |         42 | best arm                  |
| 5a-bis — witness on + vocabulary veto |     0.6338 |     34/180 = 18.9% |         42 | worse on every column     |
| 5b — vowel-run 4 instead of 3         |     0.6319 |     32/180 = 17.8% |         42 | strictly worse            |
| 5c — + unattested-token conviction    |     0.6570 |     78/180 = 43.3% |     **63** | best macro_f1, worst cost |

Exact two-sided McNemar for 5a against the shipped labels:

* from the reported deltas (b = 14, c = 2): **p = 0.0042**
* from the enumerated surface above (b = 16, c = 4): **p = 0.0118**

Either way this is a real effect, not a coin-flip. Wilson 95% intervals on Trash-recall:
22/180 = 12.2% [8.2, 17.8] against 36/180 = 20.0% [14.8, 26.4] — the intervals do overlap,
which is exactly why the *paired* test is the right one and the two marginal rates are not.

**So the correct reading of stage 5a is not "promising but underpowered". It is "a
statistically significant improvement that fails the adoption gate on two identified
lines".** Those two lines are §4.

---

## 3. The vocabulary veto is not a precondition — and `ppole` is why the old figure said it was

> 🛑 **Superseded 2026-09-23 — `ppole` is real, so at corpus scale this conclusion inverts.** It
> is the abbreviation of *popelnicová pole* (@david-spacil, 2026-09-19; W2), not an OCR doubling
> of `pole`. The veto's headline benefit was therefore protecting **vocabulary**, and at corpus
> scale the table is what keeps the witness off the archive's own words: 37,555 kept lines at risk
> with no table against 6,714 with the 113,100-document one (08f → 9b), a difference led by
> `ppole`, `ARCHAIA` and the Latin binomials. What stands is the gold reading — on the 23 gold rows
> the witness reaches, shape-only and armed score alike (round-2 5a 500 errors, 5a-bis 502) —
> which is exactly why gold cannot settle the coupling. `setup/config.txt` keeps the rule (never
> arm the witness without a table) with that reason. Kept below as written, except the `ppole` row
> of the document-frequency table and the guard's shipping status, both corrected in place.

`setup/config.txt` currently states, in capitals, *"AND DO NOT TURN IT ON WITHOUT A
LEXICON. These two keys are one decision."* The 2026-09-17 comment gives the reasoning: the
witness alone confirms 5,107 Trash verdicts and newly convicts 15,217 Clear/Noisy lines
("1:3 against"); with the veto, 3,905 against 2,306 ("1.7:1 in favour"), *"an 85% cut in
false-positive exposure for 24% of the confirmations"*.

Both ratios are scored against `categ` — the answer the pipeline already gave — and both
are one string:

* the `ppole` family is **11,685 of those 15,217** false-positive candidates (**76.8%**);
* of the 12,911 lines the veto rescued, **11,685 (90.5%) are `ppole`**.

`ppole` is attested at document frequency 35 because a pre-printed form repeats the same
misread once per document. It de-geminates to `pole` at df 163 — a ratio of 4.66. It is
damaged text. **The veto's headline benefit was protecting garbage**, and the then-shipped
`SHORT_GARBAGE_LEXICON_GEMINATE_RATIO = 4.0` guard (removed 2026-09-22, D40) is what stopped it
between the 09-17 exposure run and the 09-18 gold run.

Against gold, with that guard in place, the veto's entire measured effect is to give back
**two true Trash catches — both the line `cuxoaid ,`** — because `cuxoaid` reaches df 4.
It returns **zero** Clear lines: Clear-loss is 42 with the veto and 42 without.

Set `ppole` aside and the shape witness needs no lexicon at all to look reasonable:

|                          |  lines | Trash confirms | new Clear/Noisy convictions | ratio                |
|--------------------------|-------:|---------------:|----------------------------:|----------------------|
| all witnessed            | 20,324 |          5,107 |                      15,217 | 1:3 against          |
| minus the `ppole` family |  8,634 |          5,102 |                       3,532 | **1.44:1 in favour** |

The same string distorts the per-clause table. `initial_geminate` reads as a disaster —
94.6% of its fires are currently Clear — and is 31.6% non-Trash once `ppole` is removed,
in line with `vowel_run` (33.6%) and better behaved than `low_variety` (41.5%).

### The self-corpus lexicon has the wrong axis, and more documents will not fix it

The 09-17 next step was *"rebuild the lexicon over both full collections"* to set the
threshold. That will not separate these populations, because the confusion is not sparsity:

| token             | df at 822 docs | what it is                                                                                     |
|-------------------|---------------:|------------------------------------------------------------------------------------------------|
| `ppole`           |             35 | ~~OCR doubling of `pole` — attested garbage~~ 🛑 an abbreviation (W2) — **attested, and real** |
| `cuxoaid`         |              4 | garbage — **attested**, and it costs a gold-Trash catch                                        |
| `okraie`          |              3 | OCR of `okraje` — **attested garbage**                                                         |
| `Naiade`          |              0 | real term — **unattested**                                                                     |
| `Skelettmaterial` |              2 | real term — **unattested**                                                                     |

A *systematic* OCR error recurs across scans exactly as a word does, so document frequency
cannot tell them apart, and scaling from 822 to 113,101 documents raises every artefact's
df at least as fast as it raises a rare real term's. The de-gemination guard is the right
*shape* of answer — it asks whether a **repair** of the token is attested, not whether the
token is — and that generalises where a threshold does not.

Two corrections to that guard's own documentation, from re-measuring it on the same table.
Its load-bearing claim is exactly right: **8 tokens at or above 4× — `ppole`, `oobjekt`,
`jjámy`, `ssuti`, `vvkop`, `ssutí`, `ssutě`, `llocm` — with a genuinely empty gap from
2.60× to 4.66×.** But "36 below 2×" is 51 when both forms must be attested at `MIN_DF`, and
66 when the twin is counted at any frequency; 36 matches neither filter. And on this corpus
the guard's entire reach is one family: of the 11,690 lines where it restores a conviction,
all 11,690 are `ppole` and its 17 surface forms. It currently does exactly one job. Stage
5d (added to the runbook) measures whether that job is worth its place.

> 🛑 **Corrected 2026-09-22.** Four of those eight are real language, not damage.
> @david-spacil: `ppole` is an abbreviation (already known) and **`ssuti` / `ssutí` / `ssutě` are
> an old way of spelling *suť***. Only `llocm`, `vvkop`, `jjámy` and `oobjekt` are scanning errors.
> The ratio gap the guard is built on is therefore not a gap between damage and language at all —
> it separates *doubled-letter shapes* from everything else, and both classes sit above it. He has
> agreed the guard can be dropped, since the dictionary reaches all eight at full scale.
> *(Moved below the paragraph it corrects on 2026-09-23: placed inside it, the quote had swallowed
> the paragraph's second half.)*

---

## 4. The two lines blocking the flag, and what closes them

Clear-loss 40 → 42 is the only thing standing between stage 5a and `ADOPT-CANDIDATE`. Both
lines are identified, and each is a narrow defect rather than a policy question.

**`Frauenzimmerbad", sämtlic Gesellschastsbäder,` — gold `Clear`.** The witness's
line-level diacritic veto is `has_cz_diacs()`, Czech only, while `EXPECTED_LANGS` is
`ces,deu,eng` and `DEU_DIACS` has been in the config since the #7 Tier-1 pass. German is
where the vowel-run clause is least safe — `Frauen`, `Bauern`, `Feuer`, `Neue` are all
three-vowel runs in ordinary words — and this line reaches the clauses with no diacritic
veto at all despite carrying `ä` twice.

*Corpus scale:* 239 witnessed lines (1.18%) carry a German diacritic — 156 currently
Clear/Noisy (`Grauer, geschlämmter Ton.` and its family, readable German pottery
description), 83 currently Trash.

**`S-VIIIb` — gold `Clear`.** `is_domain_notation('S-VIII-b')` returns `True`;
`is_domain_notation('S-VIIIb')` returns `False`. The two differ by one hyphen. The corpus
carries the whole series from one excavation — `AA-VIIIb`, `E-VIIIb`, `F-VIIIb`, `J-VIIIb`,
`K-VIIIc`, `L-VIIIb`, `S-VIIIb` — 7 lines, 6 of them currently Clear.

Both now have **witness-local** guards in `text_util.py` (§7). 🛑 *2026-09-23: both were deleted
by `cc4990e` the evening they landed (D45, top of this document). The grid guard is restored
(`3b02959`); the German-diacritic veto is not.* Witness-local, not a
widening of `is_domain_notation()`, because that predicate is also read by
`rule_short_garbage`'s outer guard and the two perplexity-only routes: changing it there
would move production behaviour while the flag is still false. Inside the predicate the
change cannot do anything until the flag flips — the same discipline the existing
roman-numeral and taxonomy exemptions follow.

Measured on the gold surface, the two guards veto **exactly** the two gold-`Clear` lines
and **none** of the 16 gold-`Trash` catches. Corpus-wide they trade 84 Trash confirmations
for 162 false-positive candidates (20,324 → 20,078 lines; 5,243 → 5,005 distinct strings).

**Predicted result of re-running stage 5a with them in place: macro_f1 ≈ 0.6366,
Trash-recall 36/180, Clear-loss 40.** That beats the shipped labels and does not raise
Clear-loss, which is `ADOPT-CANDIDATE` on the repository's own gate. The prediction comes
from the text-only witness; the full stack has to confirm it, and that is one 2.5-hour A/B.

> 🛑 **Measured in round 2 (2026-09-18):** 500 errors, Trash-recall 36/180, `Clear`-loss **41**
> — 14 fixed / 1 broken, p = 0.00098, the 41st from the modal dedup rather than the witness. That
> is the only run with both guards in: `cc4990e` deleted them the same evening, and every later
> run measured the tree without them (D45, top of this document).

---

## 5. Things that are already fine — checked, closing them

**The roman-numeral exemption works.** The 2026-09-10 fix (`_RE_ROMAN_TOKEN`, applied
above every clause) clears `Sonda VIII/3` and `12.VIII.1977,` as intended. The residue is
**84 lines (0.4%)**: 57 where the numeral exceeds the 7-glyph cap (`CCLXXXVII`), 27 where
OCR has mixed the case (`XXxIX/1469-1477`). Only 15 of the 84 are currently Clear/Noisy.
Not worth a change; the known cost recorded in the code comment is the right call.

**Stage 2 parity passes** — every document delta `+0`. That is the correct result for
"the offline re-scorer reproduces what the pipeline wrote", and it is worth having. It is
*not* the rotation-divergence measurement the runbook claims it is: the 95 ARÚP / 10,258
ARUB figures were offline-vs-live on the **full collections**, and 822 delivered documents
is evidence, not that measurement. That question is still open.

---

## 6. Method: three metrics that disagreed, and which were right

**`macro_f1` is not the gate, and the class supports say why.** It averages F1 over five
classes regardless of size. With Trash at 180 and Clear at 1,305, one gold-Trash line moves
it roughly seven times as much as one gold-Clear line, so it structurally rewards pushing
the boundary toward the rare class. It said the best thing on the board about 5c (+0.0397)
— the arm with the worst Clear-loss. The current `ab_constant_eval` already demotes it to a
diagnostic; the printed supports (§7) are what make the demotion legible.

**`KL` is worse than useless here.** The arm with the best KL by a factor of six — 5c at
0.00669 against a 0.04208 baseline — is the arm everyone agrees is worst. KL compares
*marginal* category distributions, so an arm can reach the right Trash total with entirely
the wrong Trash lines and score beautifully. It should never appear in an adoption
argument.

**`Clear-loss` and `costed_score` were right every time**, including where they contradicted
`macro_f1`. Keep the gate where it is.

**One correction to my own framing of 5c.** It is not statistically null. Turning on
`SHORT_GARBAGE_LEXICON_CONVICT` is +44 Trash catches for +21 Clear losses, exact McNemar
p ≈ 0.006 — a real effect, and the largest single gain anything in #30 has produced. It is
still a reject: a 50% rise in Clear-loss fails the gate outright, and the fixture evidence
agrees (5 of 31 pinned vocabulary fixtures misjudged at `MIN_DF` 3). But the question it
leaves open is a threshold and a table, not the mechanism — it is the only thing in the
module that reaches `edelite`.

**Line counts are the wrong unit, and the issue's own premise turns on it.** 20,324 lines
are 5,243 distinct strings. By line, 78.3% of witness fires are on 2010+ documents — which
is what #30 is titled about. By distinct string, **2,907 are pre-2000 against 1,341 for
2010+**. The 2010+ concentration is repetition in database exports; the *variety* of OCR
damage is in the older scans. A rule tuned on line counts is tuned on the repetition.

---

## 7. Changes made

All four are in the attached files. Nothing is pushed. Repository test suite:
**1,197 passed, 14 skipped, 2 xfailed** — identical to the unmodified baseline; `ruff check`
and `ruff format --check` clean on every file touched.

**`text_util.py`** — the two guards from §4, both witness-local and inert while
`SHORT_GARBAGE_WITNESS_ENABLE` is false.
* `has_expected_lang_diacs()` + `_expected_lang_diacritics()` — vetoes on a diacritic of
  any `EXPECTED_LANGS` language instead of Czech alone, by unioning the `_LANG_DIACRITICS`
  entries the module already carries. Kept separate from `has_cz_diacs()`, which is read at
  fifteen other sites whose meaning is specifically "Czech".
* `_RE_FUSED_GRID_REF` — the unhyphenated twin of a shape `is_domain_notation()` already
  accepts.

**`setup/config.txt`** — comments only; all 144 key/value lines byte-identical. Replaces
the falsified "DO NOT TURN IT ON WITHOUT A LEXICON" block with the gold table and the
`cuxoaid` result; closes the vowel-run question at 3 on the 5b evidence; records 5c's gold
numbers against the convict clause; corrects the de-gemination counts and notes the
one-family reach.

> 🛑 **Both reverted since, 2026-09-18 evening (D45, top of this document).** `cc4990e` deleted
> the two guards and `031fa58` put back the old config block. The grid guard is restored
> (`3b02959`); the German veto is not; and the config block was re-stated from current evidence on
> 2026-09-23 — the rule kept, its reason now corpus exposure rather than this document's gold
> table.

**`tools/ab_constant_eval.py`** — `mcnemar_exact()` and the paired comparison already
landed at HEAD; what was missing is the denominator that §1 turns on.
* `_print_class_supports()` — the gold class supports, once, above the verdicts.
* `wilson_interval()` — 95% interval printed beside each arm's Trash-recall.
* `Trash-recall` now prints as `0.1222 (22/180)` rather than a bare rate.

**`tools/short_garbage_witness_report.py`** — the distinct-string count moves into the
default exposure block instead of appearing only under `--distinct`, with the
lines-per-string ratio and the most-repeated string. Every ratio ever quoted from that
block, the "1:3 against" included, was a line ratio.

**`issue30_runbook_job.sh`** — 5a relabelled as the shipping candidate and 5a-bis as the
control; the tool-version gap recorded with the `FORCE=1` re-run instruction; the
denominator stated in the trailer. Two new stages: **5d** (`GEMINATE_RATIO` 0 vs 4 — the
only change between the 09-17 and 09-18 numbers, never measured alone) and **5e**
(`LEXICON_MIN_DF` 2 vs 3 — the config calls 2 marginally best on self-scored data and it
has never been checked against gold).

### Not changed, deliberately

No flag is flipped. No threshold moves. No production predicate is widened —
`is_domain_notation()` keeps its near-miss, because fixing it there changes live behaviour
for seven strings and belongs behind its own parity re-score.

---

## 8. Corrections to the record

* **"only ~1,370 distinct strings"** (2026-09-17 comment) → **5,243**. 1,341 is the 2010+
  slice alone. The in-tree runbook comment already carries the right figure; the issue
  thread does not.
* **`ppole` "across 22 documents"** → **25** in the witness queue; df 35 corpus-wide.
* **The "1:3 against → 1.7:1 in favour" table** is scored against `categ`. Against gold the
  ordering reverses on every column, and 90.5% of the veto's credited benefit was one
  string that belongs in Trash. 🛑 *2026-09-23: it does not — `ppole` is an abbreviation (W2),
  so that benefit was the veto protecting a real word. Its df 35 is on the 822-document table;
  at 113,100 documents it is 229.*
* **`Trash-recall` denominators**: 180, not 90. A reader reconstructing the paired counts
  from the logs alone can land on either.
* **`setup/config.txt`'s "36 below 2×"** → 51 (both forms attested at `MIN_DF`) or 66 (twin
  at any frequency).
* **`30.runbook.md` does not exist in the repository.** It is cited as the authority for
  "do not enable without the measurement" by `text_util.py`, `setup/config.txt` and
  `tools/build_token_lexicon.py`. Only the job script exists, on the cluster. *2026-09-23: the
  in-tree pointers now name the #30 cluster job scripts (not tracked) and the plan or findings
  section that holds each result.*
* The 09-17 comment noted it was recording *"the second time in this round that
  fixture-level intuition inverted on real data"*. This is the third, and the pattern is
  sharper than "intuition": **every figure scored against `categ` has inverted when gold
  arrived.** `categ`-scored exposure is good for sizing an annotation queue and has not
  once been right about a direction.

---

## 9. What to do next, in order

1. **Re-run stage 5a with the two guards** (`FORCE=05a_witness_ab`, ~2.5h). If Clear-loss
   comes back 40 at Trash-recall 36/180, `SHORT_GARBAGE_WITNESS_ENABLE` is adoptable on the
   repository's own gate — and that is the decision #30 has been waiting on since July.
   Flipping it also means moving the four `SHORT_GARBAGE_WITNESS_*` constants out of
   `_DELIBERATELY_NOT_TUNABLE` and re-adding `rule_short_garbage_witness` to the two
   ablation lists, in the same commit.
   🛑 *Ran in round 2 (2026-09-18): `Clear`-loss 41 at Trash-recall 36/180, one line short, and
   the 41st was the dedup's. The guards were then lost (D45); the gate was later passed on the
   guard-less tree (08b, 10e: `Clear`-loss unchanged). The flag still ships off, and what it waits
   on now is annotation and the open questions, not a run.*
2. **Re-run the whole of stage 5 on current code** (`FORCE=1`, ~15h with 5d and 5e). The
   logs in hand predate the `errors` column, McNemar, and the current adoption gate.
3. **Annotate the distinct queue.** This is the binding constraint now, not the absence of
   gold. After the guards the queue is 5,005 distinct strings, of which **1,688 are never
   currently Trash** — those are the ones that decide the flag. Setting the `ppole` family
   aside as the single decision it is, the remaining 1,674 strings cover 2,670 lines with
   no short head: top 10 settle 19%, top 250 settle 47%. It is genuinely 1,674 decisions,
   and they are the only thing that can tell a wrong witness from a wrong pipeline.
4. **Stage 6 as its own job** (`RUN_STAGE6=1`, ~87h). Every `DEAD` / `LOAD-BEARING` /
   `clear_loss` verdict quoted in #30, the 23-rule sweep included, was scored against the
   pipeline's own output. `--split-cascade` is also the only instrument for the standing
   27% document-post-processing caveat.
5. **Do not rebuild the lexicon to fix the veto** (§3). Rebuild it if a wider table is
   wanted for other reasons, but `cuxoaid` at df 4 and `Naiade` at df 0 are not a sparsity
   problem and a bigger corpus moves them the wrong way.

---

# Addendum — stage 7 re-read against its own delivery (2026-09-20)

*Sources: `7_logs.log` (07a–07f) and the thirteen CSVs attached to the issue thread on
2026-09-19, recomputed directly rather than read from the summaries written when they were
produced. `ufal/atrium-alto-postprocess` at `test` HEAD.*

§0 of this document warned that *"re-running stage 5 on a current checkout is not a
formality"*. The same warning applies one level up: **re-reading a delivery is not a
formality either.** Five findings below; three of them overturn a conclusion written into
the digest, the plan and this file's own §9 between 2026-09-17 and 2026-09-19. All three
were refuted by evidence that was already inside the delivered logs.

## A1. 07a refutes the de-gemination cap rather than confirming it

The 2026-09-19 write-up says *"D25 holds at full scale"* and *"`ppole` still sits at the
lowest ratio of the eight"* — in a sentence that then gives the artefact range as starting
at 7.6×, below `ppole`'s 37.6×. Straight from `07a_geminate_lookup.log`, over 113,100
documents:

| token                      |  own df | base df |    ratio |
|----------------------------|--------:|--------:|---------:|
| **`ppole`** (abbrev.)      | **229** |   8,600 | **37.6** |
| **`ssuti`** (old spelling) | **195** |   1,667 |  **8.5** |
| **`ssutí`** (old spelling) | **142** |   1,617 | **11.4** |
| **`ssutě`** (old spelling) |  **64** |     900 | **14.1** |
| `llocm`                    |      55 |     417 |      7.6 |
| `vvkop`                    |      30 |   1,400 |     46.7 |
| `jjámy`                    |      10 |  14,799 |  1,479.9 |

By ratio `ppole` is fifth of seven. By own document frequency the gap that
`SHORT_GARBAGE_LEXICON_GEMINATE_MAX_DF = 10` was fitted to — 35 against 8 on the
822-document table, 4.4× — is **1.17×** here.

The consequence is live. The constant is an **absolute document count**, so it does not
rescale when the lexicon does, and stage 7 configured the bigger table. At `10` on it the
guard is right on **2 of 7** tokens instead of 7 of 7: every artefact except `jjámy` clears
the cap and keeps a vocabulary exemption it should not have.

**Not retuned — and then removed.** Eight tokens, seven labelled from one reading, is not a
population to fit a production threshold to, and §3 of this document already argued the
corresponding point about `min_df`. What landed first (2026-09-20) was an advisory rather
than a new value: `text_util.geminate_cap_scale_warning()`, corrected figures in the code
comment and `setup/config.txt`, and tests pinning both tables.

> ### 🛑 Superseded 2026-09-22 — the guard is gone, and the reading above is why
>
> Three of the seven are not artefacts. `ssuti`, `ssutí` and `ssutě` are an old spelling of
> *suť* (@david-spacil), so the table above is **four real words against four errors**, and
> the frequency gap the guard's threshold sat in has vocabulary on **both** sides of it. The
> per-collection signal that was going to separate them was refuted at the same time (A1's
> own §8c row, below): concentration separates *institutions*, and each institution has its
> own vocabulary.
>
> With the owner's approval — *"if the dictionary already covers all eight, it looks
> redundant"* — `SHORT_GARBAGE_LEXICON_GEMINATE_RATIO`, `SHORT_GARBAGE_LEXICON_GEMINATE_MAX_DF`
> and `geminate_cap_scale_warning()` were removed from `text_util.py` and `setup/config.txt`.
> At full scale the guard fired on **zero of 42,853** witness-queue rows, and removal is
> behaviourally identical to the `RATIO = 0` arm that 5d measured as bit-identical on all
> 2,064 gold rows.
>
> **This section is kept unchanged above the line** because the measurement is the reason the
> guard went, and a deleted table cannot be checked.

## A2. 07b is void, and it is 07c's bug one indirection further in

07b and 07c produce **byte-identical tables** — same 513 errors, same 40 Clear-loss, same
0.2917 cost, same `KL` to five decimals, 0 discordant rows, for two unrelated constants.
That is not two null results.

`quality_word_set()` is `@functools.lru_cache(maxsize=1)` over a **zero-argument** function
that reads `QUALITY_VOCABULARY_ENABLE` from module scope. `tools/ab_constant_eval.py` runs
both arms in one process, reference value first, so the `False` arm caches `None` and the
`True` arm is handed it back:

```
arm False -> None
arm True  -> None          <- the 07b result
with the cache cleared between arms:
arm False -> None
arm True  -> frozenset(...)
compute_valid_ratio('oueussd edelite sektlll', None)       = 1.00
compute_valid_ratio('oueussd edelite sektlll', <word set>) = 0.00
```

`_DERIVED_FROM_FLAG` fixed *one form* of "a flag's value gets frozen" — a module constant
built at import. A zero-argument cache is the second form and it was already in the tree.
**Fixed** by `_CACHES_FROM_FLAG` in `override_constants()`, clearing on entry and exit, plus
a **source-level guard test** that fails if a new zero-argument `lru_cache` appears in
`text_util` unregistered. `_read_token_lexicon` and `_compile_vowel_run` need no entry: both
are keyed on their arguments.

**D26 therefore has no measurement at all.** The coverage explanation offered at the time
may still be true; it is untested.

## A3. The 07f pack is a re-cut of the witness queue, not a second population

All four tabs join back into `04_witness_distinct.csv` at 100%: **2,424 of the same 5,005
strings, 17,635 of the same 20,078 lines**, re-partitioned by recoverability with tab D
down-sampled. `07f_annotation_pack.log` says so itself — the 5,005 rows are written first,
then cut into tabs.

The recoverability cross-tab quoted in the digest has the same scope issue. Its numbers
reproduce exactly from `07f_distinct_evidence.csv`, so they are correct — but they describe
the **20,078-line candidate queue**, not the corpus. "743 strings / 1,791 lines currently
`Trash` are fully recoverable" is a statement about lines the shape witness flags.

Practical effect: the annotation ask was three overlapping requests. Measured, it is **293
decisions** — 93 census rows reaching 94.8% of at-risk exposure, plus 200 sampled tail rows
at ±6.9 points. `tools/build_annotation_sample.py` builds them with the frame.

## A4. The de-duplication blast radius is 7 groups / 22 lines

The digest argues that per-line precision does not bound Clear-loss, because the modal dedup
can carry a correct occurrence down with a convicted one. True, and now sized. From
`04_witness_candidates.csv`:

| unit                                                |            count |
|-----------------------------------------------------|-----------------:|
| lines                                               |           20,078 |
| (document, string) groups — the dedup's voting unit |            5,222 |
| unanimous                                           |            5,200 |
| contested                                           |   22 (755 lines) |
| **bare plurality or tie**                           | **7 (22 lines)** |

4,903 groups are a single line, and the witness is a pure function of the line's text, so
within a group it convicts all or none — it cannot create a split.

The delivered discordant files point the same way. Cascade **on** (05a): 14 fixes, **1**
break. Cascade **off** (05f): 14 fixes, **3** breaks, and the baseline Clear-loss is 55
rather than 40. **On this gold set the cascade is protective**, which is the opposite of how
it has been read.

> 🛑 **Confounded (D45, 2026-09-23).** 05a ran with the two witness guards and 05f without them
> — `cc4990e` deleted them between the runs — and 05f's two extra breaks are exactly the two lines
> the guards vetoed. The conclusion that the cascade protects stands on same-tree evidence (08c,
> 10f), not on this pair.

## A5. 07d already reports the adoption gate passing

```
3.0: macro_f1=0.6344  errors=503  Clear-loss=40  cost=0.2829  -> ADOPT-CANDIDATE
4.0: macro_f1=0.6324  errors=503  Clear-loss=40  cost=0.2829  -> ADOPT-CANDIDATE (NOT SIGNIFICANT: n=4, p=1)
```

Against shipped (513 / 40 / 0.2917): errors down 10, cost down, **Clear-loss unchanged**.
The gate installed in §6 of this document — *"raises neither total errors, operational cost,
nor Clear-loss"* — is satisfied, and every other account still reads the flag as rejected on
Clear-loss +1.

Two caveats, which is why this is a run to schedule rather than a result to quote: 07d's base
configuration is not printed (witness-on with a lexicon is **inferred** from the Trash recall
matching the veto arms), and both arms are witness-on, so there is no paired test against
flag-off.

It is also the third distinct figure for the same nominal configuration — 504/42, 502/41,
503/40 — and that drift has never been root-caused. §9 item 1 predicted *"if Clear-loss comes
back 40 at Trash-recall 36/180"*. It came back 40 at 34/180, from a different arm than the
one that prediction was about.

## What follows — stage 8, replacing §9 items 1 and 3

| run    | what                                                                                                                                                          | settles                                                                            |
|--------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| **8a** | `ab_constant_eval` over `SHORT_GARBAGE_WITNESS_ENABLE`, `--values false,true`, stage-7 tree, **one output directory**, plus a `--no-postprocessing` companion | A5 and the 42→41→40 drift, together                                                |
| **8b** | re-run 07b                                                                                                                                                    | D26, which is currently unmeasured                                                 |
| **8c** | re-run 07c; then per-collection concentration for the eight doubled-initial tokens                                                                            | D27, and whether the de-gemination guard has a portable signal (A1)                |
| **8d** | `short_garbage_witness_report --by-group`                                                                                                                     | A corpus-wide denominator for the dedup decision, instead of the 22-line local one |

§9 item 3's framing is superseded by A3: the ask is 293 decisions against one population, not
1,674 against one plus 2,424 against another. §9 items 2, 4 and 5 stand as written.

**One discipline note, because it is the actual finding.** Four of the six instrument-level
errors this issue has logged, and three of the five above, were refuted by evidence sitting
in a file that had already been delivered. The log was right every time; the summary of the
log was not. Read the artefact.

---

# Addendum — stage 8 ran (2026-09-21)

All four runs this document specified happened, plus four more the job split out (`08a`–`08g`).
The findings are not restated here; they were recorded in this repository's dev-log digest of
[issue #30](https://github.com/ufal/atrium-alto-postprocess/issues/30) (`agent_dev_logs/digests/3.digest.md`, in
the git history since the issue closed) § "Stage 8 read against its own delivery" as T1–T15, with the stage
table and stage 9 in its plan (`agent_dev_logs/plans/3.plan.md`). What this document
needs is only the disposition of its own §9 items and its own predictions.

| this document said                               | stage 8                                                                                                                                                                                                                                                                                                                                                                                                                      |
|--------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **8a** settles A5 and the drift                  | ✅ Ran as **08b**. The gate passes — errors 513 → 503, `Clear`-loss **40 → 40**, cost 0.2829, fixes 12 / breaks 2, exact McNemar **p = 0.01294**. Both arms report `Clear`-loss 40, so the 504/42 → 502/41 → 503/40 drift was *between trees*, not within a measurement. Nothing left to root-cause. **08c** is the `--no-postprocessing` companion: `Clear`-loss 55 → 56, so the cascade is what makes the witness adoptable |
| **8b** settles D26                               | ✅ Ran as **08d**. Not a null and not merely unmeasured — a **decisive reject**: 212 fixes against 540 breaks, `Clear`-loss 40 → **180**, cost +0.3324, p ≈ 7.8e-34                                                                                                                                                                                                                                                           |
| **8c** settles D27 and the geminate signal       | ✅ Ran as **08e** and **08a**. D27 is a genuine **null** (5 fixes / 5 breaks, p = 1). The per-collection signal is **refuted and inverted**: `ppole` is 3 ARUP / 226 ARUB, the *most* concentrated of the eight, because it is a convention of one institution's 2010s forms. A1's remaining hypothesis is dead                                                                                                               |
| **8d** gives the dedup a corpus-wide denominator | ✅ Ran as **08f**. 61,682 groups; the cascade rescues **305** `Trash` lines and destroys **24** `Clear`, net **+281** in its favour — a wider margin than the 31-against-17 this document measured locally. Groups H6/H8 option 2 would change: **0**. A4's "22 lines" was correct for the queue it was measured on and is superseded for the corpus                                                                          |

**The prediction in §9 item 1 was right, and for the wrong reason.** It asked whether `Clear`-loss
would come back 40. It did — in both arms of a paired A/B, which is a stronger result than the
question anticipated, because it makes the drift a property of the trees rather than of the
measurement. 🛑 *2026-09-23 (D45): 08b ran without the two guards §9 item 1 was about, so it is
not a test of that prediction; the only run with them in, round-2 5a, gave `Clear`-loss 41.*

**And the discipline note at the end of §"What follows" applied to this document's own successor.**
Stage 8 contains a defect of exactly the kind catalogued here: **08f and 08g were run with the
vocabulary lexicon switched off**, which `setup/config.txt` says is a configuration that never
ships. The exposure they report, and the 592-decision annotation ask built from it, describe a
population roughly 3.7× larger than the shipped configuration puts at risk — about 73% of it is
exempt by attestation the moment a table is configured. The proof is in the delivered file rather
than in a log line: `ppole` is document frequency 229, above the geminate cap, so a lexicon-on
queue cannot contain it, and it is the largest row in the delivered one. 🛑 *The cap is gone
(D40, 2026-09-22); the inference holds without it — at df 229 `ppole` is attested outright.*

Two further findings were made **in the course of reading stage 8, written down as predicate
defects, and then withdrawn** — the binomial-taxonomy class and, initially, the URL class — because
they too had been measured with the lexicon off. Only the URL half survived re-measurement and is
fixed as D33. That is the seventh instrument-level error of this family, the third that was caught
before it shipped, and the first that was caught inside the same read that made it.

Read the artefact — and then check which configuration the artefact was produced in.


---

# Addendum — stage 6 ran, and R3 is closed (2026-09-22)

The 87-hour gold-scored rule sweep finished. §"What follows" above listed the
regeneration of the rule figures as outstanding; it is done. Findings are in the dev-log digest of
[issue #30](https://github.com/ufal/atrium-alto-postprocess/issues/30) (`agent_dev_logs/digests/3.digest.md`, in
the git history) § "Stage 6 read against its own delivery" (U0–U8). What belongs here is the
disposition of this document's own catalogue.

**R3 is closed.** All 23 rules are scored against the 2,064-row sidecar. The
baseline gold `Clear`-loss reads **40**, matching 08b from a separate job with a
different tool — this document has counted instrument failures for two months
and can now record the first cross-run agreement. 🛑 *It is 38 since D33 (10d/10e):
11c accounts for the shift, two `http://www.arub.cz` rows moving `Trash` → `Noisy`.
The agreement stands for the tree it was measured on.*

**And this is the eighth instrument-level error of the family catalogued here,
with a new shape.** The previous seven were a tool computing the wrong number.
This one computes the right number and draws a false conclusion from it:
`rule_short_garbage_witness` has `fire_count == 0` — true, and correct — and the
classifier turned that into `DEAD`, defined in its own docstring as "unreachable
dead code [that] can be permanently deleted ... because deletion provably changes
nothing", and printed it under "safe to retire". The rule is switched off, not
dead; 08f measured the same predicate at 100,824 lines — the lexicon-off figure,
48,909 once the lexicon was armed, see the addendum — and 08b passed its
adoption gate. **The instrument recommended deleting the feature this issue
exists to build.**

The new part, and the reason it is worth a paragraph in a document about
instrument failure: **it was known, twice, in places the artefact does not
carry.** `tests/test_pipeline_parity.py::UNREACHABLE_RULES` had the right
taxonomy — "unreachable BY CONFIGURATION" against gate shadowing — in a test file
no tool can read. And `issue30_stage6_job.sh` says it outright in its own
epilogue, before the run: *"rule_short_garbage_witness will read DEAD for as long
as SHORT_GARBAGE_WITNESS_ENABLE ships false — that is the flag, not the rule."*
That epilogue prints to the SLURM `.out` file. The file that gets attached to the
issue is `06_coverage.log`, which is what `tee` captures, and it carries the
verdict without the caveat.

So the standing instruction needs its own addendum. "Read the artefact, not the
report of the artefact" assumes the artefact carries what you need. Here it did
not, and the correction sat in a sibling file nobody pastes. **A caveat that
lives beside the output rather than in it is a caveat that does not travel.**
D35 moves this one into the tool: a fourth class, `INERT`, and a matching clause
in `RULE_COVERAGE.md`'s retirement criterion, which until now called
`fire_count == 0` the "config-independent" test — the one word that made it
unsafe.

Two smaller ones from the same delivery: `gold_clear_loss` was printed as an
absolute count beside a delta, with the baseline computed by the same pass and
discarded (**D36**, now emitted); and `decisive_cascade` is not the page cascade
three separate places said it was, because no smoothing-off baseline pass exists
in the tool at all (**D37**, corrected in place).

---

## Addendum, 2026-09-22 — the exposure pass was re-run with the lexicon armed

Stage 9b/9c. `08f_exposure_full` and `08g_annotation_ask` re-executed with
`ATRIUM_TEXT_UTILS_SHORT_GARBAGE_LEXICON_PATH` set; 08a–08e were not re-run and
are untouched. Same corpus, same 56,599,631 lines read. The full reading is in
`agent_dev_logs/digests/3.digest.md`, § "Stage 8 f/g re-run read against its own
delivery" (V0–V11). What belongs here is the part about instruments.

**The estimate this document carried was wrong in the direction the instrument
made easy.** T1 estimated from 08g's `token_status` column that 73.1% of the
at-risk exposure would evaporate once the lexicon was configured, and said in
terms that it was an estimate. Measured: **82.1%**. The gap is not sampling
noise; it is `ocr_neighbours.py`'s tokenisation being more conservative than
`_split_subtokens()` about what counts as attested, exactly the mismatch T1
named as its own caveat. **The caveat was right and the number derived from it
was still used as if it were nearly right.**

| quantity                  | T1/T2 estimate |  measured |
|---------------------------|---------------:|----------:|
| at-risk exposure exempted |          73.1% | **82.1%** |
| at-risk lines surviving   |          9,876 | **6,714** |
| at-risk strings surviving |          7,433 | **5,563** |

**A second instrument caution, and it is new.** The re-run's own log and its own
CSV do not agree with each other. `logs8.log` reports the queue at 42,248
strings; `08f_witness_distinct.csv` holds 42,853. It reports at-risk as 5,563
strings / 6,714 lines; recomputing from `08g_distinct_evidence.csv` gives 5,695 /
6,668. The differences are 2.3% of strings and 0.7% of lines, they change no
conclusion, and **they are the figures quoted to @DanaKriv**. Recorded in
`annotation_ask_README.md` rather than smoothed over. The general form is the one
this document keeps meeting: a summary line computed by a different code path
from the file beside it, with nothing forcing the two to agree.

**One instrument worked better than this document said it did.** T11 was hard on
`recoverability`, and the criticism stands — it cannot distinguish *correct but
rare* from *damaged beyond recognition*, and both score 0.00. But across the
at-risk population it is bimodal rather than flat (48.8% at 0.00, 24.8% at 1.00),
and on the two rows that matter most it is right in both directions: `Dauerleihe`
scores 0.00 because the table genuinely has never seen the word, and `J. Vysoean`
scores 1.00 because it genuinely is a damaged copy of a real name. A column that
is weaker than its name is not the same as a column that is useless, and this
document had drifted toward the second reading.

**And one measurement arrived for free.** Both runs read the same corpus through
the same three scope vetoes, and in-scope moved 7,493,429 → 7,477,924. D33 is the
only change to those vetoes between the runs, so **D33 removes 15,505 lines from
the witness's scope, corpus-wide** — a figure this document previously had to
leave unmeasured because the only version available was circular. It is a floor,
not the production effect: the witness's scope also requires `word_count <= 3`
and `rule_domain_notation` does not.

---

## Addendum, 2026-09-22 (second) — @david-spacil's answers, and one they put at risk

Recorded here because two of them bear on measurement rather than on policy.

**`ssuti` / `ssutí` / `ssutě` are an old spelling of *suť*, not scanning errors.** Four of the eight
doubled-letter tokens are real language; four are damage. The de-gemination guard's ratio gap does
not separate those classes — both sit above it — and he has agreed the guard can be dropped, since
the full-scale dictionary reaches all eight (V7). Every table in this repository that called those
three artefacts has been corrected.

**The consequence that is not documentation: the gold sidecar predates this.** He annotated
`tools/gold/sidecars/issue30_gold_2067.csv` while believing those three were damage. If any of its
2,064 rows is an `ssut*` line carrying `gold_categ = Trash`, that row is now wrong — and this is the
sidecar every `Clear`-loss figure in this issue is measured against, including the **40** the
adoption gate turns on. The sidecar carries locators and no text, so the check needs the corpus and
is cluster-side. The exposure is small — 1,667 `ssuti` lines in 56.6M, and the 822-document gold
corpus is 0.7% of the collections — which is exactly why it should be ruled out by looking rather
than by arithmetic. **Recorded as a stage-11 action; until it is done, `Clear`-loss 40 carries an
unquantified asterisk.** 🛑 *Cleared 2026-09-22: 11a found zero gold rows containing `ssuti`,
`ssutí` or `ssutě`. The figure itself is 38 since D33 (11c).*

**And a correction to the stage-8 re-run read.** V5 listed `J. Vysoean` and `B/ POSTKRANIAINY SKELET:`
among the "correctly-read" strings that a higher vowel-run threshold would spare. They are scanning
errors — `Vysočan`, `POSTKRANIÁLNÍ SKELET` — so the three-vowel clause convicts them correctly. V4's
own table had them right as damaged; V5 contradicted it two sections later. The honest trade at
min=4 is two confirmed-correct strings spared against a good deal of real damage released, which is
a much weaker case than V5 made. His alternative — gate the clause on the detected language, since
Czech has no three-vowel runs and German and French do — is the better direction and is recorded as
D44.
