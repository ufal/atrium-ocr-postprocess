"""
tests/test_categorization_routes.py
===================================
Unit coverage for the categorisation routes: the inverted/mirror lexicon and
its derivation, analyze_rotation_signals (gate behaviour), the per-line
trash_inverted route + non-diacritics hard gate, low-ppl Clear fast-track,
damaged-token capping, and structured measurement predicate boundaries.
"""

from text_util import (
    _MIRROR_GLYPH,
    _ROTATE_GLYPH,
    ROT_GHOSTLIST,
    ROT_WHITELIST,
    _has_unit_with_spaced_decimal_metre_candidate,
    _looks_like_measurement,
    _looks_like_plot_probe_header,
    _transform_word,
    analyze_rotation_signals,
    categorize_line,
    inspect_short_line_telemetry,
    is_structured_line,
)


class TestExplicitDiagnosticHardGates:
    """Test explicit hard gates routing directly on rule firing + garbage evidence."""

    def test_bigram_run_with_garbage_evidence_routes_to_trash(self):
        # High quality score base, but bigram run + low valid word ratio -> Trash. Two words, so
        # this is gate 7's short-line bigram branch (`rule_short_line`); `rule_bigram_run`, the late
        # gate, is reached from three words up.
        cat, _, reason = categorize_line(0.85, "IDIDID text", 2, 0.3, 100.0, valid_word_ratio=0.10, return_reason=True)
        assert cat == "Trash" and reason in ("trash_threshold", "trash_hard_sweep")

    def test_fragment_tokens_with_garbage_evidence_routes_to_trash(self):
        # Fragment tokens + low language score -> Trash via rule_fragment_tokens
        cat, _, reason = categorize_line(
            0.82, "a b c d e f", 6, 0.2, 120.0, lang_score=0.10, orig_lang_score=0.10, return_reason=True
        )
        assert cat == "Trash" and reason == "trash_threshold"

    def test_high_quality_score_exempts_wqx_rot_from_trash(self):
        # Each of the six section-9 gates (rule_wqx_rot, rule_vowelless,
        # rule_ledger_fragmentation, rule_mid_uppercase, rule_bigram_run,
        # rule_fragment_tokens) must only escalate to check_rescues() when
        # qs < thresh_trash (CATEG_TRASH_SCORE_MAX + 0.35) -- a real corpus
        # line ("Sonda valem 2008": rot_ratio=0.70, orig_lang_score=0.45,
        # damaged=0, garbage_density=0.0, valid_word_ratio=1.0) fires
        # rule_wqx_rot and satisfies _has_strong_garbage_evidence on its own,
        # but at qs=0.95 -- well clear of the 0.90 threshold -- it must stay
        # Clear rather than fall through to Trash.
        cat, _, reason = categorize_line(
            0.95,
            "Sonda valem 2008",
            3,
            0.30,
            55.0,
            valid_word_ratio=1.0,
            lang_score=0.15,
            orig_lang_score=0.45,
            garbage_density=0.0,
            return_reason=True,
        )
        assert cat == "Clear" and reason == "clear_threshold"


class TestShortLineTelemetry:
    """Test telemetry inspector helper for short-line route analysis."""

    def test_inspect_short_line_telemetry_captures_fields(self):
        telemetry = inspect_short_line_telemetry(
            text_source="v - 112mm",
            word_count=2,
            valid_word_ratio=1.0,
            lang_score=0.9,
            perplexity=45.0,
        )
        assert telemetry["word_count"] == 2
        assert telemetry["structured"] is True
        assert telemetry["final_category"] in ("Clear", "Noisy", "Trash")
        assert "route_reason" in telemetry


class TestGlyphTransforms:
    def test_mirror_corrected_values(self):
        assert _transform_word("pouze", _MIRROR_GLYPH) == "ezuoq"
        assert _transform_word("bude", _MIRROR_GLYPH) == "ebud"

    def test_rotate_corrected_values(self):
        assert _transform_word("pouze", _ROTATE_GLYPH) == "aznod"
        assert _transform_word("bude", _ROTATE_GLYPH) == "apnq"

    def test_short_words(self):
        assert _transform_word("po", _MIRROR_GLYPH) == "oq"
        assert _transform_word("po", _ROTATE_GLYPH) == "od"
        assert _transform_word("on", _MIRROR_GLYPH) == "no"

    def test_unmappable_glyph_aborts_word(self):
        assert _transform_word("kov", _ROTATE_GLYPH) is None


class TestTrashInvertedGate:
    def test_ghost_dominated_and_not_upright_is_trash(self):
        cat, _, reason = categorize_line(
            0.80, "oq zem", 2, 0.3, 300.0, return_reason=True, ghost_dominated=True, is_upright_czech=False
        )
        assert cat == "Trash" and reason == "trash_inverted"

    def test_upright_overrides_ghost_route(self):
        cat, _, reason = categorize_line(
            0.80, "oq náčrt", 2, 0.3, 300.0, return_reason=True, ghost_dominated=True, is_upright_czech=True
        )
        assert reason != "trash_inverted" and cat != "Trash"


class TestLowPplFastTrack:
    """The low-ppl Clear fast-track (rule_lowppl_clear): LM-confident text
    (ppl < LOWPPL_CLEAR_MAX, word_count >= 3) is promoted straight to Clear,
    independent of the score band."""

    def test_lowppl_multiword_promoted_to_clear(self):
        cat, _, reason = categorize_line(
            0.95, "krátký čistý text", 3, 0.4, 30.0, garbage_density=0.1, return_reason=True
        )
        assert cat == "Clear" and reason == "lowppl_clear"

    def test_short_fragment_no_longer_held_noisy(self):
        cat, _, reason = categorize_line(0.92, "značky.", 1, 0.4, 200.0, garbage_density=0.14, return_reason=True)
        assert cat == "Clear" and reason != "lowppl_clear"

    def test_lowppl_low_valid_ratio_capped_to_noisy(self):
        cat, _, reason = categorize_line(
            0.80, "slovo bez bez diakritiky", 4, 0.4, 30.0, return_reason=True, valid_word_ratio=0.50
        )
        assert cat == "Noisy" and reason == "noisy_threshold"


class TestGhostlist:
    def test_whitelist_and_ghostlist_disjoint(self):
        assert ROT_WHITELIST.isdisjoint(ROT_GHOSTLIST)

    def test_common_real_words_not_ghosts(self):
        for w in ("no", "od", "po", "bo", "pod", "se"):
            assert w not in ROT_GHOSTLIST

    def test_expected_ghosts_present(self):
        for g in ("aznod", "apnq", "oq", "boq", "zem"):
            assert g in ROT_GHOSTLIST


class TestAnalyzeRotationSignals:
    def test_empty_text(self):
        assert analyze_rotation_signals("") == (False, False)

    def test_diacritics_force_upright(self):
        up, ghost = analyze_rotation_signals("náčrt sondy")
        assert up is True and ghost is False

    def test_whitelist_word_forces_upright(self):
        up, _ = analyze_rotation_signals("pouze tento")
        assert up is True

    def test_ghost_dominated_short_inverted(self):
        up, ghost = analyze_rotation_signals("oq zem")
        assert up is False and ghost is True

    def test_diacritic_keeps_upright_despite_ghost(self):
        up, _ = analyze_rotation_signals("oq náčrt")
        assert up is True


class TestExtremePplRoute:
    def test_extreme_ppl_low_conf_garbage_trashed(self):
        cat, _, reason = categorize_line(
            0.80, "Alyrý cvod nede % Agrgr oAOrt", 6, 0.41, 15168.0, return_reason=True, orig_lang_score=0.6658
        )
        assert cat == "Trash" and reason == "trash_hard_sweep"

    def test_extreme_ppl_confident_text_spared(self):
        cat, _, reason = categorize_line(
            0.80, "Taxon vojcuskou povinen jest", 4, 0.45, 15168.0, return_reason=True, orig_lang_score=0.95
        )
        assert reason != "trash_hard_sweep"


class TestLmConfidentCzechBypass:
    def test_upright_czech_low_ppl_bypasses_valid_cap(self):
        cat, _, reason = categorize_line(
            0.88,
            "í nezpůsobilost ke službě nebyla",
            5,
            0.43,
            80.0,
            return_reason=True,
            valid_word_ratio=0.80,
            is_upright_czech=True,
            garbage_density=0.0,
        )
        assert cat == "Clear" and reason == "clear_threshold"

    def test_non_czech_low_valid_still_capped(self):
        cat, _, reason = categorize_line(
            0.88,
            "slovo bez diakritiky tady",
            5,
            0.43,
            80.0,
            return_reason=True,
            valid_word_ratio=0.80,
            is_upright_czech=False,
            garbage_density=0.0,
        )
        assert cat == "Noisy" and reason == "noisy_threshold"

    def test_high_garbage_czech_not_bypassed(self):
        cat, _, _ = categorize_line(
            0.88,
            "nonč mI žn dn 1074 484",
            5,
            0.22,
            131.0,
            return_reason=True,
            valid_word_ratio=0.40,
            is_upright_czech=True,
            garbage_density=0.20,
        )
        assert cat == "Noisy" or cat == "Trash"


class TestDamagedTokenCap:
    """Restored damaged-token backstop: character-level damage prevents Clear."""

    def test_damaged_token_caps_clear_score_to_noisy(self):
        # High quality score (qs=0.90), but text contains damaged token 'd^ku'
        cat, _, reason = categorize_line(0.90, "tento text obsahuje d^ku poškození", 5, 0.4, 100.0, return_reason=True)
        assert cat == "Noisy" and reason == "noisy_threshold"


class TestStructuredLineMeasurement:
    """Tightened _looks_like_measurement and is_structured_line predicates."""

    def test_legitimate_measurements_protected(self):
        assert is_structured_line("Rozměry: v - 112mm, pr.okraje - 145, pr. dna - 7") is True
        assert is_structured_line("Rozměry ; v- 144 mm, pr. okraje - 125") is True
        assert is_structured_line("v - 185 mm, pr. okraje - 20") is True
        assert is_structured_line("pr. hrdla 62mm, pr.-dna 36") is True

    def test_single_letter_units_and_probe_noise_rejected(self):
        assert _looks_like_measurement("3 m") is False
        assert _looks_like_measurement("o 5 m") is False
        assert _looks_like_measurement("cuxoaid v. 12") is False
        assert _looks_like_measurement("clouCelRa pr. 4") is False

    def test_dotted_descriptor_matches_regardless_of_trailing_space(self):
        # A trailing \b directly after a literal "." only holds when the dot is
        # glued to a following word/digit char, so OCR inserting (or not) a
        # space after "pr."/"rozm." must not change the verdict. Isolated,
        # minimal inputs only -- unlike test_legitimate_measurements_protected,
        # none of these may trip has_measurement_separator or has_unit as a
        # side channel, so each one actually isolates the dotted-keyword path.
        assert _looks_like_measurement("pr.okraje - 145") is True
        assert _looks_like_measurement("pr. okraje - 145") is True
        assert _looks_like_measurement("rozm.12") is True
        assert _looks_like_measurement("rozm. 12") is True
        assert _looks_like_measurement("pr. dna - 7") is True
        # A single dotted-descriptor hit still isn't corroboration on its own.
        assert _looks_like_measurement("pr. 4") is False

    def test_glued_bare_metre_unit_protected_but_spaced_unit_is_not(self):
        # The metre is this corpus's single most common unit by a wide margin
        # (far ahead of "mm"), so a digit glued directly to "m" is a real
        # measurement signal -- but a *spaced* bare unit ("3 m") stays
        # unmatched, since that alone is too weak a signal (see the rejected
        # probes above).
        assert _looks_like_measurement("0,2-0,4m") is True
        assert _looks_like_measurement("0,4-0,6m") is True
        assert _looks_like_measurement("Max, sila: 0,46m") is True
        assert _looks_like_measurement("3 m") is False
        assert _looks_like_measurement("o 5 m") is False


class TestSpacedDecimalMetreCandidate:
    """
    `_has_unit_with_spaced_decimal_metre_candidate` is a candidate refinement
    to `_looks_like_measurement`'s `has_unit` branch, recovering spaced bare
    metre readings ("2,10 m") that carry a decimal separator, without
    reopening the bare-integer spaced case ("3 m") the existing pinned test
    rejects. NOT YET WIRED IN -- see the comment above its definition.
    """

    def test_reported_corpus_lines_recovered(self):
        assert _has_unit_with_spaced_decimal_metre_candidate("2,10 m") is True
        assert _has_unit_with_spaced_decimal_metre_candidate("215,5 193,120 m") is True
        assert _has_unit_with_spaced_decimal_metre_candidate("z /193,445 m/") is True
        assert _has_unit_with_spaced_decimal_metre_candidate("rovina profilu z-v 214 192,740 m") is True

    def test_existing_glued_and_rejected_cases_unchanged(self):
        assert _has_unit_with_spaced_decimal_metre_candidate("0,2-0,4m") is True
        assert _has_unit_with_spaced_decimal_metre_candidate("0,4-0,6m") is True
        assert _has_unit_with_spaced_decimal_metre_candidate("max, sila: 0,46m") is True
        assert _has_unit_with_spaced_decimal_metre_candidate("3 m") is False
        assert _has_unit_with_spaced_decimal_metre_candidate("o 5 m") is False

    def test_not_yet_wired_into_looks_like_measurement(self):
        # Tripwire: if this starts failing, someone wired the candidate in
        # without updating this test -- update the assertion and promote the
        # docstring/comment above the candidate accordingly.
        assert _looks_like_measurement("2,10 m") is False


class TestPlotProbeHeader:
    """
    `_looks_like_plot_probe_header` is a rule for the "sonda: XIV." finding.
    It is wired into `is_structured_line` via `_looks_like_catalogue_reference`
    to recover valid headers lost during the measurement regex tightening.
    """

    def test_reported_variants_match(self):
        assert _looks_like_plot_probe_header("Plocha: 3; sonda: XIV.") is True
        assert _looks_like_plot_probe_header("Plocha: 3; sonda: V.") is True
        assert _looks_like_plot_probe_header("Plocha: 3; sonda: SXIV.") is True
        assert _looks_like_plot_probe_header("Plocha: 3; sonda: IV.") is True

    def test_other_plot_numbers_and_spacing_match(self):
        assert _looks_like_plot_probe_header("Plocha:12; sonda:II.") is True
        assert _looks_like_plot_probe_header("PLOCHA : 7 ; SONDA : IX.") is True

    def test_malformed_or_unrelated_lines_rejected(self):
        assert _looks_like_plot_probe_header("Plocha: 3; sonda XIV.") is False  # missing colon
        assert _looks_like_plot_probe_header("sonda: XIV.") is False  # missing Plocha field
        assert _looks_like_plot_probe_header("Plocha: sonda: XIV.") is False  # missing plot number
        assert _looks_like_plot_probe_header("Plocha: 3; sonda: XIVQ.") is False  # non-roman letter
        assert _looks_like_plot_probe_header("Plocha 3, sonda XIV") is False  # no trailing dot
        assert _looks_like_plot_probe_header("Nádoba 2* Zvoncový pohár") is False  # unrelated prose

    def test_wired_into_is_structured_line(self):
        # Documents that the rule is actively wired in. If this passes,
        # `_looks_like_plot_probe_header` is successfully hooked into the main routing.
        assert is_structured_line("Plocha: 3; sonda: XIV.") is True
