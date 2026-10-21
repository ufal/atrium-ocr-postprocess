# How a line is categorised

ocr-postprocess gives every text line one of five categories, so that a reader of the output knows which
lines can go on to NLP, which need correcting, and which need another OCR pass. This page explains how it
decides and where the decision is weak. The complete rules, with every constant, are in
[the reference](reference/categorization_rules.md); what was measured is on
[the evaluation page](evaluation/categorization.md); the dated decisions are in
[the decision record](decisions/2026-10-categorization.md).

## The five categories

| Category   | Meaning                                                                                                | What to do with it                         |
|------------|--------------------------------------------------------------------------------------------------------|--------------------------------------------|
| `Clear`    | Legible. Whatever mistakes it has, nobody needs the page image to read it.                             | pass it on to NLP                          |
| `Noisy`    | Easily decipherable through a mistake: a swapped letter, a digit inside a word, a few lost characters. | correct the readable words                 |
| `Trash`    | Illegible: nobody can read it.                                                                         | re-process the page with another OCR tool  |
| `Non-text` | Structured but not language: numbers, codes, stamps, dates, measurements.                              | check it for identifiers of finds or sites |
| `Empty`    | Nothing on the line.                                                                                   | ignore it                                  |

The definition is the data providers': **`Trash` means illegible**, however useful the line is, so a correctly
scanned web address is `Clear` (@david-spacil, 2026-09-22; agreed by @DanaKriv, 2026-10-01). `Non-text` cannot be
told from `Trash` without the page image, so hand annotation leaves it out. The program approximates this
definition; [Known limits](#known-limits) says where it falls short.

## The idea

A line is judged on its own text, with two models as witnesses: a language identifier (FastText) says which
language the line looks like and how sure it is, and a small language model (Qwen2.5-0.5B) says how surprising
the line is (its perplexity). Neither decides alone. Most lines are decided by a **quality score** built from
nine measurements of the text; a short list of **rules** decides the cases the score gets wrong, such as an
upside-down scan or a web address; and in the batch pipeline, a few **document passes** use the rest of the
page to correct isolated mistakes.

## The steps

1. **Pre-filter.** A blank line is `Empty`. Numbers, dates, codes, stamps and very short or symbol-only lines are
   `Non-text`. No model runs for either. Two common OCR errors are repaired (`poh1ed` → `pohled`).
2. **Language.** FastText's label is kept for an expected or trusted language and replaced by the collection's
   default otherwise. Its confidence, weighted by how expected the language is, is the *trust* the later steps
   read.
3. **Measurements.** Symbols and digits inside words, mid-word capitals, repeated and fused letters, vowel
   share, letters an upside-down scan produces, and the perplexity (capped for lines of one or two words, which
   give any language model too little context).
4. **Quality score.** A weighted sum of nine of these, between 0 and 1.
5. **Rules, in order.** The first that matches decides: the address rule, three perplexity rules, the
   upside-down scan, an all-capitals scramble, too many symbols, short garbage, short lines, very low
   perplexity, and six structural red flags.
6. **The score decides the rest.** Under 0.55 the line is `Trash`, unless a rescue applies; above it, the line is
   `Clear`, or `Noisy` when too few of its words pass the word test or a word is damaged.
7. **Document passes** (batch pipeline only): the same text gets the same category throughout a document; a
   `Noisy` line inside four `Trash` lines becomes `Trash`; very clean and very bad pages correct their
   outliers; a run of upside-down lines becomes `Trash`. They change the category, never the score.

Each step, with its constants: [the reference](reference/categorization_rules.md).

## What the score means

The stored `quality_score` is moved into the band of the line's category: `Trash` < 0.55 ≤ `Noisy` < 0.80 ≤
`Clear`. **0.80 is where the `Clear` band starts; it does not decide between `Clear` and `Noisy`.** That is
decided by the word test and damaged tokens. The document passes can leave a category that disagrees with its
score. The category, not the score, is the decision.

## Why the main rules exist

- **Addresses first.** Every measurement asks how word-like a string is, and an address is not trying to be a
  word, so web and e-mail addresses get their category before anything else is read.
- **Perplexity alone is not enough.** On short lines a language model is often more surprised by real domain
  vocabulary than by garbage, so a perplexity rule convicts only when FastText cannot place the line either,
  and no perplexity rule reads a line of one or two words.
- **Upside-down scans** read as plausible letters; they are caught by the share of letters that turn into one
  another, and by words that are flip images of common Czech words. Any Czech diacritic protects a line.
- **Structural red flags need a second witness.** A flag on its own (many one-letter tokens, a repeated
  two-letter run) changes nothing unless the line also shows strong evidence of garbage, because ordinary
  catalogue lines raise the same flags.
- **Rescues** turn a `Trash` verdict into `Noisy` for a clean entry that trails off in dots, a numbered heading or
  abbreviation, and a clean reference line.

## Who runs it

One function, `classify_TEXT.score_line()`, decides every line. Three entry points call it and none reimplements
it:

| Caller                           | Role                                | Pre-filter                      | Model values                                     | Document passes |
|----------------------------------|-------------------------------------|---------------------------------|--------------------------------------------------|-----------------|
| `classify_TEXT.py`               | the batch pipeline (Step 4.1)       | yes                             | FastText and the perplexity model, live          | yes             |
| `service/text_inference.py`      | `/process` and `/score_record`      | yes (since v1.12.0-beta)        | FastText and the perplexity model, per request   | **no**          |
| `tools/recategorize_from_csv.py` | the offline re-scorer               | already applied in the CSV rows | the frozen `orig_lang_score` / `perplex` columns | yes             |

- Only the model-derived inputs differ: language, language confidence and perplexity. Every other measurement
  is recomputed from the text by the same code, so the re-scorer can replay a configuration change over
  existing CSVs without a GPU (`tests/test_scoring_single_source.py`; `tests/test_service_hardening.py` pins the
  service against the batch path line by line).
- The document passes need a whole document, so the service answers each line as the scorer gave it: for a
  whole document its categories can differ from a batch run's. Its `pages[].quality_score` is the mean over the
  lines a model judged (`Clear`, `Noisy`, `Trash`); the batch `avg_quality_score` averages `Clear` and `Noisy`.
- `/score_record` scores the lines of a record another tool wrote (a born-digital record,
  atrium-digital-convert#4) with the same categories and constants, without reading, splitting or reordering
  them. A line carrying the converter's decode verdict (`Garbage`, `Inverted`) is left as it is, and `text` is
  never written back.

## Foreign-language lines

The rules were calibrated on Czech archaeological reports with German and English passages in them. A line in
another language meets two things: how its **language label** is used, and **Czech-first heuristics** that act on
every line whatever its label.

- The label changes only the trust: an expected language (`ces`, `deu`, `eng`) counts fully, a trusted foreign
  one (`fra`, `pol`, `ita`, `slk`) at 0.85, any other at 0.50. A confident wrong label therefore costs only part
  of its trust.
- The word test knows Czech function words only, so a clean English or German sentence can read `Noisy`.
- `w`, `q` and `x` are rare in Czech and count against a word, which hurts German, Polish and English most.
- A Czech diacritic, or a Czech function word such as `den` or `on`, makes any line "upright Czech" and protects it
  from the upside-down-scan rules, also in other languages.

The cases, each pinned by a test, are in
[the reference](reference/categorization_rules.md#foreign-language-lines).

## Known limits

- **Short lines without Czech diacritics.** About 26,000 genuine garbage lines over both collections read
  `Clear` (`oueussd` is one): a `Clear` verdict on a line of one to three tokens without diacritics is less certain
  than it looks. The rule that would narrow this is built and held for the pilot.
- **Other languages,** as above.
- **A mis-scanned address** gets the same category as a correct one.
- **The service and the batch pipeline** can differ on a whole document: only the batch pipeline runs the
  document passes.

The measurements behind these: [the evaluation page](evaluation/categorization.md).

## What is frozen

The category semantics are frozen for the S2 pilot (decision of 2026-09-30 on
[ocr-postprocess#2](https://github.com/ufal/atrium-ocr-postprocess/issues/2)); only `LOWPPL_CLEAR_MAX` moved inside
it (50 → 35, 2026-10-11). After the pilot, a change is measured over the collections and the gold set before it is
adopted ([Measuring a change](evaluation/categorization.md#measuring-a-change)). The settings are listed in
[configuration](configuration.md).
