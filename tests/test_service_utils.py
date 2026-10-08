"""
tests/test_service_utils.py – Unit tests for service/utils.py: the pure ALTO
parsing / box-normalisation helpers behind the FastAPI /process endpoint.
"""

import pytest

pytest.importorskip("lxml")

from service.utils import (  # noqa: E402
    AltoUnreadable,
    normalize_boxes,
    parse_alto_xml,
    parse_alto_xml_lines,
    post_process_text,
)

_ALTO_BASIC = """<?xml version="1.0" encoding="UTF-8"?>
<alto><Layout><Page WIDTH="1000" HEIGHT="2000"><PrintSpace><TextLine>
<String CONTENT="Hello" HPOS="10" VPOS="20" WIDTH="50" HEIGHT="30"/>
<String CONTENT="World" HPOS="70" VPOS="20" WIDTH="60" HEIGHT="30"/>
</TextLine></PrintSpace></Page></Layout></alto>"""

_ALTO_HYP = """<?xml version="1.0" encoding="UTF-8"?>
<alto><Layout><Page WIDTH="1000" HEIGHT="2000"><TextLine>
<String CONTENT="Hyphen" HPOS="10" VPOS="20" WIDTH="50" HEIGHT="30"/>
<HYP CONTENT="-"/>
</TextLine></Page></Layout></alto>"""

_ALTO_SUBS = """<?xml version="1.0" encoding="UTF-8"?>
<alto><Layout><Page WIDTH="1000" HEIGHT="2000"><TextLine>
<String CONTENT="be" HPOS="10" VPOS="20" WIDTH="50" HEIGHT="30" SUBS_TYPE="HypPart1" SUBS_CONTENT="beautiful"/>
</TextLine></Page></Layout></alto>"""

_ALTO_XXE_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE alto [ <!ENTITY xxe SYSTEM "file://{secret}"> ]>
<alto><Layout><Page WIDTH="100" HEIGHT="100"><TextLine>
<String CONTENT="&xxe;" HPOS="0" VPOS="0" WIDTH="10" HEIGHT="10"/>
</TextLine></Page></Layout></alto>"""


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


# ── parse_alto_xml ──────────────────────────────────────────────────────────
def test_parse_basic_words_boxes_and_dims(tmp_path):
    words, boxes, dims = parse_alto_xml(_write(tmp_path, "a.xml", _ALTO_BASIC))
    assert words == ["Hello", "World"]
    assert boxes == [[10, 20, 60, 50], [70, 20, 130, 50]]
    assert dims == (1000, 2000)


def test_parse_appends_explicit_hyphen_tag(tmp_path):
    words, _, _ = parse_alto_xml(_write(tmp_path, "h.xml", _ALTO_HYP))
    assert words == ["Hyphen-"]


def test_parse_subs_hyppart1_expands_with_full_word(tmp_path):
    words, _, _ = parse_alto_xml(_write(tmp_path, "s.xml", _ALTO_SUBS))
    assert words == ["be- {beautiful}"]


def test_parse_missing_page_returns_empty(tmp_path):
    words, boxes, dims = parse_alto_xml(_write(tmp_path, "n.xml", "<alto><Layout/></alto>"))
    assert words == [] and boxes == [] and dims == (0, 0)


def test_parse_malformed_xml_returns_empty(tmp_path):
    words, boxes, dims = parse_alto_xml(_write(tmp_path, "bad.xml", "<alto><not-closed>"))
    assert words == [] and boxes == [] and dims == (0, 0)


def test_parse_does_not_resolve_external_entities_xxe(tmp_path):
    """Hardened parser must not load an external SYSTEM entity (XXE)."""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET_XXE", encoding="utf-8")
    doc = _ALTO_XXE_TMPL.format(secret=secret)
    words, _, _ = parse_alto_xml(_write(tmp_path, "xxe.xml", doc))
    assert "TOPSECRET_XXE" not in " ".join(words)


# ── normalize_boxes ─────────────────────────────────────────────────────────
def test_normalize_scales_to_0_1000():
    out = normalize_boxes([[0, 0, 500, 1000]], width=1000, height=2000)
    assert out == [[0, 0, 500, 500]]


def test_normalize_clamps_to_1000():
    out = normalize_boxes([[0, 0, 5000, 5000]], width=1000, height=1000)
    assert out == [[0, 0, 1000, 1000]]


def test_normalize_empty_boxes_returns_empty():
    assert normalize_boxes([], 100, 100) == []


def test_normalize_zero_dimension_returns_zero_boxes():
    assert normalize_boxes([[1, 2, 3, 4]], width=0, height=100) == [[0, 0, 0, 0]]


# ── parse_alto_xml_lines ─────────────────────────────────────────────────────
def test_parse_lines_groups_words_by_textline(tmp_path):
    lines, boxes, dims = parse_alto_xml_lines(_write(tmp_path, "a.xml", _ALTO_BASIC))
    # _ALTO_BASIC has one TextLine containing "Hello" and "World".
    assert lines == ["HelloWorld"]
    assert dims == (1000, 2000)
    # Box spans both words: x from 10 to 70+60=130, y from 20 to 20+30=50.
    assert boxes == [[10, 20, 130, 50]]


def test_parse_lines_preserves_sp_as_space(tmp_path):
    doc = """<?xml version="1.0" encoding="UTF-8"?>
<alto><Layout><Page WIDTH="1000" HEIGHT="2000"><TextLine>
<String CONTENT="Hello" HPOS="10" VPOS="20" WIDTH="50" HEIGHT="30"/>
<SP/>
<String CONTENT="World" HPOS="70" VPOS="20" WIDTH="60" HEIGHT="30"/>
</TextLine></Page></Layout></alto>"""
    lines, _, _ = parse_alto_xml_lines(_write(tmp_path, "sp.xml", doc))
    assert lines == ["Hello World"]


def test_parse_lines_subs_hyppart1_annotation_within_one_line(tmp_path):
    lines, _, _ = parse_alto_xml_lines(_write(tmp_path, "s.xml", _ALTO_SUBS))
    assert lines == ["be- {beautiful}"]


def test_parse_lines_missing_page_returns_empty(tmp_path):
    lines, boxes, dims = parse_alto_xml_lines(_write(tmp_path, "n.xml", "<alto><Layout/></alto>"))
    assert lines == [] and boxes == [] and dims == (0, 0)


def test_parse_lines_malformed_xml_returns_empty(tmp_path):
    lines, boxes, dims = parse_alto_xml_lines(_write(tmp_path, "bad.xml", "<alto><not-closed>"))
    assert lines == [] and boxes == [] and dims == (0, 0)


# strict=True is what the service's process_alto passes: a file that is not ALTO raises instead of
# coming back as the blank page it is indistinguishable from.
@pytest.mark.parametrize(
    "content, why",
    [
        ("<alto><not-closed>", "not well-formed"),
        ("<alto><Layout/></alto>", "no <Page>"),
        ('<alto><Layout><Page WIDTH="wide" HEIGHT="2000"/></Layout></alto>', "not a number"),
        ("", "not well-formed"),
    ],
    ids=["broken-xml", "no-page", "bad-size", "empty-file"],
)
def test_strict_parse_raises_for_a_file_that_is_not_alto(tmp_path, content, why):
    with pytest.raises(AltoUnreadable, match=why):
        parse_alto_xml_lines(_write(tmp_path, "x.xml", content), strict=True)


def test_strict_parse_still_returns_a_page_without_text_as_empty(tmp_path):
    blank = '<alto><Layout><Page WIDTH="1000" HEIGHT="2000"><PrintSpace/></Page></Layout></alto>'
    lines, boxes, dims = parse_alto_xml_lines(_write(tmp_path, "blank.xml", blank), strict=True)
    assert lines == [] and boxes == [] and dims == (1000, 2000)


def test_strict_parse_of_valid_alto_is_the_lenient_result(tmp_path):
    path = _write(tmp_path, "a.xml", _ALTO_BASIC)
    assert parse_alto_xml_lines(path, strict=True) == parse_alto_xml_lines(path)


def test_alto_unreadable_is_a_value_error():
    assert issubclass(AltoUnreadable, ValueError)


def test_parse_lines_does_not_resolve_external_entities_xxe(tmp_path):
    """Line-level parser must use the same hardened parser as parse_alto_xml."""
    secret = tmp_path / "secret2.txt"
    secret.write_text("TOPSECRET_XXE_LINES", encoding="utf-8")
    doc = _ALTO_XXE_TMPL.format(secret=secret)
    lines, _, _ = parse_alto_xml_lines(_write(tmp_path, "xxe2.xml", doc))
    assert "TOPSECRET_XXE_LINES" not in " ".join(lines)


# ── post_process_text ────────────────────────────────────────────────────────
def test_post_process_empty_input_returns_empty_string():
    assert post_process_text([], []) == ""


def test_post_process_single_line_no_separator():
    assert post_process_text(["Hello"], [[0, 0, 50, 20]]) == "Hello"


def test_post_process_joins_close_lines_with_newline():
    # Second line starts just below the first (small gap) -> single "\n".
    out = post_process_text(["Line one", "Line two"], [[0, 0, 100, 20], [0, 22, 100, 42]])
    assert out == "Line one\nLine two"


def test_post_process_large_gap_inserts_paragraph_break():
    # Second line starts far below the first -> "\n\n" paragraph break.
    out = post_process_text(["Para one", "Para two"], [[0, 0, 100, 20], [0, 200, 100, 220]])
    assert out == "Para one\n\nPara two"
