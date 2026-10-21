# Evaluation of the line categories

What has been measured about the five categories, on which data, and what it does not show. The rules are
in [the reference](../reference/categorization_rules.md); the decisions the measurements settled are in
[the decision record](../decisions/2026-10-categorization.md).

## Tests are not an accuracy measurement

The test suite pins **what the program does**: `tests/test_categorization_walkthrough.py` follows each worked
example through every step, `tests/test_scoring_single_source.py` holds the batch pipeline, the service and the
offline re-scorer to one scoring function, and `tests/test_service_hardening.py` compares the service with the
batch path line by line. A green suite says the categories are what the rules give. It does not say the rules
give the categories a person would.

Accuracy has been measured on the sets below only. A full validation on a gold set, and fine-tuning a model on
it ([ocr-postprocess#4](https://github.com/ufal/atrium-ocr-postprocess/issues/4)), are deferred by the AMČR
baseline ([atrium-project#67](https://github.com/ufal/atrium-project/issues/67)).

## The data

| Set                                    | What it is                                                                                           | Where                                                   |
|----------------------------------------|------------------------------------------------------------------------------------------------------|---------------------------------------------------------|
| Human gold set                         | 2,067 lines labelled by hand (2,064 scored rows in the sidecar); the adoption gate for a rule change | [`tools/gold/GOLD.md`](../../tools/gold/GOLD.md)        |
| Annotators' decisions of the #30 round | @david-spacil's re-check of 508 lines; @DanaKriv's 357 decisions on the census and the sample        | [`docs/issue30/`](../issue30/README.md)                 |
| Both collections                       | 113,101 documents, 71.8 million lines (ARÚP and ARÚB), re-scored in memory for a corpus diff         | `tools/corpus_change.py`; the run `DOC_LINE_CATEG_1210` |

## Results that bear on using the categories

- **Short lines without Czech diacritics (since `v1.5.0-beta`).** Narrowing `rule_short_garbage` promoted
  367,208 lines over both collections and demoted none, lowering `Trash` by 247,252 lines (−8.68 %). Human
  grading of 484 promoted lines: 76.5 % right, 9.6 % borderline, 13.9 % wrong, an improvement of 5.5 : 1. The
  cost: about 26,000 genuine OCR-garbage lines now read `Clear` (95 % CI [19,300; 34,600]). **A `Clear` verdict
  on a line of one to three tokens without Czech diacritics is less certain than the label suggests.** A consumer
  that filters on `categ == "Clear"` should know it.
- **`LOWPPL_CLEAR_MAX` 50 → 35 (2026-10-11).** Over both collections it moves 62,859 lines and the page band of
  1,128 of 1,226,349 pages; of the labelled lines it moves, 63 become right and none wrong.
- **Foreign-language lines.** The rules are Czech-first; the cases where that shows are listed, with the lines
  that pin them, in [the reference](../reference/categorization_rules.md#known-limits-and-what-to-measure-next).
  They have not been measured over a corpus.

## Candidates measured and not adopted

| Candidate                                                     | Measured on                          | Result                                                                    | Decision                              |
|---------------------------------------------------------------|--------------------------------------|---------------------------------------------------------------------------|---------------------------------------|
| The shape witness for `rule_short_garbage` (wired, ships off) | gold set; #30 annotations            | passes the adoption gate (gold errors 510 → 499, `Clear`-loss 38 = 38)    | held, to be decided with the floor    |
| The witness floor                                             | both collections, blind annotation   | 25,448 lines moved: about 3,920 fixed against 13,509 broken               | rejected, 2026-10-05 (@david-spacil)  |
| A real vocabulary in place of the shape-only word test        | gold set                             | 212 fixes against 540 breaks                                              | rejected                              |
| Page-relative perplexity for one- and two-word lines          | —                                    | needs calibrating on a real ARÚP/ARÚB run first                           | off                                   |

The full figures, with their dates and commits, are in
[the decision record](../decisions/2026-10-categorization.md#gate-6-the-shape-witness-_has_shape_garbage_evidence).

## Document passes

The batch pipeline's identical-text vote (`pp_dedup`), measured over both collections: 43,103 groups of the
same text in a document, 43,052 of them unanimous and 51 contested; the vote rescues 51 lines and pulls 10
readable ones down. The other three passes have not been measured on their own.

## Measuring a change

1. Replay the change over existing CSVs without a GPU with
   [`tools/recategorize_from_csv.py`](../../tools/recategorize_from_csv.py), which calls the pipeline's own
   scoring function.
2. Measure its reach and its effect over a corpus with `tools/corpus_change.py` (`reach`, then `recat` and
   `diff`), and against the gold set.
3. See which rules a run actually reached with `tools/rule_coverage_report.py`.

After the S2 pilot, the known limits are measured this way before any of them is changed.
