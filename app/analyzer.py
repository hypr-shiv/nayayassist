"""
Orchestration, validation, scoring, and Q&A engine for NyayaAssist.
The ONLY module permitted to assemble the SourcePack, invoke the LLM, validate citations,
and assign confidence scores (Architectural Rule 4.1).
Strictly enforces Invariants INV-01 to INV-10.
"""

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional

from app import config, nlp, retrieval
from app.models import (
    AnalysisResult, AnalysisRun, Block, Claim, Citation, DateItem,
    GlossaryItem, Page, StatuteSection, Answer, LLMExplainOutput, LLMQAOutput
)
from app.llm import get_llm_provider

logger = logging.getLogger("nyayaassist.analyzer")

# Prompt Definitions (T-106)
EXPLAIN_SYSTEM_PROMPT = """You are a legal document assistant explaining an Indian legal document to an ordinary citizen.
Use ONLY the numbered statements provided in the prompt (<doc_sentence id="D#"> and <law_section id="L#">).
Never use outside knowledge. Never guess, invent, or extrapolate facts.
Every claim MUST cite at least one document reference (e.g. ["D1"]).
Plain language, maximum 25 words per claim.
Provide objective factual information only. Do NOT give legal advice, strategy, or outcome predictions.
Never say what the recipient should argue, file, or whether they will win or lose.
If critical information is missing from the document, list it under "unclear".

Respond strictly in valid JSON matching this schema:
{
  "claims": [
    {
      "plain_text": "Plain English statement under 25 words",
      "document_refs": ["D1"],
      "statute_refs": ["L1"]
    }
  ],
  "unclear": ["List of missing facts, e.g. 'Date of receipt not stated'"]
}"""

QA_SYSTEM_PROMPT = """You are a legal document query assistant answering a specific citizen question about their document.
Answer strictly and exclusively using the provided numbered lines (<doc_sentence id="D#"> and <law_section id="L#">).
Do NOT extrapolate, deduce, or give legal advice. Maximum 80 words.
Every statement must cite at least one document reference (e.g. ["D1"]).
If the question asks for legal strategy (e.g., 'Should I pay?', 'Will I win?', 'What should I argue?'), you must refuse:
State: "NyayaAssist provides document facts only, not legal strategy. Consult a licensed advocate registered with the State Bar Council."

Respond strictly in valid JSON matching this schema:
{
  "answer": "Concise answer strictly grounded in the lines provided",
  "document_refs": ["D1"],
  "law_refs": ["L1"]
}"""


class SourcePack:
    """Delimited container for document sentences and statute sections."""
    def __init__(self):
        self.doc_sources: Dict[str, Dict[str, Any]] = {}   # D1 -> {text, page, block_id, bbox, ocr_conf}
        self.law_sources: Dict[str, StatuteSection] = {}    # L1 -> StatuteSection

    def add_doc_sentence(self, text: str, page: int, block_id: str, bbox: List[float], ocr_conf: Optional[float]) -> str:
        tag = f"D{len(self.doc_sources) + 1}"
        self.doc_sources[tag] = {
            "text": text,
            "page": page,
            "block_id": block_id,
            "bbox": bbox,
            "ocr_conf": ocr_conf
        }
        return tag

    def add_statute(self, statute: StatuteSection) -> str:
        # Check if already added
        for tag, s in self.law_sources.items():
            if s.id == statute.id:
                return tag
        tag = f"L{len(self.law_sources) + 1}"
        self.law_sources[tag] = statute
        return tag

    def render_xml(self) -> str:
        """Renders delimited XML tags to prevent prompt injection (INV-09)."""
        lines = ["<document_sources>"]
        for tag, data in self.doc_sources.items():
            lines.append(f'  <doc_sentence id="{tag}">{data["text"]}</doc_sentence>')
        lines.append("</document_sources>")
        
        if self.law_sources:
            lines.append("<statutory_sources>")
            for tag, s in self.law_sources.items():
                verified_note = "[VERIFIED INDIA CODE]" if s.verified else "[UNVERIFIED PARAPHRASE]"
                lines.append(f'  <law_section id="{tag}" status="{verified_note}">{s.act} {s.section}: {s.title} - {s.text}</law_section>')
            lines.append("</statutory_sources>")
            
        return "\n".join(lines)


def _check_claim_numbers(claim_text: str, cited_text: str) -> bool:
    """
    Validates that every numeric figure in an LLM claim exists in cited source text
    using token-boundary regex (INV-03 / T-103).
    Normalizes thousands separator commas (e.g. 50,000 or 5,00,000) so formatting
    differences don't fail, while preventing '2025' satisfying '5'.
    """
    # Normalize internal digit grouping commas in both texts (e.g. 50,000 -> 50000)
    norm_cited = re.sub(r"(?<=\d),(?=\d)", "", cited_text)
    norm_claim = re.sub(r"(?<=\d),(?=\d)", "", claim_text)
    
    # Extract numbers (integers, decimals)
    numbers_in_claim = re.findall(r"\b\d+(?:\.\d+)?\b", norm_claim)
    
    for num_str in numbers_in_claim:
        pattern = rf"\b{re.escape(num_str)}\b"
        if not re.search(pattern, norm_cited):
            return False
            
    return True


def _score_confidence(
    source_relation: str,
    ocr_conf: Optional[float],
    is_computed: bool,
    statute: Optional[StatuteSection],
    number_check_passed: bool
) -> Tuple[str, List[str]]:
    """
    Deterministic rule-based confidence scoring engine (INV-04..06 / ADR-005).
    Never permits the model to self-rate confidence.
    The most severe downgrade wins.
    """
    reasons = []
    level = "green"

    # Red condition 1: Unsupported numbers in claim (INV-03)
    if not number_check_passed:
        return "red", ["Claim contains numerical figures not found in the cited document passage."]

    # Red condition 2: OCR confidence below 60 on cited text
    if ocr_conf is not None and ocr_conf < 60.0:
        return "red", [f"Cited document passage has low OCR scan confidence ({ocr_conf}%). Verify against the original."]

    # Yellow condition 1: Computed or estimated dates (INV-06)
    if is_computed:
        level = "yellow"
        reasons.append("Date is estimated from relative duration phrases in the document.")

    # Yellow condition 2: OCR confidence between 60 and 79
    if ocr_conf is not None and 60.0 <= ocr_conf < 80.0:
        level = "yellow"
        reasons.append(f"Cited passage has moderate OCR scan quality ({ocr_conf}%).")

    # Yellow condition 3: No law cited, or matched on keywords only (rule mode)
    if statute is None:
        if level != "red":
            level = "yellow"
            reasons.append("No specific statutory provision cited.")
    elif source_relation == "possibly_related":
        if level != "red":
            level = "yellow"
            reasons.append("Statutory section matched by keyword ranking; not confirmed to apply.")

    # Yellow condition 4: Statute text is unverified against official gazette (INV-05)
    if statute is not None and not statute.verified:
        if level != "red":
            level = "yellow"
            reasons.append(f"Statutory text for {statute.act} {statute.section} is not yet verified against the official India Code gazette.")

    if not reasons and level == "green":
        reasons.append("Directly supported by clear document text and verified statute citation.")

    return level, reasons


def _build_rule_claims(key_sents: List[Tuple[str, str, str]], blocks: List[Block], store: retrieval.StatuteStore) -> List[Claim]:
    """Generates deterministic rule-mode claims from key sentences (INV-01). Capped at Yellow."""
    claims = []
    block_map = {b.id: b for b in blocks}

    for idx, (sent, label, b_id) in enumerate(key_sents):
        blk = block_map.get(b_id)
        if not blk:
            continue
            
        claim_id = f"claim-rule-{idx+1}"
        plain_text = f"{label}: \"{sent}\""
        
        # Search for keyword-matched law
        statutes = store.search(sent, limit=1)
        citations = [
            Citation(
                kind="document",
                relation="quoted",
                ref_id=blk.id,
                label=f"Page {blk.page}",
                quote=sent,
                page=blk.page,
                bbox=blk.bbox
            )
        ]
        
        statute_item = None
        if statutes:
            statute_item = statutes[0]
            citations.append(Citation(
                kind="statute",
                relation="possibly_related",
                ref_id=statute_item.id,
                label=f"{statute_item.act}, {statute_item.section}",
                quote=statute_item.title,
                verified=statute_item.verified,
                source_url=statute_item.source_url
            ))
            
        conf, reasons = _score_confidence(
            source_relation="possibly_related",
            ocr_conf=blk.ocr_conf,
            is_computed=False,
            statute=statute_item,
            number_check_passed=True
        )
        
        claims.append(Claim(
            id=claim_id,
            text=plain_text,
            confidence=conf,
            reasons=reasons,
            citations=citations
        ))
        
    return claims


def analyze_document_pipeline(
    file_path,
    doc_id: str,
    filename: str,
    pages: List[Page],
    blocks: List[Block],
    warnings: List[str]
) -> Tuple[AnalysisResult, AnalysisRun]:
    """
    Main orchestration pipeline executing deterministic NLP, BM25 retrieval,
    LLM plain language generation, citation validation, and rule scoring.
    """
    start_time = time.time()
    full_text = " ".join(b.text for b in blocks)
    store = retrieval.get_statute_store()
    
    # 1. Classification
    doc_type, type_conf = nlp.classify_document(full_text)
    
    # 2. Date Extraction & Relative Deadlines
    explicit_dates = []
    for b in blocks:
        explicit_dates.extend(nlp.extract_dates_from_text(b.text, block_id=b.id))
        
    computed_deadlines = nlp.compute_relative_deadlines(blocks, explicit_dates)
    all_dates = explicit_dates + computed_deadlines
    # Sort timeline chronologically (FR-25)
    all_dates.sort(key=lambda d: d.date_iso if d.date_iso else "9999-99-99")
    
    # Check for passed deadlines (FR-25)
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for d in all_dates:
        if d.role == "deadline" and d.date_iso and d.date_iso < today_iso:
            exp_warn = f"Response deadline ({d.date_iso}) has already passed (expired)."
            if exp_warn not in warnings:
                warnings.append(exp_warn)
    
    # 3. Glossary matching
    try:
        glossary_path = config.BASE_DIR / "data" / "glossary.json"
        with open(glossary_path, "r", encoding="utf-8") as f:
            glossary_raw = json.load(f)
    except Exception:
        glossary_raw = []
    glossary_items = nlp.find_glossary_terms(full_text, glossary_raw)
    
    # 4. Procedural guidance
    procedural_steps = nlp.get_procedural_steps(doc_type)
    
    # 5. Build SourcePack for explanation
    key_sentences = nlp.pick_key_sentences(blocks, limit=8)
    pack = SourcePack()
    block_map = {b.id: b for b in blocks}
    
    for sent, label, b_id in key_sentences:
        blk = block_map.get(b_id)
        if blk:
            pack.add_doc_sentence(sent, blk.page, blk.id, blk.bbox, blk.ocr_conf)
            # Find relevant statutes
            matched_statutes = store.search(sent, limit=1)
            for s in matched_statutes:
                pack.add_statute(s)
                
    # 6. Explanation Generation (LLM or Rule Mode)
    llm = get_llm_provider()
    claims: List[Claim] = []
    unclear: List[str] = []
    execution_mode = "rule_based"
    provider_name = llm.name
    
    if llm.name != "none":
        try:
            xml_sources = pack.render_xml()
            user_prompt = f"Please explain the legal notice using only the following numbered statements:\n\n{xml_sources}"
            llm_raw = llm.generate_json(EXPLAIN_SYSTEM_PROMPT, user_prompt)
            
            # Pydantic validation (INV-08)
            llm_output = LLMExplainOutput(**llm_raw)
            unclear.extend(llm_output.unclear)
            
            # Post-Generation Validation (INV-01, INV-02, INV-03)
            for idx, c in enumerate(llm_output.claims):
                # INV-01: Drop claims with no valid document citation
                valid_doc_refs = [r for r in c.document_refs if r in pack.doc_sources]
                if not valid_doc_refs:
                    continue  # Discard claim lacking valid D#
                    
                # INV-02: Drop unknown statute refs
                valid_law_refs = [r for r in c.statute_refs if r in pack.law_sources]
                
                # Gather cited source text for number validation (INV-03)
                combined_cited_text = " ".join(pack.doc_sources[r]["text"] for r in valid_doc_refs)
                num_check_passed = _check_claim_numbers(c.plain_text, combined_cited_text)
                
                # Build Citations
                citations = []
                mean_ocr = []
                for d_ref in valid_doc_refs:
                    d_data = pack.doc_sources[d_ref]
                    if d_data["ocr_conf"] is not None:
                        mean_ocr.append(d_data["ocr_conf"])
                    citations.append(Citation(
                        kind="document",
                        relation="supports",
                        ref_id=d_data["block_id"],
                        label=f"Page {d_data['page']}",
                        quote=d_data["text"],
                        page=d_data["page"],
                        bbox=d_data["bbox"]
                    ))
                    
                statute_item = None
                if valid_law_refs:
                    statute_item = pack.law_sources[valid_law_refs[0]]
                    citations.append(Citation(
                        kind="statute",
                        relation="supports" if statute_item.verified else "possibly_related",
                        ref_id=statute_item.id,
                        label=f"{statute_item.act}, {statute_item.section}",
                        quote=statute_item.title,
                        verified=statute_item.verified,
                        source_url=statute_item.source_url
                    ))
                    
                claim_ocr = float(sum(mean_ocr)/len(mean_ocr)) if mean_ocr else None
                conf, reasons = _score_confidence(
                    source_relation="supports" if (statute_item and statute_item.verified) else "possibly_related",
                    ocr_conf=claim_ocr,
                    is_computed=False,
                    statute=statute_item,
                    number_check_passed=num_check_passed
                )
                
                claims.append(Claim(
                    id=f"claim-llm-{idx+1}",
                    text=c.plain_text,
                    confidence=conf,
                    reasons=reasons,
                    citations=citations
                ))
                
            execution_mode = f"llm_{llm.name}"
            
        except Exception as e:
            # Fallback to rules on error (INV-08)
            warnings.append(f"LLM generation failed ({str(e)}). Fell back to deterministic rule extraction.")
            claims = _build_rule_claims(key_sentences, blocks, store)
            execution_mode = "rule_based_fallback"
    else:
        # 100% Deterministic Rule Mode
        claims = _build_rule_claims(key_sentences, blocks, store)

    duration_ms = int((time.time() - start_time) * 1000)
    
    # 7. Audit Run Record (T-105 / INV-10) - ZERO document text in audit
    audit_run = AnalysisRun(
        id=uuid.uuid4().hex,
        document_id=doc_id,
        mode=execution_mode,
        llm_provider=provider_name,
        model=getattr(llm, "model", None),
        prompt_version=config.PROMPT_VERSION,
        schema_version=config.SCHEMA_VERSION,
        duration_ms=duration_ms,
        started_at=datetime.now(timezone.utc).isoformat()
    )
    
    result = AnalysisResult(
        doc_id=doc_id,
        filename=filename,
        doc_type=doc_type,
        type_confidence=type_conf,
        pages=pages,
        blocks=blocks,
        dates=all_dates,
        claims=claims,
        glossary=glossary_items,
        procedural_steps=procedural_steps,
        warnings=warnings,
        unclear=unclear,
        disclaimer=config.DISCLAIMER
    )
    
    return result, audit_run


def answer_question(blocks: List[Block], question: str) -> Answer:
    """Answers a user question strictly using extractive document sentences and law (FR-61..64)."""
    clean_q = question.strip()
    
    # 1. Strategy Refusal Guard (FR-64 / UPL Defense)
    strategy_pattern = (
        r"\b(should i pay|will i win|how to argue|my defence|my defense|chances of winning|"
        r"should i settle|should i contest|can i win|what should i do|what strategy|give me advice|"
        r"will i go to jail|will the court dismiss|how to defend|should i ignore|what to do)\b"
    )
    if re.search(strategy_pattern, clean_q, re.IGNORECASE):
        return Answer(
            answer="NyayaAssist provides document facts only, not legal strategy. We cannot advise whether you should contest or settle. Consult a licensed advocate registered with your State Bar Council.",
            citations=[],
            confidence="red",
            disclaimer=config.DISCLAIMER
        )

    # 2. Find top 4 document sentences
    store = retrieval.get_statute_store()
    all_sentences = []
    block_map = {}
    for b in blocks:
        for s in nlp.split_sentences(b.text):
            if len(s.strip()) > 10:
                all_sentences.append(s.strip())
                block_map[s.strip()] = b
                
    # Token matching
    q_tokens = retrieval.tokenize(clean_q)
    scored_sents = []
    for s in all_sentences:
        s_tokens = set(retrieval.tokenize(s))
        overlap = len(set(q_tokens).intersection(s_tokens))
        if overlap > 0:
            scored_sents.append((s, overlap))
            
    scored_sents.sort(key=lambda x: x[1], reverse=True)
    top_sents = [s for s, _ in scored_sents[:4]]
    
    if not top_sents:
        return Answer(
            answer="The document does not appear to state information answering this question.",
            citations=[],
            confidence="red",
            disclaimer=config.DISCLAIMER
        )
        
    top_statutes = store.search(clean_q, limit=2)
    
    # 3. Model Q&A or Extractive Fallback (FR-63)
    llm = get_llm_provider()
    if llm.name != "none":
        try:
            pack = SourcePack()
            for s in top_sents:
                b = block_map[s]
                pack.add_doc_sentence(s, b.page, b.id, b.bbox, b.ocr_conf)
            for st in top_statutes:
                pack.add_statute(st)
                
            xml_pack = pack.render_xml()
            u_prompt = f"Question: {clean_q}\n\nSources:\n{xml_pack}"
            raw_qa = llm.generate_json(QA_SYSTEM_PROMPT, u_prompt)
            qa_output = LLMQAOutput(**raw_qa)
            
            # Validate citations
            cites = []
            for d_ref in qa_output.document_refs:
                if d_ref in pack.doc_sources:
                    d_data = pack.doc_sources[d_ref]
                    cites.append(Citation(
                        kind="document",
                        relation="supports",
                        ref_id=d_data["block_id"],
                        label=f"Page {d_data['page']}",
                        quote=d_data["text"],
                        page=d_data["page"],
                        bbox=d_data["bbox"]
                    ))
                    
            if not cites:
                # If model dropped citations, fall back to extractive quote
                raise ValueError("Model answer lacked document citations.")
                
            return Answer(
                answer=qa_output.answer,
                citations=cites,
                confidence="yellow",
                disclaimer=config.DISCLAIMER
            )
        except Exception:
            pass  # Fall through to extractive fallback

    # Deterministic Extractive Fallback (INV-08 / FR-63)
    closest = top_sents[0]
    b = block_map[closest]
    return Answer(
        answer=f"Closest passage found in your document: \"{closest}\"",
        citations=[
            Citation(
                kind="document",
                relation="quoted",
                ref_id=b.id,
                label=f"Page {b.page}",
                quote=closest,
                page=b.page,
                bbox=b.bbox
            )
        ],
        confidence="yellow",
        disclaimer=config.DISCLAIMER
    )
