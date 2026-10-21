# What we need from you on issue #30, and why

**For:** @david-spacil
**Issue:** [ufal/atrium-alto-postprocess#30](https://github.com/ufal/atrium-alto-postprocess/issues/30)
**Time needed:** none for the items below. **You answered this on 2026-09-22 and every question below is now closed or
waiting on @DanaKriv.** Your answers are recorded in place, marked ✅.

> ⏳ **Opened since, 2026-09-22 (evening).** Your answers, and the code that followed them, raised a
> few questions we had written down as "for Dana and David" without ever asking. They are now asked
> in the issue thread, after [comment 61](https://github.com/ufal/atrium-alto-postprocess/issues/30#issuecomment-5783543756),
> and summarised in [§ 9](#9-opened-by-your-answers--open) below. One is for you alone (which
> revision to re-check your 508 lines against, § 3); three — Q4, Q5 (in two parts) and Q6 — are
> for you and @DanaKriv together.
>
> ✅ **2026-09-23: yours is answered** — `master`, with the re-check itself done and the repair it
> prompted **confirmed**: 336/508 on `3b02959` (§ 3). You are taking Q1–Q6 through with @DanaKriv
> by e-mail. One new question is for you alone, and it is optional ([§ 10](#10-new--q8), Q8).

> ## ✅ Answered — 2026-09-22
>
> | item  | your answer                                                                                                                                                             | what it settles                                                                                                                   |
> |-------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|
> | § 2   | Option 1; drop option 3                                                                                                                                                 | the de-duplication step stays as it is                                                                                            |
> | § 3   | after the tag                                                                                                                                                           | nothing blocked                                                                                                                   |
> | § 4   | **`ssuti` / `ssutí` / `ssutě` are an old spelling of *suť* — they are `Clear`**; the other four stay errors; no objection to dropping the safeguard                     | a two-month assumption, corrected                                                                                                 |
> | § 6   | **`Trash` = illegible.** Legible is `Clear`, easily decipherable is `Noisy`, *regardless of usefulness*. Re-process rather than delete. `Non-text` needs the page image | the definition the whole annotation rests on                                                                                      |
> | § 7   | 3+ vowels is right for Czech and wrong for German and French — **split by language, or try 4+**; and two of our examples were mislabelled                               | the direction of the next change — **built and measured since**: a global 4 is dead, the split gives 12 fixes / 1 break (see § 7) |
> | § 8.1 | close the raw-perplexity thread                                                                                                                                         | done                                                                                                                              |
>
> **Two of them correct us rather than answer us**, and both corrections are carried through every
> document in this folder rather than noted here and forgotten. They are marked 🛑 where they appear.

**What changed since the last version (2026-09-22).** Stage 6, an 87-hour measurement, has
finished. It answers your standing objection that our figures were graded by the program against
its own output. It also produced one number that affects item 2. See § 8.2.

*Earlier, on 2026-09-21:* the run we asked you to sanity-check has happened and it passed, so that
item is now reported rather than asked. The de-duplication question in item 2 now has a figure
measured across both whole collections, instead of one measured on 822 documents. That changes our
recommendation from "probably" to "clearly".

**A note on wording.** This document used to use our internal shorthand. We have rewritten it in
plain English. Where a technical term is genuinely needed, it is explained the first time it
appears.

---

## 0. Where we are, in eight lines

Your patch is merged and measured. It moved 367,208 lines back toward being kept. A human graded
484 of them: 76.5% right, 9.6% borderline, 13.9% wrong. The price is about 26,000 rubbish lines
that are now kept as good text.

The **shape witness** — the narrower rule meant to clean those up — **passes its acceptance test**
as of stage 8. Errors fell from 513 to 503, readable lines lost stayed at 40, and the cost measure
went down. Your Roman-numeral finding is closed and has stayed closed. *(The 40 is 38 since the
web-address exception in item 5: two correctly scanned `http://www.arub.cz` lines stopped being
discarded. The rule's effect on it is unchanged — 38 with it off, 38 with it on.)*

The rule is still switched off, and the reason has changed twice. Last week we found that the run
which measured *how much text the rule would affect* had been made with the vocabulary dictionary
switched off — a setting we would never ship. **That measurement has now been repeated properly.**
The answer is larger than we estimated: **82% of the apparent risk was not risk at all.** What the
rule would actually discard is **6,714 lines**, not 37,555. **Nothing about your patch or your
gradings is affected.**

What the repeat also showed is the reason for the new item 7 below. With the dictionary on, the
largest thing the rule would discard is `Dauerleihe` — German for *permanent loan* — on 286 lines
that are scanned perfectly correctly. One test inside the rule accounts for **two thirds** of
everything it would discard, and there is a single setting that narrows it. That is now the open
engineering question, and it is a question about archaeology as much as about code.

---

## 1. The run you were asked to check has happened, and it passed

In the previous version we asked whether the "503 errors / 40 lines lost" figure looked wrong to
you before we spent a run on it. We spent the run. The figure was real.

Stage 8b compares the rule switched off against the rule switched on, in one process, writing to
one place — so the two halves cannot differ for any reason except the rule itself:

```
OFF: errors=513  readable lines lost=40  cost=0.2917
ON : errors=503  readable lines lost=40  cost=0.2829
     line by line: 12 lines fixed, 2 lines broken, significance p=0.013
```

The rule also finds more of the rubbish it is meant to find: 12.2% → 18.9% of the lines a human
called `Trash` (the range of uncertainty on that is 13.8%–25.2%). Our acceptance test asks for no
increase in errors, no increase in cost, and no increase in readable lines lost. All three hold.

**And the drift is explained.** The same setting had reported 42, then 41, then 40 readable lines
lost, and we could never account for it. Both halves of this single run report 40. The differences
were between three different working versions of the code, not three different answers to one
question. There is nothing left to investigate. *(It has moved once more since, to 38, and that step
is explained too: the web-address exception in item 5 stopped two `http://www.arub.cz` lines being
discarded — see *And now it is settled* in § 7.)*

**No action needed from you here.** It is included because you were asked for an opinion and you
are owed the outcome.

## 2. The de-duplication decision — now measured on the whole archive

You have had this question since Round 2. It has been answered twice on samples. This is the first
answer measured on both collections in full.

**The mechanism.** When the same line of text appears several times in one document, a cleaning
step gives every copy the same category — whichever category the majority of the copies received.
So if the new rule condemns three copies of a word and two of those copies were correct, the two
correct ones are pulled down as well.

**How big it is across the whole archive.** This has now been measured three times: on 5,222
groups, then on 61,682, and now on 43,103 with the dictionary loaded. The third measurement is the
one to use, because the earlier two counted repeated lines the rule can no longer reach. **The
answer is the same each time, and the margin is wider at every scale.**

|                                            |                                    |
|--------------------------------------------|------------------------------------|
| groups of repeated lines the step votes on | 43,103                             |
| of those, already unanimous                | 43,052                             |
| contested at all                           | 51                                 |
| decided by a clear majority                | 34                                 |
| decided by a tie                           | 17                                 |
| **groups your option 3 would change**      | **0**                              |
| readable lines the vote pulls down         | 10 (in 8 groups)                   |
| rubbish lines the vote rescues             | **51** (in 41 groups)              |
| **net effect**                             | **+41 lines in the step's favour** |

On the first sample the step rescued 31 lines and lost 17. Across the whole archive it rescues five
for every one it costs. Contested cases are also rarer than anyone expected. 87% of the lines the
rule reaches appear only once inside their own document, so the step has nothing to vote on.

Ties also cannot hurt you, for a reason worth knowing. When the vote is tied, the code picks the
first category in alphabetical order, and `Clear` comes before `Noisy`, which comes before `Trash`.
So a tie can never land on `Trash`. That is an accident of the alphabet rather than a design
decision, and it is holding weight — worth remembering if anyone ever renames a category.

**Your three options, with the full-archive numbers:**

1. **Accept it as it is.** The step rescues about five lines for every one it costs.
2. **Test the eight page-smoothing settings.** They are all controlled by configuration, so this
   costs nothing to try. Still not done.
3. **Stop a bare majority from pushing a readable line into `Trash`.** Measured reach across both
   collections: **zero groups.** The situation it was written for does not occur.

**We recommend option 1, and we think option 3 can now be dropped.** Not because it was a bad
idea. It has simply been measured against the data it would run on, twice, in two different
configurations, and found nothing to act on either time. **The decision is still yours.** One line
is enough.

> ✅ **Answered 2026-09-22 — option 1, nothing else needed.** The step stays as it is and option 3
> is dropped. This item is closed.

## 3. Please re-check your 508 lines against the current rule

> ✅ **Answered 2026-09-22 — you will do this after the tag.** Recorded, not chased. Nothing else
> in this issue waits on it.
>
> ⏳ **One thing to know before you start.** The tag is
> [`v1.5.1-beta`](https://github.com/ufal/atrium-alto-postprocess/releases/tag/v1.5.1-beta)
> (`09c9640`). Two changes to the rule landed on `master` *after* it: the doubled-letter safeguard
> was removed (§ 4b — measured identical on your 2,064 lines), and the three-vowel test was split
> by language (§ 7 — it changes one of the two lines the rule gets wrong). So the tag and `master`
> will not give the same figure on your 508. Which one to check against is asked in the thread
> (§ 9); our suggestion is `master`, which gives back the tag's single three-vowel threshold when
> `SHORT_GARBAGE_WITNESS_VOWEL_RUN_EXEMPT_LANGS` is set empty.

> ## ✅ Done — 2026-09-23, on `master`, and it found a regression
>
> *"Q7: master. We ran the tag as well, so nothing waits on the choice."* Whole documents
> re-scored with `tools/recategorize_from_csv.py`, witness on, **no lexicon** (the shipped
> default):
>
> | revision                                        | agreement with gold |
> |-------------------------------------------------|--------------------:|
> | `4ed309a` witness off                           |             326/508 |
> | `09c9640` (`v1.5.1-beta`) witness on            |             334/508 |
> | `4ed309a` witness on                            |             335/508 |
> | `4ed309a` witness on, `VOWEL_RUN_EXEMPT_LANGS=` |             334/508 |
> | **`3b02959` witness on** — exemption restored   |         **336/508** |
>
> On `4ed309a` the tag and `master` differ on one line only — `Frauenzimmerbad", sämtlic
> Gesellschastsbäder,`, which `master` gets right. On `master` as it then stood (`4ed309a`) the
> witness moves **16 lines: 11 fixed, 2 broken, 3 wrong either way**, which confirms the "at most 2"
> bound directly rather than by set inclusion.
>
> 🛑 **One of the two breaks should not have happened.** `S-VIIIb` broke because its exemption was
> **not in the code**. It was added on 2026-09-18 and deleted the same evening by an unrelated
> lexicon commit (`cc4990e`), together with a German-diacritic veto; no test held either, so
> nothing noticed. With a lexicon, `viiib` is attested and the line survives anyway — which is why
> our stage-8 runs never saw it — but the shipped configuration has none. **The grid-reference
> exemption is restored and now pinned by tests**, and on the same run `master` reads **336/508,
> with one break** — ✅ **confirmed** by you on `3b02959` (below). The German veto was deliberately
> not restored with it: the language split now overlaps it, and it needs its own measurement.
>
> **The one remaining break is `Lokolieace: •VIII,`** — gold `Noisy`, a damaged `Lokalisace` a
> reader can still work out. The short-line rule can only answer `Trash`, so this is exactly the
> case Q6 asks about.
>
> ✅ **Confirmed 2026-09-23 on `3b02959`:** *"336/508, `S-VIIIb` is now Clear, `Lokolieace: •VIII,` is
> the only break (15 moved: 11 fixed, 1 broken, 3 wrong either way). With the flag off, there's
> 0/508 change against `4ed309a`."* So the restored exemption changes nothing until the rule is
> switched on. The tag (334) and `master` (336) now differ by two lines: `Frauenzimmerbad` (the
> language split) and `S-VIIIb` (the restored exemption).

Your measurement was the deciding one, and it was right: 26 lines moved, 12 improved, 12 made
worse, and every one of the 12 failures contained a Roman numeral.

Since then the rule has gained four exceptions: Roman numerals, Latin family names, joined grid
references such as `S-VIIIb`, and — until 2026-09-22 — the doubled-letter guard described in
item 4, which has since been removed on your answer. It has now also
gained a web-address exception, described in item 5.

🛑 *Corrected 2026-09-23: the `S-VIIIb` exception was missing from the code from the evening of
2026-09-18 until 2026-09-23, although this paragraph listed it — see the ✅ block above.*

On the full set of 2,064 annotated lines the rule now improves 12 and worsens 2. Your 508 lines are
part of that set, so the number it worsens within your sample cannot be more than 2 — which is
sound reasoning, but indirect. It was your finding and your tooling, and a direct figure on the 508
would close it properly. ✅ *It has: 1 break on `3b02959` (the ✅ block above).*

## 4. The eight doubled-letter tokens — answered, and we had three of them wrong

> ## 🛑 ✅ Answered 2026-09-22, and it corrects us
>
> **`ssuti`, `ssutí` and `ssutě` are not scanning errors.** They are an **old way of spelling the
> word *suť*** — so they are `Clear`. The other four doubled-letter spellings (`llocm`, `vvkop`,
> `jjámy`, `oobjekt`) remain scanning errors.
>
> That changes the count this issue has worked from since July. It is not **one** legitimate
> spelling against seven errors. It is **four against four**: `ppole` and the three `ssut*` forms
> are real language, and four are damage.
>
> **On the safeguard:** *"if the dictionary already covers all eight, it looks redundant – no
> objection to dropping it."* It does cover all eight (below), so we will drop it. See § 4b.
>
> **Every table in this folder that called these three "artefacts" has been corrected.** The
> measurement was already pointing this way — a spelling that appears in 195 separate documents is
> not what a scanning artefact looks like, and we said so — but we did not draw the conclusion.

**This item also no longer holds anything up.** The measurement was repeated with the archive's
dictionary loaded, and **none of the eight spellings appears anywhere in the list of text the rule
can reach** — not one row out of 42,853. Three of them (`ssutí`, `ssutě`, `jjámy`) carry Czech
accents, and the rule never looks at lines with accents. The other five are protected by the
dictionary: they appear in far more documents than the safeguard's limit allows.

The rest of this item is kept as it was written. It is how we got here, and its conclusion — that
no automatic test separates the two classes — survives your answer.

On 2026-09-19 you told us that `ppole` is an abbreviation for *popelnicová pole*, and that the
other doubled-first-letter examples really are scanning errors. Since then we have tried three ways
to make that distinction automatically:

1. **Compare it to the base word.** `ppole` sits fifth of seven. No use.
2. **Count how many documents it appears in.** On 822 documents this separated cleanly: `ppole` in
   35 documents, every confirmed error in 8 or fewer. Across all 113,100 documents it becomes 229
   against 195. No use.
3. **Check whether it is concentrated in one collection.** This is the one you would expect to
   work: a scanning error belongs to the machine that produced it, while an abbreviation is a
   shared convention. Measured:

| spelling    |    ARÚP |    ARUB |                                       |
|-------------|--------:|--------:|---------------------------------------|
| **`ppole`** |   **3** | **226** | **abbreviation** — *popelnicová pole* |
| **`ssuti`** | **152** |  **43** | **old spelling of *suť***             |
| **`ssutí`** | **116** |  **26** | **old spelling of *suť***             |
| **`ssutě`** |  **46** |  **18** | **old spelling of *suť***             |
| `llocm`     |      44 |      11 | scanning error                        |
| `vvkop`     |       1 |      29 | scanning error                        |
| `jjámy`     |       2 |       8 | scanning error                        |
| `oobjekt`   |       2 |       2 | scanning error                        |

**With your answer, the table reads differently and the conclusion gets simpler.** We used to read
it as a paradox. The one legitimate spelling was the most one-sided of the eight, so concentration
pointed exactly the wrong way. It is not a paradox. **Both kinds of legitimate spelling are house
conventions, and each house is a different one** — `ppole` belongs to ARUB's forms, `ssut*` to
ARÚP's. The four real scanning errors are small and split across both. So concentration cannot
separate the classes. It separates *institutions*, and each institution has its own vocabulary.

(Measured separately, and it still holds: `ppole` appears on 2 lines in the 1990s, 45 in the 2000s
and 13,144 in the 2010s. It arrives with the forms.)

### 4b. The safeguard — removed on your answer

**Remove it**, on your answer. The doubled-letter guard exists to let the rule convict a
doubled-letter spelling *even though* the dictionary knows it. At full scale the dictionary knows
all eight and the guard fires on none of them. So it is a threshold fitted to one small table that
now changes nothing. And with `ssut*` turning out to be real language, keeping it would be a
standing risk of convicting real words for no measured benefit.

> ✅ **Done, 2026-09-22.** The guard is out of the code, the two configuration keys are out of
> `setup/config.txt`, and the tests that asserted the old behaviour have been replaced by one
> that asserts the new one: **anything the dictionary attests keeps its exemption**. It needed no
> further input from you.
>
> Two things are worth knowing about the change. **It cannot affect a production run** — the
> program ships with no dictionary configured and the rule switched off, so the guard had nothing
> to act on there; it only ever ran in our measurement tools. And **it was already measured**: the
> guard has a setting that switches it off completely, and switching it off was tested against
> your 2,064 annotated lines on 2026-09-19 and changed not one of them. Removing the code is the
> same thing as that setting, made permanent.

*(Written before the change, kept as the reasoning:)* We have not made the change yet: it is a
code change and this round was documentation. It is recorded as the next step, and it needs no
further input from you.

## 5. Two findings about the rule itself, one of which we got wrong first

Included because we have made a point of telling you when our own conclusions did not survive
contact with the data. This one we caught ourselves.

**What we thought we had found.** `Lepus europaeus`, `Mammalia indet.` and `Triticum monococcum`
all trigger the rule, while `Poaceae` does not. That looked like a gap: the exception for Latin
family names does not cover two-part species names, which would matter a great deal in the
osteology and archaeobotany reports.

**What is actually true.** We had tested with the vocabulary dictionary switched off — the same
mistake that caused the sizing error in § 0. Every one of those terms is in the full dictionary and
is already exempt once the dictionary is loaded. There is no gap, and we did not add a rule for
one. We had drafted a "genus + species" pattern before checking. When we measured it, we found it
would also have exempted `Chenopoaium hycnaum`, `Loua (oxkuku` and `Laaid. bazic 64999/` — letting
real rubbish through to fix a problem that did not exist.

**What was a real gap.** Web and e-mail addresses **quoted only once**. The addresses in page
footers appear so often that the dictionary recognises them. But an address cited in a single
document can never be recognised that way, by definition. 144 such lines survived even with the
dictionary loaded:

```
(zdroj: http://www.turistika.cz/mista/krakovec-mistni-cast-laskova).
1 https://www.mza.cz/indikacniskici/skica/detail/1669
roku 1820 (http://www.hrady.cz/index.php?OID=1291).
e-mail: officeauappmost.cz          <- the scan lost the @; the label survived
```

The program now recognises this shape. It catches all 144, and wrongly catches none of the other
7,289 lines at risk, nor any of our stored rubbish examples. **No action needed.** It is mentioned
because your re-check of the 508 lines in item 3 — now done — ran on this shape of the rule.

**And one problem we have deliberately left unsolved.** `Kaukasus`, `Hallstatthaus` and
`Schuhleistenkeilbruchstueck` are real words. They appear in too few documents to be recognised by
the dictionary, and the rule condemns them for having too few different letters. There is no cut-off
on length or letter variety that separates them from real rubbish of the same shape: `vodovod` and
`PSSPPOP` are both seven letters with the same letter variety, and `VODOVOD` and `PSSPPOP` are both
capitals. Tuning a threshold on two examples is exactly what we did with the doubled-letter guard in
item 4, and item 4 shows what that costs. So it is recorded as a known, accepted problem, with a
test that will notice if it changes.

## 6. A question about definitions, for you and @DanaKriv together

The full-archive measurement turned up something that is not a bug, and may matter more than the
switch itself.

**77% of what the program throws away in this part of the archive has nothing wrong with it** — no
garbled characters, no strange symbols. Specifically, it marked as `Trash` (the two addresses
have been `Noisy` since the web-address exception in item 5):

* `http://www.arub.cz` — 5,309 lines, scanned perfectly correctly;
* `e-mail: mhauer@zip-ops.cz`;
* `ARCHAIA Brno o.p.s.` — while marking `ARCHAIA` on its own as `Clear`.

Measured against the definitions we work to — `Clear` is correct text, `Noisy` is damaged but still
readable, `Trash` is *nobody can read this* — none of those is `Trash`. Anyone can read them.

One label is being asked two different questions:

1. **Can a person read this?** That is what the definitions describe.
2. **Is this running text worth keeping and searching?** That is a different question, and it is
   the one the program seems to be answering in practice.

We first thought there was an easy answer: send page footers and form labels to **`Non-text`**.
Checking the documents properly, that does not work either — and the reason is a second problem we
should have told you about earlier.

**The five categories are defined in two places, and the definitions do not agree.** Dana's guide
defines them by *legibility*. The main `README.md` defines them by *what the program did and what
should happen next*. Three specific conflicts:

|               | Dana's guide says                                                       | `README.md` says                                                                                                                                              |
|---------------|-------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `Trash`       | "scanning rubbish — nothing is lost by deleting it"                     | "should be re-processed by another OCR tool"                                                                                                                  |
| `Non-text`    | "not language at all — a table border, a page decoration, a ruler mark" | whatever the pre-filter caught: too short, too few different characters, under 30% letters — and it is meant to be searched for **site and find identifiers** |
| a legible URL | `Clear`                                                                 | can be `Trash` via an override                                                                                                                                |

The `Trash` row matters to you directly as a data provider: **"delete it" and "re-scan it with a
different OCR tool" are not the same instruction**, and which one is meant changes what you would
do with the output.

The `Non-text` row is why our easy answer fails. The program already uses `Non-text` to hold real,
meaningful content — an inventory number such as `A123/2024` is marked `Non-text` today, and the
README says that category "may be checked for identifiers of finds/sites". So it is not an empty
shelf we can move web addresses onto. Putting them there would mix two different things in one
category.

**This needs deciding before the labelling, not after.** If Dana applies the definitions as
written in her guide, she will mark a correctly scanned web address as `Clear`, the measured
accuracy of the rule will shift, and nobody will be able to tell whether that came from the labels
or from the definition.

**What we would like from the two of you** is a decision on the first question — is the label about
*legibility* or about *usefulness as text*? — and we will then make the program, the guide and the
README agree with each other, rather than continuing with three descriptions of five categories.
The same question is § 7.2 of Dana's guide.

> ## ✅ Answered 2026-09-22 — legibility, pending @DanaKriv
>
> * **`Trash` = illegible.** Anything legible is `Clear`; easily decipherable is `Noisy` —
>   **regardless of how useful the line is to us.**
> * **Delete versus re-OCR:** no strong opinion, and most of it is probably a handwriting-recognition
>   candidate, so **re-process rather than delete**.
> * **`Non-text`:** you cannot tell it from `Trash` without the source image. Apart from the first few
>   hundred lines of your original annotation (1940s ARÚB, complete documents), you labelled only
>   `Clear`/`Noisy`/`Trash` — *"with a few exceptions, most of them originate from Tomáš Krofta's
>   annotations"* — and whatever the pre-filter marked `Non-text` was out of scope.
>
>   🛑 *Corrected 2026-09-22: this line first read "1950s"; your comment says 1940s, and the gold
>   file agrees. Of its 45 `Non-text` rows, 24 are in 1940s ARÚB documents (`MTX194…`) and the
>   other 21 sit in just seven later documents (18 in four ARÚP documents from the 1970s–2000s,
>   3 in three ARÚB ones) — which would fit the exceptions you attribute to Tomáš Krofta.*
>
> **What follows, once @DanaKriv agrees.**
>
> 1. **`http://www.arub.cz` is `Clear`.** It is perfectly legible. We moved it from `Trash` to
>    `Noisy` when we taught the program to recognise web addresses — the right direction, and one
>    step short of where your answer puts it.
> 2. **Dana's guide holds the correct definitions** and stops being one of two candidates. The
>    conflict table above is resolved in its favour on legibility.
> 3. **The `README.md` row you might have expected to be wrong is right.** It says `Trash` "should
>    be re-processed by another OCR tool", which is exactly your delete-versus-re-OCR answer. Only
>    its *legibility* semantics needed reconciling, not the processing column.
> 4. **`Non-text` is out of scope for annotation**, and Dana is being told the same. That also
>    explains the shape of the annotated set — 45 `Non-text` rows against 1,302 `Clear` — which we
>    had never accounted for.
>
> This is marked *pending @DanaKriv* because you framed it as your view rather than a joint
> decision, and she is the other half of it. Nothing stops her labelling in the meantime; what
> waits is how the answers are scored.

## 7. One setting decides most of this, and the choice is archaeological

This item is new, and it is the one we would most like your view on after item 6.

**What the rule is made of.** The shape witness is four separate tests. A line is condemned if any
one of them fires:

| test                        | what it looks for      | share of the text the rule would discard | how often it agrees with the program's current answer |
|-----------------------------|------------------------|-----------------------------------------:|------------------------------------------------------:|
| **three vowels in a row**   | `aue`, `eui`, `oea`    |                                **64.6%** |                                                 79.8% |
| few different letters       | `OUUIUO`, `Aa/III 116` |                                    19.7% |                                                 80.3% |
| doubled first letter        | `vvkop`, `llocm`       |                                     9.0% |                                                 88.3% |
| the same letter 3× in a row | `sektlll`              |                                     1.2% |                                                 97.9% |

Read the last column as a rough accuracy check. It is the share of each test's firings that land on
text the program already throws away. A high number therefore means the test is picking out things
that already look like rubbish. **The three-vowel test is the biggest by far and the least
accurate.**
It alone accounts for about 4,300 of the roughly 6,700 lines at stake.

**And it is why correctly scanned words are on the list.** `Dauerleihe` has *aue*. `FEUILLETON`
has *eui*. `J. Vysoean` has *oea*. Meanwhile the marks the rule was written for — the page stamps
that read `OUUITN`, `OUOISP`, `OUUIUO` — have four or more vowels in a row.

🛑 *`J. Vysoean` is not correctly scanned: it is a damaged `Vysočan` (your correction, 2026-09-22),
so the test catches it rightly. The same slip is struck through a few lines below.*

**So the obvious move is to require four instead of three.** Measured over the full list:

| vowels required | lines the rule would discard that are kept today | change |
|----------------:|-------------------------------------------------:|-------:|
|     **3** (now) |                                            6,668 |      — |
|           **4** |                                            2,891 | −56.6% |
|               5 |                                            2,436 | −63.5% |

~~At four, every correctly-read item we can name is spared — `Dauerleihe`, `FEUILLETON.`,
`J. Vysoean`, `POSTKRANIAINY SKELET` — and the page stamps are still caught, along with
`eaual to:` (which has *eaua*, four in a row).~~

> 🛑 **That sentence was wrong, and you caught it.** `J. Vysoean` and `B/ POSTKRANIAINY SKELET:` are
> **not** correctly read. They are scanning errors — `Vysočan` and `POSTKRANIÁLNÍ SKELET` — so the
> three-vowel test catches them **correctly**, and four would let them through. (Our own table in
> § 6 of Dana's guide had them right as damaged; this sentence contradicted it.)

**The honest version of the trade at four.** It spares two strings we can confirm are correctly
read: `Dauerleihe` (286 lines) and `FEUILLETON.` (13). At the same time it releases real scanning
errors — `J. Vysoean` (125), `lenaye` (46), `Poeitaecxy soubor` (26), `noienm k.` (19) and
`B/ POSTKRANIAINY SKELET:` (10). The page stamps and `eaual to:` (*eaua*, four in a row) are still
caught. That is a much weaker case than the one we put to you, and it is the reason your other
suggestion is the better one.

**We have not made this change, and we would like you to tell us whether to.** Two reasons for
caution, stated plainly *(📊 measured since, and the change was not made: four instead of three
fixes 2 lines and breaks 2 against your labels, and finds less rubbish — see the 📊 block below)*:

1. **The figures above are an estimate.** They were produced by applying the test's own pattern to
   the saved text, not by re-running the rule. We will re-run it properly, and we do not expect the
   numbers to move much, but they may move.
2. **This is exactly how we went wrong twice before.** The doubled-letter limit in item 4 was
   chosen because it fitted a handful of examples someone could name. So was an earlier threshold.
   Neither survived being measured on the whole archive. Choosing four because it spares four words
   we can name is the same move, so it has to be checked against @DanaKriv's labels first.

**The question for you is not the number.** It is this: **is a run of three vowels evidence of a
scanning error in this archive?** In ordinary Czech it very nearly is. In an archive that also
carries German museum terms, French section headings, Latin species names and foreign place names,
we suspect it is not. You know this material and we do not.

> ## ✅ And now it is settled — 2026-09-22, final runs
>
> The last runs finished. **Your answer was right, and the split works.** Measured
> against your and Dana's 2,064 labelled lines, splitting by language gives:
>
> * **12 lines improved, 1 line made worse** — better than the current rule, which
>   improves 12 and worsens 2.
> * **Total mistakes fall from 503 to 502**, and the count of readable lines wrongly
>   thrown away falls from 38 to 37.
>
> Both numbers move the right way, so nothing about our acceptance test needs
> changing. The line it stops destroying is the German sentence:
> `Frauenzimmerbad", sämtlic Gesellschastsbäder,`.
>
> **Two honest cautions, because they matter more than the result.**
>
> **First, it works partly by luck.** The program guesses a language for every
> line, and on short damaged lines that guess is often wrong. It called
> `deutendes. Alhimiaal` **Afrikaans** — a person would read it as German. That
> mistake is the only reason the rule still catches it correctly. It also called
> `http://www.arub.cz` **Cantonese**. And the German sentence above was recognised
> as German with only 36% confidence, which is barely better than a guess.
>
> So the rule now depends on a signal that is unreliable on exactly the kind of
> text it is meant to judge. It gives the right answer here; we cannot promise it
> gives the right answer everywhere, and we would rather say that now.
>
> **Second, one number needs reading carefully.** 44.5% of the labelled lines are
> not recognised as Czech — which sounds like the split matters enormously. It does
> not mean that. The list includes Vietnamese, Estonian, Xhosa and Uzbek, which do
> not appear in this archive; that is the guesser failing, not foreign text. The
> share that the split actually acts on — German and French — is **5.8%**. That is
> the number to remember.
>
> **Also settled:** the old spellings `ssuti`, `ssutí` and `ssutě` do not appear
> anywhere in your labelled lines, so your correction does not change any of the
> figures above. And we have finally explained something that has bothered us since
> July: the count of readable lines wrongly discarded moved 42 → 41 → 40 → 38 over
> several months without an explanation. The last step is now accounted for — it is
> the two `http://www.arub.cz` lines, which stopped being thrown away when we taught
> the program to recognise web addresses.

> ## 📊 And we have now measured it — 2026-09-22
>
> Two runs finished after you answered. Both scored the program against the 2,064 lines you and
> @DanaKriv labelled.
>
> **First: four vowels instead of three is not an improvement.** We tried it, and against your
> labels it changes nothing that matters — the same number of mistakes, the same number of readable
> lines lost, and two lines fixed against two lines broken. At the same time it finds *less*
> rubbish: 32 `Trash` lines out of 180 instead of 34. So the simple change you offered as a
> fallback is now closed. It is not harmful; it is just not worth making.
>
> **Second: the new rule as a whole passes its test.** Switching it on reduces mistakes from 513 to
> 503 and does not lose a single extra readable line. It finds 34 of the 180 `Trash` lines instead
> of 22 — half as many again. Twelve lines improve, two get worse, and the result is statistically
> solid rather than luck.
>
> **The interesting part is those two lines that get worse.** They are the only two the rule gets
> wrong in the whole set, and both are caught by the three-vowel test — the exact test you told us
> was wrong for German and French. One of them is a German sentence:
>
> ```
> Frauenzimmerbad", sämtlic Gesellschastsbäder,
> ```
>
> **So your answer and our measurement agree completely.** Everything else the rule does is
> uncontested; the only thing in dispute is the one test you identified, on the one kind of text
> you identified.
>
> 🛑 **Superseded by the final runs — read the ✅ block above instead.** The three paragraphs below
> were a prediction, made before anyone had looked at which language the program assigned to these
> lines. That check has since been done: the rubbish line the prediction expected to lose was
> detected as Afrikaans rather than German, so the split does not release it. The measured result
> is **12 improved, 1 made worse**, total mistakes and readable lines lost both *down*, and the
> acceptance test passes as written — nothing about it needs arguing. Kept for the record.
>
> **What the language split would do, worked out line by line.** We have now built it — three
> vowels count as damage in Czech, four in German and French — and checked it against those same
> 14 lines. It would spare the German sentence above, which is a clear win. It would also let
> through one line of real rubbish that the current rule catches. So: **one fewer readable line
> destroyed, one more rubbish line kept.**
>
> That is a better trade than it sounds, because the two mistakes are not equally bad — losing a
> readable German sentence is worse than keeping a line of rubbish. But it does mean the total
> number of mistakes goes up by one, and our own acceptance test refuses any change that raises
> that total. **So the rule we wrote for ourselves may be the thing standing in the way, not the
> change.** We would rather tell you that now than discover it after the next run.
>
> One thing we still need before any of this is final: the program records which language it
> thinks each line is, and nobody has looked at what it says for these four lines. That is the next
> check and it is a small one.

> ## ✅ Answered 2026-09-22 — split by language, or failing that try four
>
> *"For Czech, 3+ vowels in a row is a good rule – Czech has no triphthongs. For German and French
> it does damage (`Dauerleihe`, `FEUILLETON`). So either split by language, or try 4+."*
>
> **This is the better answer and it changes what we build.** The test is a fact about Czech
> phonology, so applying it to German and French is a category error rather than a badly chosen
> threshold. Blunting it to four does not fix that — it just stops convicting a lot of genuine
> damage as well, as the corrected paragraph above now shows.
>
> **What it costs, stated plainly.** The program already records a detected language for every line.
> It is not currently passed to the part of the code that makes this decision, so the split is a
> real change rather than a setting, and it needs measuring like anything else. We are not treating
> your one-line answer as authorisation for that; it is the direction, and the measurement comes
> first.
>
> **What we are doing about it, in order:**
>
> 1. Count how many of the lines at risk are detected as *not* Czech. If that is 3%, the split buys
>    almost nothing and four is the pragmatic choice after all. If it is 40%, the split is the whole
>    answer. This costs nothing and is the next measurement.
> 2. Whichever way that goes, check it against @DanaKriv's labels before changing anything.
>
> ✅ **Both steps done, 2026-09-22.** (1) The not-Czech share is 44.5% of labelled lines on the raw
> label, but that number misleads — its tail is Vietnamese, Estonian, Xhosa and Uzbek, i.e. the
> detector failing. The split acts only on German and French: **5.8%**. The split was built anyway,
> because it is the right rule rather than a large one. (2) Checked against the 2,064 labelled lines
> you and @DanaKriv produced: **12 improved, 1 made worse** (the ✅ block above). It ships inside the
> new rule, which stays switched off; @DanaKriv's 357 new decisions are what it will be checked
> against next.

## 8. Three smaller items

**8.1 Is the raw perplexity thread finished?** ✅ **Answered 2026-09-22 — yes, closed.** The
367,208 lines arrived via filesender and we read them. Nothing further was intended and the thread
is closed.

**8.2 Two things stage 6 settled, one of which you raised.**

Stage 6 — an 87-hour measurement of all 23 rules — finished on 2026-09-22.

* **Your objection about self-graded figures is answered.** You were right. Every verdict this
  issue quoted about which rules matter had been graded by the program against its own output,
  which cannot disagree with itself. All 23 rules are now graded against your 2,064 annotated
  lines. A detail worth having: this run puts the baseline at **40 readable lines lost**, the same
  number stage 8b reports from a completely separate job using a different tool. That is the first
  time two runs in this issue have independently agreed on anything.
* **And it produced a number that affects item 2.** Measured rule by rule against your labels,
  **the short-garbage rule destroys 7 of those 40 readable lines.** *(40 was the baseline then; it
  is 38 since the web-address exception in item 5.)* The rule your patch narrowed is
  itself part of the cost the new rule is being judged against. The short-line rule protects 31 and
  the reference-floor rule protects 10. We are not proposing to act on any of this. It changes how
  the number 40 should be read, and 40 is the number this decision has turned on since July.

One caution if you open the raw table: seven rules score *better* when removed. Five of them move
the result by less than a single annotated line out of 2,064, the run has no significance test
attached at all, and the two that clear that threshold both cost readable lines. It is not a list
of rules to delete, and we have written that into our records so that nobody reads it that way
later.

**8.3 Corrections to things we told you before.**

* We reported that the vocabulary check had "zero effect", then that the result was void. It is now
  properly measured, and it is a **clear rejection**: 212 lines improved against 540 made worse, and
  readable lines lost rising from 40 to 180. It finds 78% of the rubbish, but destroys 14% of all
  good lines to do it — and what it destroys is the archive's own style of writing: site codes,
  inventory numbers, citations, place names with administrative qualifiers. It is not a language
  problem: only 3% of the destroyed lines are German and 55% are Czech.
* We reported the symbol-stripping result as void. It is now measured and has **genuinely no
  effect**: 5 lines improved, 5 made worse.
* We reported the reach of the de-duplication step as 7 groups / 22 lines. That was correct for the
  sample it was measured on. Item 2 above is the full-archive figure and replaces it.
* 🛑 We described `J. Vysoean` and `B/ POSTKRANIAINY SKELET:` as correctly read in § 7, while
  describing them as damaged in Dana's guide. You corrected it; both documents now say damaged.
* 🛑 We called `ssuti`, `ssutí` and `ssutě` confirmed scanning errors in six places across this
  repository. That was our own reading of a frequency table, not anything you had told us. You
  corrected it, and all six now say *old spelling of suť*.


## 9. Opened by your answers — open

Asked in the issue thread after
[comment 61](https://github.com/ufal/atrium-alto-postprocess/issues/30#issuecomment-5783543756),
and numbered there as Q1–Q7. Q1–Q3 are for @DanaKriv alone and are in her guide; the five rows
below involve you (Q5 comes in two parts). One line each is enough, and "no opinion" is an answer.

**A correction first, to comment 61.** It says that listing a word in `setup/word_lists.txt`
`[allowed]` stops the program discarding it. Today that is only partly true. A listed word stops
counting against the line's quality score, so a line made **only** of listed words can no longer be
discarded by the short-line rule. But the new rule's shape tests still read a listed word: with
`ssuti` listed and the rule switched on, `ssuti` alone stays `Clear` while `ssuti vfetennl` goes to
`Trash` on `ssuti`'s doubled first letter (`vfetennl` trips no test). Q5 (b) asks whether that
should change.

| #       | for         | question                                                                                                                                                                                                                                                                                                          |
|---------|-------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Q4**  | both of you | Once § 6 is agreed, what should **every** recognised web or e-mail address be — `Clear` or `Noisy`? `DOMAIN_NOTATION_CATEG` in `setup/config.txt` holds the answer and ships empty. The program cannot tell a damaged address (`e-mail: officeauappmost.cz`) from a correct one, so the category applies to both. |
| **Q5a** | both of you | Which `[allowed]` entries should we switch on? The four you confirmed (`ppole`, `ssuti`, `ssutí`, `ssutě`), all the candidates in the file, a named subset, or none yet? Switching on changes stored categories, so we re-score and report before and after.                                                      |
| **Q5b** | both of you | Should a listed word also be exempt from the new rule's shape tests, so that it can never be the reason a line is discarded?                                                                                                                                                                                      |
| **Q6**  | both of you | The short-line rule can only answer `Trash`. Under § 6, a short damaged line a person can still work out is `Noisy`, but on 1–3 words the program cannot reliably tell. Which mistake is better — `Trash` (re-processed, nothing deleted) or `Noisy`? Or leave it until the 357 decisions are in.                 |
| **Q7**  | you         | ✅ **Answered 2026-09-23: `master`** — and the re-check is done and confirmed: **336/508** on `3b02959`, 15 moved, 11 fixed / 1 broken / 3 wrong either way. It read 335/508 with 2 broken until the lost `S-VIIIb` exemption was restored. See § 3.                                                               |

---

**Thank you — all of it is answered.** Two of your answers corrected us rather than replying to us,
and both were things we had measured and misread rather than things we could not see: `ssut*`
appears in 195 separate documents, and our own annotation guide already called `J. Vysoean` damaged.
That is worth saying because it is the third time in this issue that reading our own output more
carefully would have got there first.

Nothing in §§ 1–8 waits on you. What remains there is @DanaKriv's agreement on § 6 and her 357
decisions — plus the follow-up questions in § 9, which your answers made possible to ask.

## 10. New — Q8

**For you, asked 2026-09-23, and optional.** One line is enough, and "don't know" is an answer. It
switches nothing on: the answer decides whether a measurement is worth running.

**Q8 — In this archive, are `ä`, `ö`, `ü` and `ß` reliable signs of German, or do damaged Czech
scans produce them** — a Czech `á` read as `ä`, or `ů` as `ü`?

**Why we ask.** A line carrying a Czech accent is never judged by the new rule's shape tests: the
accent is taken as proof that the line is real text. German letters get no such protection. An
exception for them was measured on 2026-09-18, then lost together with the `S-VIIIb` one (§ 3),
and we did not restore it: the language split now covers the one line it was fixing
(`Frauenzimmerbad", sämtlic Gesellschastsbäder,`), and the exception reaches much further. On the
822-document corpus your labelled lines come from it would cover **239** of the lines the rule
reaches, **156** of them kept today — readable German such as `Grauer, geschlämmter Ton.`.

If those letters are reliable here, the exception is worth measuring properly. If damaged Czech
often produces them, it would shelter rubbish and we would drop it.

→ *almost always German* / *often misread Czech* / *don't know*
