"""
Tests for Model Context Protocol (MCP) server endpoints and deterministic tools.
Verifies MCP tools comply with INV-01 to INV-10 and return proper statutory disclaimers.
"""

import pytest
import json
from app.mcp_server import (
    calculate_deadline,
    search_statute_corpus,
    validate_claim_citation,
    lookup_glossary_term,
    get_disclaimer,
    get_all_glossary_terms
)
from app import config


def test_mcp_calculate_deadline_calendar():
    res = calculate_deadline("The recipient must clear all dues within 15 days of this notice", "2024-03-01", "issue")
    assert res["computed_date_iso"] == "2024-03-16"
    assert res["confidence"] == "yellow"
    assert res["is_working_days"] is False
    assert config.DISCLAIMER in res["disclaimer"]


def test_mcp_calculate_deadline_working_day_guard():
    res = calculate_deadline("The recipient must respond within 15 working days", "2024-03-01", "issue")
    assert res["computed_date_iso"] is None
    assert res["is_working_days"] is True
    assert "working days" in res["basis"].lower()
    assert config.DISCLAIMER in res["disclaimer"]


def test_mcp_search_statute_corpus():
    results = search_statute_corpus("cheque dishonour section 138", max_results=2)
    assert len(results) > 0
    top = results[0]
    assert "138" in top["section"]
    assert top["is_verified"] is True
    assert "indiacode.nic.in" in top["source_url"]


def test_mcp_validate_claim_citation_valid():
    claim = "The notice demands payment of 50,000 rupees within 15 days."
    cited = ["You are hereby required to pay the sum of Rs 50000 within 15 days of receipt."]
    res = validate_claim_citation(claim, cited, statute_is_verified=True)
    assert res["is_valid"] is True
    assert res["confidence_level"] == "green"
    assert config.DISCLAIMER in res["disclaimer"]


def test_mcp_validate_claim_citation_invalid_number_downgrades_to_red():
    claim = "The notice demands 999999 rupees."
    cited = ["You owe Rs 50000 as per the agreement."]
    res = validate_claim_citation(claim, cited, statute_is_verified=True)
    assert res["is_valid"] is False
    assert res["confidence_level"] == "red"
    assert any("numerical figures not found" in r for r in res["reasons"])


def test_mcp_lookup_glossary_term():
    res = lookup_glossary_term("vakalatnama")
    assert "meaning" in res
    assert "advocate" in res["meaning"].lower() or "lawyer" in res["meaning"].lower() or "authoriz" in res["meaning"].lower()

    not_found = lookup_glossary_term("nonexistent_gibberish_term")
    assert "error" in not_found


def test_mcp_resources():
    disclaimer = get_disclaimer()
    assert config.DISCLAIMER in disclaimer
    assert "ADVOCATE" in disclaimer.upper()

    all_terms = get_all_glossary_terms()
    terms = json.loads(all_terms)
    assert isinstance(terms, list)
    assert len(terms) >= 20
