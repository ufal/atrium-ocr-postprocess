"""
tests/test_witness_floor.py
===========================
(#30 D47) The witness floor: a short line the shape witness convicts is never
`Clear`; where the cascade would say `Clear` it says `Noisy`.

The floor lives in `categorize_line()`, after `determine_category()`, so these
tests pin the cascade's answer with a stub and read only what the floor does to
it. That isolates the three properties stage 13 relies on:

  * it ships OFF and is inert while off,
  * it can only turn `Clear` into `Noisy`, never touch any other verdict,
  * it acts on exactly the witness's own population (short, convicted lines).
"""

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import text_util as tu  # noqa: E402

CONVICTED = "oueussd"  # the witness's canonical garbage example (tests/test_short_garbage_witness_wiring.py)
RULE = "rule_short_garbage_witness_floor"


def _call(text, *, verdict="Clear", lang="ces_Latn", wc=None):
    """categorize_line() with the cascade's verdict pinned to `verdict`."""
    wc = len(text.split()) if wc is None else wc
    return tu.categorize_line(0.9, text, wc, 0.4, 100.0, return_reason=True, lang=lang)


@pytest.fixture
def pinned(monkeypatch):
    """Replace the cascade with a fixed verdict; the floor is what remains."""

    def _pin(verdict, reason="clear_threshold"):
        monkeypatch.setattr(tu, "determine_category", lambda *a, **k: (verdict, reason))

    return _pin


def test_ships_off_and_is_registered_as_config_gated():
    assert tu.SHORT_GARBAGE_WITNESS_FLOOR is False
    assert tu.CONFIG_GATED_RULES[RULE] == "SHORT_GARBAGE_WITNESS_FLOOR"
    assert tu.rule_is_config_gated_off(RULE)
    with tu.override_constants({"SHORT_GARBAGE_WITNESS_FLOOR": True}):
        assert not tu.rule_is_config_gated_off(RULE)


def test_the_shipped_config_key_exists_and_is_false():
    import configparser

    parser = configparser.RawConfigParser()
    parser.optionxform = str
    parser.read(_ROOT / "setup" / "config.txt")
    assert parser.get("TEXT_UTILS", "SHORT_GARBAGE_WITNESS_FLOOR").strip().lower() == "false"


def test_the_fixture_line_really_is_convicted():
    assert tu.shape_garbage_clauses(CONVICTED, "ces_Latn"), "fixture no longer convicted: pick another line"


def test_inert_while_the_flag_is_off(pinned):
    pinned("Clear")
    categ, _score, reason = _call(CONVICTED)
    assert (categ, reason) == ("Clear", "clear_threshold")


def test_clear_becomes_noisy_when_armed(pinned):
    pinned("Clear")
    with tu.override_constants({"SHORT_GARBAGE_WITNESS_FLOOR": True}):
        categ, score, reason = _call(CONVICTED)
    assert (categ, reason) == ("Noisy", "noisy_threshold")
    # the score is re-aligned into the Noisy band like any other Noisy verdict
    assert tu.CATEG_TRASH_SCORE_MAX <= score < tu.CATEG_NOISY_SCORE_MAX


@pytest.mark.parametrize("verdict", ["Trash", "Noisy", "Non-text", "Empty"])
def test_never_touches_a_verdict_other_than_clear(pinned, verdict):
    pinned(verdict, "whatever")
    with tu.override_constants({"SHORT_GARBAGE_WITNESS_FLOOR": True}):
        categ, _score, reason = _call(CONVICTED)
    assert (categ, reason) == (verdict, "whatever")


@pytest.mark.parametrize(
    "text",
    [
        "II/C",  # domain notation: vetoed by the witness itself
        "S-VIIIb",  # fused grid reference, gold Clear
        "dřevo",  # Czech diacritics
        "malakofauna",  # on the reviewed [allowed] list (Q5b)
        "kus",  # too short to have a shape
    ],
)
def test_lines_the_witness_does_not_convict_stay_clear(pinned, text):
    pinned("Clear")
    with tu.override_constants({"SHORT_GARBAGE_WITNESS_FLOOR": True}):
        categ, _score, reason = _call(text)
    assert (categ, reason) == ("Clear", "clear_threshold")


def test_longer_than_the_short_line_bound_is_out_of_scope(pinned):
    pinned("Clear")
    text = " ".join([CONVICTED] * (tu.ISOLATED_CHAR_MIN_TOKENS + 1))
    with tu.override_constants({"SHORT_GARBAGE_WITNESS_FLOOR": True}):
        categ, _score, _reason = _call(text)
    assert categ == "Clear"


def test_independent_of_the_witness_flag(pinned):
    """The floor needs neither half of gate 6 nor SHORT_GARBAGE_WITNESS_ENABLE."""
    pinned("Clear")
    for enable in (False, True):
        with tu.override_constants({"SHORT_GARBAGE_WITNESS_FLOOR": True, "SHORT_GARBAGE_WITNESS_ENABLE": enable}):
            assert _call(CONVICTED)[0] == "Noisy"


def test_disabled_rules_switches_it_off(pinned):
    pinned("Clear")
    with tu.override_constants({"SHORT_GARBAGE_WITNESS_FLOOR": True, "DISABLED_RULES": frozenset({RULE})}):
        assert _call(CONVICTED)[0] == "Clear"


def test_fire_is_recorded_only_when_it_acts(pinned):
    pinned("Clear")
    with tu.override_constants({"SHORT_GARBAGE_WITNESS_FLOOR": True}):
        with tu.rule_fire_capture() as counts:
            _call(CONVICTED)
            _call("II/C")
    assert counts.get(RULE) == 1


def test_the_lang_label_reaches_the_witness(pinned):
    """The raw language label is passed through, as at gate 6 (D44's vowel-run split)."""
    pinned("Clear")
    with tu.override_constants({"SHORT_GARBAGE_WITNESS_FLOOR": True}):
        for lang in ("ces_Latn", "deu_Latn", None):
            expected = bool(tu.shape_garbage_clauses(CONVICTED, lang))
            assert (_call(CONVICTED, lang=lang)[0] == "Noisy") is expected


# ---------------------------------------------------------------------------
# End to end, through the production re-scorer and the REAL cascade.
#
# The stubbed tests above pin what the floor does to a verdict. These pin the
# two properties the plan's "Done when" names that a stub cannot: a flag-off
# re-score moves nothing, and a flag-on one moves only `Clear` -> `Noisy`, in
# all four combinations with SHORT_GARBAGE_WITNESS_ENABLE. The flags are read at
# import time, so each combination is a subprocess (same reason as
# tests/test_short_garbage_witness_wiring.py).
# ---------------------------------------------------------------------------
import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402

_CHILD = r"""
import json, sys
sys.path.insert(0, {root!r})
sys.path.insert(0, {tools!r})
import recategorize_from_csv as rc

expected, known = rc._load_lang_config({config!r})
out = {{}}
for text, ppl, lang_score, original_lang in json.loads(sys.argv[1]):
    row = {{
        "text": text,
        "original_text": text,
        "original_lang": original_lang,
        "orig_lang_score": str(lang_score),
        "perplex": str(ppl),
        "categ": "Noisy",
        "word_count": str(len(text.split())),
    }}
    out[text] = rc._rescore_row(row, expected, known)["categ"]
print(json.dumps(out))
"""

_ROWS = [
    ("oueussd", 850.00, 0.9163, "ces_Latn"),
    ("sektlll", 850.00, 0.60, "ces_Latn"),
    ("edelite", 850.00, 0.60, "ces_Latn"),
    ("malakofauna", 1210.00, 0.5600, "isl_Latn"),
    ("Equus caballus", 640.00, 0.7700, "ast_Latn"),
    ("II/C", 6.0e7, 0.4000, "ces_Latn"),
    ("1 ks", 5.0e6, 0.5000, "ces_Latn"),
    ("S-VIIIb", 850.00, 0.4000, "ces_Latn"),
    ("Dauerleihe", 900.00, 0.9000, "deu_Latn"),
    ("Prostor nalezu", 300.00, 0.9500, "ces_Latn"),
]


def _rescore(*, floor: bool, witness: bool) -> dict:
    env = dict(os.environ)
    env["ATRIUM_TEXT_UTILS_SHORT_GARBAGE_WITNESS_FLOOR"] = "true" if floor else "false"
    env["ATRIUM_TEXT_UTILS_SHORT_GARBAGE_WITNESS_ENABLE"] = "true" if witness else "false"
    script = _CHILD.format(root=str(_ROOT), tools=str(_ROOT / "tools"), config=str(_ROOT / "setup" / "config.txt"))
    proc = subprocess.run(
        [sys.executable, "-c", script, json.dumps(_ROWS)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_ROOT),
        timeout=300,
    )
    assert proc.returncode == 0, f"re-score failed (floor={floor}, witness={witness}):\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("witness", [False, True])
def test_end_to_end_floor_only_ever_moves_clear_to_noisy(witness):
    off = _rescore(floor=False, witness=witness)
    on = _rescore(floor=True, witness=witness)
    moved = {t: (off[t], on[t]) for t in off if off[t] != on[t]}
    assert all(pair == ("Clear", "Noisy") for pair in moved.values()), moved


@pytest.mark.parametrize("witness", [False, True])
def test_end_to_end_flag_off_equals_the_shipped_default(witness):
    """`false` must be byte-identical to not having the key: the shipped config says false."""
    explicit = _rescore(floor=False, witness=witness)
    env = {k: v for k, v in os.environ.items() if k != "ATRIUM_TEXT_UTILS_SHORT_GARBAGE_WITNESS_FLOOR"}
    env["ATRIUM_TEXT_UTILS_SHORT_GARBAGE_WITNESS_ENABLE"] = "true" if witness else "false"
    script = _CHILD.format(root=str(_ROOT), tools=str(_ROOT / "tools"), config=str(_ROOT / "setup" / "config.txt"))
    proc = subprocess.run(
        [sys.executable, "-c", script, json.dumps(_ROWS)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_ROOT),
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == explicit


def test_end_to_end_the_floor_reaches_something():
    """A floor that never fires on its own fixture would pass every test above vacuously."""
    moved = [
        t
        for t, (a, b) in {
            t: (_rescore(floor=False, witness=False)[t], _rescore(floor=True, witness=False)[t])
            for t in ("oueussd", "sektlll", "edelite")
        }.items()
        if (a, b) == ("Clear", "Noisy")
    ]
    assert moved, "no fixture line was Clear -> Noisy under the floor; pick lines the cascade answers Clear"
