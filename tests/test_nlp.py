"""
Unit and invariant tests for NLP and temporal extraction (Phase 2 & 7).
Verifies INV-01, INV-03, INV-06, T-102, and T-103.
"""

import pytest
from app import nlp
from app.models import Block, DateItem
from app.analyzer import _check_claim_numbers


def test_classify_cheque_dishonour_notice():
    text = "Legal notice under Section 138 of the Negotiable Instruments Act. Cheque bounced due to insufficient funds."
    doc_type, conf = nlp.classify_document(text)
    assert doc_type == "cheque_dishonour_notice"
    assert conf >= 0.5


def test_classify_court_summons():
    text = "Court of Civil Judge. Summons to defendant. Order VIII Rule 1 CPC requires filing written statement within 30 days."
    doc_type, conf = nlp.classify_document(text)
    assert doc_type == "court_summons"
    assert conf >= 0.5


def test_date_parsing_numeric_and_roles():
    text = "Notice dated 15/03/2024. Next hearing on 25.04.2024 before the magistrate."
    dates = nlp.extract_dates_from_text(text)
    assert len(dates) == 2
    
    dates_by_role = {d.role: d.date_iso for d in dates}
    assert dates_by_role.get("issue") == "2024-03-15"
    assert dates_by_role.get("hearing") == "2024-04-25"


def test_date_parsing_textual():
    text = "Notice served on 15th March 2024. Reply due on April 10, 2024."
    dates = nlp.extract_dates_from_text(text)
    assert len(dates) == 2
    assert dates[0].date_iso == "2024-03-15"
    assert dates[1].date_iso == "2024-04-10"


def test_computed_deadline_anchored_on_issue():
    # FR-22 / INV-06
    blocks = [
        Block(
            id="b1", page=1, bbox=[0.1, 0.1, 0.9, 0.2],
            text="Notice dated 15th March 2024. You are hereby required to pay the sum within 15 days.",
            source="text_layer"
        )
    ]
    explicit = nlp.extract_dates_from_text(blocks[0].text, block_id="b1")
    computed = nlp.compute_relative_deadlines(blocks, explicit)
    
    assert len(computed) == 1
    deadline = computed[0]
    assert deadline.is_computed is True
    assert deadline.date_iso == "2024-03-30"  # 15 Mar + 15 days
    assert "Estimated" in deadline.computation_basis or "estimated" in deadline.computation_basis


def test_working_day_guard_blocks_calendar_date():
    # T-102 / TD-03 Working-Day Guard
    blocks = [
        Block(
            id="b1", page=1, bbox=[0.1, 0.1, 0.9, 0.2],
            text="Notice dated 15th March 2024. You must file a response within 15 working days.",
            source="text_layer"
        )
    ]
    explicit = nlp.extract_dates_from_text(blocks[0].text, block_id="b1")
    computed = nlp.compute_relative_deadlines(blocks, explicit)
    
    assert len(computed) == 1
    deadline = computed[0]
    assert deadline.is_computed is True
    assert deadline.date_iso is None  # MUST NOT compute a calendar date
    assert "working days" in deadline.computation_basis


def test_number_boundary_check():
    # T-103 / TD-04 / INV-03 Token-boundary number check
    cited_text = "Notice issued in the year 2025 demanding payment of Rs 50,000."
    
    # "5" should FAIL because it only exists inside "2025" and "50,000"
    assert _check_claim_numbers("The document allows 5 days to respond.", cited_text) is False
    
    # "50000" or "50,000" should PASS
    assert _check_claim_numbers("The notice demands payment of Rs 50,000.", cited_text) is True
    assert _check_claim_numbers("Payment demanded is 50000.", cited_text) is True
    
    # "2025" should PASS
    assert _check_claim_numbers("Notice was issued in 2025.", cited_text) is True


def test_glossary_term_matching():
    text = "The court initiated ex parte proceedings against the drawer after cheque dishonour."
    raw_glossary = [
        {"term": "Ex parte", "meaning": "Proceedings conducted in absence of one party."},
        {"term": "Dishonour", "meaning": "Bank refusal to pay cheque."},
        {"term": "Mesne Profits", "meaning": "Wrongful possession damages."}
    ]
    matched = nlp.find_glossary_terms(text, raw_glossary)
    terms = [m.term for m in matched]
    assert "Ex parte" in terms
    assert "Dishonour" in terms
    assert "Mesne Profits" not in terms
