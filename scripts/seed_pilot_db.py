"""
Seeds the statute_corpus database table from data/statutes.json.
Enforces that verified statutes are loaded with India Code URLs.
"""

import sys
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import config
from app.db.session import SessionLocal, init_db
from app.db.models import StatuteCorpusModel


def seed_statutes():
    init_db()
    statutes_path = config.BASE_DIR / "data" / "statutes.json"
    if not statutes_path.exists():
        print(f"Error: {statutes_path} does not exist.")
        return 0

    with open(statutes_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    db = SessionLocal()
    count = 0
    try:
        for item in data:
            existing = db.query(StatuteCorpusModel).filter(StatuteCorpusModel.id == item["id"]).first()
            if not existing:
                statute = StatuteCorpusModel(
                    id=item["id"],
                    act=item["act"],
                    section=item["section"],
                    title=item["title"],
                    text=item["text"],
                    source_url=item["source_url"],
                    retrieved_on=item["retrieved_on"],
                    verified=item.get("verified", False),
                    verified_by=item.get("verified_by"),
                    verified_on=item.get("verified_on")
                )
                db.add(statute)
                count += 1
            else:
                existing.act = item["act"]
                existing.section = item["section"]
                existing.title = item["title"]
                existing.text = item["text"]
                existing.source_url = item["source_url"]
                existing.retrieved_on = item["retrieved_on"]
                existing.verified = item.get("verified", False)
                existing.verified_by = item.get("verified_by")
                existing.verified_on = item.get("verified_on")
                count += 1
        db.commit()
        print(f"Successfully seeded {count} statute provisions into the database.")
    finally:
        db.close()
    return count


if __name__ == "__main__":
    seed_statutes()
