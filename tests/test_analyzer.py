"""
Unit and invariant tests for analyzer and confidence scoring (Phase 4 & 7).
Verifies INV-01, INV-02, INV-04, INV-05, INV-06, and ADR-005.
"""

import pytest
from app import analyzer
from app.models import StatuteSection, Block


def test_confidence_deterministic_scoring():
    # INV-04: Explicit rule-based scoring
    # 1. Clean quote + Verified statute -> Green
    verified_statute = StatuteSection(
        id="NIA-138", act="Negotiable Instruments Act, 1881", section="138",
        title="Cheque Dishonour", text="Section 138 text...", source_url="https://indiacode.nic.in",
        retrieved_on="2026-11-21", verified=True
    )
    level, reasons = analyzer._score_confidence(
        source_relation="supports",
        ocr_conf=95.0,
        is_computed=False,
        statute=verified_statute,
        number_check_passed=True
    )
    assert level == "green"

    # 2. Low OCR (< 60) -> Red
    level_red, reasons_red = analyzer._score_confidence(
        source_relation="supports",
        ocr_conf=45.0,
        is_computed=False,
        statute=verified_statute,
        number_check_passed=True
    )
    assert level_red == "red"
    assert "low OCR" in reasons_red[0]


def test_unverified_statute_caps_yellow():
    # INV-05: Unverified law section caps confidence at Yellow
    unverified_statute = StatuteSection(
        id="TEST-LAW", act="Some Act", section="1",
        title="Title", text="Text...", source_url="https://example.com",
        retrieved_on="2026-11-21", verified=False
    )
    level, reasons = analyzer._score_confidence(
        source_relation="supports",
        ocr_conf=95.0,
        is_computed=False,
        statute=unverified_statute,
        number_check_passed=True
    )
    assert level == "yellow"
    assert any("not yet verified" in r for r in reasons)


def test_computed_dates_cap_yellow():
    # INV-06: Computed dates cap at Yellow
    level, reasons = analyzer._score_confidence(
        source_relation="supports",
        ocr_conf=95.0,
        is_computed=True,
        statute=None,
        number_check_passed=True
    )
    assert level == "yellow"
    assert any("estimated from relative duration" in r for r in reasons)


def test_unsupported_number_downgrades_to_red():
    # INV-03: Unsupported figures force Red
    level, reasons = analyzer._score_confidence(
        source_relation="supports",
        ocr_conf=95.0,
        is_computed=False,
        statute=None,
        number_check_passed=False
    )
    assert level == "red"
    assert any("numerical figures not found" in r for r in reasons)


def test_unknown_refs_discarded_by_source_pack():
    # INV-02: Unknown reference IDs must be discarded
    pack = analyzer.SourcePack()
    pack.add_doc_sentence("Sentence 1", page=1, block_id="b1", bbox=[0.1, 0.1, 0.9, 0.2], ocr_conf=90.0)
    
    # Pack has D1 only. References to D99 or L42 must not resolve
    assert "D1" in pack.doc_sources
    assert "D99" not in pack.doc_sources
    assert "L42" not in pack.law_sources


def test_rule_mode_always_cites_document():
    # INV-01: Rule mode claims always quote directly with document citation
    key_sents = [("Notice demands payment of Rs 50,000.", "Sets a time limit", "b1")]
    blocks = [
        Block(
            id="b1", page=1, bbox=[0.1, 0.1, 0.9, 0.2],
            text="Notice demands payment of Rs 50,000 within 15 days.",
            source="text_layer"
        )
    ]
    from app import retrieval
    store = retrieval.get_statute_store()
    claims = analyzer._build_rule_claims(key_sents, blocks, store)
    
    assert len(claims) == 1
    c = claims[0]
    assert len(c.citations) >= 1
    assert c.citations[0].kind == "document"
    assert c.citations[0].ref_id == "b1"
    assert c.confidence == "yellow"  # Rule mode caps at yellow (ADR-005)
