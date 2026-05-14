from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from .database import SessionLocal, engine, Base
from . import crud
import uvicorn
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(title="ChartWatch ASX Tracker", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/scans")
def get_scans(db: Session = Depends(get_db)):
    return crud.get_all_scans(db)

@app.post("/run-scrape")
def run_scrape(db: Session = Depends(get_db)):
    from .scraper import get_latest_chartwatch_url, parse_chartwatch_page
    url = get_latest_chartwatch_url()
    if url:
        data = parse_chartwatch_page(url)
        if data:
            crud.create_scan_entries(db, data)
            return {"status": "success", "records_added": len(data), "url": url}
        return {"status": "no data parsed"}
    return {"status": "no article found"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
