"""
tests/test_extract_llm_alto.py – Unit tests for the pure image/config helpers in
extract_LLM_ALTO_2_TXT.py.

That module imports torch + transformers at module scope, so those tests only run
where those libraries are fully installed (e.g. the GPU environment). We gate on
the *actual* module import rather than importorskip-ing individual dependency
names, because a partial/namespace install (e.g. a `transformers` on the path
without AutoConfig) would slip past importorskip and then hard-error at
collection. Trying the real import and skipping keeps the rest of the suite
runnable. Only the model-free helpers are covered here — the GLM inference path
needs a live checkpoint.

(atrium-project#10) That skip is now PER-TEST instead of module-level. The accretion
test at the bottom exercises the document-record contribution this stage makes, which
runs entirely through document_hook + atrium_document and needs no ML library at all —
a module-level skip hid it behind dependencies it does not use, and this file's zero
document_json coverage is exactly why the identical pattern went unverified here.
"""

import pytest

import document_hook
from atrium_document import DocumentRecord, load_document

try:
    from PIL import Image

    from extract_LLM_ALTO_2_TXT import _load_extract_config, resize_if_huge, trim_whitespace

    _GLM_IMPORT_ERROR = None
except Exception as exc:  # torch / transformers / tqdm / pandas / PIL missing or partial
    _GLM_IMPORT_ERROR = exc

needs_glm_deps = pytest.mark.skipif(
    _GLM_IMPORT_ERROR is not None,
    reason=f"extract_LLM_ALTO_2_TXT dependencies unavailable: {_GLM_IMPORT_ERROR}",
)


@needs_glm_deps
def test_resize_if_huge_downscales_longest_side():
    out = resize_if_huge(Image.new("RGB", (4000, 2000), "white"), max_dim=1000)
    assert out.size == (1000, 500)


@needs_glm_deps
def test_resize_if_huge_keeps_small_image():
    out = resize_if_huge(Image.new("RGB", (300, 200), "white"), max_dim=1000)
    assert out.size == (300, 200)


@needs_glm_deps
def test_trim_whitespace_crops_to_content():
    img = Image.new("RGB", (200, 200), "white")
    for x in range(90, 110):
        for y in range(90, 110):
            img.putpixel((x, y), (0, 0, 0))
    out = trim_whitespace(img, padding=5)
    assert out.size[0] < 200 and out.size[1] < 200


@needs_glm_deps
def test_trim_whitespace_blank_image_unchanged():
    out = trim_whitespace(Image.new("RGB", (120, 120), "white"))
    assert out.size == (120, 120)


@needs_glm_deps
def test_load_extract_config_defaults_when_missing(tmp_path):
    cfg = _load_extract_config(str(tmp_path / "nope.txt"))
    assert cfg["model_path"] == "THUDM/glm-4v-9b"
    assert cfg["max_new_tokens"] == 4096


@needs_glm_deps
def test_load_extract_config_reads_overrides(tmp_path):
    cfgfile = tmp_path / "config.txt"
    cfgfile.write_text(
        "[EXTRACT]\nLLM_MODEL = my/model\nLLM_MAX_NEW_TOKENS = 128\nWORKERS_MAX_LLM = 3\n",
        encoding="utf-8",
    )
    cfg = _load_extract_config(str(cfgfile))
    assert cfg["model_path"] == "my/model"
    assert cfg["max_new_tokens"] == 128
    assert cfg["max_workers"] == 3


# ── Accretion contract (atrium-project#10) ────────────────────────────────────
#
# Ported from tests/test_extract_json.py, which held the only copy of this test even
# though all four extract_*.py stages run the IDENTICAL production pattern
# (pages_and_content_from_text -> write_document_block with merge_blocks={"pages"} +
# set_blocks={"content"}). On this path only the engine string differs.


def test_accretion_preserves_unrelated_metadata_and_co_owned_fields(tmp_path):
    """The GLM extractor may only touch its own `content` block and its own fields
    inside the field-split `pages` block; every other block, and every other tool's
    fields on a shared `pages` row, must come out exactly as they went in."""
    doc_dir = tmp_path / "doc"
    doc_dir.mkdir()

    with DocumentRecord("CTX77", "page-classification", out_dir=str(doc_dir)) as doc:
        # `vlm:` is an ORIGIN_ORIGINATORS prefix this repo owns — the truthful origin for
        # a page whose text came out of a vision model rather than an OCR engine, and the
        # value that authorises this stage to write the positional blocks (Issue #18 §1a).
        doc.set_source(sha256="d" * 64, filename="CTX77.alto.xml", origin="vlm:glm-4v")
        doc.merge_block("pages", [{"page": "1", "category": "Text", "category_confidence": 0.77}])
        doc.set_block("page_categories", {"1": "Text"})
    with DocumentRecord.open(
        "CTX77", "nlp-enrich", baseline=str(doc_dir / "CTX77.document.json"), out_dir=str(doc_dir)
    ) as doc:
        doc.set_block("entities", [{"surface": "Olomouc", "type_teitok": "LOC"}])

    baseline_path = doc_dir / "CTX77.document.json"
    baseline_before = load_document(str(baseline_path))

    txt_dir = tmp_path / "txt"
    (txt_dir / "CTX77").mkdir(parents=True)
    (txt_dir / "CTX77" / "CTX77-1.txt").write_text("Transcribed page text", encoding="utf-8")

    pages, content = document_hook.pages_and_content_from_text(str(txt_dir), "CTX77", [1], engine="glm:THUDM/glm-4v-9b")
    document_hook.write_document_block(
        str(doc_dir),
        "CTX77",
        "r7",
        "paradata/r7_ocr-postprocess.json",
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
    assert row["category_confidence"] == 0.77
    assert row["ocr"] == {"engine": "glm:THUDM/glm-4v-9b"}

    assert after["content"] == {"text": "Transcribed page text"}
