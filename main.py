from fastapi import FastAPI, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, Boolean, desc, text
from sqlalchemy.orm import sessionmaker, Session, declarative_base
import requests
try:
    import cloudscraper as _cloudscraper
    _HAS_CLOUDSCRAPER = True
except Exception:
    _cloudscraper = None
    _HAS_CLOUDSCRAPER = False
from bs4 import BeautifulSoup
import re
from datetime import date, datetime, timedelta
import uvicorn
import os
import json

# ====================== DATABASE SETUP ======================
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./chartwatch.db")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
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
    trend_type = Column(String(20))
    is_strong_demand = Column(Boolean, default=False)
    is_strong_supply = Column(Boolean, default=False)

    def to_dict(self):
        return {
            "id": self.id,
            "scan_date": self.scan_date.isoformat() if self.scan_date else None,
            "company": self.company,
            "code": self.code,
            "last_price": self.last_price,
            "mo_change": self.mo_change,
            "yr_change": self.yr_change,
            "trend_type": self.trend_type,
            "is_strong_demand": self.is_strong_demand,
            "is_strong_supply": self.is_strong_supply,
        }


Base.metadata.create_all(bind=engine)

app = FastAPI(title="ChartWatch ASX Tracker")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ====================== SCRAPER ======================

BASE_URL = "https://www.marketindex.com.au"
CATEGORY_URL = f"{BASE_URL}/news/category/technical-analysis"
SLUG_PATTERN = "chartwatch-asx-scans"


def _make_scraper():
    """
    Return a cloudscraper session if available (bypasses Cloudflare JS challenges),
    otherwise fall back to a requests.Session with browser-like headers.
    """
    if _HAS_CLOUDSCRAPER:
        try:
            return _cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
        except Exception:
            pass
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-AU,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    return s


def _fetch(url: str, scraper=None) -> tuple[str | None, int]:
    """Fetch a URL; returns (html, status_code). On failure returns (None, code)."""
    s = scraper or _make_scraper()
    try:
        r = s.get(url, timeout=30, allow_redirects=True)
        return (r.text if r.status_code == 200 else None), r.status_code
    except Exception:
        return None, 0


def _find_chartwatch_in_html(html: str) -> str | None:
    """Return first ChartWatch article URL found in an HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    # href match
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if SLUG_PATTERN in href.lower():
            return href if href.startswith("http") else BASE_URL + href
    # visible text match
    for tag in soup.find_all(["h2", "h3", "h4", "a"]):
        if "chartwatch" in tag.get_text(strip=True).lower():
            a = tag if tag.name == "a" else tag.find_parent("a") or tag.find("a")
            if a and a.get("href"):
                href = a["href"]
                return href if href.startswith("http") else BASE_URL + href
    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            for item in (data if isinstance(data, list) else [data]):
                u = item.get("url", "")
                if SLUG_PATTERN in u.lower():
                    return u
        except (json.JSONDecodeError, AttributeError):
            continue
    return None


def _guess_direct_urls() -> list[str]:
    """
    ChartWatch articles follow the slug pattern:
      /news/chartwatch-asx-scans-{day}-{month}-{year}
    e.g. /news/chartwatch-asx-scans-12-may-2025
    Try today and the previous 14 days to cover the weekly cadence.
    """
    today = date.today()
    return [
        f"{BASE_URL}/news/{SLUG_PATTERN}-{(today - timedelta(days=d)).day}"
        f"-{(today - timedelta(days=d)).strftime('%B').lower()}"
        f"-{(today - timedelta(days=d)).year}"
        for d in range(14)
    ]


def _ddg_search(scraper) -> str | None:
    """DuckDuckGo Lite HTML search — no API key, rarely rate-limited."""
    try:
        query = f"site:marketindex.com.au {SLUG_PATTERN}"
        url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
        html, _ = _fetch(url, scraper)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            # DuckDuckGo wraps links as uddg= query params
            m = re.search(r"uddg=(https?[^&]+)", href)
            clean = requests.utils.unquote(m.group(1)) if m else href
            if "marketindex.com.au" in clean and SLUG_PATTERN in clean.lower():
                return clean
    except Exception:
        pass
    return None


def get_latest_chartwatch_url() -> tuple[str | None, str]:
    """
    Multi-strategy discovery of the latest ChartWatch ASX Scans article.
    Returns (url, error_message).
    """
    scraper = _make_scraper()
    errors: list[str] = []

    # ── Strategy 1: Guess URL directly from date slug ────────────────────────
    for candidate in _guess_direct_urls():
        html, status = _fetch(candidate, scraper)
        if html and len(html) > 5000:   # real article page, not a redirect/404
            return candidate, ""
    errors.append("Direct URL guessing: no match in last 14 days.")

    # ── Strategy 2: Category page via cloudscraper ───────────────────────────
    html, status = _fetch(CATEGORY_URL, scraper)
    if html:
        url = _find_chartwatch_in_html(html)
        if url:
            return url, ""
        errors.append(f"Category page loaded (HTTP {status}) but no link found.")
    else:
        errors.append(f"Category page blocked (HTTP {status}).")

    # ── Strategy 3: DuckDuckGo HTML search ──────────────────────────────────
    url = _ddg_search(scraper)
    if url:
        return url, ""
    errors.append("DuckDuckGo search returned no results.")

    return None, " | ".join(errors)


def _parse_float(text: str) -> float | None:
    """Extract the first float from a string, ignoring % signs and whitespace."""
    text = text.strip().replace("%", "").replace(",", "")
    m = re.search(r"-?\d+\.?\d*", text)
    return float(m.group()) if m else None


def _is_valid_asx_code(code: str) -> bool:
    """ASX codes are 1-6 uppercase letters (sometimes with a digit at end)."""
    return bool(re.fullmatch(r"[A-Z]{1,5}[0-9]?", code.strip()))


def parse_chartwatch_page(url: str) -> tuple[list[dict], str]:
    """
    Download the ChartWatch article and extract stock entries.
    Returns (records, error_message).

    Each record is a dict with keys matching ScanEntry columns.
    """
    scraper = _make_scraper()
    scraper.headers["Referer"] = CATEGORY_URL
    html, status = _fetch(url, scraper)
    if not html:
        return [], f"Failed to load article (HTTP {status}): site blocked request or article not found."

    soup = BeautifulSoup(html, "html.parser")

    # --- Attempt 1: HTML <table> extraction ---
    records = _parse_tables(soup, url)
    if records:
        return records, ""

    # --- Attempt 2: Structured <div>/<span> rows ---
    records = _parse_div_rows(soup)
    if records:
        return records, ""

    # --- Attempt 3: Regex over plain text ---
    records = _parse_text_fallback(soup.get_text("\n"))
    if records:
        return records, ""

    return [], "Could not extract any stock data from the article."


def _parse_tables(soup: BeautifulSoup, source_url: str) -> list[dict]:
    """Try to extract entries from HTML tables in the article."""
    records = []
    tables = soup.find_all("table")

    for table in tables:
        headers_row = table.find("tr")
        if not headers_row:
            continue

        # Build a column-index map from header text
        col_map = {}
        for i, th in enumerate(headers_row.find_all(["th", "td"])):
            h = th.get_text(strip=True).lower()
            if any(k in h for k in ("company", "name", "stock")):
                col_map["company"] = i
            elif any(k in h for k in ("code", "ticker", "asx")):
                col_map["code"] = i
            elif any(k in h for k in ("price", "last")):
                col_map["last_price"] = i
            elif "mo" in h or "month" in h:
                col_map["mo_change"] = i
            elif "yr" in h or "year" in h:
                col_map["yr_change"] = i
            elif "trend" in h:
                col_map["trend_type"] = i

        if "code" not in col_map:
            continue  # not a stock table

        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue

            def cell(key):
                idx = col_map.get(key)
                return cells[idx].get_text(strip=True) if idx is not None and idx < len(cells) else ""

            code = cell("code").upper()
            if not _is_valid_asx_code(code):
                continue

            row_text = row.get_text(" ").lower()
            record = {
                "company": cell("company") or code,
                "code": code,
                "last_price": _parse_float(cell("last_price")),
                "mo_change": _parse_float(cell("mo_change")),
                "yr_change": _parse_float(cell("yr_change")),
                "trend_type": cell("trend_type") or _infer_trend(row_text),
                "is_strong_demand": "strong demand" in row_text or "demand" in row_text,
                "is_strong_supply": "strong supply" in row_text or "supply" in row_text,
            }
            records.append(record)

    return records


def _parse_div_rows(soup: BeautifulSoup) -> list[dict]:
    """
    Fallback: look for repeated div/li structures that contain ASX codes.
    Handles card-style layouts common in modern news sites.
    """
    records = []
    # ASX code pattern: 1-5 uppercase letters, possibly followed by one digit
    code_pattern = re.compile(r"\b([A-Z]{2,5}[0-9]?)\b")
    price_pattern = re.compile(r"\$?\s*(\d+\.\d{2,3})")
    pct_pattern = re.compile(r"([+-]?\d+\.?\d*)\s*%")

    # Look for elements that look like rows: contain an ASX code + a price
    for el in soup.find_all(["div", "li", "article", "section"]):
        txt = el.get_text(" ", strip=True)
        codes = code_pattern.findall(txt)
        prices = price_pattern.findall(txt)
        pcts = pct_pattern.findall(txt)

        if not codes or not prices:
            continue
        # Skip if too long (likely a full section, not a single row)
        if len(txt) > 500:
            continue

        code = codes[0]
        if not _is_valid_asx_code(code):
            continue

        trend = _infer_trend(txt.lower())
        records.append({
            "company": code,
            "code": code,
            "last_price": _parse_float(prices[0]) if prices else None,
            "mo_change": _parse_float(pcts[0]) if len(pcts) > 0 else None,
            "yr_change": _parse_float(pcts[1]) if len(pcts) > 1 else None,
            "trend_type": trend,
            "is_strong_demand": "strong demand" in txt.lower() or "demand" in txt.lower(),
            "is_strong_supply": "strong supply" in txt.lower() or "supply" in txt.lower(),
        })

    # De-duplicate by ASX code
    seen = set()
    unique = []
    for r in records:
        if r["code"] not in seen:
            seen.add(r["code"])
            unique.append(r)
    return unique


def _parse_text_fallback(text: str) -> list[dict]:
    """
    Last resort: regex over plain article text.
    Looks for patterns like:  BHP  $45.20  +3.5%  +12.1%  Uptrend
    """
    records = []
    # Match lines containing an ASX code followed by numeric data
    line_re = re.compile(
        r"\b([A-Z]{2,5}[0-9]?)\b"      # ASX code
        r"[^\n]{0,60}"                   # anything on same line
        r"\$?\s*(\d+\.\d{1,4})"         # price
        r"[^\n]{0,60}"
        r"([+-]?\d+\.?\d*)\s*%"         # first % (mo change)
        r"(?:[^\n]{0,30}([+-]?\d+\.?\d*)\s*%)?"  # optional second % (yr change)
    )
    seen = set()
    for m in line_re.finditer(text):
        code = m.group(1)
        if code in seen or not _is_valid_asx_code(code):
            continue
        seen.add(code)
        context = m.group(0).lower()
        records.append({
            "company": code,
            "code": code,
            "last_price": _parse_float(m.group(2)),
            "mo_change": _parse_float(m.group(3)),
            "yr_change": _parse_float(m.group(4)) if m.group(4) else None,
            "trend_type": _infer_trend(context),
            "is_strong_demand": "demand" in context,
            "is_strong_supply": "supply" in context,
        })
    return records


def _infer_trend(text: str) -> str:
    text = text.lower()
    if "uptrend" in text or "up trend" in text:
        return "Uptrend"
    if "downtrend" in text or "down trend" in text:
        return "Downtrend"
    if "sideways" in text or "neutral" in text:
        return "Sideways"
    return "Unknown"


def save_records(db: Session, records: list[dict], scan_date: date) -> int:
    """
    Insert records into the DB, skipping any (date, code) duplicates.
    Returns the number of new rows inserted.
    """
    # Get codes already stored for this date
    existing_codes = {
        row.code for row in db.query(ScanEntry.code)
        .filter(ScanEntry.scan_date == scan_date)
        .all()
    }

    inserted = 0
    for r in records:
        if r["code"] in existing_codes:
            continue
        entry = ScanEntry(scan_date=scan_date, **r)
        db.add(entry)
        inserted += 1

    if inserted:
        db.commit()

    return inserted


# ====================== API ENDPOINTS ======================

@app.post("/run-scrape")
def run_scrape(
    db: Session = Depends(get_db),
    manual_url: str | None = Query(None, description="Paste the article URL directly to skip auto-discovery"),
):
    if manual_url:
        url, err = manual_url.strip(), ""
    else:
        url, err = get_latest_chartwatch_url()

    if not url:
        return JSONResponse(status_code=502, content={"status": "error", "message": err})

    records, err = parse_chartwatch_page(url)
    if not records:
        return JSONResponse(status_code=502, content={
            "status": "error",
            "message": err or "Parser returned no records.",
            "article_url": url,
        })

    today = date.today()
    inserted = save_records(db, records, today)

    return {
        "status": "success",
        "article_url": url,
        "records_found": len(records),
        "records_inserted": inserted,
        "scan_date": today.isoformat(),
        "preview": records[:5],
    }


@app.get("/scans")
def get_scans(
    db: Session = Depends(get_db),
    scan_date: str | None = Query(None, description="Filter by date (YYYY-MM-DD)"),
    trend: str | None = Query(None, description="Filter by trend_type e.g. Uptrend"),
    strong_demand: bool | None = Query(None),
    strong_supply: bool | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    q = db.query(ScanEntry).order_by(desc(ScanEntry.scan_date), ScanEntry.code)

    if scan_date:
        try:
            d = datetime.strptime(scan_date, "%Y-%m-%d").date()
            q = q.filter(ScanEntry.scan_date == d)
        except ValueError:
            return JSONResponse(status_code=400, content={"error": "Invalid date format"})
    if trend:
        q = q.filter(ScanEntry.trend_type == trend)
    if strong_demand is not None:
        q = q.filter(ScanEntry.is_strong_demand == strong_demand)
    if strong_supply is not None:
        q = q.filter(ScanEntry.is_strong_supply == strong_supply)

    rows = q.limit(limit).all()
    return {"count": len(rows), "results": [r.to_dict() for r in rows]}


@app.get("/dates")
def get_available_dates(db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT DISTINCT scan_date, COUNT(*) as cnt FROM scan_entries GROUP BY scan_date ORDER BY scan_date DESC")
    ).fetchall()
    return [{"date": str(r[0]), "count": r[1]} for r in rows]


@app.delete("/scans")
def delete_scans_by_date(
    scan_date: str = Query(..., description="Date to delete (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
):
    try:
        d = datetime.strptime(scan_date, "%Y-%m-%d").date()
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid date format"})
    deleted = db.query(ScanEntry).filter(ScanEntry.scan_date == d).delete()
    db.commit()
    return {"deleted": deleted, "date": scan_date}


# ====================== FRONTEND ======================

@app.get("/debug-fetch")
def debug_fetch(url: str = Query(default=CATEGORY_URL)):
    """
    Fetch any URL with the scraper session and return status, headers,
    and the first 3000 chars of body. Useful for diagnosing 403s.
    """
    scraper = _make_scraper()
    try:
        resp = scraper.get(url, timeout=20)
        return {
            "url": url,
            "status_code": resp.status_code,
            "response_headers": dict(resp.headers),
            "body_preview": resp.text[:3000],
        }
    except requests.RequestException as e:
        return {"url": url, "error": str(e)}


@app.get("/", response_class=HTMLResponse)
def home():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ChartWatch ASX Tracker</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body { font-family: system-ui, sans-serif; }
  .badge { display:inline-block; padding:2px 8px; border-radius:9999px; font-size:.7rem; font-weight:600; }
  .badge-up   { background:#064e3b; color:#34d399; }
  .badge-down { background:#450a0a; color:#f87171; }
  .badge-side { background:#1e293b; color:#94a3b8; }
  .badge-demand { background:#1e3a5f; color:#93c5fd; }
  .badge-supply { background:#3b1e5f; color:#c4b5fd; }
  th { cursor:pointer; user-select:none; }
  th:hover { background:#334155; }
  .spinner { display:inline-block; width:14px; height:14px; border:2px solid #ffffff44;
             border-top-color:#fff; border-radius:50%; animation:spin .6s linear infinite; vertical-align:middle; }
  @keyframes spin { to { transform:rotate(360deg); } }
  #debugBox { font-family:monospace; font-size:.75rem; white-space:pre-wrap; word-break:break-all; }
</style>
</head>
<body class="bg-gray-950 text-gray-100 min-h-screen">
<div class="max-w-7xl mx-auto px-4 py-8 space-y-6">

  <!-- Header -->
  <div>
    <h1 class="text-3xl font-bold tracking-tight">📊 ChartWatch ASX Tracker</h1>
    <p class="text-gray-400 text-sm mt-1">Scraper for MarketIndex ChartWatch ASX Scans</p>
  </div>

  <!-- Scan card -->
  <div class="bg-gray-900 rounded-2xl p-5 space-y-4">
    <h2 class="font-semibold text-gray-200">Run Scan</h2>

    <!-- Auto scan row -->
    <div class="flex flex-wrap gap-3 items-center">
      <button id="btnAuto" onclick="runScan(false)"
        class="bg-emerald-600 hover:bg-emerald-500 px-5 py-2.5 rounded-xl font-semibold text-sm flex items-center gap-2 transition-colors">
        <span id="autoIcon">▶</span> Auto-detect latest article
      </button>
      <span class="text-gray-500 text-sm">or paste the article URL below</span>
    </div>

    <!-- Manual URL row -->
    <div class="flex flex-wrap gap-2">
      <input id="manualUrl" type="url"
        placeholder="https://www.marketindex.com.au/news/chartwatch-asx-scans-…"
        class="flex-1 min-w-0 bg-gray-800 border border-gray-700 rounded-xl px-4 py-2.5 text-sm
               placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500">
      <button onclick="runScan(true)"
        class="bg-blue-600 hover:bg-blue-500 px-5 py-2.5 rounded-xl font-semibold text-sm transition-colors whitespace-nowrap">
        Scrape this URL
      </button>
    </div>

    <!-- Status -->
    <div id="status" class="hidden p-4 rounded-xl text-sm"></div>
  </div>

  <!-- Debug panel (collapsed by default) -->
  <details class="bg-gray-900 rounded-2xl">
    <summary class="px-5 py-4 cursor-pointer font-semibold text-gray-300 text-sm select-none
                    hover:text-white transition-colors list-none flex items-center gap-2">
      <span>🔍 Debug: test what the scraper sees at any URL</span>
    </summary>
    <div class="px-5 pb-5 space-y-3 border-t border-gray-800 pt-4">
      <div class="flex gap-2">
        <input id="debugUrl" type="url" value="https://www.marketindex.com.au/news/category/technical-analysis"
          class="flex-1 bg-gray-800 border border-gray-700 rounded-xl px-4 py-2 text-sm
                 focus:outline-none focus:ring-2 focus:ring-yellow-500">
        <button onclick="runDebug()"
          class="bg-yellow-600 hover:bg-yellow-500 px-4 py-2 rounded-xl text-sm font-semibold transition-colors whitespace-nowrap">
          Fetch
        </button>
      </div>
      <div id="debugBox" class="bg-gray-950 rounded-xl p-4 text-gray-400 min-h-[60px] max-h-96 overflow-auto hidden"></div>
    </div>
  </details>

  <!-- Filters + table -->
  <div class="bg-gray-900 rounded-2xl p-4 flex flex-wrap gap-4 items-end">
    <div>
      <label class="block text-xs text-gray-400 mb-1">Scan Date</label>
      <select id="filterDate" onchange="loadScans()"
        class="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm min-w-[160px]">
        <option value="">— All dates —</option>
      </select>
    </div>
    <div>
      <label class="block text-xs text-gray-400 mb-1">Trend</label>
      <select id="filterTrend" onchange="loadScans()"
        class="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm">
        <option value="">All</option>
        <option value="Uptrend">Uptrend</option>
        <option value="Downtrend">Downtrend</option>
        <option value="Sideways">Sideways</option>
        <option value="Unknown">Unknown</option>
      </select>
    </div>
    <div>
      <label class="block text-xs text-gray-400 mb-1">Signal</label>
      <select id="filterSignal" onchange="loadScans()"
        class="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm">
        <option value="">All</option>
        <option value="demand">Strong Demand</option>
        <option value="supply">Strong Supply</option>
      </select>
    </div>
    <div>
      <label class="block text-xs text-gray-400 mb-1">Search</label>
      <input id="filterSearch" oninput="renderTable()" placeholder="Code or company…"
        class="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm w-44">
    </div>
    <button onclick="loadScans()" class="ml-auto bg-slate-700 hover:bg-slate-600 px-4 py-2 rounded-lg text-sm font-semibold transition-colors self-end">
      ↺ Refresh
    </button>
    <span id="rowCount" class="text-xs text-gray-500 self-end"></span>
  </div>

  <div class="bg-gray-900 rounded-2xl overflow-hidden">
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead class="bg-gray-800 text-gray-400 text-xs uppercase tracking-wider">
          <tr>
            <th onclick="sortTable('code')"       class="px-4 py-3 text-left">Code</th>
            <th onclick="sortTable('company')"    class="px-4 py-3 text-left">Company</th>
            <th onclick="sortTable('scan_date')"  class="px-4 py-3 text-left">Date</th>
            <th onclick="sortTable('last_price')" class="px-4 py-3 text-right">Price</th>
            <th onclick="sortTable('mo_change')"  class="px-4 py-3 text-right">Mo %</th>
            <th onclick="sortTable('yr_change')"  class="px-4 py-3 text-right">Yr %</th>
            <th onclick="sortTable('trend_type')" class="px-4 py-3 text-left">Trend</th>
            <th class="px-4 py-3 text-left">Signals</th>
          </tr>
        </thead>
        <tbody id="tableBody" class="divide-y divide-gray-800">
          <tr><td colspan="8" class="px-4 py-8 text-center text-gray-500">
            Run a scan or paste an article URL above to load data.
          </td></tr>
        </tbody>
      </table>
    </div>
  </div>

</div>
<script>
let allRows = [], sortKey = 'scan_date', sortAsc = false;

async function runScan(useManual) {
  const manualUrl = document.getElementById('manualUrl').value.trim();
  if (useManual && !manualUrl) {
    showStatus('error', '❌ Please paste an article URL first.'); return;
  }
  const btn = useManual
    ? document.querySelector('[onclick="runScan(true)"]')
    : document.getElementById('btnAuto');
  const icon = document.getElementById('autoIcon');
  btn.disabled = true;
  if (!useManual) icon.innerHTML = '<span class=\"spinner\"></span>';
  showStatus('info', useManual
    ? `Scraping <code class="text-blue-300">${manualUrl}</code>…`
    : 'Auto-detecting latest ChartWatch article…');

  let fetchUrl = '/run-scrape';
  if (useManual) fetchUrl += '?manual_url=' + encodeURIComponent(manualUrl);

  try {
    const res  = await fetch(fetchUrl, { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showStatus('success',
        `✅ <strong>${data.records_found}</strong> records found — ` +
        `<strong>${data.records_inserted}</strong> new rows saved for <strong>${data.scan_date}</strong>. ` +
        `<a href="${data.article_url}" target="_blank" class="underline text-blue-300">View article ↗</a>`);
      await loadDates(); await loadScans();
    } else {
      showStatus('error',
        `❌ ${data.message || 'Unknown error'}<br>` +
        (data.article_url
          ? `Article tried: <a href="${data.article_url}" target="_blank" class="underline text-blue-300">${data.article_url}</a><br>`
          : '') +
        `<span class="text-gray-400">Tip: open the article in your browser, copy the URL, and paste it in the manual field above.</span>`);
    }
  } catch (e) {
    showStatus('error', `❌ Network error: ${e.message}`);
  } finally {
    btn.disabled = false;
    if (!useManual) icon.textContent = '▶';
  }
}

async function runDebug() {
  const url = document.getElementById('debugUrl').value.trim();
  const box  = document.getElementById('debugBox');
  box.classList.remove('hidden');
  box.textContent = 'Fetching…';
  try {
    const res  = await fetch('/debug-fetch?url=' + encodeURIComponent(url));
    const data = await res.json();
    box.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    box.textContent = 'Error: ' + e.message;
  }
}

async function loadDates() {
  const res   = await fetch('/dates');
  const dates = await res.json();
  const sel   = document.getElementById('filterDate');
  const cur   = sel.value;
  sel.innerHTML = '<option value="">— All dates —</option>'
    + dates.map(d =>
        `<option value="${d.date}" ${d.date===cur?'selected':''}>
          ${d.date} (${d.count} stocks)
        </option>`).join('');
}

async function loadScans() {
  const dateVal   = document.getElementById('filterDate').value;
  const trendVal  = document.getElementById('filterTrend').value;
  const signalVal = document.getElementById('filterSignal').value;
  let url = '/scans?limit=1000';
  if (dateVal)              url += '&scan_date=' + dateVal;
  if (trendVal)             url += '&trend='     + trendVal;
  if (signalVal==='demand') url += '&strong_demand=true';
  if (signalVal==='supply') url += '&strong_supply=true';
  const res  = await fetch(url);
  const data = await res.json();
  allRows = data.results || [];
  renderTable();
}

function sortTable(key) {
  sortAsc = (sortKey === key) ? !sortAsc : true;
  sortKey = key;
  renderTable();
}

function renderTable() {
  const search = document.getElementById('filterSearch').value.toLowerCase();
  let rows = allRows.filter(r =>
    !search ||
    (r.code    || '').toLowerCase().includes(search) ||
    (r.company || '').toLowerCase().includes(search));

  rows.sort((a, b) => {
    let av = a[sortKey], bv = b[sortKey];
    if (av == null) av = sortAsc ?  Infinity : -Infinity;
    if (bv == null) bv = sortAsc ?  Infinity : -Infinity;
    if (typeof av === 'string') av = av.toLowerCase();
    if (typeof bv === 'string') bv = bv.toLowerCase();
    return sortAsc ? (av > bv ? 1 : av < bv ? -1 : 0)
                   : (av < bv ? 1 : av > bv ? -1 : 0);
  });

  document.getElementById('rowCount').textContent = rows.length + ' row' + (rows.length!==1?'s':'');
  const tbody = document.getElementById('tableBody');
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="px-4 py-8 text-center text-gray-500">No results.</td></tr>`;
    return;
  }
  const pct = v => v==null
    ? '<span class="text-gray-600">—</span>'
    : `<span class="${v>=0?'text-emerald-400':'text-red-400'}">${v>=0?'+':''}${v.toFixed(2)}%</span>`;
  const trendBadge = t => {
    if (!t||t==='Unknown') return '<span class="badge badge-side">?</span>';
    if (t==='Uptrend')   return '<span class="badge badge-up">↑ Up</span>';
    if (t==='Downtrend') return '<span class="badge badge-down">↓ Down</span>';
    return `<span class="badge badge-side">${t}</span>`;
  };
  tbody.innerHTML = rows.map(r => {
    const sigs = [
      r.is_strong_demand ? '<span class="badge badge-demand">Demand</span>' : '',
      r.is_strong_supply ? '<span class="badge badge-supply">Supply</span>' : '',
    ].filter(Boolean).join(' ') || '<span class="text-gray-600">—</span>';
    return `<tr class="hover:bg-gray-800/60 transition-colors">
      <td class="px-4 py-3 font-bold text-blue-300">
        <a href="https://www.marketindex.com.au/asx/${r.code.toLowerCase()}" target="_blank" class="hover:underline">${r.code}</a>
      </td>
      <td class="px-4 py-3 text-gray-300 max-w-[200px] truncate" title="${r.company||''}">${r.company||'—'}</td>
      <td class="px-4 py-3 text-gray-400 text-xs">${r.scan_date||'—'}</td>
      <td class="px-4 py-3 text-right font-mono">${r.last_price!=null?'$'+r.last_price.toFixed(2):'—'}</td>
      <td class="px-4 py-3 text-right font-mono">${pct(r.mo_change)}</td>
      <td class="px-4 py-3 text-right font-mono">${pct(r.yr_change)}</td>
      <td class="px-4 py-3">${trendBadge(r.trend_type)}</td>
      <td class="px-4 py-3">${sigs}</td>
    </tr>`;
  }).join('');
}

function showStatus(type, html) {
  const el = document.getElementById('status');
  el.className = 'p-4 rounded-xl text-sm ' + {
    success: 'bg-emerald-900/50 border border-emerald-700 text-emerald-200',
    error:   'bg-red-900/50 border border-red-700 text-red-200',
    info:    'bg-blue-900/50 border border-blue-700 text-blue-200',
  }[type];
  el.innerHTML = html;
  el.classList.remove('hidden');
}

(async () => { await loadDates(); await loadScans(); })();
</script>
</body>
</html>"""


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
