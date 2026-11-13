"""
tests/test_document_hook.py — tests for the LIVE document_hook.py surface
(write_document_block / resolve_document_json_dir / pages_and_content_from_text),
which every stage script (page_split, aggregate_STAT, classify_TEXT, the four
extract_*) actually calls.

Record schema 2.0 (atrium-project#84): this repo is the record builder. The hook writes
the positional blocks of every record, whatever its origin (the origin guard and the OCR
hand-off are gone), keys page rows by `page_index`, gives each line its `text_source` and
`bbox_precision` and each page its `content_status`, and writes no run data: the record
read and written are named in the stage's paradata instead, by SHA-512.
"""

import json
import os

import jsonschema
import pytest

import document_hook
from atrium_document import load_document
from atrium_paradata import ParadataLogger
from document_hook import (
    document_path,
    pages_and_content_from_text,
    resolve_document_json_dir,
    write_document_block,
)


def test_resolve_document_json_dir_env_wins(monkeypatch):
    monkeypatch.setenv("DOCUMENT_JSON_DIR", "/from/env")
    assert resolve_document_json_dir("/from/config") == "/from/env"


def test_resolve_document_json_dir_falls_back_to_config(monkeypatch):
    monkeypatch.delenv("DOCUMENT_JSON_DIR", raising=False)
    assert resolve_document_json_dir("/from/config") == "/from/config"


def test_resolve_document_json_dir_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DOCUMENT_JSON_DIR", raising=False)
    assert resolve_document_json_dir(None) == ""
    assert resolve_document_json_dir("") == ""


def test_write_document_block_noop_when_dir_falsy(monkeypatch, tmp_path):
    # Rule 3 / enablement: a falsy document_json_dir must return immediately,
    # before any filesystem access — verified by running from an empty cwd.
    monkeypatch.chdir(tmp_path)
    result = write_document_block("", "CTX01", set_blocks={"content": {"text": "hi"}})
    assert result is None
    assert list(tmp_path.iterdir()) == []


def test_write_document_block_merges_without_erasing_co_owned_fields(tmp_path):
    doc_dir = str(tmp_path)

    # page-classification writes first: category + category_confidence on page 1.
    from atrium_document import DocumentRecord

    with DocumentRecord("CTX01", "page-classification", out_dir=doc_dir) as doc:
        doc.merge_block("pages", [{"page_index": 1, "category": "Text", "category_confidence": 0.97}])

    # alto-postprocess contributes via the live hook — quality fields on the SAME page row.
    write_document_block(
        doc_dir,
        "CTX01",
        merge_blocks={"pages": [{"page": "1", "quality_score": 0.98, "quality_band": "Clear"}]},
    )

    record = load_document(document_path(doc_dir, "CTX01"))
    assert len(record["pages"]) == 1
    page = record["pages"][0]
    # Both contributions must survive on the same row.
    assert page["category"] == "Text"
    assert page["category_confidence"] == 0.97
    assert page["quality_score"] == 0.98
    assert page["quality_band"] == "Clear"
    assert page["page_index"] == 1 and page["page"] == "1"


def test_write_document_block_set_blocks_uses_set_block(tmp_path):
    doc_dir = str(tmp_path)
    write_document_block(
        doc_dir,
        "CTX02",
        set_blocks={"content": {"text": "full document text"}},
    )
    record = load_document(document_path(doc_dir, "CTX02"))
    assert record["content"] == {"text": "full document text"}
    assert "assembled" not in record and "provenance" not in record, "no run data in the record"


def test_write_document_block_keeps_an_amcr_seed(tmp_path, capsys):
    """atrium-project#71: a seed at the record's path (a foreign doc_id, the archive's sha512,
    file name and media type) is a valid baseline. The stage keeps the seed's identity and adds
    the origin, as record schema 2.0 holds it (`legacy` for PCSS ALTO); it stamps nothing."""
    seed = {
        "doc_id": "AMCR-F-CTX01",
        "source": {"sha512": "c" * 128, "filename": "CTX01.pdf", "media_type": "application/pdf"},
    }
    with open(document_path(str(tmp_path), "CTX01"), "w", encoding="utf-8") as f:
        json.dump(seed, f)

    written = write_document_block(
        str(tmp_path),
        "CTX01",
        source={
            "sha512": "c" * 128,
            "filename": "CTX01.alto.xml",
            "media_type": "application/alto+xml",
            "origin": "ABBYY-ALTO",
        },
        merge_blocks={"pages": [{"page": "1", "quality_score": 0.98, "quality_band": "Clear"}]},
    )

    assert written == document_path(str(tmp_path), "CTX01")
    record = load_document(written)
    assert record["doc_id"] == "AMCR-F-CTX01"
    assert record["source"] == {**seed["source"], "origin": "legacy"}
    assert "assembled" not in record and "provenance" not in record
    assert "WARNING" not in capsys.readouterr().err


def test_write_document_block_names_the_record_in_the_paradata(tmp_path):
    """Paradata 3.0 (#84 §4): the record read and the record written, by distribution name and
    SHA-512, the output with the blocks and fields the stage wrote; never a path."""
    doc_dir = str(tmp_path / "records")
    os.makedirs(doc_dir)
    with open(document_path(doc_dir, "CTX05"), "w", encoding="utf-8") as f:
        json.dump({"doc_id": "CTX05", "source": {"sha512": "c" * 128}}, f)
    run = ParadataLogger(program="ocr-postprocess", config={}, paradata_dir=None)

    write_document_block(
        doc_dir,
        "CTX05",
        merge_blocks={"lines": [{"page": "1", "line": 1, "text": "a line"}]},
        logger=run,
    )
    run.finalize()
    (read,), (wrote,) = run.record["inputs"], run.record["outputs"]
    assert read["name"] == wrote["name"] == "atrium/document-json"
    assert len(read["sha512"]) == len(wrote["sha512"]) == 128 and read["sha512"] != wrote["sha512"]
    assert wrote["record_fields"]["lines"] == ["page", "line", "text"]
    assert doc_dir not in json.dumps(run.record)


def _write_pages(output_text_dir, file_id, page_texts):
    save_dir = output_text_dir / file_id
    save_dir.mkdir(parents=True, exist_ok=True)
    for page_id, text in page_texts.items():
        (save_dir / f"{file_id}-{page_id}.txt").write_text(text, encoding="utf-8")


def test_pages_and_content_default_one_row_per_page(tmp_path):
    _write_pages(tmp_path, "doc1", {1: "Page one text", 2: "   "})

    pages, content = pages_and_content_from_text(str(tmp_path), "doc1", [1, 2])

    assert pages == [
        {"page_index": 1, "page": "1", "content_status": "text"},
        {"page_index": 2, "page": "2", "content_status": "empty"},
    ], "keyed by page_index, no engine (a paradata parameter since 2.0)"
    assert content == {"text": "Page one text\n\n   "}


def test_pages_and_content_force_single_page_collapses_rows(tmp_path):
    _write_pages(tmp_path, "doc2", {1: "Page one text", 2: "Page two text", 3: "Page three text"})

    pages, content = pages_and_content_from_text(str(tmp_path), "doc2", [1, 2, 3], force_single_page=True)

    # Exactly one schema-valid pages[] row, regardless of source page count.
    assert pages == [{"page_index": 1, "page": "1", "content_status": "text"}]
    # content.text is identical to the non-forced case: force_single_page is an
    # assembly-only policy switch, it does not change what gets extracted.
    assert content == {"text": "Page one text\n\nPage two text\n\nPage three text"}


def test_pages_and_content_force_single_page_skips_unreadable_pages_without_duplication(tmp_path):
    # Only page 1 is actually written to disk; page 2 is missing (e.g. its
    # extraction failed) and must be skipped, not guessed at or duplicated.
    _write_pages(tmp_path, "doc3", {1: "Only readable page"})

    pages, content = pages_and_content_from_text(str(tmp_path), "doc3", [1, 2], force_single_page=True)

    assert len(pages) == 1
    assert content == {"text": "Only readable page"}


def test_pages_and_content_force_single_page_no_readable_pages_yields_no_rows(tmp_path):
    pages, content = pages_and_content_from_text(str(tmp_path), "doc4", [1, 2], force_single_page=True)

    assert pages == []
    assert content == {"text": None}


# ── (atrium-project#10 D4) the Layer D validation gate at the chokepoint ─────
#
# These pin the POLICY, not merely that the call exists: hard-fail on this repo's own
# output, warn on an inherited baseline, and degrade loudly (never silently) when
# jsonschema is unavailable. Before this, validate_document() had zero production call
# sites in any of the five repos, so "no doc.json is emitted if validation fails"
# protected nothing at all.


def _write_raw_record(path, record):
    """A baseline written WITHOUT DocumentRecord, so it can be deliberately invalid."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)


def test_invalid_inherited_baseline_warns_but_still_accretes(tmp_path, capsys):
    """An upstream tool's invalid record must not stall this stage: warn, name the
    schema error, keep going. Refusing here would turn one bad record into a stalled
    pipeline, and rule 6 already commits to passing unknown content through."""
    doc_dir = str(tmp_path)
    # quality_score has `maximum: 1` in the schema — 5 is invalid, and load_document
    # is happy to read it, which is exactly the situation this policy is about.
    _write_raw_record(
        document_path(doc_dir, "CTXbad"),
        {
            "schema_version": "2.0",
            "record_type": "atrium-document",
            "doc_id": "CTXbad",
            "pages": [{"page_index": 1, "page": "1", "quality_score": 5}],
        },
    )

    write_document_block(
        doc_dir,
        "CTXbad",
        merge_blocks={"pages": [{"page": "1", "quality_band": "Clear"}]},
    )

    err = capsys.readouterr().err
    assert "inherited baseline" in err
    assert "does not validate" in err
    # The stage's own contribution landed, and the record was still written.
    record = load_document(document_path(doc_dir, "CTXbad"))
    assert record["pages"][0]["quality_band"] == "Clear"
    # Its own output gate was downgraded to a warning, since the defect is inherited.
    assert "downgraded" in err


def test_invalid_own_output_raises_and_emits_no_record(tmp_path):
    """The other half of the policy: this repo must never EMIT an invalid record. The
    raise happens inside DocumentRecord's context manager body, which is what keeps
    finalize() from running — so there is no record and no leftover .tmp either."""
    doc_dir = str(tmp_path)
    with pytest.raises(jsonschema.ValidationError):
        write_document_block(
            doc_dir,
            "CTXown",
            # quality_score is ours to write, so nothing filters it — it is simply
            # out of the schema's [0, 1] range.
            merge_blocks={"pages": [{"page": "1", "quality_score": 42}]},
        )
    assert list(tmp_path.iterdir()) == []


def test_missing_jsonschema_warns_once_and_does_not_block_the_write(tmp_path, monkeypatch, capsys):
    """validate_document() raises RuntimeError when jsonschema is absent, deliberately,
    so a gate cannot quietly become a no-op. The hook must catch THAT case apart from a
    real validation failure: one loud warning naming the dependency, then continue —
    a standalone run cannot be made to depend on an optional package (rule 3)."""

    def _no_jsonschema(_record):
        raise RuntimeError("jsonschema is not installed, so the record cannot be validated.")

    monkeypatch.setattr(document_hook, "validate_document", _no_jsonschema)
    monkeypatch.setattr(document_hook, "_VALIDATION_UNAVAILABLE_WARNED", False)

    doc_dir = str(tmp_path)
    write_document_block(doc_dir, "CTXnojs", set_blocks={"content": {"text": "hi"}})
    write_document_block(doc_dir, "CTXnojs", set_blocks={"content": {"text": "hi again"}})

    err = capsys.readouterr().err
    assert "jsonschema" in err
    assert "DISABLED" in err
    # Loud once, not once per document — a batch run holds thousands of them.
    assert err.count("validation is DISABLED") == 1
    assert load_document(document_path(doc_dir, "CTXnojs"))["content"] == {"text": "hi again"}


# ── (atrium-project#10 D8) merge_block()'s silent filtering, made loud ───────


def test_merge_of_a_field_outside_this_repos_grant_raises(tmp_path):
    """`category` belongs to page-classification. merge_block() drops it silently and
    the result still validates (pages[] requires only `page_index`), which is how a wrong
    grant produced rows stripped down to their key. assert_fields_survived() turns that
    into a failure at the call site that got it wrong."""
    doc_dir = str(tmp_path)
    with pytest.raises(RuntimeError, match="dropped by merge_block"):
        write_document_block(
            doc_dir,
            "CTXgrant",
            merge_blocks={"pages": [{"page": "1", "quality_score": 0.9, "category": "Text"}]},
        )
    assert list(tmp_path.iterdir()) == []


def test_declared_fields_survive_the_merge(tmp_path):
    """The same assertion must stay silent for every field this repo really owns, on
    both blocks it merges into — otherwise it would fire on production call sites."""
    doc_dir = str(tmp_path)
    write_document_block(
        doc_dir,
        "CTXok",
        merge_blocks={
            "pages": [{"page": "1", "quality_score": 0.9, "quality_band": "Clear"}],
            "lines": [
                {
                    "page": "1",
                    "line": 1,
                    "text": "a line",
                    "block": "p1-b1",
                    "lang": "ces",
                    "quality_score": 0.8,
                    "categ": "Clear",
                }
            ],
        },
    )
    record = load_document(document_path(doc_dir, "CTXok"))
    assert record["lines"][0]["text"] == "a line" and record["lines"][0]["block"] == "p1-b1"
    assert record["pages"][0]["content_status"] == "text"


def test_write_document_block_records_source_once(tmp_path, capsys):
    doc_dir = str(tmp_path)
    write_document_block(
        doc_dir,
        "CTX03",
        source={"sha512": "a" * 128, "sha256": "f" * 64, "filename": "CTX03.alto.xml", "origin": "ABBYY-ALTO"},
    )
    # A later write must not overwrite an existing source (set_source is first-writer-wins).
    write_document_block(
        doc_dir,
        "CTX03",
        source={"sha512": "b" * 128, "filename": "other.xml"},
    )
    record = load_document(document_path(doc_dir, "CTX03"))
    assert record["source"] == {"sha512": "a" * 128, "filename": "CTX03.alto.xml", "origin": "legacy"}
    assert "conflicting values" in capsys.readouterr().err


# ── record schema 2.0: the builder writes every record's positional blocks (#84) ─────────────

_LINES = [
    {"page": "1", "line": 1, "text": "a line", "categ": "Clear", "quality_score": 0.9, "bbox": [1, 2, 30, 12]},
    {"page": "1", "line": 2, "text": "no box"},
]


@pytest.mark.parametrize(
    "origin, mapped",
    [
        ("digital-born-pdf", "digital"),
        ("digital-born-docx", "digital"),
        ("ABBYY-ALTO", "legacy"),
        ("ocr:pero", "atr"),
        ("vlm:qwen", "atr"),
        ("unknown-method", None),
        ("", None),
    ],
)
def test_an_input_origin_maps_onto_the_records_three(origin, mapped):
    assert document_hook.record_origin(origin) == mapped


def _seed_source(doc_dir, doc_id, origin, pages=None):
    from atrium_document import DocumentRecord

    with DocumentRecord(doc_id, "ocr-postprocess", out_dir=str(doc_dir)) as doc:
        doc.set_source(sha512="c" * 128, filename=f"{doc_id}.bin", origin=origin)
        if pages:
            doc.merge_block("pages", pages)


@pytest.mark.parametrize("origin, text_source", [("digital", "text-layer"), ("legacy", "legacy"), ("atr", "atr")])
def test_every_origin_gets_its_lines_with_their_text_source(tmp_path, origin, text_source):
    """Until #84 a born-digital record was digital-convert's, and this hook dropped its lines and
    pages (the origin guard); the record builder now writes them for every origin."""
    doc_dir = str(tmp_path)
    _seed_source(doc_dir, "R1", origin)
    write_document_block(
        doc_dir,
        "R1",
        merge_blocks={"pages": [{"page": "1"}, {"page": "2"}], "lines": _LINES},
    )
    record = load_document(document_path(doc_dir, "R1"))
    assert [(r["text_source"], r["bbox_precision"] if "bbox" in r else None) for r in record["lines"]] == [
        (text_source, "measured"),
        (text_source, None),
    ]
    assert [(p["page_index"], p["content_status"]) for p in record["pages"]] == [(1, "text"), (2, "empty")]


def test_an_unknown_origin_writes_no_text_source(tmp_path):
    doc_dir = str(tmp_path)
    write_document_block(
        doc_dir,
        "NEW1",
        source={"sha512": "d" * 128, "filename": "NEW1.txt", "origin": "unknown-method"},
        merge_blocks={"lines": _LINES[1:]},
    )
    record = load_document(document_path(doc_dir, "NEW1"))
    assert "origin" not in record["source"] and "text_source" not in record["lines"][0]


def test_write_document_block_writes_back_in_place_onto_a_seed_keyed_differently(tmp_path):
    """(atrium-project#68) The record keeps the seed's doc_id, but it is written back to the
    file it was read from. finalize()'s default is `<seed id>.document.json`, which no later
    stage and no caller looks up: the next write_document_block() call re-read the untouched
    seed and the one after it overwrote this one."""
    doc_dir = str(tmp_path)
    _seed_source(doc_dir, "C-202000543A-DT-27", "legacy")
    os.replace(document_path(doc_dir, "C-202000543A-DT-27"), document_path(doc_dir, "scan"))

    written = write_document_block(doc_dir, "scan", merge_blocks={"lines": _LINES})

    assert written == document_path(doc_dir, "scan")
    assert sorted(os.listdir(doc_dir)) == ["scan.document.json"]
    record = load_document(written)
    assert record["doc_id"] == "C-202000543A-DT-27"
    assert record["lines"][0]["text"] == "a line"


# ── (#31 Phase 4) SOURCE_ORIGIN_BY_KIND ───────────────────────────────────────


def test_parse_origin_by_kind_accepts_commas_newlines_and_any_case():
    parsed = document_hook.parse_origin_by_kind("XLSX = ocr:generic,\n pptx=ocr:tesseract ,", ["xlsx", "pptx", "odt"])
    assert parsed == {"xlsx": "ocr:generic", "pptx": "ocr:tesseract"}
    assert document_hook.parse_origin_by_kind("", ["xlsx"]) == {}


@pytest.mark.parametrize(
    "raw,why",
    [
        ("xlsx ocr:generic", "is not <kind> = <origin>"),
        ("xls = ocr:generic", "unknown kind"),
        ("xlsx = ocr:a, xlsx = ocr:b", "given twice"),
        ("xlsx =", "empty origin"),
        ("xlsx = scanned", "matches no known origin prefix"),
    ],
)
def test_parse_origin_by_kind_rejects_typos_naming_the_key(raw, why):
    with pytest.raises(ValueError, match="SOURCE_ORIGIN_BY_KIND") as info:
        document_hook.parse_origin_by_kind(raw, ["xlsx", "pptx"])
    assert why in str(info.value)


def test_resolve_input_origin_precedence():
    kw = dict(override="ocr:cli", env="ocr:env", configured="ocr:cfg", by_kind={"xlsx": "ocr:kind"})
    resolve = document_hook.resolve_input_origin
    assert resolve("xlsx", "digital-born-xlsx", **kw) == "ocr:cli"
    assert resolve("xlsx", "digital-born-xlsx", **{**kw, "override": ""}) == "ocr:env"
    assert resolve("xlsx", "digital-born-xlsx", **{**kw, "override": "", "env": ""}) == "ocr:kind"
    assert resolve("docx", "digital-born-docx", **{**kw, "override": "", "env": ""}) == "ocr:cfg"
    assert resolve("docx", "digital-born-docx") == "digital-born-docx"


# ── (#31 Phase 5) resume helpers ─────────────────────────────────────────────


def test_write_if_changed_keeps_an_unchanged_file_untouched(tmp_path):
    import os

    path = tmp_path / "p.txt"
    assert document_hook.write_text_if_changed(path, "řádek\nřádek 2", newline="\n") is True
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns - 10**12, st.st_mtime_ns - 10**12))
    old = os.stat(path).st_mtime_ns
    assert document_hook.write_text_if_changed(path, "řádek\nřádek 2", newline="\n") is False
    assert os.stat(path).st_mtime_ns == old
    assert document_hook.write_text_if_changed(path, "jiný řádek", newline="\n") is True
    assert path.read_text(encoding="utf-8") == "jiný řádek"
    assert document_hook.write_bytes_if_changed(tmp_path / "b.bin", b"\x00") is True


def test_output_is_current_compares_file_times(tmp_path):
    import os

    src, out = tmp_path / "in.txt", tmp_path / "out.csv"
    assert document_hook.output_is_current(out, [src]) is False  # no output yet
    src.write_text("x", encoding="utf-8")
    out.write_text("y", encoding="utf-8")
    st = os.stat(src)
    os.utime(src, ns=(st.st_atime_ns - 10**12, st.st_mtime_ns - 10**12))
    assert document_hook.output_is_current(out, [src, tmp_path / "missing.txt"]) is True
    os.utime(src, ns=(st.st_atime_ns + 10**12, st.st_mtime_ns + 10**12))
    assert document_hook.output_is_current(out, [src]) is False


def test_read_page_index_keeps_doc_ids_as_text(tmp_path):
    path = tmp_path / "stats.csv"
    path.write_text("file,page,path\n0001,1,a\nNA,2,b\n12,3,c\n", encoding="utf-8")
    df = document_hook.read_page_index(str(path))
    assert list(df["file"]) == ["0001", "NA", "12"]
    assert list(df["page"]) == [1, 2, 3]
