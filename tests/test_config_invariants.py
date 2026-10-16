"""
tests/test_config_invariants.py
===============================

The relations the categoriser's constants must keep, and who refuses to run when they break.

`text_util.constant_invariant_errors()` is the one definition. Three entry points apply it: the offline
re-scorer (`tools/recategorize_from_csv.validate_constants`, since the beginning), and -- new -- the
service at start (`service/text_api.lifespan`) and the batch stage (`classify_TEXT.main`). Neither
reads it at import: `tests/test_config_constants.py` sets `SHORT_PPL_CAP` above the threshold on purpose to
test the reader, and stage-16 runs vary constants through `ATRIUM_TEXT_UTILS_*`; a research process has to be
able to import the module. What serves or produces categories does not run on them.
"""

import asyncio
import sys
from pathlib import Path

import pytest

import text_util as tu

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(_ROOT / "tools"))


def test_the_shipped_constants_hold():
    assert tu.constant_invariant_errors() == []
    # ... and by the numbers the docs state: Trash band below the Noisy one, the cap below the ceiling.
    assert tu.CATEG_TRASH_SCORE_MAX < tu.CATEG_NOISY_SCORE_MAX
    assert tu.SHORT_PPL_CAP < tu.PERPLEXITY_THRESHOLD_MAX


def test_an_inverted_score_band_is_named(monkeypatch):
    monkeypatch.setattr(tu, "CATEG_TRASH_SCORE_MAX", 0.95)
    [error] = tu.constant_invariant_errors()
    assert "CATEG_TRASH_SCORE_MAX (0.95) must be < CATEG_NOISY_SCORE_MAX (0.8)" == error


def test_equal_bands_are_inverted_too(monkeypatch):
    monkeypatch.setattr(tu, "CATEG_TRASH_SCORE_MAX", tu.CATEG_NOISY_SCORE_MAX)
    assert len(tu.constant_invariant_errors()) == 1


def test_the_short_line_cap_must_stay_below_the_ceiling(monkeypatch):
    monkeypatch.setattr(tu, "SHORT_PPL_CAP", tu.PERPLEXITY_THRESHOLD_MAX)
    [error] = tu.constant_invariant_errors()
    assert error.startswith("SHORT_PPL_CAP (1000) must be < PERPLEXITY_THRESHOLD_MAX (1000)")


def test_the_weights_must_sum_to_a_positive_number(monkeypatch):
    for name in tu.QS_WEIGHT_NAMES:
        monkeypatch.setattr(tu, name, 0.0)
    assert tu.constant_invariant_errors() == ["the QS_WEIGHT_* constants must sum to a positive number"]


def test_every_broken_relation_is_reported_at_once(monkeypatch):
    monkeypatch.setattr(tu, "CATEG_TRASH_SCORE_MAX", 0.95)
    monkeypatch.setattr(tu, "SHORT_PPL_CAP", 5000.0)
    assert len(tu.constant_invariant_errors()) == 2


def test_candidate_values_replace_the_live_ones_without_touching_them():
    assert tu.constant_invariant_errors({"CATEG_TRASH_SCORE_MAX": 0.99}) != []
    assert tu.constant_invariant_errors({"CATEG_TRASH_SCORE_MAX": 0.40, "CATEG_NOISY_SCORE_MAX": 0.70}) == []
    assert tu.CATEG_TRASH_SCORE_MAX == 0.55, "a candidate must never leak into the live constants"


def test_the_weight_names_are_the_engines_and_the_rescorers_is_the_same_list():
    import recategorize_from_csv as R

    assert R.QS_WEIGHT_NAMES is tu.QS_WEIGHT_NAMES
    assert all(hasattr(tu, name) for name in tu.QS_WEIGHT_NAMES)


def test_the_rescorer_applies_the_same_check():
    import recategorize_from_csv as R

    with pytest.raises(ValueError, match="CATEG_TRASH_SCORE_MAX"):
        R.validate_constants({"CATEG_TRASH_SCORE_MAX": 0.95, "CATEG_NOISY_SCORE_MAX": 0.80})
    with pytest.raises(ValueError, match="SHORT_PPL_CAP"):
        R.validate_constants({"SHORT_PPL_CAP": 1500.0})
    R.validate_constants({})  # nothing overridden: the live constants hold


def test_the_service_refuses_to_start_on_a_broken_ordering(monkeypatch):
    from service import text_api

    monkeypatch.setattr(tu, "CATEG_TRASH_SCORE_MAX", 0.95)

    def never_loaded():
        pytest.fail("the models must not even start loading")

    monkeypatch.setattr(text_api.text_manager, "load_models", never_loaded)

    async def start():
        async with text_api.lifespan(text_api.app):
            pass

    with pytest.raises(RuntimeError, match=r"invalid \[TEXT_UTILS\] constants: CATEG_TRASH_SCORE_MAX"):
        asyncio.run(start())


def test_the_batch_stage_refuses_to_run_on_a_broken_ordering(monkeypatch):
    import classify_TEXT

    monkeypatch.setattr(tu, "SHORT_PPL_CAP", 5000.0)
    with pytest.raises(SystemExit) as stop:
        classify_TEXT.main([])
    assert "SHORT_PPL_CAP" in str(stop.value)
    assert "nothing was classified" in str(stop.value)


def test_the_qwen_scale_warning_is_quiet_at_the_shipped_value(monkeypatch, capsys):
    """It used to fire (threshold 500) on every run of the shipped configuration, whose Qwen value is 1000."""
    import classify_TEXT

    assert tu.PERPLEXITY_THRESHOLD_MAX < classify_TEXT._QWEN_SCALE_WARN_ABOVE
    assert classify_TEXT._QWEN_SCALE_WARN_ABOVE <= 3000.0 / 1.5, "distilgpt2's shipped value is still caught"
    assert "Configuration Mismatch" not in capsys.readouterr().out
