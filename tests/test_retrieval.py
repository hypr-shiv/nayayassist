"""
Unit tests for legal corpus retrieval and verified provisions (Phase 3 & 7).
Verifies T-104 (4-provision verified corpus).
"""

import pytest
from app import retrieval


def test_statute_store_contains_four_verified_provisions():
    # T-104: 4 verified statutory provisions
    store = retrieval.get_statute_store()
    assert len(store.sections) >= 4
    
    expected_ids = {"NIA-138", "CPC-O8-R1", "CPA-69", "TPA-106"}
    found_ids = {s.id for s in store.sections}
    assert expected_ids.issubset(found_ids)
    
    for s_id in expected_ids:
        statute = store.get_by_id(s_id)
        assert statute is not None
        assert statute.verified is True
        assert statute.verified_by is not None
        assert statute.source_url.startswith("https://www.indiacode.nic.in")
        assert not statute.text.startswith("[UNVERIFIED")


def test_cpc_order_viii_contains_ninety_day_outer_limit():
    # TD-07: CPC Order VIII Rule 1 must include the 90-day outer limit
    store = retrieval.get_statute_store()
    cpc = store.get_by_id("CPC-O8-R1")
    assert cpc is not None
    assert "ninety days" in cpc.text.lower()
    assert "thirty days" in cpc.text.lower()


def test_bm25_retrieval_relevance():
    store = retrieval.get_statute_store()
    
    # Query for cheque bounce
    cheque_results = store.search("cheque bounce dishonour insufficient funds", limit=1)
    assert len(cheque_results) > 0
    assert cheque_results[0].id == "NIA-138"
    
    # Query for written statement
    ws_results = store.search("written statement defence summons timeline", limit=1)
    assert len(ws_results) > 0
    assert ws_results[0].id == "CPC-O8-R1"
