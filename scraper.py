import requests
from bs4 import BeautifulSoup
import re
from datetime import date
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "ChartWatchTracker/1.0"}

def get_latest_chartwatch_url():
    url = "https://www.marketindex.com.au/news/category/technical-analysis"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            if 'chartwatch-asx-scans' in a['href'].lower():
                return "https://www.marketindex.com.au" + a['href']
        return None
    except Exception as e:
        logger.error(f"Error fetching latest URL: {e}")
        return None

def parse_chartwatch_page(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, 'html.parser')
        text = soup.get_text()
        
        # Extract date
        date_match = re.search(r'(\d{1,2})\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{4})', text, re.I)
        scan_date = date.today()
        if date_match:
            try:
                from datetime import datetime
                dt = datetime.strptime(date_match.group(0), "%d %b %Y")
                scan_date = dt.date()
            except:
                pass

        entries = []
        
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            if len(rows) < 2:
                continue
            table_text = table.get_text()
            trend_type = "up" if "Uptrends" in table_text else "down"
            
            for row in rows[1:]:
                cols = [td.get_text(strip=True) for td in row.find_all('td')]
                if len(cols) < 4:
                    continue
                code = cols[1].strip().upper() if len(cols) > 1 else ""
                if not code or len(code) < 3:
                    continue
                try:
                    company = cols[0]
                    price_str = re.sub(r'[^\d.]', '', cols[2])
                    price = float(price_str) if price_str else None
                    mo_str = re.sub(r'[^\d.-]', '', cols[3])
                    mo = float(mo_str) if mo_str else None
                    yr = None
                    if len(cols) > 4:
                        yr_str = re.sub(r'[^\d.-]', '', cols[4])
                        yr = float(yr_str) if yr_str else None
                    
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
                except Exception:
                    continue

        # Strongest excess
        demand_match = re.search(r'strongest excess demand.*?:(.+?)(?=####|The stocks|Downtrends|$)', text, re.I | re.S)
        supply_match = re.search(r'strongest excess supply.*?:(.+?)(?=####|The stocks|Uptrends|$)', text, re.I | re.S)

        demand_codes = re.findall(r'\(([A-Z0-9]{3,5})\)', demand_match.group(1)) if demand_match else []
        supply_codes = re.findall(r'\(([A-Z0-9]{3,5})\)', supply_match.group(1)) if supply_match else []

        for entry in entries:
            if entry["code"] in demand_codes:
                entry["is_strong_demand"] = True
            if entry["code"] in supply_codes:
                entry["is_strong_supply"] = True

        return entries
    except Exception as e:
        logger.error(f"Error parsing page: {e}")
        return []
