"""
Deterministic NLP engine for NyayaAssist.
Must NOT import any other module from app/ except models.py and config.py (Architectural Rule 4.1).
Handles classification, date extraction, relative deadlines, working-day guards (T-102),
glossary term detection, and procedural guidance.
"""

import re
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from app import config
from app.models import DateItem, GlossaryItem, Block

# Month name mapping
MONTH_NAMES = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

# Number words to integer
WORD_NUMS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "twenty": 20, "twenty-one": 21, "thirty": 30, "forty-five": 45, "sixty": 60, "ninety": 90
}

# Classification keywords & weightings (FR-10)
DOC_TYPE_KEYWORDS = {
    "cheque_dishonour_notice": [
        "138", "negotiable instruments", "cheque", "dishonour", "insufficient funds",
        "memo", "drawer", "payee", "cheque bounce", "bank memo"
    ],
    "court_summons": [
        "summons", "order viii", "plaint", "written statement", "civil suit",
        "appear in person", "court of", "magistrate", "judge", "suit no", "ex parte"
    ],
    "eviction_notice": [
        "vacate", "eviction", "landlord", "tenant", "tenancy", "premises",
        "hand over peaceful possession", "arrears of rent", "lease deed", "quit notice"
    ],
    "consumer_complaint_summons": [
        "consumer disputes", "district commission", "state commission", "consumer protection",
        "complainant", "deficiency of service", "unfair trade practice"
    ],
    "tax_notice": [
        "income tax", "assessment year", "scrutiny", "section 142", "section 148",
        "demand notice", "incometax", "pan", "assessing officer"
    ],
    "agreement": [
        "agreement", "memorandum of understanding", "mou", "terms and conditions",
        "indemnity", "first part", "second part", "witnesseth", "shall mutually agree"
    ],
    "legal_notice": [
        "legal notice", "under instructions from my client", "hereby call upon you",
        "notice to quit", "failing which proceedings"
    ]
}

# Key Sentence Heuristic Markers
KEY_SENTENCE_MARKERS = [
    (r"\b(within\s+\d+\s+days|within\s+[a-z]+\s+days)\b", "Sets a time limit"),
    (r"\b(appear|attendance|presence|call upon you to attend)\b", "Requires you to attend court"),
    (r"\b(pay|payment|sum of rs|amount of rs|fine|demand)\b", "Mentions money owed or a penalty"),
    (r"\b(vacate|peaceful possession|quit|hand over)\b", "Requires you to leave the premises"),
    (r"\b(shall|must|hereby called upon|strictly prohibited)\b", "Places a duty or condition on you"),
]


def classify_document(full_text: str) -> Tuple[str, float]:
    """
    Classifies legal notice into 7 categories based on keyword frequency.
    Returns (doc_type, confidence).
    """
    lower_text = full_text.lower()
    scores: Dict[str, int] = {}
    
    for doc_type, keywords in DOC_TYPE_KEYWORDS.items():
        hits = sum(1 for kw in keywords if re.search(rf"\b{re.escape(kw)}\b", lower_text))
        scores[doc_type] = hits

    best_type, best_hits = max(scores.items(), key=lambda item: item[1])
    
    if best_hits == 0:
        return "unknown", 0.0
        
    confidence = round(min(1.0, best_hits / 4.0), 2)
    return best_type, confidence


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    """Validates calendar date via standard library, rejecting invalid dates like 31/02."""
    try:
        return date(year, month, day)
    except (ValueError, OverflowError):
        return None


def extract_dates_from_text(text: str, block_id: Optional[str] = None) -> List[DateItem]:
    """
    Extracts explicit dates from text and tags roles.
    Assumes DD/MM/YYYY for ambiguous numeric dates (TD-05 / FR-20).
    """
    date_items: List[DateItem] = []
    
    # 1. Numeric Dates: DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
    num_pattern = r"\b(\d{1,2})[\/\.-](\d{1,2})[\/\.-](\d{4})\b"
    for m in re.finditer(num_pattern, text):
        raw = m.group(0)
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        valid = _safe_date(year, month, day)
        if valid:
            date_items.append(DateItem(
                raw=raw,
                date_iso=valid.isoformat(),
                role=_determine_date_role(text, m.start()),
                is_computed=False,
                block_id=block_id,
                sentence=text
            ))

    # 2. Textual Dates: 15th March 2024 or March 15, 2024
    month_regex = "|".join(MONTH_NAMES.keys())
    text_pattern1 = rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_regex})\s+(\d{{4}})\b"
    for m in re.finditer(text_pattern1, text, re.IGNORECASE):
        raw = m.group(0)
        day = int(m.group(1))
        month = MONTH_NAMES[m.group(2).lower()]
        year = int(m.group(3))
        valid = _safe_date(year, month, day)
        if valid:
            date_items.append(DateItem(
                raw=raw,
                date_iso=valid.isoformat(),
                role=_determine_date_role(text, m.start()),
                is_computed=False,
                block_id=block_id,
                sentence=text
            ))

    text_pattern2 = rf"\b({month_regex})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b"
    for m in re.finditer(text_pattern2, text, re.IGNORECASE):
        raw = m.group(0)
        month = MONTH_NAMES[m.group(1).lower()]
        day = int(m.group(2))
        year = int(m.group(3))
        valid = _safe_date(year, month, day)
        if valid:
            date_items.append(DateItem(
                raw=raw,
                date_iso=valid.isoformat(),
                role=_determine_date_role(text, m.start()),
                is_computed=False,
                block_id=block_id,
                sentence=text
            ))

    return date_items


def _determine_date_role(text: str, match_pos: int) -> str:
    """Assigns functional role based on proximity keywords in the local clause or sentence."""
    sub_before = text[:match_pos]
    sub_after = text[match_pos:]
    
    # Break on sentence or clause delimiters (. ! ? ; \n)
    last_break = max(sub_before.rfind("."), sub_before.rfind("\n"), sub_before.rfind(";"), sub_before.rfind("!"))
    clause_before = sub_before[last_break + 1:] if last_break != -1 else sub_before
    
    delims = [sub_after.find(d) for d in [".", "\n", ";", "!"] if sub_after.find(d) != -1]
    next_break = min(delims) if delims else len(sub_after)
    clause_after = sub_after[:next_break]
    
    clause = f"{clause_before} {clause_after}".lower()
    
    if any(k in clause for k in ["hearing", "appear", "bench", "court date", "appearance"]):
        return "hearing"
    if any(k in clause for k in ["reply", "response", "within", "pay before", "deadline", "by or before", "due"]):
        return "deadline"
    if any(k in clause for k in ["dated", "issued", "date of notice", "notice dated", "on this day", "dispatch"]):
        return "issue"
    if any(k in clause for k in ["receipt", "served", "delivered on", "received on", "service of"]):
        return "received"
    return "mentioned"


def split_sentences(text: str) -> List[str]:
    """Splits text into sentences while protecting legal abbreviations (No., Rs., etc.)."""
    abbrs = ["No.", "Rs.", "Sec.", "Mr.", "Ms.", "Mrs.", "Dr.", "Shri.", "Ltd.", "Pvt.", "Co.", "vs.", "v.", "Adv."]
    masked = text
    for ab in abbrs:
        masked = re.sub(rf"\b{re.escape(ab)}", ab.replace(".", "§DOT§"), masked, flags=re.IGNORECASE)
    masked = re.sub(r"(?<=\d)\.(?=\d)", "§DOT§", masked)
    parts = re.split(r"[.!?]\s+|\n+", masked)
    return [p.replace("§DOT§", ".").strip() for p in parts if p.strip()]


def compute_relative_deadlines(blocks: List[Block], explicit_dates: List[DateItem]) -> List[DateItem]:
    """
    Computes relative deadlines from 'within N days' phrases (FR-22).
    Enforces Working-Day Guard (T-102 / TD-03).
    """
    computed: List[DateItem] = []
    
    # Locate anchor dates (prefer received, fallback to issue)
    anchor_date: Optional[date] = None
    anchor_type = "issue"
    
    for d in explicit_dates:
        if d.role == "received" and d.date_iso:
            anchor_date = datetime.strptime(d.date_iso, "%Y-%m-%d").date()
            anchor_type = "received"
            break
            
    if not anchor_date:
        for d in explicit_dates:
            if d.role == "issue" and d.date_iso:
                anchor_date = datetime.strptime(d.date_iso, "%Y-%m-%d").date()
                anchor_type = "issue"
                break

    for block in blocks:
        # Split block into sentences with abbreviation protection
        sentences = split_sentences(block.text)
        for sent in sentences:
            # 1. Working-day guard check (T-102 / TD-03)
            working_match = re.search(r"\bwithin\s+(\d+|[a-z\-]+)\s+(working|business|court)\s+days?\b", sent, re.IGNORECASE)
            if working_match:
                phrase = working_match.group(0)
                computed.append(DateItem(
                    raw=phrase,
                    date_iso=None,
                    role="deadline",
                    is_computed=True,
                    computation_basis="Response window specified in working days. Calendar date cannot be calculated without court calendar.",
                    block_id=block.id,
                    sentence=sent
                ))
                continue

            # 2. Standard calendar days relative match
            rel_match = re.search(r"\bwithin\s+(\d+|[a-z\-]+)\s+days?\b", sent, re.IGNORECASE)
            if rel_match and not re.search(r"\b(working|business|court)\b", sent, re.IGNORECASE):
                phrase = rel_match.group(0)
                num_str = rel_match.group(1).lower()
                
                days = None
                if num_str.isdigit():
                    days = int(num_str)
                elif num_str in WORD_NUMS:
                    days = WORD_NUMS[num_str]
                    
                if days is not None and anchor_date is not None:
                    calc_date = anchor_date + timedelta(days=days)
                    computed.append(DateItem(
                        raw=phrase,
                        date_iso=calc_date.isoformat(),
                        role="deadline",
                        is_computed=True,
                        computation_basis=f"Computed: {days} days from notice {anchor_type} date ({anchor_date.isoformat()}). Labelled as estimated.",
                        block_id=block.id,
                        sentence=sent
                    ))
                elif days is not None and anchor_date is None:
                    computed.append(DateItem(
                        raw=phrase,
                        date_iso=None,
                        role="deadline",
                        is_computed=True,
                        computation_basis=f"Response window of {days} days applies, but anchor date (issue or receipt) is not stated.",
                        block_id=block.id,
                        sentence=sent
                    ))

    return computed


def find_glossary_terms(full_text: str, glossary_data: List[Dict[str, str]]) -> List[GlossaryItem]:
    """Finds legal terms present in the document using whole-word matching (FR-50)."""
    matched: List[GlossaryItem] = []
    seen = set()
    
    for item in glossary_data:
        term = item["term"]
        pattern = rf"\b{re.escape(term)}\b"
        if re.search(pattern, full_text, re.IGNORECASE):
            norm_term = term.lower()
            if norm_term not in seen:
                seen.add(norm_term)
                matched.append(GlossaryItem(
                    term=term,
                    meaning=item["meaning"]
                ))
    return matched[:25]


def pick_key_sentences(blocks: List[Block], limit: int = 8) -> List[Tuple[str, str, str]]:
    """
    Picks top marker-scored sentences for deterministic rule-based claims.
    Returns list of (sentence_text, category_label, block_id).
    """
    candidates = []
    seen = set()
    
    for block in blocks:
        sentences = split_sentences(block.text)
        for s in sentences:
            s_clean = s.strip()
            if len(s_clean) < 15 or s_clean in seen:
                continue
                
            for pattern, label in KEY_SENTENCE_MARKERS:
                if re.search(pattern, s_clean, re.IGNORECASE):
                    seen.add(s_clean)
                    candidates.append((s_clean, label, block.id))
                    break
                    
    return candidates[:limit]


def get_procedural_steps(doc_type: str) -> List[str]:
    """Returns general administrative procedural steps based on document type (FR-70)."""
    steps = {
        "cheque_dishonour_notice": [
            "Preserve the physical notice, envelope, and postal acknowledgement card (or WhatsApp screenshot) showing the exact date of receipt.",
            "Calculate your 15-day response window strictly from the date of physical receipt, not the date printed on the letterhead.",
            "Obtain your bank account statements for the relevant period showing whether funds were available or why payment was stopped.",
            "Consult a licensed advocate immediately to draft and dispatch a formal legal reply within the 15-day window."
        ],
        "court_summons": [
            "Check the court name, suit number, and the exact next hearing date printed on the summons.",
            "Note the 30-day statutory timeline under Order VIII Rule 1 CPC to file your Written Statement of defence.",
            "Engage a licensed advocate to file a Vakalatnama and enter an appearance before the court on or before the hearing date.",
            "Ensure you do not ignore the summons, as failure to appear may lead to ex-parte proceedings against you."
        ],
        "eviction_notice": [
            "Verify the notice period stated against your written lease agreement or the standard statutory period under Section 106 Transfer of Property Act.",
            "Review your rent payment receipts, bank transfer records, and security deposit counterfoils.",
            "Check whether local state Rent Control legislation applies to your tenancy, which may provide statutory protection against summary eviction.",
            "Consult a legal aid advocate or civil lawyer before taking any step to vacate or reply."
        ],
        "consumer_complaint_summons": [
            "Inspect the complaint copy attached with the notice to examine the allegations of deficiency of service.",
            "Note the limitation period under Section 69 of the Consumer Protection Act, 2019 (2-year limit from cause of action).",
            "Prepare your written version and compilation of documents in defence within the statutory reply window."
        ],
        "tax_notice": [
            "Verify the notice authenticity on the official e-filing portal (eportal.incometax.gov.in) using the Document Identification Number (DIN).",
            "Check the specific section quoted (e.g. Section 142(1) or Section 148) and the prescribed compliance date.",
            "Gather the requested financial records, books of accounts, and bank statements for the relevant Assessment Year.",
            "Consult a Chartered Accountant or tax advocate before submitting a response online."
        ]
    }
    
    return steps.get(doc_type, [
        "Carefully read all pages of the document and record the date and manner in which you received it.",
        "Preserve the original document, envelopes, and all related communications or receipts.",
        "Identify whether any specific deadline is stated for response or appearance.",
        "Consult a licensed legal practitioner or legal aid clinic for advice on your specific legal rights."
    ])
