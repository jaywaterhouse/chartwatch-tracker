from fastapi import FastAPI, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, Boolean
from sqlalchemy.orm import sessionmaker, Session, declarative_base
import requests
from bs4 import BeautifulSoup
import re
from datetime import date
import uvicorn
import os

# ====================== CONFIG ======================
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./chartwatch.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ====================== MODELS ======================
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

# ====================== APP ======================
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
        resp = requests.get("https://www.marketindex.com.au/news/category/technical-analysis", 
                          headers={"User-Agent": "ChartWatchTracker/1.0"}, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            if 'chartwatch-asx-scans' in a['href'].lower():
                return "https://www.marketindex.com.au" + a['href']
    except Exception as e:
        print("URL fetch error:", e)
    return None

def parse_chartwatch_page(url):
    try:
        resp = requests.get(url, headers={"User-Agent": "ChartWatchTracker/1.0"}, timeout=20)
        soup = BeautifulSoup(resp.text, 'html.parser')
        text = soup.get_text()

        scan_date = date.today()
        entries = []

        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            if len(rows) < 2: continue
            trend_type = "up" if "Uptrends" in table.get_text() else "down"
            for row in rows[1:]:
                cols = [td.get_text(strip=True) for td in row.find_all('td')]
                if len(cols) < 4: continue
                try:
                    company = cols[0]
                    code = cols[1].upper().strip()
                    if len(code) > 5 or not code.isalnum(): continue
                    price = float(re.sub(r'[^\d.]', '', cols[2]))
                    mo = float(re.sub(r'[^\d.-]', '', cols[3]))
                    yr = float(re.sub(r'[^\d.-]', '', cols[4])) if len(cols) > 4 else 0.0
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

        # Strongest excess demand/supply
        demand_match = re.search(r'strongest excess demand.*?:(.+?)(?=####|The stocks|Downtrends|Strong)', text, re.I | re.S)
        supply_match = re.search(r'strongest excess supply.*?:(.+?)(?=####|The stocks|Uptrends|Strong)', text, re.I | re.S)

        demand_codes = re.findall(r'\(([A-Z0-9]{3,5})\)', str(demand_match.group(1)) if demand_match else "")
        supply_codes = re.findall(r'\(([A-Z0-9]{3,5})\)', str(supply_match.group(1)) if supply_match else "")

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
def home():
    return """
<!DOCTYPE html>
<html>
<head>
    <title>ChartWatch ASX Tracker</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body class="bg-gray-950 text-white min-h-screen">
    <div class="max-w-5xl mx-auto p-8">
        <div class="text-center mb-12">
            <h1 class="text-5xl font-bold mb-4">📈 ChartWatch ASX Tracker</h1>
            <p class="text-gray-400">Daily Strongest Excess Demand & Supply Scans</p>
        </div>
        
        <div class="flex gap-4 justify-center mb-12">
            <button onclick="runScan()" 
                    class="bg-emerald-600 hover:bg-emerald-700 px-10 py-5 rounded-2xl text-xl font-semibold flex items-center gap-3 transition">
                <span>🚀 Run Today's Scan</span>
            </button>
        </div>

        <div id="result" class="bg-gray-900 rounded-2xl p-6 text-sm font-mono min-h-[200px] whitespace-pre-wrap"></div>
        
        <div class="mt-12 text-center text-gray-500 text-sm">
            Best run after 9:15 AEST • Data from marketindex.com.au
        </div>
    </div>

    <script>
        async function runScan() {
            const btn = document.querySelector('button');
            const originalText = btn.innerHTML;
            btn.innerHTML = 'Running...';
            btn.disabled = true;
            
            try {
                const res = await fetch('/run-scrape', {method: 'POST'});
                const data = await res.json();
                document.getElementById('result').innerHTML = JSON.stringify(data, null, 2);
            } catch(e) {
                document.getElementById('result').innerHTML = 'Error: ' + e;
            }
            
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    </script>
</body>
</html>
    """

@app.post("/run-scrape")
def run_scrape(db: Session = Depends(get_db)):
    url = get_latest_chartwatch_url()
    if not url:
        return {"status": "error", "message": "Could not find latest ChartWatch article. Try again later."}
    
    data = parse_chartwatch_page(url)
    if not data:
        return {"status": "error", "message": "Failed to parse the article. The page layout may have changed."}

    count = 0
    for item in data:
        entry = ScanEntry(**item)
        db.add(entry)
        count += 1
    db.commit()

    return {
        "status": "success", 
        "records_added": count,
        "message": f"Successfully saved {count} stocks from today's ChartWatch scan.",
        "date": str(date.today())
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
