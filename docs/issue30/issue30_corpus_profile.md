# What the archive actually looks like to the text-quality program

**For:** @david-spacil and @DanaKriv
**Issue:** [ufal/atrium-alto-postprocess#30](https://github.com/ufal/atrium-alto-postprocess/issues/30)
**Why this exists:** every other document in this issue is about a decision. This one is about
the archive itself. The stage-8 run was the first to look at **all 113,100 documents** rather than
a sample of 822. What it found says more about the archive than about the program.

Nothing here asks you for anything. The two documents that do are
[`issue30_annotation_guide.md`](issue30_annotation_guide.md) (@DanaKriv) and
[`issue30_review_request.md`](issue30_review_request.md) (@david-spacil).

---

## 0. The one-paragraph version

The program sorts every scanned line into `Clear`, `Noisy`, `Trash`, `Non-text` or `Empty`. We
have been trying to switch on a rule that would recover rubbish currently sitting in `Clear`. To
size the risk we measured every short line in both collections — 56.6 million lines read, 7.5
million in scope, **48,909 that the rule can reach**, of which **6,714 are lines the program
keeps today**. Those 6,714 are the only ones a decision can change.

**The largest things the rule would discard are not scanning errors.** The biggest single item is
`Dauerleihe`, German for *permanent loan*, on 286 lines that are scanned perfectly. After it come
a damaged personal name, a form label, and `FEUILLETON.` — another ordinary word, read correctly.
That is not a bug in the rule so much as a fact about the archive: **its characteristic text is
not prose**, and most automatic quality tests are built for prose.

**This section was rewritten on 2026-09-22**, after the measurement was repeated in the
configuration the program actually runs in. The first version of it described a much larger and
quite different set of text. What changed and why is in § 3.

---

## 1. The archive is two collections, and they do not write the same way

Document identifiers carry their collection and their year, so the corpus can be split without
anyone labelling anything.

|                             | ARUP (`CTX…`) | ARUB (`MTX…`) |
|-----------------------------|--------------:|--------------:|
| documents                   |        65,424 |        47,677 |
| `Mammalia` (zooarchaeology) |   1,142 lines |           159 |
| `Lepus europaeus`           |           902 |            92 |
| `ppole`                     |        **11** |    **15,662** |
| `http://www.arub.cz`        |             0 |         5,309 |

The osteological vocabulary lives in ARUP; `ppole` and the footer URLs are ARUB. This matters
beyond bookkeeping, because it broke the last idea we had for telling abbreviations from scanning
errors automatically — see §4.

## 2. The modern half of the archive is a different kind of document

This issue is titled *"Algorithm change for 2010+ years documents"*. The full-collection pass is
the first measurement that shows why that framing was right.

|   decade | lines the rule reaches | of those, currently kept as good text |      rate |
|---------:|-----------------------:|--------------------------------------:|----------:|
|     1950 |                  6,444 |                                 2,493 |     38.7% |
|     1970 |                  4,902 |                                 1,177 |     24.0% |
|     1990 |                  5,353 |                                 1,121 |     20.9% |
|     2000 |                 14,593 |                                 5,508 |     37.7% |
| **2010** |             **40,926** |                            **20,083** | **49.1%** |
|     2020 |                 16,489 |                                 3,817 |     23.1% |

**Two thirds of the entire risk sits in documents from 2010 onwards.** And the *reason* lines get
flagged changes completely across that boundary:

|                                | pre-2010 | 2010 onwards |
|--------------------------------|----------|--------------|
| vowel run (`oueussd`)          | 76%      | 23%          |
| doubled first letter (`ppole`) | 8%       | **66%**      |
| too few distinct letters       | 14%      | 9%           |

Older documents are typed or printed prose that the scanner damaged. **Newer documents are forms**
— structured excavation records with short field values, one or two words per line: `KULTURA:
ppole`, `Čtverec: 13/14 Sonda: - Jiné: -`, `sonda/kontext: JZ`. A rule written to catch damaged
*words* meets, in the modern half of the archive, a population of *field values*, and 96% of what
it flags there is a single legitimate abbreviation.

`ppole` itself has a birth date, which is the clearest evidence that it is a convention rather
than damage:

| decade | 1990 | 2000 |       2010 |  2020 |
|--------|-----:|-----:|-----------:|------:|
| lines  |    2 |   45 | **13,144** | 2,482 |

## 3. What the rule actually reaches, once you look at all of it

> **The first table below is out of date on purpose.** It is what the rule appeared to reach when
> the archive's own dictionary was switched off by mistake. It is kept because the first
> annotation request was built from it, and because the contrast with § 3b is the most useful
> thing in this document.

With the dictionary off, of the 36,744 lines that are currently kept as good text and would be
newly discarded:

| what it is                           |  lines | distinct strings |     share |
|--------------------------------------|-------:|-----------------:|----------:|
| `ppole` and its spellings            | 15,707 |               23 | **42.7%** |
| everything else                      | 14,974 |            9,209 |     40.8% |
| `ARCHAIA` — the excavator's own name |  3,915 |              196 |     10.7% |
| Latin species names                  |  1,737 |               82 |      4.7% |
| web addresses and e-mail             |    411 |              207 |      1.1% |

**Roughly 59% is text that was read correctly.** An abbreviation for *popelnicová pole*, a company
name in a page header, `Lepus europaeus`, `Triticum monococcum`, `http://www.arub.cz`. The
question for that 59% is never "did the scanner get this right". It plainly did. The question is
"is this the kind of text the project wants to keep" — which is a different question, and a human
one.

**The good news, and it is genuinely good:** the program already has a mechanism for this. It
builds a dictionary from the archive's own words, and anything appearing across enough documents
is left alone. **The table above was measured with that dictionary switched off by mistake.** With
it switched on, every class in it except the long tail disappears: the risk falls from 37,555
lines to **6,714**, a reduction of 82%. The annotation request has been re-cut from the smaller
set and is now with @DanaKriv.

### 3b. What the rule reaches once the dictionary is on

This is the list that matters. Measured 2026-09-22 over both collections, with the program
configured the way it ships:

| what it is                                              | lines | share of the risk |
|---------------------------------------------------------|------:|------------------:|
| `Dauerleihe` and its spellings — German, read correctly |   286 |              4.3% |
| `J. Vysoean` — a personal name, damaged                 |   125 |              1.9% |
| `Dated=Dated (relatively)` — a form label               |    47 |              0.7% |
| everything else, almost all of it appearing once        | 6,256 |             93.2% |

**The shape of the problem has changed completely.** Before, a handful of enormous repeated items
carried most of the risk and could be settled in a few decisions. Now **96% of the text at risk
appears exactly once**, and even the 500 most common pieces of it cover only 22% of the lines at
risk. There is no efficient list left to work down.

### 3c. Why a correctly scanned German word is at risk at all

This is worth understanding, because the same thing will happen again.

The rule looks for shapes that usually mean damage. One of them is **three vowels in a row**.
`Dauerleihe` has *aue*; `FEUILLETON` has *eui*; the name `Vysoean` has *oea* — though that one is a
damaged `Vysočan` (@david-spacil, 2026-09-22), so the test catches it rightly. The dictionary
is supposed to rescue real words from that test — but the dictionary is built from **this
archive's own text**. It therefore protects whatever this archive says often, and offers nothing
to a word that is perfectly correct but rare *here*.

German museum vocabulary is exactly that. So are French loanwords and foreign place names. The
program has never seen `Dauerleihe` often enough to know it, and it cannot tell the correct
spelling apart from the damaged ones — `auerleihe`, `Bauerleihe`, `Jauerleihe` — because it has
never seen any of them.

**There is a single setting behind this**, and @david-spacil has now told us it is the wrong lever.

The rule can be told to require four vowels in a row instead of three. That cuts what it would
discard by more than half. We first wrote that it would spare "every correctly-read item on the
list" and named `J. Vysoean` and `B/ POSTKRANIAINY SKELET:` among them. **That was wrong, and he
corrected it.** Both are scanning errors — `Vysočan` and `POSTKRANIÁLNÍ SKELET` — so the
three-vowel test catches them correctly and four would let them go. The honest version: four spares
two words we can confirm are correct, and releases a good deal of real damage with them.

**His answer points at the real problem.** Czech has no runs of three vowels, so in Czech such a
run really is good evidence of damage. German and French words have them naturally. The test is a
fact about one language being applied to three, which is why blunting it helps the wrong cases:

> *"For Czech, 3+ vowels in a row is a good rule – Czech has no triphthongs. For German and French
> it does damage. So either split by language, or try 4+."*

The program already records a detected language for every line, so splitting the rule by language
is possible. It is not a setting, though — that language is not currently passed to the part of
the code that makes this decision — so it is a change to be measured rather than switched on. The
first measurement is simply counting how many of the lines at risk are detected as not Czech.

✅ *Done since, 2026-09-22: the split is built and measured — against the 2,064 labelled lines it
improves 12 and makes 1 worse, with total mistakes and readable lines lost both down — and it ships
inside the new rule, which is still switched off. Simply requiring four vowels everywhere was
measured too, and is not worth doing.*

## 4. The abbreviation problem has no automatic solution, and we now know that for certain

> **Corrected 2026-09-22.** This section used to call `ssuti`, `ssutí` and `ssutě` scanning errors.
> @david-spacil has since told us they are **an old way of spelling the word *suť***, so they are
> real language. The count is not one legitimate spelling against seven errors; it is **four
> against four**. The conclusion below is unchanged — no counting method separates the two classes
> — but the reason is now simpler, and it is at the end of the section.

`ppole` is an OCR-plausible shape (a doubled first letter) that is actually an abbreviation. We
tried three ways to separate that class from genuine doubled-letter scanning errors like `vvkop`:

1. **How much commoner is the base word?** — `ppole` sits mid-pack among the eight doubled-letter
   tokens. No.
2. **How many documents does it appear in?** — on 822 documents this separated cleanly (35 against
   8 or fewer). On all 113,100 it collapses to 229 against 195. No.
3. **Is it concentrated in one collection?** — the idea being that a scanner artefact belongs to
   the machine that made it, while an abbreviation is a shared convention. Measured:

| spelling    |    ARUP |    ARUB |                            |
|-------------|--------:|--------:|----------------------------|
| **`ppole`** |   **3** | **226** | abbreviation, ARUB's forms |
| **`ssuti`** | **152** |  **43** | old spelling, ARÚP's forms |
| `vvkop`     |       1 |      29 | scanning error             |
| `jjámy`     |       2 |       8 | scanning error             |

**This is where the answer changed the reasoning rather than just a label.** We used to read the
table as an inversion: the one legitimate spelling was the most one-sided of the eight, so
concentration pointed exactly the wrong way. With `ssuti` now known to be real language, the table
says something much plainer.

**Both kinds of real language are house conventions, and each house is a different one.** `ppole`
belongs to ARUB's forms; `ssut*` belongs to ARÚP's. The genuine scanning errors are small and
appear in both. So concentration cannot separate real language from damage, because what it
actually separates is *institutions* — and each institution has its own vocabulary.

**So no counting method works.** This is why @david-spacil's reading of those eight tokens was not
a formality. At this scale it was the only evidence there was, and it reversed three of the eight.

## 5. The archive's register is abbreviated, coded and full of proper nouns — and that breaks dictionary tests

We tested a change that would require a line's words to be found in the archive's own vocabulary
before calling the line good. It finds a lot of genuine rubbish — but it destroys **540 lines of
perfectly good text for every 212 it fixes**, and it multiplies the number of good lines lost 4.5×
(40 → 180).

The expectation was that this would be a language problem: a Czech-centric dictionary convicting
the archive's German. **It is not** — 3% of the destroyed lines carry German markers, 55% carry
Czech ones. What it actually destroys is the archive's characteristic *register*:

```
Bezmírov /s.o. Kroměříž/.              Málek, J., 1966: Vegetační vymezení lesních oblastí…
Věc : Stará Kouřim / Kolín/            větší soubor z eneol.výš.sídliště - publ. Masek, AR IV 1962
Čtverec: 13/14 Sonda: - Jiné: -        NASEZN. 1I/650/7086
sonda/kontext: JZ                      V Hradci Králové, 27.111.1974
J-IXb                                  Dra I.L. Červinky.
```

Site names with administrative qualifiers, form fields, bibliographic citations, inventory codes,
dates, personal names. Median length: three words. **A dictionary has nothing to say about any of
it, and this is the archive's most characteristic and arguably most valuable content** — the part
that makes a find retrievable. Any future proposal of the form "check the words against a word
list" runs into this, and should be measured against it before it is believed.

A related asymmetry worth knowing, because it appears in the annotation files: the dictionary
counts *documents*, not occurrences. `Dauerleihe` appears on 267 lines but in very few documents,
so it counts as unattested. Anything concentrated in a handful of documents — a museum loan
register, a particular form's labels — looks unattested however often it occurs.

## 6. The program currently answers the same string in several ways

Two measurements worth having in mind when reading any per-line quality figure.

**The same text, in one document, answered both ways.** `Mammalia indet.` appears **416 times in a
single document** and is stored as `Trash` 209 times and `Noisy` 207 times. Identical text, same
document, near coin-flip.

**The same organisation, answered three ways depending on typesetting:**

|                           | stored as                                |
|---------------------------|------------------------------------------|
| `ARCHAIA`                 | `Clear` 1,008 · `Trash` 116 · `Noisy` 52 |
| `ARCHAIA Brno o.p.s.`     | `Noisy` 680 · `Trash` 441                |
| `Archaia Brno o.p.s.`     | `Noisy` 359                              |
| `ARCHAIA Olomouc, o.p.s.` | `Noisy` 183 · `Trash` 6                  |

Neither is a scandal — these lines sit near a threshold, and small differences in surrounding
context push them either way. But it is why the annotation request asks about *strings* rather
than lines, and why one answer is allowed to settle many lines at once.

## 7. The finding with the widest consequences: `Trash` is being asked two questions at once

The definitions this project works to are:

* **`Clear`** — no mistakes in the words.
* **`Noisy`** — a human can recognise the meaning through a minor mistake.
* **`Trash`** — nobody can read this.

Measured against those definitions, the current `Trash` bucket does not match. **76.9% of the
flagged `Trash` lines contain no repeated-character run and no unusual glyph at all.** Concretely,
the program used to store as `Trash`:

* `http://www.arub.cz` — 5,309 lines, read correctly, perfectly legible;
* `e-mail: mhauer@zip-ops.cz` — 181 of its 241 lines;
* `ARCHAIA Brno o.p.s.` — 316 lines, while calling `ARCHAIA` `Clear`.

By the definition above, none of those is `Trash`. Anyone can read them.

**Since this was written, the first two have moved.** The program now recognises web and e-mail
addresses and calls them `Noisy` rather than `Trash`. That is an improvement and it is still not
right, because `Noisy` means *readable through a minor mistake* and there is no mistake in a
correctly scanned web address. `ARCHAIA Brno o.p.s.` has not moved and is still `Trash`.

> ## ✅ Answered 2026-09-22 by @david-spacil, pending @DanaKriv
>
> * **`Trash` = illegible.** Anything legible is `Clear`; easily decipherable is `Noisy` —
>   **regardless of how useful the line is to us.**
> * **Delete or scan again?** Scan again. Most of it is probably a handwriting-recognition
>   candidate.
> * **`Non-text`** cannot be told from `Trash` without the scanned page, so it is out of scope for
>   annotation.
>
> **So legibility wins over usefulness**, and `http://www.arub.cz` is `Clear`. The program's move
> from `Trash` to `Noisy` was the right direction and one step short.
>
> One thing this resolves in the program's favour: the main `README.md` says `Trash` "should be
> re-processed by another OCR tool", which is exactly his answer on delete-versus-re-OCR. Only the
> legibility half of the two descriptions needed reconciling.

What has happened is that one label is answering two different questions:

1. **Can a human read this?** — legibility. That is what the definitions describe.
2. **Is this running text worth keeping and indexing?** — usefulness. A URL, a form label, an
   inventory code and a species name are all perfectly legible and none of them are prose.

We first thought the second question already had a home — **`Non-text`**. Checking the documents
properly, it does not, and that turned up a second problem we should have found sooner.

**The five categories are written down in two places, and the two do not agree.** The annotation
guide defines them by whether a person can read the line. The main `README.md` defines them by what
the program did and what should happen to the line next. Three conflicts matter:

|               | the annotation guide says                                               | `README.md` says                                                                                                                                              |
|---------------|-------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `Trash`       | "scanning rubbish — nothing is lost by deleting it"                     | "should be re-processed by another OCR tool"                                                                                                                  |
| `Non-text`    | "not language at all — a table border, a page decoration, a ruler mark" | whatever the pre-filter caught: too short, too few different characters, under 30% letters — and it is meant to be searched for **site and find identifiers** |
| a legible URL | `Clear`                                                                 | can be `Trash`                                                                                                                                                |

The `Trash` row matters to you as data providers. **"Delete it" and "re-scan it with another OCR
tool" are not the same instruction.** Which one is meant changes what you would do with the output.

The `Non-text` row is why our easy answer fails. The program already puts real, meaningful content
there — an inventory number such as `A123/2024` is `Non-text` today, and the README says that
category "may be checked for identifiers of finds/sites". It is not an empty shelf we can move web
addresses onto.

**So this needs a decision from you two, and it should come before the annotation.** The question
is the first one. **Is the label about whether a person can read the line? Or about whether the
line is useful as text?**

* If the answer is *readability*, then a correctly scanned URL is `Clear`. The program is
  mislabelling several thousand lines, and the quality figures will move once that is fixed.
* If the answer is *usefulness*, then the definitions need rewriting — and we will need a new home
  for footers, URLs and form labels, because `Non-text` is already occupied.

Either answer works. What does not work is leaving it unsaid. An annotator applying the written definitions will mark
`http://www.arub.cz` as `Clear`. The measured accuracy of the rule will then change, and nobody
will be able to tell whether that came from the annotator or from the definition.

Once you decide, we will make the program, the annotation guide and the README agree with one
another. At the moment there are three descriptions of five categories.

## 7b. Which rules actually protect the readable text, and which destroy it

Added 2026-09-22, after a long measurement (87 hours) finished. It is the first
time we can say what each individual rule does to text **a human called readable**,
rather than what it does to the program's own earlier opinion.

The program applies 23 rules. Switching each one off in turn and re-scoring
against your 2,064 annotated lines gives this:

| rule                       | what it does to lines a human called `Clear` |
|----------------------------|----------------------------------------------|
| the short-line rule        | **protects 31**                              |
| the reference-floor rule   | protects 10                                  |
| **the short-garbage rule** | **destroys 7**                               |
| the hard-sweep rule        | destroys 2                                   |
| two others                 | destroy 1 each                               |

**The third row is the one worth knowing.** The short-garbage rule is the rule
this whole issue has been about — the one your patch narrowed in July. It is
itself responsible for **7 of the 40** readable lines the program lost when this
was measured (before the web-address change; the baseline is 38 since). The
figure of 40 has been the yardstick for every decision in this issue. It turns
out that part of it is produced by the very rule we have been trying to make
safer. It is not a fixed background cost.

**One caution about the same measurement, because the table it comes from looks
more decisive than it is.** Seven of the 23 rules score *better* when removed. It
is tempting to read that as "delete seven rules". We do not think it is, for a
plain reason. The measurement has no statistical test attached. And five of those
seven move the score by less than a single annotated line out of 2,064. That is
noise, not a finding. Only two move enough to be worth a proper test, and both of
them **cost** readable lines when removed. Nothing here is a reason to remove
anything; we are recording it so that nobody reads the raw table later and
concludes otherwise.

**And one thing the measurement got wrong about itself**, which is worth a
sentence because it concerns the rule at the centre of this issue. The run
reported the new shape-witness rule as *dead code, safe to delete*. It is not
dead — it is switched off, so of course it never fired. The same rule had already
been measured reaching tens of thousands of lines. The tool has been fixed so it now says
"switched off" instead of "dead", but it is a fair illustration of why we keep
re-reading these runs rather than trusting their summaries: the instrument
recommended deleting the feature the project has spent two months building.

## 8. What follows from all of this

* **The risk of switching the new rule on is much smaller than the raw numbers suggest**, because
  the archive's own dictionary already protects the abbreviation, the organisation name, the
  species names and the footer URLs. That measurement has now been made properly, and it removes
  82% of the apparent risk.
* **What is left is a genuine long tail** — 6,714 lines across 5,563 different strings, 96% of
  which occur exactly once. That is the part where a human eye is irreplaceable, and it is what
  the annotation request samples.
* **Two things could not be settled by any amount of computation, and @david-spacil has now settled
  both.** The eight doubled-letter tokens: four are real language, four are errors (§ 4). And
  `Trash` means **illegible**, not not-prose (§ 7), pending @DanaKriv's agreement. Neither answer
  came from more measuring, and in both cases the measurements we already had were pointing the
  right way without us reading them that way.
* **The next question is a language question, not a threshold.** The test that accounts for most
  of what the rule would discard looks for three vowels in a row, which is sound Czech and wrong
  for German and French (§ 3c). *It has since been answered that way: the language split is built
  and measured (12 improved, 1 made worse) and ships switched off with the rule.*
* **The rule we have been narrowing is part of the cost, not just the fix.** The
  short-garbage rule destroys 7 of the 40 readable lines the program lost when
  this was measured (38 since the web-address change); the
  short-line rule saves 31. Both numbers are new, and neither was available
  before the annotations were scored against the rules individually.
* **Automatic quality tests built for prose will keep misfiring on this archive.** Its most
  characteristic text is short, abbreviated, coded and proper-noun-heavy. That is worth stating
  once, plainly, because it will come up again the next time a vocabulary or language-model signal
  is proposed.

---

_Measured 2026-09-22 from the repeated stage-8 run, the first one made with the archive's own
dictionary loaded: 113,100 documents, 56,599,631 lines read, 7,477,924 in scope, **48,909 reached
by the rule**, **6,714 of them kept today**. Sections 0 and 3 were rewritten on that date; § 7b was
added on 2026-09-22 from a separate 23-rule review scored against the 2,064 annotated lines; the
remaining sections were measured on 2026-09-21 and are unaffected by the repeat, because they
describe the archive rather than the rule. Every figure here is recomputed from the files attached
to issue #30 and is reproducible from them. The technical write-up is
`agent_dev_logs/digests/30.digest.md`, § "Stage 8 f/g re-run read against its own delivery"._
