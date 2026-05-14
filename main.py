from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import uvicorn
from database import SessionLocal, engine, Base
import models
import crud
import scraper

Base.metadata.create_all(bind=engine)

app = FastAPI(title="ChartWatch ASX Tracker")

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

@app.get("/")
def home():
    return {"message": "ChartWatch ASX Tracker is running! Go to /docs for API"}

@app.get("/scans")
def get_scans(db: Session = Depends(get_db)):
    return crud.get_all_scans(db)

@app.post("/run-scrape")
def run_scrape(db: Session = Depends(get_db)):
    url = scraper.get_latest_chartwatch_url()
    if url:
        data = scraper.parse_chartwatch_page(url)
        if data:
            crud.create_scan_entries(db, data)
            return {"status": "success", "records_added": len(data), "message": f"Successfully scraped {len(data)} stocks"}
        else:
            return {"status": "warning", "message": "No data parsed from article"}
    return {"status": "error", "message": "Could not find latest ChartWatch article"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
