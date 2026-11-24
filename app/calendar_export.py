"""
iCalendar (.ics) serialization for NyayaAssist deadlines.
Pure serialization module (Architectural Rule 4.1). Must NOT do parsing or analysis.
Implements FR-90, FR-91, and TD-15.
"""

from datetime import datetime, date, timezone, timedelta
from typing import List, Literal

from app import config
from app.models import DateItem


def generate_ics_calendar(
    doc_id: str,
    filename: str,
    dates: List[DateItem],
    kind: Literal["deadline", "all"] = "deadline"
) -> str:
    """
    Builds RFC 5545 iCalendar data with all-day VEVENT blocks and T-2 day alarms.
    """
    valid_roles = ["deadline"] if kind == "deadline" else ["deadline", "hearing"]
    target_dates = [d for d in dates if d.role in valid_roles and d.date_iso]
    
    if not target_dates:
        raise ValueError("No dated events matching the criteria found for calendar export.")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//NyayaAssist//Legal Document Intelligence//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH"
    ]
    
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    
    for idx, item in enumerate(target_dates):
        # RFC 5545 all-day event requires DTEND to be the day after DTSTART
        try:
            start_dt = date.fromisoformat(item.date_iso)
            clean_start = start_dt.strftime("%Y%m%d")
            clean_end = (start_dt + timedelta(days=1)).strftime("%Y%m%d")
        except Exception:
            clean_start = item.date_iso.replace("-", "")
            clean_end = clean_start
            
        uid = f"nyaya-{doc_id}-{item.role}-{idx+1}@nyayaassist.in"
        
        role_label = "LEGAL DEADLINE" if item.role == "deadline" else "COURT HEARING"
        summary = f"⚠️ {role_label}: {filename}"
        
        desc_parts = [
            f"Stated/Computed Date: {item.date_iso}" + (" (Estimated)" if item.is_computed else ""),
            f"Raw text in document: \"{item.raw}\""
        ]
        if item.computation_basis:
            desc_parts.append(f"Basis: {item.computation_basis}")
        if item.sentence:
            desc_parts.append(f"Passage: \"{item.sentence}\"")
        desc_parts.append(f"DISCLAIMER: {config.DISCLAIMER}")
        
        description = "\\n\\n".join(desc_parts)
        
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART;VALUE=DATE:{clean_start}",
            f"DTEND;VALUE=DATE:{clean_end}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{description}",
            "STATUS:CONFIRMED",
            "TRANSP:TRANSPARENT",
            "BEGIN:VALARM",
            "TRIGGER:-P2D",
            "ACTION:DISPLAY",
            f"DESCRIPTION:Reminder: 2 days remaining for {role_label.lower()}",
            "END:VALARM",
            "END:VEVENT"
        ])
        
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
        
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
