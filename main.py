from fastapi import FastAPI, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, Boolean, desc, text
from sqlalchemy.orm import sessionmaker, Session, declarative_base
import requests
from bs4 import BeautifulSoup
import re
from datetime import date, datetime
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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}

BASE_URL = "https://www.marketindex.com.au"
CATEGORY_URL = f"{BASE_URL}/news/category/technical-analysis"


def get_latest_chartwatch_url() -> tuple[str | None, str]:
    """
    Scrape the technical-analysis category page and return the URL of
    the most recent ChartWatch ASX Scans article.
    Returns (url, error_message).
    """
    try:
        resp = requests.get(CATEGORY_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        return None, f"Failed to load category page: {e}"

    soup = BeautifulSoup(resp.text, "html.parser")

    # Strategy 1 — look for <a> tags whose href contains 'chartwatch'
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "chartwatch" in href.lower():
            full_url = href if href.startswith("http") else BASE_URL + href
            return full_url, ""

    # Strategy 2 — look inside article/card headings for "ChartWatch" text
    for tag in soup.find_all(["h2", "h3", "h4", "a"]):
        if "chartwatch" in tag.get_text(strip=True).lower():
            a = tag if tag.name == "a" else tag.find_parent("a") or tag.find("a")
            if a and a.get("href"):
                href = a["href"]
                full_url = href if href.startswith("http") else BASE_URL + href
                return full_url, ""

    # Strategy 3 — find any article link from the news listing JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                url = item.get("url", "")
                if "chartwatch" in url.lower():
                    return url, ""
        except (json.JSONDecodeError, AttributeError):
            continue

    return None, "No ChartWatch article link found on the category page."


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
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        return [], f"Failed to load article: {e}"

    soup = BeautifulSoup(resp.text, "html.parser")

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
def run_scrape(db: Session = Depends(get_db)):
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
  body { font-family: 'Inter', system-ui, sans-serif; }
  .trend-up   { color: #34d399; }
  .trend-down { color: #f87171; }
  .trend-side { color: #94a3b8; }
  .badge { display:inline-block; padding:2px 8px; border-radius:9999px; font-size:0.7rem; font-weight:600; }
  .badge-up   { background:#064e3b; color:#34d399; }
  .badge-down { background:#450a0a; color:#f87171; }
  .badge-side { background:#1e293b; color:#94a3b8; }
  .badge-demand { background:#1e3a5f; color:#93c5fd; }
  .badge-supply { background:#3b1e5f; color:#c4b5fd; }
  th { cursor:pointer; user-select:none; }
  th:hover { background:#334155; }
  .spinner { display:inline-block; width:16px; height:16px; border:2px solid #fff3;
             border-top-color:#fff; border-radius:50%; animation:spin .6s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
</style>
</head>
<body class="bg-gray-950 text-gray-100 min-h-screen">

<div class="max-w-7xl mx-auto px-4 py-8">

  <!-- Header -->
  <div class="flex flex-wrap items-center gap-4 mb-8">
    <div>
      <h1 class="text-3xl font-bold tracking-tight">📊 ChartWatch ASX Tracker</h1>
      <p class="text-gray-400 text-sm mt-1">Automated scraper for MarketIndex ChartWatch scans</p>
    </div>
    <div class="ml-auto flex gap-3">
      <button id="btnScrape" onclick="runScan()"
        class="bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-700 px-5 py-2.5 rounded-xl font-semibold text-sm transition-colors flex items-center gap-2">
        <span id="scrapeIcon">▶</span> Run Today's Scan
      </button>
      <button onclick="loadScans()"
        class="bg-slate-700 hover:bg-slate-600 px-5 py-2.5 rounded-xl font-semibold text-sm transition-colors">
        ↺ Refresh Table
      </button>
    </div>
  </div>

  <!-- Status banner -->
  <div id="status" class="hidden mb-4 p-4 rounded-xl text-sm font-mono"></div>

  <!-- Filters -->
  <div class="bg-gray-900 rounded-2xl p-4 mb-4 flex flex-wrap gap-4 items-end">
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
      <input id="filterSearch" oninput="filterTable()" placeholder="Code or company…"
        class="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm w-48">
    </div>
    <div class="ml-auto text-xs text-gray-500 self-center" id="rowCount"></div>
  </div>

  <!-- Table -->
  <div class="bg-gray-900 rounded-2xl overflow-hidden">
    <div class="overflow-x-auto">
      <table class="w-full text-sm" id="mainTable">
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
            Click "Run Today's Scan" or "Refresh Table" to load data.
          </td></tr>
        </tbody>
      </table>
    </div>
  </div>

</div>

<script>
let allRows = [];
let sortKey = 'code';
let sortAsc = true;

async function runScan() {
  const btn = document.getElementById('btnScrape');
  const icon = document.getElementById('scrapeIcon');
  btn.disabled = true;
  icon.innerHTML = '<span class="spinner"></span>';
  showStatus('info', 'Fetching the latest ChartWatch article…');

  try {
    const res = await fetch('/run-scrape', { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showStatus('success',
        `✅ Scraped <strong>${data.records_found}</strong> records — ` +
        `<strong>${data.records_inserted}</strong> new rows inserted for ${data.scan_date}. ` +
        `<a href="${data.article_url}" target="_blank" class="underline text-blue-400">View article ↗</a>`
      );
      await loadDates();
      await loadScans();
    } else {
      showStatus('error', `❌ ${data.message || 'Unknown error'}`
        + (data.article_url ? ` — Article: <a href="${data.article_url}" target="_blank" class="underline">${data.article_url}</a>` : '')
      );
    }
  } catch (e) {
    showStatus('error', `❌ Network error: ${e.message}`);
  } finally {
    btn.disabled = false;
    icon.textContent = '▶';
  }
}

async function loadDates() {
  const res = await fetch('/dates');
  const dates = await res.json();
  const sel = document.getElementById('filterDate');
  const current = sel.value;
  sel.innerHTML = '<option value="">— All dates —</option>'
    + dates.map(d => `<option value="${d.date}" ${d.date === current ? 'selected':''}>
        ${d.date} (${d.count} stocks)
      </option>`).join('');
}

async function loadScans() {
  const dateVal   = document.getElementById('filterDate').value;
  const trendVal  = document.getElementById('filterTrend').value;
  const signalVal = document.getElementById('filterSignal').value;

  let url = '/scans?limit=1000';
  if (dateVal)  url += `&scan_date=${dateVal}`;
  if (trendVal) url += `&trend=${trendVal}`;
  if (signalVal === 'demand') url += '&strong_demand=true';
  if (signalVal === 'supply') url += '&strong_supply=true';

  const res = await fetch(url);
  const data = await res.json();
  allRows = data.results || [];
  renderTable();
}

function filterTable() {
  renderTable();
}

function sortTable(key) {
  if (sortKey === key) { sortAsc = !sortAsc; }
  else { sortKey = key; sortAsc = true; }
  renderTable();
}

function renderTable() {
  const search = document.getElementById('filterSearch').value.toLowerCase();
  let rows = allRows.filter(r =>
    !search ||
    (r.code   || '').toLowerCase().includes(search) ||
    (r.company|| '').toLowerCase().includes(search)
  );

  rows.sort((a, b) => {
    let av = a[sortKey], bv = b[sortKey];
    if (av == null) av = sortAsc ? Infinity : -Infinity;
    if (bv == null) bv = sortAsc ? Infinity : -Infinity;
    if (typeof av === 'string') av = av.toLowerCase();
    if (typeof bv === 'string') bv = bv.toLowerCase();
    return sortAsc ? (av > bv ? 1 : av < bv ? -1 : 0)
                   : (av < bv ? 1 : av > bv ? -1 : 0);
  });

  document.getElementById('rowCount').textContent = `${rows.length} row${rows.length !== 1 ? 's' : ''}`;

  const tbody = document.getElementById('tableBody');
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="px-4 py-8 text-center text-gray-500">No results.</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map(r => {
    const pct = v => v == null ? '<span class="text-gray-600">—</span>'
      : `<span class="${v >= 0 ? 'text-emerald-400' : 'text-red-400'}">${v >= 0 ? '+' : ''}${v.toFixed(2)}%</span>`;

    const trendBadge = () => {
      if (!r.trend_type || r.trend_type === 'Unknown')
        return '<span class="badge badge-side">?</span>';
      if (r.trend_type === 'Uptrend')
        return '<span class="badge badge-up">↑ Uptrend</span>';
      if (r.trend_type === 'Downtrend')
        return '<span class="badge badge-down">↓ Downtrend</span>';
      return `<span class="badge badge-side">${r.trend_type}</span>`;
    };

    const signals = [
      r.is_strong_demand ? '<span class="badge badge-demand">Demand</span>' : '',
      r.is_strong_supply ? '<span class="badge badge-supply">Supply</span>' : '',
    ].filter(Boolean).join(' ');

    return `<tr class="hover:bg-gray-800/60 transition-colors">
      <td class="px-4 py-3 font-bold text-blue-300">
        <a href="https://www.marketindex.com.au/asx/${r.code.toLowerCase()}"
           target="_blank" class="hover:underline">${r.code}</a>
      </td>
      <td class="px-4 py-3 text-gray-300 max-w-[200px] truncate">${r.company || '—'}</td>
      <td class="px-4 py-3 text-gray-400 text-xs">${r.scan_date || '—'}</td>
      <td class="px-4 py-3 text-right font-mono">${r.last_price != null ? '$' + r.last_price.toFixed(2) : '—'}</td>
      <td class="px-4 py-3 text-right font-mono">${pct(r.mo_change)}</td>
      <td class="px-4 py-3 text-right font-mono">${pct(r.yr_change)}</td>
      <td class="px-4 py-3">${trendBadge()}</td>
      <td class="px-4 py-3">${signals || '<span class="text-gray-600">—</span>'}</td>
    </tr>`;
  }).join('');
}

function showStatus(type, html) {
  const el = document.getElementById('status');
  el.className = 'mb-4 p-4 rounded-xl text-sm ' + {
    success: 'bg-emerald-900/50 border border-emerald-700 text-emerald-200',
    error:   'bg-red-900/50 border border-red-700 text-red-200',
    info:    'bg-blue-900/50 border border-blue-700 text-blue-200',
  }[type];
  el.innerHTML = html;
  el.classList.remove('hidden');
}

// Load data on page open
(async () => { await loadDates(); await loadScans(); })();
</script>
</body>
</html>"""


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
