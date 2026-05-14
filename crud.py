from sqlalchemy.orm import Session
from .models import ScanEntry
from datetime import date

def create_scan_entries(db: Session, entries: list):
    for entry in entries:
        # Avoid duplicates
        existing = db.query(ScanEntry).filter(
            ScanEntry.scan_date == entry["scan_date"],
            ScanEntry.code == entry["code"]
        ).first()
        if not existing:
            db_entry = ScanEntry(**entry)
            db.add(db_entry)
    db.commit()

def get_all_scans(db: Session):
    return db.query(ScanEntry).order_by(ScanEntry.scan_date.desc()).all()
