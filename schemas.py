from pydantic import BaseModel
from datetime import date
from typing import Optional

class ScanEntryResponse(BaseModel):
    id: int
    scan_date: date
    company: str
    code: str
    last_price: Optional[float] = None
    mo_change: Optional[float] = None
    yr_change: Optional[float] = None
    trend_type: str
    is_strong_demand: bool = False
    is_strong_supply: bool = False

    class Config:
        from_attributes = True
