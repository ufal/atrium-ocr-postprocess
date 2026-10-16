"""
tests/test_service_hardening.py
===============================

The service scores a line the way the batch pipeline does, and one request at a time on the GPU.

Four defects, found reading the production path for the 2026-10-16 release:

* **No pre-filter.** `/process` and `/score_record` called the shared scorer on every line, skipping the
  CPU pre-filter the batch pipeline runs first (classify_TEXT.process_document). `1998` came back `Trash`
  (batch: `Non-text`), `XIV.` and `A1739` came back `Clear` (batch: `Non-text`), `poh1ed` was not repaired
  to `pohled`. A born-digital page's Trash share -- what atrium-digital-convert routes on -- moved with it.
* **No limit on concurrent GPU work.** Every request runs in its own thread; the GPU memory a perplexity
  batch or a LayoutReader window needs is sized for one of them (9.5 GB at 32 lines, on a card shared with
  page-classification). Two at once doubled the peak and scored the batches 99999.
* **`/health?deep=true` could not fail.** It tested `device is None`, which the constructor sets to "cpu".
* **A licence left out.** An API run named neither the perplexity model nor, for an ALTO page, LayoutLMv3
  (CC BY-NC-SA 4.0), so its paradata said CC BY-NC 4.0 where a batch run of the same file says BY-NC-SA.

The perplexity model and FastText are stubbed, so no model is loaded.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import classify_TEXT as LC
import text_util as tu
from service import text_api
from service import text_inference as ti
from service.text_inference import NO_LANG, TextModelManager


def _manager(lang="ces_Latn", score=0.9, by_text=None):
    """A manager whose FastText answers *lang*/*score* for every line (or per lowercased line in *by_text*)."""
    m = TextModelManager()
    ft = MagicMock()

    def predict(texts, k=1):
        label, conf = (by_text or {}).get(texts[0], (lang, score))
        return [[f"__label__{label}"]], [[conf]]

    ft.predict.side_effect = predict
    m.ft_model = ft
    m.ppl_model = MagicMock()
    m.ppl_model.config.max_position_embeddings = 1024
    m.ppl_tokenizer = MagicMock(side_effect=lambda text: {"input_ids": text.split()})
    m.device = "cpu"
    return m


@pytest.fixture
def lm(monkeypatch):
    """The perplexity model, stubbed: records every batch it is asked about."""
    seen = []

    def fake(texts, *_a):
        seen.append(list(texts))
        return [60.0] * len(texts)

    monkeypatch.setattr(ti, "calculate_perplexity_batch", fake)
    return seen


# ── the pre-filter ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "line, category",
    [("1998", "Non-text"), ("XIV.", "Non-text"), ("A1739", "Non-text"), ("- 14 -", "Non-text"), ("   ", "Empty")],
)
def test_a_line_the_prefilter_settles_costs_no_model_call(line, category, lm):
    m = _manager()
    [entry] = m._classify_lines([line])
    assert entry["category"] == category
    assert (entry["quality_score"], entry["perplexity"]) == (0.0, 0.0)
    assert (entry["lang"], entry["original_lang"]) == (NO_LANG, NO_LANG)
    assert (entry["lang_score"], entry["orig_lang_score"]) == (0.0, 0.0)
    assert entry["line_num"] == 1
    assert lm == [], "the perplexity model was never asked about it"
    m.ft_model.predict.assert_not_called()


def test_these_were_the_answers_before(lm):
    """What the unfiltered service said, for the record: the shared scorer alone, on the same lines."""
    KB = frozenset({"ces", "deu", "eng", "fra", "pol", "ita", "slk"})
    before = {}
    for line in ("1998", "XIV.", "A1739"):
        sig = LC.score_line(
            text_content=line,
            original_text=line,
            original_lang="ces_Latn",
            original_lang_score=0.9,
            perplexity=60.0,
            known_lang_bases=KB,
            expected_langs=["ces", "deu", "eng"],
        )
        before[line] = sig["categ"]
    assert before == {"1998": "Trash", "XIV.": "Clear", "A1739": "Clear"}
    after = {e["text"]: e["category"] for e in _manager()._classify_lines(list(before))}
    assert after == {"1998": "Non-text", "XIV.": "Non-text", "A1739": "Non-text"}


def test_a_mixed_batch_keeps_its_order_and_numbers_every_line(lm):
    m = _manager()
    lines = ["První řádek textu", "1998", "Druhý řádek textu", "   ", "poh1ed"]
    out = m._classify_lines(lines)
    assert [e["line_num"] for e in out] == [1, 2, 3, 4, 5]
    assert [e["category"] for e in out][1] == "Non-text" and out[3]["category"] == "Empty"
    assert [e["text"] for e in out] == ["První řádek textu", "1998", "Druhý řádek textu", "", "pohled"]
    # Only the lines that go on are scored, on the repaired text, and FastText sees the same.
    assert lm == [["První řádek textu", "Druhý řádek textu", "pohled"]]
    assert m.ft_model.predict.call_count == 3


def test_the_ocr_repair_changes_the_text_but_not_what_the_noise_measures_are_read_from(lm):
    """`poh1ed`: classified as `pohled`; garbage density and the vowel ratio ride the submitted line,
    as in the batch pipeline (`score_line`: the pre-repair line, so cleaning cannot hide noise)."""
    seen = {}
    real = ti.score_line

    def spy(**kw):
        seen.update(text_content=kw["text_content"], original_text=kw["original_text"])
        return real(**kw)

    with patch.object(ti, "score_line", spy):
        _manager()._classify_lines(["poh1ed"])
    assert seen == {"text_content": "pohled", "original_text": "poh1ed"}


@pytest.mark.parametrize(
    "line, lang, score, ppl",
    [
        ("Opomenulé nebo opozděné ohlášení trestů se pokutou peněžitou", "ces_Latn", 1.0, 153.0),
        ("Die Ausgrabung wurde von dem Museum durchgeführt.", "deu_Latn", 0.98, 60.0),
        ("oueussd", "isl_Latn", 0.9163, 4820.0),
        ("ClAŕ,", "ces_Latn", 0.40, 850.0),
        ("http://www.arub.cz", "eng_Latn", 0.40, 2000.0),
        ("Fundort unbekannt", "nld_Latn", 0.30, 1500.0),
        ("poh1ed", "ces_Latn", 0.8, 300.0),
    ],
)
def test_the_service_answers_what_the_batch_pipeline_writes(line, lang, score, ppl, monkeypatch):
    """Per line, the service and `classify_TEXT.process_document` agree: the same pre-filter, then the
    same scorer with the same model values. (The document passes that follow in batch are not per line.)"""
    monkeypatch.setattr(ti, "calculate_perplexity_batch", lambda texts, *a: [ppl] * len(texts))
    m = _manager(by_text={tu.pre_filter_line(line)[1].lower(): (lang, score)})
    [api] = m._classify_lines([line])

    category, clean = tu.pre_filter_line(line)
    expected_langs, known = ti._lang_config()
    batch = LC.score_line(
        text_content=clean,
        original_text=line,
        original_lang=lang,
        original_lang_score=score,
        perplexity=ppl,
        known_lang_bases=known,
        expected_langs=expected_langs,
    )
    assert category == "Process"
    assert (api["text"], api["category"]) == (clean, batch["categ"])
    assert api["quality_score"] == round(batch["quality_score"], 4)
    assert (api["lang"], api["lang_score"]) == (batch["lang"], round(batch["lang_score"], 4))


def test_the_fast_track_values_are_the_batch_csvs():
    """The batch pipeline writes these columns for a pre-filter answer (`_fast_track_row`); the service
    entry carries the same values under its own names."""
    row = LC._fast_track_row("f", "1", 1, "1998", "1998", "", "", "Non-text")
    csv_row = dict(zip(LC.CSV_HEADER, row, strict=True))
    entry = ti._fast_track_entry("1998", "Non-text")
    assert entry["category"] == csv_row["categ"] == "Non-text"
    assert entry["quality_score"] == float(csv_row["quality_score"])
    assert (entry["lang"], entry["original_lang"]) == (csv_row["lang"], csv_row["original_lang"]) == (NO_LANG,) * 2
    assert entry["perplexity"] == float(csv_row["perplex"])
    assert entry["word_weird"] == float(csv_row["word_weird"])
    assert entry["garbage_density"] == float(csv_row["garbage_density"])


# ── the record gets no language for a line none was identified for ──────────────────────────


def test_a_prefilter_answer_contributes_no_language_to_the_record():
    result = {"cleaned_lines": [ti._fast_track_entry("1998", "Non-text") | {"line_num": 1}]}
    [record] = text_api._lines_records_from_result(result, "1")
    assert record["categ"] == "Non-text" and record["quality_score"] == 0.0 and record["text"] == "1998"
    assert "lang" not in record, "'N/A' is not a language; the batch path omits it too"


def test_the_page_score_is_the_mean_of_the_lines_a_model_judged():
    rows = [
        {"page": "1", "line": 1, "categ": "Clear", "quality_score": 0.9},
        {"page": "1", "line": 2, "categ": "Non-text", "quality_score": 0.0},
        {"page": "1", "line": 3, "categ": "Empty", "quality_score": 0.0},
        {"page": "1", "line": 4, "categ": "Noisy", "quality_score": 0.7},
        {"page": "2", "line": 1, "categ": "Non-text", "quality_score": 0.0},
    ]
    pages = {p["page"]: p for p in text_api._page_records_from_lines(rows)}
    assert pages["1"]["quality_score"] == 0.8 and pages["1"]["quality_band"] == "Clear"
    assert "quality_score" not in pages["2"] and "quality_band" not in pages["2"], "nothing was judged there"


# ── one GPU pass at a time ───────────────────────────────────────────────────────────────────


def test_concurrent_requests_queue_for_the_gpu(monkeypatch):
    state = {"active": 0, "peak": 0, "calls": 0}
    guard = threading.Lock()

    def slow(texts, *_a):
        with guard:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            state["calls"] += 1
        time.sleep(0.03)
        with guard:
            state["active"] -= 1
        return [60.0] * len(texts)

    monkeypatch.setattr(ti, "calculate_perplexity_batch", slow)
    m = _manager()
    lines = [f"řádek textu číslo {n}" for n in range(6)]
    threads = [threading.Thread(target=m._classify_lines, args=(lines,)) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert state["calls"] == 5
    assert state["peak"] == 1, "two perplexity passes were on the GPU at once"


def test_the_layout_reader_holds_the_same_lock(tmp_path, monkeypatch, lm):
    m = _manager()
    m.layout_model = MagicMock()
    held = []

    def reader(lines, boxes, model, device, notes=None):
        held.append(m._gpu_lock.locked())
        return lines, boxes

    xml = tmp_path / "p.xml"
    xml.write_text(
        '<alto><Layout><Page WIDTH="100" HEIGHT="100"><PrintSpace><TextBlock>'
        '<TextLine HPOS="1" VPOS="1" WIDTH="50" HEIGHT="5"><String CONTENT="první" HPOS="1" VPOS="1" WIDTH="20" HEIGHT="5"/></TextLine>'
        "</TextBlock></PrintSpace></Page></Layout></alto>",
        encoding="utf-8",
    )
    with patch.object(ti, "boxes2inputs", MagicMock()), patch.object(ti, "_run_layout_reader", reader):
        m.process_alto(str(xml))
    assert held == [True]
    assert not m._gpu_lock.locked(), "released again"


def test_the_lock_is_released_when_scoring_raises(monkeypatch):
    def boom(texts, *_a):
        raise RuntimeError("not a memory error and not caught here")

    monkeypatch.setattr(ti, "calculate_perplexity_batch", boom)
    m = _manager()
    with pytest.raises(RuntimeError):
        m._classify_lines(["první řádek textu"])
    assert not m._gpu_lock.locked()


# ── /health?deep=true ────────────────────────────────────────────────────────────────────────


def test_deep_health_fails_until_the_models_are_loaded():
    m = TextModelManager()
    assert m.device == "cpu", "the constructor sets it: it says nothing about the load"
    with patch.object(text_api, "text_manager", m):
        assert text_api._deep_health() == "text models not loaded"
        m._models_loaded = True
        assert text_api._deep_health() == "text models not loaded", "flag alone is not enough"
        m.ppl_model, m.ft_model = object(), object()
        assert text_api._deep_health() is None


# ── the licences an API run records ──────────────────────────────────────────────────────────


def test_the_perplexity_model_is_a_component_when_a_model_scored_a_line(monkeypatch):
    m = TextModelManager()
    with patch.object(text_api, "text_manager", m):
        scored = [ti._fast_track_entry("1998", "Non-text"), {"text": "první řádek", "category": "Clear"}]
        assert text_api._model_components(scored) == {"qwen2.5_0.5b"}
        assert text_api._model_components([ti._fast_track_entry("1998", "Non-text")]) == set()
        assert text_api._model_components([]) == set() and text_api._model_components(None) == set()
        m.ppl_model_name = "distilgpt2"
        assert text_api._model_components(scored) == {"distilgpt2"}


def test_an_alto_page_ordered_by_layoutreader_records_its_licence(tmp_path, monkeypatch, lm):
    xml = tmp_path / "p.xml"
    xml.write_text(
        '<alto><Layout><Page WIDTH="100" HEIGHT="100"><PrintSpace><TextBlock>'
        '<TextLine HPOS="1" VPOS="1" WIDTH="50" HEIGHT="5"><String CONTENT="první" HPOS="1" VPOS="1" WIDTH="20" HEIGHT="5"/></TextLine>'
        "</TextBlock></PrintSpace></Page></Layout></alto>",
        encoding="utf-8",
    )
    with_layout = _manager()
    with_layout.layout_model = MagicMock()
    used = set()
    with (
        patch.object(ti, "boxes2inputs", MagicMock()),
        patch.object(ti, "_run_layout_reader", lambda lines, boxes, *a, **k: (lines, boxes)),
    ):
        with_layout.process_alto(str(xml), components=used)
    assert used == {"layoutlmv3"}

    document_order = set()
    _manager().process_alto(str(xml), components=document_order)  # no layout model: document order
    assert document_order == set()
