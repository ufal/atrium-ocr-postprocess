# How to help us decide what the text-quality program keeps and throws away

**For:** @DanaKriv, and anyone else reading the archive's scanned text.
**Time needed:** about two to four hours in total. You can stop at any point and send what you have.
**Issue:** [ufal/atrium-alto-postprocess#30](https://github.com/ufal/atrium-alto-postprocess/issues/30)
**Files you need:** `census.csv`, `sample.csv`, `frame.json`

---

> ## ✅ Ready to start — 2026-09-22
>
> **The earlier notice asking you to wait is withdrawn.** The files have been rebuilt and
> replaced, and these are the ones to work on.
>
> What happened: we measured the risk with one of the program's own safeguards switched off by
> mistake. With it switched on — which is how the program actually runs — **82% of what we were
> going to ask you about cannot be affected at all**, including the single biggest item of the
> old list, `ppole`. The request is now **357 decisions instead of 792**, and most of it is a
> random sample rather than a list of the most common text, because once the protected items are
> removed there is no "most common" left: 96% of what remains occurs exactly once.
>
> Nothing you may already have done is wasted. Any answers from the earlier files still join back.
>
> **One thing is still open, and § 7.2 is where it is written down.** It is a question about what
> `Trash` is for. @david-spacil answered it on 2026-09-22 — **`Trash` means illegible, regardless
> of how useful the line is** — and § 4 gives you the definitions that follow from it. What is
> left is whether you agree; a sentence either way settles it, and it does not block the labelling.
> § 7.1 is now answered too and is kept only in case you want to dissent.
>
> ⏳ **Three more short questions for you are in the issue thread** (Q1–Q3, asked after
> [comment 61](https://github.com/ufal/atrium-alto-postprocess/issues/30#issuecomment-5783543756)):
> the § 7.2 agreement, the § 7.1 dissent, and roughly when a first batch of answers might come. Three
> more (Q4–Q6) are for you and @david-spacil together — what a web address should be, which words
> the new `[allowed]` list should protect, and what the short-line rule should answer when it cannot
> tell. [`README.md`](README.md) lists them all under "Still open".
>
> ⏳ **2026-09-23:** @david-spacil is going through Q1–Q6 with you by e-mail. Once you have answers,
> a one-line summary of them in the issue thread keeps the record in one place.

---

## 1. The short version

We have a program that reads the scanned text of the archive line by line and sorts every line
into one of five boxes: **Clear**, **Noisy**, **Trash**, **Non-text** and **Empty**. Lines in
`Trash` are thrown away. Lines in `Clear` are kept as good text.

Earlier this year you pointed out that the program was throwing away far too much. We changed
it, and it now keeps about **367,000 more lines** than before. That fixed your problem, but it
created a smaller one: some real rubbish is now being kept as good text — we estimate about
**26,000 lines**.

We have written a new rule that should catch that rubbish. Before we switch it on, we need to
know one thing: **does it also destroy text that is worth keeping?**

We cannot answer this ourselves. The program can only compare itself against its own earlier
answers, which proves nothing. We need a person who knows the archive to look at the actual
text and say what each line is.

**That is what we are asking you for.** It is 357 decisions.

---

## 2. Why we are asking you and not solving it in code

This is worth one paragraph, because it explains why the request is small and why it is the
only thing that can help.

The new rule can affect **48,909 lines** across the whole archive. Of those lines, a few dozen
currently have a human opinion attached — far less than one line in a thousand. Every decision we
have made so far therefore rests on about **fifteen lines** where switching the rule on changes
the answer. Fifteen lines is not enough to decide anything about an archive of 113,100 documents,
and no amount of extra programming changes that number. Only a person looking at text changes it.

What programming *did* change is **how many of those 48,909 lines are genuinely at stake**. The
program keeps a dictionary built from the archive's own words, and anything appearing across
enough documents is left alone by the new rule. We had first measured the risk without that
dictionary loaded. With it loaded, the number of lines that are both kept today and threatened by
the rule drops from **37,555 to 6,714**. That is why this request is so much smaller than the
first one.

---

## 3. What is in the two files

The 48,909 lines are not 48,909 different pieces of text. The archive repeats itself: the same
word appears on printed forms in many documents. In total there are **42,248 different pieces of
text**. The repetition is much weaker than it used to be, though, because the items that repeated
most are the ones the dictionary now protects: on average each piece of text appears only **1.1
times**, and 96% of the text at risk appears exactly once.

So we are not asking you to look at lines. We are asking you to look at **each different piece
of text once**. One decision settles every line that contains it.

We have also removed everything where your answer could not change the outcome, apart from **39
deliberate checks** in `census.csv`. Most of the queue is text the program **already** throws away;
the new rule would simply agree with it, so a label there changes nothing — the 39 are there only so
we can see whether it agrees for the right reasons.

| file         |    rows | what it is                                                                                                                    |
|--------------|--------:|-------------------------------------------------------------------------------------------------------------------------------|
| `census.csv` | **157** | The most common at-risk text, every piece of text the program currently answers in two different ways, and 39 checks.         |
| `sample.csv` | **200** | A random selection from the long tail of rare text. These are chosen by computer so that we can calculate a result from them. |
| `frame.json` |       — | A small technical file. **Please send it back with the others.** Without it the 200 rows cannot be turned into a number.      |

`census.csv` settles **18.1%** of the problem in 157 decisions, and `sample.csv` estimates the
rest to within about seven percentage points.

The old version of this file claimed 82.8% in 592 decisions, and that figure was real — but it
was measured over text most of which was never at risk. The items that made the old list
efficient — `ppole`, the `ARCHAIA` company name, `Lepus europaeus`, `vodovod` — are exactly the
ones the dictionary already protects. Take them out and there is no efficient list left, so the
work now rests on `sample.csv` rather than on a list of the most common text.

One row of the 157 often covers several spellings of the same thing: 357 decisions cover **516
different spellings** in total. The `variants` column shows which ones.

---

## 4. What to do

**Fill in the `gold_categ` column. Nothing else.**

Write one of these five words:

| write      | when                                                                                                                             |
|------------|----------------------------------------------------------------------------------------------------------------------------------|
| `Clear`    | You can read this. It does not have to be useful — a web address or a form label counts.                                         |
| `Noisy`    | You can work out what it says, through a mistake. *"Easily decipherable."*                                                       |
| `Trash`    | Nobody can read this.                                                                                                            |
| `Non-text` | **Please leave blank.** See the note below.                                                                                      |
| `Empty`    | There is nothing here.                                                                                                           |

**These three definitions are @david-spacil's, given on 2026-09-22**, and they answer the question
in § 7.2 below. The important word is in the second column of the first row: **it does not have to
be useful.** A correctly scanned web address is `Clear`, even though nobody wants it in a search
index. Whether a line is worth keeping is our problem, not a label.

**Please leave `Non-text` alone.** @david-spacil's reason is a good one: you cannot tell `Non-text`
from `Trash` without looking at the scanned page, and you do not have the page. He labelled only
`Clear`, `Noisy` and `Trash` for exactly that reason, apart from a few hundred early lines where he
did have complete documents. Please do the same. A blank cell is skipped and costs nothing.

**If you are not sure, leave the cell empty.** This is not laziness and it costs us nothing. An
empty cell is skipped completely. A guess is worse than no answer, because we cannot tell the
two apart afterwards and we will build on it. Several of the mistakes in this project came from
somebody filling in a value that looked reasonable.

You may also use the `confidence` and `note` columns for anything you want to tell us. They are
free text and entirely optional. A note saying *"this is an abbreviation used in the 1970s
forms"* is extremely useful to us even when the label itself is obvious.

**Do not change any other column, and do not re-sort or delete rows.** We join your answers back
to the archive using the text in the first column.

---

## 5. The columns, and how to read them

Here is a real row from `census.csv` (row 7):

| column             | value                                          | what it means                                                     |
|--------------------|------------------------------------------------|-------------------------------------------------------------------|
| `text`             | `eaual to:`                                    | The text to judge.                                                |
| `variants`         | `eaual to: \| eaual to: 1106 \| …`             | Other spellings of the same thing, covered by the same decision.  |
| `lines_settled`    | `21`                                           | Your one answer settles 21 lines of the archive.                  |
| `categ_current`    | `Clear:14\|Noisy:7`                            | What the program says **today**. Here it disagrees with itself.   |
| `nearest_attested` | `eaual -> equal (15, edit1)`                   | The closest real word found elsewhere in the archive.             |
| `recoverability`   | `1.00`                                         | How much of the line the archive can reconstruct (0.00 to 1.00).  |
| `clauses`          | `vowel_run`                                    | Which part of the new rule reacted. Technical; you can ignore it. |
| `gold_categ`       | *(empty)*                                      | **Your answer goes here.**                                        |

### About `categ_current`

This is the program's current opinion, and we show it for one reason: where it shows **two
different answers for the same text**, as above, the program is contradicting itself and your
decision resolves that directly. Those rows are all in `census.csv` and are worth doing even if
you do nothing else.

Please **do not** treat `categ_current` as a suggestion. Where it is wrong is exactly what we
are trying to find out, and if the answers simply agree with it we learn nothing.

### About `nearest_attested` and `recoverability`

These come from a dictionary we built out of the archive itself — every word that appears in
several different documents. They are **hints, not conclusions**, and they are unreliable in one
direction in particular:

* A **high** `recoverability` means the archive can reconstruct the words. That is good evidence
  that the line is real text. `eaual to:` is *"equal to:"*.
* A **zero** `recoverability` means only that **the archive has nothing to say about it**. It is
  *not* evidence of rubbish. `Kaukasus` and `Schuhleistenkeilbruchstueck` both score zero and
  both are perfectly good German archaeological words.
* `edit1` in `nearest_attested` means "one letter different". **This is often a coincidence.**
  `Linum` and `ilium` are one letter apart and both are real Latin words. We can put a number on
  how much of the evidence this affects: **90% of the suggestions in these two files are `edit1`**
  (102 of 113; the rest are 10 known scanner confusions and 1 restored accent), and they were
  generated with a setting that our own tooling warns is too
  permissive on an archive this size. We meant to tighten that setting when the files were rebuilt
  and did not, so this has not improved. Please treat `nearest_attested` as a prompt to look,
  never as a reason to agree.

And "recoverable" does not mean "damaged". `ppole` can be reconstructed as `pole`, but David has
told us it is an **abbreviation** for *popelnicová pole*, not a scanning error at all. That
correction changed several of our conclusions, which is a good illustration of why we are asking
you rather than measuring harder.

The opposite case is in the files too. `Dauerleihe` scores **0.00**, which here means only that
this archive has never used the word often enough for us to know it. It is a perfectly good
German word. A score of zero is not evidence against a line.

---

## 6. Where to start, and some real rows

`census.csv` is sorted so that the largest decisions come first — the first row alone covers
305 lines. Please read § 7.2 before working down it.

**None of the examples in the earlier version of this guide are here any more.** `ppole`,
`ARCHAIA`, `Lepus europaeus`, `vodovod` and `Mammalia indet.` are all protected by the archive's
own dictionary, so no answer about them can change anything. They have been removed from the
request. What is left is a different kind of text:

| text                       | lines | the program says                 | our question                                                              |
|----------------------------|------:|----------------------------------|---------------------------------------------------------------------------|
| `Dauerleihe`               |   305 | `Clear:277` `Trash:19` `Noisy:9` | German for *permanent loan*. Scanned correctly. Should it stay `Clear`?   |
| `J. Vysoean`               |   125 | `Noisy:125`                      | ✅ Confirmed damaged — *Vysočan*. Decipherable (`Noisy`) or not (`Trash`)? |
| `Dated=Dated (relatively)` |    47 | `Noisy:47`                       | A form field label, not prose — under § 4, usefulness does not count.     |
| `lenaye`                   |    47 | `Clear:46` `Trash:1`             | Damaged, and we cannot tell from what. Decipherable, or illegible?        |
| `Aa/III 116`               |    24 | `Trash:21` `Noisy:3`             | A find identifier, answered two ways. Which is right?                     |
| `eaual to:`                |    21 | `Clear:14` `Noisy:7`             | Almost certainly *equal to:*. One letter wrong. `Clear` or `Noisy`?       |
| `FEUILLETON.`              |    13 | `Noisy:13`                       | A real word — the feature section of a newspaper. Scanned correctly.      |
| `B/ POSTKRANIAINY SKELET:` |    11 | `Noisy:11`                       | ✅ Confirmed damaged — *POSTKRANIÁLNÍ SKELET*, accents lost.               |

**Two of these are the heart of the request.** `Dauerleihe` and `FEUILLETON.` are ordinary words,
read perfectly by the scanner. The rule flags them because they have three vowels in a row, which
usually means damage, and because a dictionary built from Czech archaeology has never seen either
word. If your answer is that they are `Clear`, that is exactly the result we need.

**The rows marked ✅ are ones @david-spacil has already confirmed as damaged.** You are not
re-deciding those. Your `Clear` or `Noisy` call on them still matters, because that is the
difference between keeping a line and throwing it away.

He has also told us why the three-vowel test exists and where it goes wrong. Czech has no runs of
three vowels, so in Czech such a run is good evidence of damage. German and French words have them
naturally — which is why `Dauerleihe` and `FEUILLETON.` are on this list, and why `J. Vysoean` is
on it for a completely different reason.

`B/ POSTKRANIAINY SKELET:` is the mirror image of your original complaint — damaged but readable,
and currently only just being kept. Under the definitions in § 4 the question is only whether you
can still work out what it says — that makes it `Noisy`, however little the line is worth. If you
think usefulness *should* count, that is a disagreement with § 7.2, and it is worth telling us
before you label rather than expressing it row by row.

---

## 7. Three questions that are not in the files — two are now answered

### 7.1 A policy question about repeated text (answered), kept here for your dissent

The program has a cleaning step that works like this: if the same text appears several times in
one document, all copies are given the **same** answer — whichever answer the majority of them
got.

This means that if the new rule throws away three copies of a word and two copies were correct,
those two correct copies are thrown away as well.

> **@david-spacil answered on 2026-09-22: leave the step as it is.** He also asked us to drop the
> other option we had offered — stopping a bare majority from pushing a readable line into
> `Trash`. Measured across both collections, that option changes **nothing at all**: it reaches
> zero groups. The situation it was written for does not occur.
>
> **This item is closed unless you disagree.** One line is enough if you do.

**And the numbers here were wrong, so they are replaced rather than quietly updated.** An earlier
version of this page said 61,682 groups, 24 good lines lost, 305 rescued, "twelve lines for every
one it costs". That measurement was made with the archive's own dictionary switched **off** — a
setting this pipeline never ships — so it counted repeated lines the new rule can no longer reach.
Measured properly, across both collections in full:

|                                           |                                    |
|-------------------------------------------|------------------------------------|
| groups of repeated text the step votes on | 43,103                             |
| of those, already unanimous               | 43,052                             |
| contested at all                          | 51                                 |
| readable lines the vote pulls down        | 10                                 |
| rubbish lines the vote rescues            | **51**                             |
| **net effect**                            | **+41 lines in the step's favour** |

So it protects about **five** lines for every one it costs, not twelve. We have also measured what
happens if we turn it off: the new rule makes **more** mistakes, not fewer.

**The one thing still open is whether losing those 10 readable lines is a trade the archive
accepts.** If you think it is not, please say so.

### 7.2 The definition question — @david-spacil has answered it, and we need to know if you agree

This one came out of the full-collection measurement and it may matter more than the labels.

> ## ✅ His answer, 2026-09-22 — legibility
>
> *"`Trash` = illegible. Anything legible is `Clear`, easily decipherable is `Noisy` — regardless
> of how useful the line is to us."*
>
> **That is the first of the two options described below**, and it is the one § 4 of this guide
> already tells you to work to. So a correctly scanned web address such as `http://www.arub.cz` is
> `Clear`, not `Trash`.
>
> **We are recording it as his view, not as a joint decision, because you are the other half of
> it.** Nothing stops you labelling in the meantime — what waits on your agreement is how the
> answers get scored, not whether you can start. The rest of this section is the reasoning that
> produced the question, kept so you can disagree with it on the evidence rather than on our
> summary of it.

The definitions we work to are:

* **`Clear`** — no mistakes in the words.
* **`Noisy`** — a person can still tell what it says, through a minor mistake.
* **`Trash`** — nobody can read this.

By those definitions, these lines are not `Trash`. Anyone can read them:

* `http://www.arub.cz` — 5,309 lines, scanned perfectly correctly;
* `e-mail: mhauer@zip-ops.cz`;
* `ARCHAIA Brno o.p.s.` — while `ARCHAIA` on its own is marked `Clear`.

The program used to mark all three as `Trash`. Since then we have taught it to recognise web
addresses and e-mail addresses, so those two now come out as **`Noisy`** instead. That is better,
and it is not right: `Noisy` means *a person can tell what it says, through a minor mistake*, and
there is no mistake in `http://www.arub.cz` at all. `ARCHAIA Brno o.p.s.` is unchanged and is
still `Trash`.

So the question has not gone away — it has only moved. And in fact **77% of everything the
program throws away in this part of the archive has nothing wrong with it at all** — no garbled
letters, no strange symbols. It is readable text that simply is not prose: web addresses, form
labels, inventory codes, company names in page headers.

We think one label is being asked two different questions:

1. **Can a person read this?** — which is what the definitions describe; and
2. **Is this running text worth keeping and searching?** — which is a different question, and the
   one the program seems to be answering in practice.

**The two readings, and his answer is the first of them:**

* If it is *readability*, then a correctly scanned web address is `Clear`, and we should fix the
  program — it is currently discarding several thousand perfectly readable lines.
* If it is *usefulness as text*, then the definitions need rewriting, and there is already a
  category for the second question — **`Non-text`** — which is where footers, URLs and form
  labels would belong.

Either answer is workable and we will do whichever the two of you prefer. What we cannot do is
leave it unsaid. Suppose you start labelling and mark `http://www.arub.cz` as `Clear`. That is the
correct answer under the definitions as written, and under his answer. But if the definition were
still open, the measured accuracy of the new rule would change and nobody could tell whether that
came from the definition or from the rule.

**So the only thing we need from you here is agreement or dissent — a sentence either way.** If
you agree, § 4 already has the definitions you work to and nothing changes for you. If you do not,
say so before you start and we will settle it first.

### 7.3 A re-review we owe you

In July you reviewed a batch of documents with rotated pages and found differences in 95
documents from ARÚP and 10,258 from ARUB. That review compared against the batch we had sent
you, not against a fresh run of the corrected program. We are producing a fresh run now. **You
do not need to do anything yet** — we will send it, and then the question is whether those
differences are still there.

---

## 8. What happens to your answers

1. We join them back to the archive lines, and check the join before we use it.
2. We compare the new rule against your labels and count how many lines it gets **wrong**, not
   how well it scores. (We used a score for a while. It recommended the worst setting we tried.)
3. We decide whether to switch the rule on, and we publish the numbers either way.

**Nothing you write changes the program directly.** No label is applied automatically. If your
answers say the rule is harmful, we do not switch it on — that is the point of asking.

If anything in the files is unclear, or a row is impossible to judge without seeing the page it
came from, please say so rather than working around it. We can send the surrounding lines for
any row.

Thank you — this is the part of the work that nothing else can replace.
