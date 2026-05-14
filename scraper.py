import requests
from bs4 import BeautifulSoup
import re
from datetime import date
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "ChartWatchTracker/1.0 (+https://github.com/jaywaterhouse/chartwatch-tracker)"}

def get_latest_chartwatch_url():
    url = "https://www.marketindex.com.au/news/category/technical-analysis"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if 'chartwatch-asx-scans' in href.lower():
                return "https://www.marketindex.com.au" + href
        logger.warning("No ChartWatch link found")
        return None
    except Exception as e:
        logger.error(f"Error fetching news list: {e}")
        return None

def parse_chartwatch_page(url: str):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
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
            table_text = table.get_text().lower()
            trend_type = "up" if "uptrends" in table_text else "down"
            
            for row in rows[1:]:
                cols = [td.get_text(strip=True) for td in row.find_all('td')]
                if len(cols) < 4:
                    continue
                code = cols[1].strip().upper() if len(cols) > 1 else ""
                if not re.match(r'^[A-Z0-9]{3,5}$', code):
                    continue
                try:
                    company = cols[0]
                    price = float(re.sub(r'[^\d.]', '', cols[2]))
                    mo = float(re.sub(r'[^\d.-]', '', cols[3]))
                    yr = float(re.sub(r'[^\d.-]', '', cols[4])) if len(cols) > 4 else None
                    
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
        demand_match = re.search(r'strongest excess demand.*?:(.+?)(?=####|The stocks|strongest excess supply|Downtrends)', text, re.I | re.S)
        supply_match = re.search(r'strongest excess supply.*?:(.+?)(?=####|The stocks|strongest excess demand|Uptrends)', text, re.I | re.S)

        demand_codes = re.findall(r'\(([A-Z0-9]{3,5})\)', demand_match.group(1)) if demand_match else []
        supply_codes = re.findall(r'\(([A-Z0-9]{3,5})\)', supply_match.group(1)) if supply_match else []

        for entry in entries:
            if entry["code"] in demand_codes:
                entry["is_strong_demand"] = True
            if entry["code"] in supply_codes:
                entry["is_strong_supply"] = True

        logger.info(f"Parsed {len(entries)} entries from {scan_date}")
        return entries
    except Exception as e:
        logger.error(f"Error parsing page {url}: {e}")
        return []
