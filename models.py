from sqlalchemy import Column, Integer, String, Float, Date, Boolean
from .database import Base
from datetime import date

class ScanEntry(Base):
    __tablename__ = "scan_entries"
    id = Column(Integer, primary_key=True, index=True)
    scan_date = Column(Date, nullable=False)
    company = Column(String, nullable=False)
    code = Column(String(10), nullable=False)
    last_price = Column(Float)
    mo_change = Column(Float)
    yr_change = Column(Float)
    trend_type = Column(String(10))  # up or down
    is_strong_demand = Column(Boolean, default=False)
    is_strong_supply = Column(Boolean, default=False)
