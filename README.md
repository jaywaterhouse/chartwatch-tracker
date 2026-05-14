# ChartWatch ASX Tracker

Daily scanner for Carl Capolingua's ChartWatch ASX Scans from marketindex.com.au.

## Features
- Automatic daily scrape at ~9:15 AEST
- Tracks strongest excess demand & supply
- Historical rankings by frequency
- Professional dashboard

## Local Run
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000 in browser (frontend served via backend for now).
