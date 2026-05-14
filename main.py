from fastapi import FastAPI, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, Boolean
from sqlalchemy.orm import sessionmaker, Session, declarative_base
import requests
from bs4 import BeautifulSoup
import re
from datetime import date
import uvicorn
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./chartwatch.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class ScanEntry(Base):
    __tablename__ = "scan_entries"
    id = Column(Integer, primary_key=True, index=True)
    scan_date = Column(Date, nullable=False)
    company = Column(String, nullable=False)
    code = Column(String(10), nullable=False)
    last_price = Column(Float)
    mo_change = Column(Float)
    yr_change = Column(Float)
    trend_type = Column(String(10))
    is_strong_demand = Column(Boolean, default=False)
    is_strong_supply = Column(Boolean, default=False)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="ChartWatch ASX Tracker")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ====================== DEBUG SCRAPER ======================
def get_latest_chartwatch_url():
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get("https://www.marketindex.com.au/news/category/technical-analysis", 
                           headers=headers, timeout=30)
        print(f"News page status: {resp.status_code}")
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        links_found = 0
        
        for a in soup.find_all('a', href=True):
            href = a['href'].lower()
            if 'chartwatch-asx-scans' in href:
                links_found += 1
                full_url = "https://www.marketindex.com.au" + href if href.startswith('/') else href
                print(f"Found ChartWatch link #{links_found}: {full_url}")
                return full_url  # Return the first (newest) one
        
        print(f"Total ChartWatch links found: {links_found}")
        return None
    except Exception as e:
        print(f"Error fetching news page: {e}")
        return None

def parse_chartwatch_page(url: str):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=30)
        soup = BeautifulSoup(resp.text, 'html.parser')
        text = soup.get_text()
        
        print(f"Successfully loaded article: {url}")
        return [{"status": "parsed", "records": 0, "url": url}]  # Temporary for testing
    except Exception as e:
        print(f"Parse error: {e}")
        return []

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
    <head><title>ChartWatch ASX Tracker</title>
    <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-gray-900 text-white p-8">
        <div class="max-w-4xl mx-auto">
            <h1 class="text-4xl font-bold mb-8">📊 ChartWatch ASX Tracker</h1>
            <button onclick="runScan()" class="bg-green-600 hover:bg-green-700 px-8 py-4 rounded-2xl text-xl font-semibold">
                Run Today's Scan (Debug Mode)
            </button>
            <div id="result" class="mt-8 text-sm font-mono"></div>
        </div>
        <script>
            async function runScan() {
                const res = await fetch('/run-scrape', {method: 'POST'});
                const data = await res.json();
                document.getElementById('result').innerHTML = `<pre>${JSON.stringify(data, null, 2)}</pre>`;
            }
        </script>
    </body>
    </html>
    """

@app.post("/run-scrape")
def run_scrape(db: Session = Depends(get_db)):
    print("=== Starting scan ===")
    url = get_latest_chartwatch_url()
    if not url:
        return {"status": "error", "message": "Could not find latest ChartWatch article. Check server logs."}
    
    data = parse_chartwatch_page(url)
    return {
        "status": "success", 
        "message": "Found and loaded the latest article!",
        "url": url
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
