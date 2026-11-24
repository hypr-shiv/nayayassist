"""
NyayaAssist Model Context Protocol (MCP) Server.
Implements the MCP specification from MCP.md over stdio transport.
Exposes deterministic legal parsing, deadline calculations, and citation verification.
Strictly enforces INV-01 to INV-10.
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    from mcp.server.mcpserver import MCPServer
    MCPServerClass = MCPServer
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP
        MCPServerClass = FastMCP
    except ImportError:
        MCPServerClass = None

from app import config, nlp, retrieval, analyzer, parser
from app.models import DateItem, Block, StatuteSection

# Initialize Server
if MCPServerClass:
    mcp = MCPServerClass(
        name="NyayaAssist Legal Intelligence",
        description="Deterministic legal document parsing, deadline calculations, and citation verification for Indian law."
    )
else:
    mcp = None


def calculate_deadline(duration_phrase: str, anchor_date_iso: str, anchor_type: str = "issue") -> dict:
    """Deterministically calculate a deadline date from a relative phrase like 'within 15 days'."""
    explicit = [DateItem(raw=anchor_date_iso, date_iso=anchor_date_iso, role=anchor_type)]
    dummy_block = Block(id="b0", page=1, bbox=[0, 0, 1, 1], text=duration_phrase, source="text_layer")
    computed = nlp.compute_relative_deadlines([dummy_block], explicit)
    
    if computed:
        d = computed[0]
        return {
            "computed_date_iso": d.date_iso,
            "is_working_days": d.is_computed and d.date_iso is None,
            "confidence": "yellow",
            "basis": d.computation_basis,
            "disclaimer": config.DISCLAIMER
        }
    return {
        "computed_date_iso": None,
        "error": "Could not extract relative duration phrase",
        "disclaimer": config.DISCLAIMER
    }


def search_statute_corpus(query: str, max_results: int = 3) -> list[dict]:
    """Retrieve verified Indian statutes (e.g. Section 138 NI Act, Order VIII Rule 1 CPC)."""
    store = retrieval.get_statute_store()
    results = store.search(query, limit=max_results)
    return [
        {
            "statute_code": s.id,
            "act": s.act,
            "section": s.section,
            "title": s.title,
            "verbatim_text": s.text,
            "is_verified": s.verified,
            "source_url": s.source_url
        }
        for s in results
    ]


def validate_claim_citation(claim_text: str, cited_sentence_texts: list[str], statute_is_verified: bool = False) -> dict:
    """Validates that a claim cites text properly and passes token-boundary number checks (INV-01, INV-03)."""
    combined_text = " ".join(cited_sentence_texts)
    num_check_passed = analyzer._check_claim_numbers(claim_text, combined_text)
    
    dummy_statute = StatuteSection(
        id="MOCK-LAW", act="Indian Law", section="1", title="Title", text="Text",
        source_url="https://indiacode.nic.in", retrieved_on="2026-11-21", verified=statute_is_verified
    ) if statute_is_verified else None
    
    conf, reasons = analyzer._score_confidence(
        source_relation="supports",
        ocr_conf=95.0,
        is_computed=False,
        statute=dummy_statute,
        number_check_passed=num_check_passed
    )
    return {
        "is_valid": num_check_passed,
        "confidence_level": conf,
        "reasons": reasons,
        "number_check_passed": num_check_passed,
        "disclaimer": config.DISCLAIMER
    }


def lookup_glossary_term(term: str) -> dict:
    """Retrieves plain-language definition for Indian legal terminology."""
    glossary_path = config.BASE_DIR / "data" / "glossary.json"
    if glossary_path.exists():
        with open(glossary_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            if item["term"].lower() == term.strip().lower():
                return {"term": item["term"], "meaning": item["meaning"]}
    return {"error": f"Term '{term}' not found in seed glossary."}


def get_disclaimer() -> str:
    """Returns the mandatory statutory disclaimer."""
    return config.DISCLAIMER


def get_all_glossary_terms() -> str:
    """Returns all curated legal terminology."""
    glossary_path = config.BASE_DIR / "data" / "glossary.json"
    if glossary_path.exists():
        return glossary_path.read_text(encoding="utf-8")
    return "[]"


# Register tools and resources if mcp server instance is available
if mcp:
    mcp.tool()(calculate_deadline)
    mcp.tool()(search_statute_corpus)
    mcp.tool()(validate_claim_citation)
    mcp.tool()(lookup_glossary_term)
    mcp.resource("nyaya://config/disclaimer")(get_disclaimer)
    mcp.resource("nyaya://glossary/all")(get_all_glossary_terms)


if __name__ == "__main__":
    if mcp:
        mcp.run(transport="stdio")
    else:
        print("MCP is not installed. Install mcp via 'pip install mcp'")
