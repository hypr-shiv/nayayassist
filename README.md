# NyayaAssist (न्याय Assist)
> **Local-First AI Legal Document Intelligence Platform for Indian Citizens**

[![Tests: Passing (28/28)](https://img.shields.io/badge/tests-28%20passed%20(100%25)-success)](#verification)
[![Invariants: INV--01--10 Active](https://img.shields.io/badge/trust%20invariants-INV--01%20to%20INV--10-blue)](#the-10-trust-invariants)
[![Local-First: Zero Paid APIs](https://img.shields.io/badge/architecture-local--first%20%7C%20zero%20cost-green)](#architecture)
[![License: MIT / Apache 2.0](https://img.shields.io/badge/license-MIT-purple)](#license)

---

## 1. What is NyayaAssist?

NyayaAssist helps ordinary citizens in India understand legal notices, court summons, and eviction letters. When a user uploads a PDF or photo, the system:
1. **Extracts layout and selectable text** with automated OCR fallback for phone photos.
2. **Classifies the notice type** across 7 common Indian legal dispute categories.
3. **Identifies dates and computes response deadlines** (with strict working-day guards).
4. **Explains statements in plain language** (≤25 words per point).
5. **Provides 100% click-to-highlight traceability** linking every claim directly to a bounding box on the original document.
6. **Grounds claims in verified India Code statutes** (e.g. Section 138 NI Act, Order VIII Rule 1 CPC).
7. **Answers in-document follow-up questions** with zero hallucination.
8. **Exports deadlines to calendars** (.ics format with 2-day reminder alarms).

> [!IMPORTANT]
> **Legal Information, Not Legal Advice**: NyayaAssist provides objective factual extraction and deadline tracking to help citizens prepare. It strictly refuses to provide litigation strategy or case outcome predictions (avoiding Unauthorised Practice of Law - UPL).

---

## 2. Quickstart & Local Setup

### Prerequisites
* Python 3.11+
* (Optional for OCR) Tesseract OCR installed locally (`eng` or `eng+hin`)
* (Optional for LLM Mode) [Ollama](https://ollama.ai) with `qwen2.5:3b` (optimized for 8 GB RAM systems)

### Installation
```bash
# Clone the repository
git clone https://github.com/your-org/nyayaassist.git
cd nyayaassist

# Install dependencies
pip install -r requirements.txt
```

### Run the Local Web Server
```bash
uvicorn app.main:app --reload --port 8000
```
Open [http://localhost:8000](http://localhost:8000) in your browser to access the interactive Dual-Pane Workbench.

---

## 3. Running the Test Suite

NyayaAssist enforces a strict test-driven development workflow verifying all 10 Trust Invariants:

```bash
pytest
```
*Current test suite: **28 passed (100% success rate) in 2.5s**.*

---

## 4. The 10 Trust Invariants

| ID | Invariant Rule | What Code Does |
|---|---|---|
| **INV-01** | Every claim cites the document | Drop any claim missing a valid `D#` citation. Rule mode claims always quote. |
| **INV-02** | Discard unknown reference IDs | Strip hallucinated references not present in the known `SourcePack`. |
| **INV-03** | Token-boundary number check | Verify numeric figures using `\bN\b` regex; downgrade to Red if unsupported. |
| **INV-04** | Explicit rule-based scoring | Confidence (`green`, `yellow`, `red`) is computed exclusively in code (`_score()`). |
| **INV-05** | Unverified statute caps at yellow | Unverified India Code statutes permanently cap confidence at Yellow. |
| **INV-06** | Computed dates cap at yellow | Relative deadlines (e.g. "within 15 days") are labelled "(estimated)" and capped at Yellow. |
| **INV-07** | Mandatory statutory disclaimer | Every API response and calendar event embeds `config.DISCLAIMER`. |
| **INV-08** | Schema validation & fallback | JSON parse or validation failures gracefully fall back to deterministic rules. |
| **INV-09** | Untrusted document input | Document text is enclosed in `<doc_sentence>` XML delimiters with zero tool access. |
| **INV-10** | Zero document text in logs | Application logs, traces, and error payloads scrub all document text and PII. |

---

## 5. Architectural Repository Documentation

| Document | Purpose |
|---|---|
| [**PRD.md**](PRD.md) | Product Requirements Document, user personas, and backlog. |
| [**ARCHITECTURE.md**](ARCHITECTURE.md) | Technical system boundaries, data flow, and invariant map. |
| [**DESIGN.md**](DESIGN.md) | Anti-AI-slop design system, dual-workbench layout, and CSS tokens. |
| [**DATABASE.md**](DATABASE.md) | PostgreSQL 16 schema, pgvector hybrid retrieval, and DPDPA retention. |
| [**AGENTS.md**](AGENTS.md) | Autonomous agent operating rules, coding standards, and PR checklist. |
| [**PLAN.md**](PLAN.md) | Master phase-by-phase implementation and execution plan. |
| [**FEATURE.md**](FEATURE.md) | Functional feature specifications and 8 GB RAM hardware calibration. |
| [**AUTH.md**](AUTH.md) | Passwordless magic links, DPDPA consent modal, and Row-Level Security. |
| [**MCP.md**](MCP.md) | Model Context Protocol tools, resources, and IDE client integration. |
| [**INTEGRATION.md**](INTEGRATION.md) | Upstream ingestion webhooks, WhatsApp bot gateway, and Python SDK. |

---

## 6. Model Context Protocol (MCP) Integration

NyayaAssist includes a built-in MCP server for developer agents and legal aid clinics:

```bash
python -m app.mcp_server
```

### Config for Cursor / Antigravity / Claude Desktop:
```json
{
  "mcpServers": {
    "nyayaassist": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/path/to/nyayaassist"
    }
  }
}
```

---

## 7. License & Compliance
* Codebase: MIT License
* Statutes: Official government gazettes from [India Code](https://www.indiacode.nic.in)
* Compliance: Digital Personal Data Protection Act, 2023 (DPDPA)
