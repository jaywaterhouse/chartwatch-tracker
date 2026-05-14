from fastapi import FastAPI, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, Boolean
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.sql import func
import requests
from bs4 import BeautifulSoup
import re
from datetime import date
import uvicorn
import os

# ====================== DATABASE ======================
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./chartwatch.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class ScanEntry(Base):
    __tablename__ = "scan_entries"
    id = Column(Integer, primary_key=True, index=True)
    scan_date = Column(Date, nullable=False, index=True)
    company = Column(String, nullable=False)
    code = Column(String(10), nullable=False, index=True)
    last_price = Column(Float)
    mo_change = Column(Float)
    yr_change = Column(Float)
    trend_type = Column(String(10))  # up / down
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

# ====================== SCRAPER ======================
def get_latest_chartwatch_url():
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get("https://www.marketindex.com.au/news/category/technical-analysis", headers=headers, timeout=20)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        for a in soup.find_all('a', href=True):
            href = a['href'].lower()
            if 'chartwatch-asx-scans' in href and 'fortescue' in href:  # latest one
                full_url = "https://www.marketindex.com.au" + href if href.startswith('/') else href
                print(f"✅ Using article: {full_url}")
                return full_url
        return None
    except Exception as e:
        print("News list error:", e)
        return None

def parse_chartwatch_page(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(resp.text, 'html.parser')
        text = soup.get_text()
        scan_date = date.today()

        entries = []

        # Extract tables
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            if len(rows) < 3: 
                continue
            trend_type = "up" if any("Uptrends" in cell.get_text() for cell in table.find_all(['th','td'])) else "down"
            
            for row in rows[1:]:
                cols = [td.get_text(strip=True) for td in row.find_all('td')]
                if len(cols) < 4: 
                    continue
                try:
                    company = cols[0]
                    code = re.search(r'([A-Z0-9]{2,5})', cols[1] or "").group(1) if cols[1] else ""
                    if not code: 
                        continue
                    price = float(re.sub(r'[^0-9.]', '', cols[2] or "0"))
                    mo = float(re.sub(r'[^0-9.-]', '', cols[3] or "0"))
                    yr = float(re.sub(r'[^0-9.-]', '', cols[4] or "0")) if len(cols) > 4 else 0.0
                    
                    entries.append({
                        "scan_date": scan_date,
                        "company": company,
                        "code": code,
                        "last_price": price,
                        "mo_change": mo,
                        "yr_change": yr,
                        "trend_type": trend_type,
                        "is_strong_demand": False,
                        "is_strong_supply": False
                    })
                except:
                    continue

        # Strongest sections
        demand = re.search(r'strongest excess demand.*?:(.+?)(?=####|The stocks|Downtrends)', text, re.I|re.S)
        supply = re.search(r'strongest excess supply.*?:(.+?)(?=####|The stocks|Uptrends)', text, re.I|re.S)

        demand_codes = re.findall(r'\(([A-Z0-9]{2,5})\)', demand.group(1) if demand else "")
        supply_codes = re.findall(r'\(([A-Z0-9]{2,5})\)', supply.group(1) if supply else "")

        for e in entries:
            if e["code"] in demand_codes:
                e["is_strong_demand"] = True
            if e["code"] in supply_codes:
                e["is_strong_supply"] = True

        return entries
    except Exception as e:
        print("Parse error:", e)
        return []

# ====================== ROUTES ======================
@app.get("/", response_class=HTMLResponse)
def home(db: Session = Depends(get_db)):
    scans = db.query(ScanEntry).order_by(ScanEntry.scan_date.desc()).limit(20).all()
    return f"""
    <html>
    <head><title>ChartWatch ASX Tracker</title><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-gray-900 text-white p-8">
        <div class="max-w-6xl mx-auto">
            <h1 class="text-4xl font-bold mb-2">📊 ChartWatch ASX Tracker</h1>
            <p class="mb-8 text-gray-400">Daily Excess Demand & Supply Scans</p>
            
            <button onclick="runScan()" class="bg-green-600 hover:bg-green-700 px-8 py-4 rounded-2xl text-xl font-semibold mb-10">
                Run Today's Scan
            </button>
            
            <div id="result" class="mb-12"></div>
            
            <h2 class="text-2xl mb-4">Recent Scans</h2>
            <div class="bg-gray-800 rounded-xl p-6">
                {len(scans)} records in database
            </div>
        </div>
        <script>
            async function runScan() {{
                const res = await fetch('/run-scrape', {{method: 'POST'}});
                const data = await res.json();
                document.getElementById('result').innerHTML = `<pre class="bg-gray-800 p-6 rounded-xl overflow-auto max-h-96">${{JSON.stringify(data, null, 2)}}</pre>`;
            }}
        </script>
    </body>
    </html>
    """

@app.post("/run-scrape")
def run_scrape(db: Session = Depends(get_db)):
    url = get_latest_chartwatch_url()
    if not url:
        return {"status": "error", "message": "Could not find latest ChartWatch article"}

    data = parse_chartwatch_page(url)
    if not data:
        return {"status": "error", "message": "Failed to parse data from article"}

    # Save (basic dedup)
    added = 0
    for item in data:
        existing = db.query(ScanEntry).filter(ScanEntry.scan_date == item["scan_date"], ScanEntry.code == item["code"]).first()
        if not existing:
            db.add(ScanEntry(**item))
            added += 1
    db.commit()

    return {
        "status": "success",
        "records_parsed": len(data),
        "records_added": added,
        "message": "Scan completed successfully"
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
