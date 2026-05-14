from sqlalchemy.orm import Session
from . import models

def create_scan_entries(db: Session, entries: list):
    for entry in entries:
        db_entry = models.ScanEntry(**entry)
        db.add(db_entry)
    db.commit()

def get_all_scans(db: Session):
    return db.query(models.ScanEntry).order_by(models.ScanEntry.scan_date.desc()).all()
