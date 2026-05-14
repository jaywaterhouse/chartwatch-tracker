from pydantic import BaseModel
from datetime import date
from typing import Optional

class ScanEntryBase(BaseModel):
    scan_date: date
    company: str
    code: str
    last_price: Optional[float] = None
    mo_change: Optional[float] = None
    yr_change: Optional[float] = None
    trend_type: str
    is_strong_demand: bool = False
    is_strong_supply: bool = False

class ScanEntryResponse(ScanEntryBase):
    id: int
    class Config:
        from_attributes = True
