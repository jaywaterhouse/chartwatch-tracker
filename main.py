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

# ====================== CONFIG ======================
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

# ====================== IMPROVED SCRAPER ======================
def get_latest_chartwatch_url():
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get("https://www.marketindex.com.au/news/category/technical-analysis", 
                           headers=headers, timeout=20)
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        for a in soup.find_all('a', href=True):
            href = a['href'].lower()
            if 'chartwatch-asx-scans' in href:
                full_url = "https://www.marketindex.com.au" + href if href.startswith('/') else href
                print(f"✅ Found article: {full_url}")
                return full_url
        print("❌ No ChartWatch link found")
        return None
    except Exception as e:
        print(f"Error getting news page: {e}")
        return None


def parse_chartwatch_page(url: str):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(resp.text, 'html.parser')
        text = soup.get_text()

        entries = []
        scan_date = date.today()

        # Parse tables
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            if len(rows) < 2: continue
            
            trend_type = "up" if "Uptrends" in table.get_text() else "down"
            
            for row in rows[1:]:
                cols = [td.get_text(strip=True) for td in row.find_all('td')]
                if len(cols) < 4: continue
                try:
                    company = cols[0]
                    code = cols[1].upper().strip()
                    if len(code) > 6 or not code.isalnum(): continue
                    
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

        # Strongest Demand / Supply
        demand_match = re.search(r'strongest excess demand.*?:(.+?)(?=####|The stocks|Downtrends)', text, re.I | re.S)
        supply_match = re.search(r'strongest excess supply.*?:(.+?)(?=####|The stocks|Uptrends)', text, re.I | re.S)

        demand_codes = re.findall(r'\(([A-Z0-9]{2,5})\)', demand_match.group(1) if demand_match else "")
        supply_codes = re.findall(r'\(([A-Z0-9]{2,5})\)', supply_match.group(1) if supply_match else "")

        for e in entries:
            if e["code"] in demand_codes:
                e["is_strong_demand"] = True
            if e["code"] in supply_codes:
                e["is_strong_supply"] = True

        print(f"✅ Parsed {len(entries)} stocks")
        return entries
    except Exception as e:
        print(f"Parse error: {e}")
        return []

# ====================== ROUTES ======================
@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
    <head><title>ChartWatch ASX Tracker</title>
    <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-gray-900 text-white p-8">
        <div class="max-w-5xl mx-auto">
            <h1 class="text-4xl font-bold mb-2">📊 ChartWatch ASX Tracker</h1>
            <p class="text-gray-400 mb-8">Daily Strongest Excess Demand & Supply</p>
            
            <button onclick="runScan()" 
                    class="bg-green-600 hover:bg-green-700 px-8 py-4 rounded-2xl text-xl font-semibold mb-8">
                Run Today's Scan
            </button>
            
            <div id="result" class="text-sm"></div>
        </div>
        <script>
            async function runScan() {
                const res = await fetch('/run-scrape', {method: 'POST'});
                const data = await res.json();
                document.getElementById('result').innerHTML = `<pre class="bg-gray-800 p-6 rounded-xl">${JSON.stringify(data, null, 2)}</pre>`;
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
        return {"status": "error", "message": "Failed to parse the article."}

    # Simple save
    for item in data:
        entry = ScanEntry(**item)
        db.add(entry)
    db.commit()

    return {
        "status": "success", 
        "records": len(data),
        "message": f"Successfully saved {len(data)} stocks"
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
