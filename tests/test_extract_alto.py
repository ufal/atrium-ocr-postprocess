"""
Tests for extract_ALTO_2_TXT.py pure-logic helpers.
"""

import document_hook
from atrium_document import DocumentRecord, load_document
from extract_ALTO_2_TXT import _dehyphenate


def test_dehyphenate_standard_hyphen():
    text = "This is a split-\nword test."
    # The hyphen is removed and the fragments are joined without a space
    assert _dehyphenate(text) == "This is a splitword test.\n"


def test_dehyphenate_multiple_lines():
    text = "First line.\nSec-\nond line.\nThird."
    assert _dehyphenate(text) == "First line.\nSecond line.\nThird.\n"


def test_dehyphenate_no_hyphen():
    text = "Line one\nLine two"
    # Preserves normal line breaks
    assert _dehyphenate(text) == "Line one\nLine two\n"


def test_dehyphenate_typographical_hyphen_variants():
    # \xad (soft hyphen), \u2013 (en-dash), \u2014 (em-dash)
    # FIX: Place the hyphens at the end of the line where the function looks for them
    text = "Soft\xad\nhyphen\nEn\u2013\ndash\nEm\u2014\ndash\n"
    assert _dehyphenate(text) == "Softhyphen\nEndash\nEmdash\n"


# \u2500\u2500 Accretion contract (atrium-project#10) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
#
# Ported from tests/test_extract_json.py, which had the only copy of this test even
# though all four extract_*.py stages run the IDENTICAL production pattern
# (pages_and_content_from_text -> write_document_block with merge_blocks={"pages"} +
# set_blocks={"content"}). This file and test_extract_llm_alto.py had zero
# document_json references, so the alto-tools and GLM paths' accretion was covered by
# nothing at all \u2014 the uneven coverage that let J1/D2 through elsewhere.


def test_accretion_preserves_unrelated_metadata_and_co_owned_fields(tmp_path):
    """The alto-tools extractor may only touch its own `content` block and its own
    fields inside the field-split `pages` block; every other block, and every other
    tool's fields on a shared `pages` row, must come out exactly as they went in."""
    doc_dir = tmp_path / "doc"
    doc_dir.mkdir()

    # A baseline as earlier stages leave it: page-classification's block and fields,
    # nlp-enrich's entities, and the first-writer-wins `source` (whose `origin` is what
    # authorises this repo to write the positional blocks at all \u2014 Issue #18 \u00a71a).
    with DocumentRecord("CTX88", "page-classification", out_dir=str(doc_dir)) as doc:
        doc.set_source(sha256="b" * 64, filename="CTX88.alto.xml", origin="ABBYY-ALTO")
        doc.merge_block("pages", [{"page": "1", "category": "Text", "category_confidence": 0.88}])
        doc.set_block("page_categories", {"1": "Text"})
    with DocumentRecord.open(
        "CTX88", "nlp-enrich", baseline=str(doc_dir / "CTX88.document.json"), out_dir=str(doc_dir)
    ) as doc:
        doc.set_block("entities", [{"surface": "Brno", "type_teitok": "LOC"}])

    baseline_path = doc_dir / "CTX88.document.json"
    baseline_before = load_document(str(baseline_path))

    # Now the alto-tools extraction contributes its share, exactly as main() does.
    txt_dir = tmp_path / "txt"
    (txt_dir / "CTX88").mkdir(parents=True)
    (txt_dir / "CTX88" / "CTX88-1.txt").write_text("Extracted page text", encoding="utf-8")

    pages, content = document_hook.pages_and_content_from_text(str(txt_dir), "CTX88", [1], engine="alto-tools")
    document_hook.write_document_block(
        str(doc_dir),
        "CTX88",
        "r9",
        "paradata/r9_ocr-postprocess.json",
        merge_blocks={"pages": pages},
        set_blocks={"content": content},
    )

    after = load_document(str(baseline_path))

    assert after["source"] == baseline_before["source"]
    assert after["page_categories"] == baseline_before["page_categories"]
    assert after["entities"] == baseline_before["entities"]

    assert len(after["pages"]) == 1
    row = after["pages"][0]
    assert row["category"] == "Text"
    assert row["category_confidence"] == 0.88
    assert row["ocr"] == {"engine": "alto-tools"}

    assert after["content"] == {"text": "Extracted page text"}
